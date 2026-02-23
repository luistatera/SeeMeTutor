"""
POC 03 - Multilingual Pedagogy

FastAPI + WebSocket proof of concept focused on strict multilingual tutoring
contracts:
- immersion
- guided bilingual
- auto language matching

It instruments language purity, guided adherence, fallback latency, and language
flip behavior while streaming real-time audio to Gemini Live.

Usage:
    cd pocs/03_multilingual
    uvicorn main:app --reload --port 8300
    # Open http://localhost:8300
"""

import asyncio
import base64
import binascii
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("poc_multilingual")

# ---------------------------------------------------------------------------
# Gemini backend: Vertex AI (same auth path as main app)
# ---------------------------------------------------------------------------
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", "seeme-tutor"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "europe-west1"))

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL = "gemini-live-2.5-flash-native-audio"
METRIC_PUSH_MIN_GAP_S = 0.15

_SUPPORTED_LANGUAGE_MODES = {"guided_bilingual", "immersion", "auto"}
_SUPPORTED_LANGS = {"en", "pt", "de"}

PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "luis": {
        "name": "Luis",
        "policy": {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "de-DE",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 5,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        },
    },
    "daughter": {
        "name": "Daughter",
        "policy": {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "pt-PT",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 5,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        },
    },
    "wife": {
        "name": "Wife",
        "policy": {
            "mode": "auto",
            "l1": "pt-PT",
            "l2": "en-US",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 5,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        },
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
You are SeeMe, a warm tutor for multilingual pedagogy testing.

ACTIVE PROFILE: {profile_name}
ACTIVE LANGUAGE CONTRACT:
{language_contract}

Hard behavior rules:
1. You may respond only in English, Portuguese, or German.
2. Use exactly one language per tutor turn.
3. Never mix languages in one response.
4. Respect the language contract strictly; do not improvise outside it.
5. When producing L2, speak slower and clearer than normal conversation.
6. If the learner is confused, simplify and reassure before continuing.
7. Keep responses concise (2 to 4 sentences).
8. Do not expose internal controls.

Mode behavior:
- immersion: stay in L2 unless fallback forces L1.
- guided_bilingual: you MUST strictly alternate languages every turn.
  On "explain" turns: respond ENTIRELY in L1. Every word must be L1.
  On "practice" turns: respond ENTIRELY in L2. Every word must be L2.
  The INTERNAL CONTROL tells you which phase is active. Obey it absolutely.
- auto: match learner language dynamically; default to L1 if uncertain.

You may receive hidden control updates starting with "INTERNAL CONTROL:".
These are mandatory instructions. Never mention or quote them.

At session start, greet the student in the contract language for this mode and
ask what they want to practice.
"""

CONFUSION_PATTERNS = [
    re.compile(
        r"\b(i\s*(?:do\s*not|don't)\s*(?:get|understand)|i\s*(?:am|'m)\s*confused|"
        r"i\s*(?:am|'m)\s*lost|not\s*sure|can\s*you\s*explain|what\s*does\s*that\s*mean)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ich\s*verstehe\s*(?:das\s*)?nicht|ich\s*bin\s*verwirrt|"
        r"keine\s*ahnung|ich\s*komme\s*nicht\s*mit|was\s*bedeutet\s*das)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(n[aã]o\s*entendi|nao\s*entendi|n[aã]o\s*percebi|nao\s*percebi|"
        r"estou\s*confus|pode\s*explicar|n[aã]o\s*sei|nao\s*sei|estou\s*perdid)\b",
        re.IGNORECASE,
    ),
]

WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")
SPACES_RE = re.compile(r"\s+")

LANG_MARKERS = {
    "en": {
        "the", "this", "that", "with", "what", "why", "how", "because", "are", "is",
        "you", "your", "can", "could", "would", "should", "understand", "practice",
    },
    "pt": {
        "não", "nao", "você", "voce", "porque", "como", "para", "com", "isso", "está",
        "estou", "uma", "que", "de", "do", "da", "obrigado", "entendi", "explicar",
    },
    "de": {
        "ich", "nicht", "und", "ist", "der", "die", "das", "du", "wir", "für", "ein",
        "eine", "den", "dem", "verstanden", "erklären", "bitte", "kann", "warum",
    },
}

SPECIAL_DE_CHARS = set("äöüß")
SPECIAL_PT_CHARS = set("ãõáàâéêíóôúç")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 03 - Multilingual Pedagogy")

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

_TRANSCRIPT_LABELS = {
    "tutor": "Tutor",
    "student": "Student",
    "event": "EVENT",
    "error": "ERROR",
}


# ---------------------------------------------------------------------------
# Session logging (JSONL + details + transcript files)
# ---------------------------------------------------------------------------
def _create_session_log(session_id: str):
    """Create per-session logs and return (fh, write_fn, close_fn)."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = LOGS_DIR / f"{ts}_{session_id}.jsonl"
    details_path = LOGS_DIR / f"{ts}_{session_id}_details.log"
    transcript_path = LOGS_DIR / f"{ts}_{session_id}_transcript.log"
    fh = open(jsonl_path, "a", buffering=1)

    details_lines: list[str] = []
    transcript_lines: list[str] = []

    def write(source: str, event: str, **extra):
        now = datetime.datetime.now()
        entry = {
            "ts": now.isoformat(timespec="milliseconds"),
            "t": round(time.time() * 1000),
            "src": source,
            "event": event,
            **extra,
        }
        fh.write(json.dumps(entry) + "\n")

        text = extra.get("text", "")
        if source != "client" or not text:
            return

        if event.startswith("transcript_"):
            tr_type = event[len("transcript_") :]
            label = _TRANSCRIPT_LABELS.get(tr_type, tr_type.upper())
            ts_short = now.strftime("%H:%M:%S")
            transcript_lines.append(f"{ts_short} {label}: {text}")
        else:
            ms = f"{now.microsecond // 1000:03d}"
            ts_detail = now.strftime("%H:%M:%S.") + ms
            details_lines.append(f"[{ts_detail}] {text}")

    def close_logs():
        fh.close()

        details_text = "\n".join(reversed(details_lines))
        transcript_text = "\n".join(reversed(transcript_lines))

        details_path.write_text(details_text + ("\n" if details_text else ""))
        transcript_path.write_text(transcript_text + ("\n" if transcript_text else ""))

        details_rollup = LOGS_DIR / "details.log"
        transcript_rollup = LOGS_DIR / "transcript.log"
        with details_rollup.open("a") as out:
            out.write(f"=== session {session_id} ({ts}) ===\n")
            if details_text:
                out.write(details_text + "\n")
        with transcript_rollup.open("a") as out:
            out.write(f"=== session {session_id} ({ts}) ===\n")
            if transcript_text:
                out.write(transcript_text + "\n")

    logger.info("Session log: %s", jsonl_path)
    return fh, write, close_logs


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------
def _parse_int(value: Any, fallback: int, minimum: int = 1, maximum: int = 8) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _language_label(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized.startswith("en"):
        return "English"
    if normalized.startswith("pt"):
        return "Portuguese"
    if normalized.startswith("de"):
        return "German"
    return code or "English"


def _language_short(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("pt"):
        return "pt"
    if normalized.startswith("de"):
        return "de"
    return "en"


def _default_language_policy() -> dict[str, Any]:
    return {
        "mode": "auto",
        "l1": "en-US",
        "l2": "en-US",
        "explain_language": "l1",
        "practice_language": "l2",
        "no_mixed_language_same_turn": True,
        "max_l2_turns_before_recap": 5,
        "confusion_fallback": {
            "after_confusions": 2,
            "fallback_language": "l1",
            "fallback_turns": 2,
        },
    }


def _normalize_language_policy(policy: dict | None, fallback: dict[str, Any]) -> dict[str, Any]:
    source = policy if isinstance(policy, dict) else {}
    source_confusion = source.get("confusion_fallback") if isinstance(source.get("confusion_fallback"), dict) else {}
    fallback_confusion = fallback.get("confusion_fallback") if isinstance(fallback.get("confusion_fallback"), dict) else {}

    mode = str(source.get("mode") or fallback.get("mode") or "auto").strip().lower()
    if mode not in _SUPPORTED_LANGUAGE_MODES:
        mode = str(fallback.get("mode") or "auto")

    return {
        "mode": mode,
        "l1": str(source.get("l1") or fallback.get("l1") or "en-US"),
        "l2": str(source.get("l2") or fallback.get("l2") or "en-US"),
        "explain_language": str(source.get("explain_language") or fallback.get("explain_language") or "l1"),
        "practice_language": str(source.get("practice_language") or fallback.get("practice_language") or "l2"),
        "no_mixed_language_same_turn": bool(
            source.get("no_mixed_language_same_turn")
            if source.get("no_mixed_language_same_turn") is not None
            else fallback.get("no_mixed_language_same_turn", True)
        ),
        "max_l2_turns_before_recap": _parse_int(
            source.get("max_l2_turns_before_recap"),
            _parse_int(fallback.get("max_l2_turns_before_recap"), 5, minimum=1, maximum=8),
            minimum=1,
            maximum=8,
        ),
        "confusion_fallback": {
            "after_confusions": _parse_int(
                source_confusion.get("after_confusions"),
                _parse_int(fallback_confusion.get("after_confusions"), 2, minimum=1, maximum=5),
                minimum=1,
                maximum=5,
            ),
            "fallback_language": str(
                source_confusion.get("fallback_language")
                or fallback_confusion.get("fallback_language")
                or "l1"
            ).strip().lower(),
            "fallback_turns": _parse_int(
                source_confusion.get("fallback_turns"),
                _parse_int(fallback_confusion.get("fallback_turns"), 2, minimum=1, maximum=6),
                minimum=1,
                maximum=6,
            ),
        },
    }


def _build_profile_policy(profile_id: str, mode_override: str = "") -> tuple[str, dict[str, Any]]:
    preset = PROFILE_PRESETS.get(profile_id) or PROFILE_PRESETS["luis"]
    profile_name = str(preset.get("name") or "Student")
    base_policy = _normalize_language_policy(preset.get("policy"), _default_language_policy())

    mode_override = str(mode_override or "").strip().lower()
    if mode_override in _SUPPORTED_LANGUAGE_MODES:
        base_policy["mode"] = mode_override

    return profile_name, base_policy


def _build_language_contract(language_policy: dict[str, Any]) -> str:
    mode = language_policy.get("mode", "auto")
    l1 = language_policy.get("l1", "en-US")
    l2 = language_policy.get("l2", "en-US")
    l1_label = _language_label(l1)
    l2_label = _language_label(l2)

    confusion = language_policy.get("confusion_fallback", {})
    after_confusions = _parse_int(confusion.get("after_confusions"), 2, minimum=1, maximum=5)
    fallback_turns = _parse_int(confusion.get("fallback_turns"), 2, minimum=1, maximum=6)
    fallback_key = str(confusion.get("fallback_language") or "l1").lower()
    fallback_label = l1_label if fallback_key == "l1" else l2_label

    max_l2 = _parse_int(language_policy.get("max_l2_turns_before_recap"), 5, minimum=1, maximum=8)

    contract_parts = [
        f"Mode: {mode}.",
        f"L1: {l1_label}.",
        f"L2: {l2_label}.",
        "No mixed language in the same turn.",
    ]

    if mode == "guided_bilingual":
        contract_parts.extend(
            [
                f"Explain mode: {l1_label}.",
                f"Practice mode: {l2_label}.",
                "Alternate explain and practice turns.",
                f"After {max_l2} consecutive L2 turns, produce one short L1 recap.",
            ]
        )
    elif mode == "immersion":
        contract_parts.extend(
            [
                f"Default output is {l2_label}.",
                f"Use {l1_label} only for fallback or explicit learner request.",
                f"After {max_l2} consecutive L2 turns, produce one short L1 recap.",
            ]
        )
    else:
        contract_parts.extend(
            [
                "Match learner language dynamically.",
                f"Default to {l1_label} when uncertain.",
            ]
        )

    contract_parts.append(
        f"If confusion is detected {after_confusions} times in a row, force {fallback_label} for {fallback_turns} tutor turns."
    )

    return " ".join(contract_parts)


# ---------------------------------------------------------------------------
# Language heuristics
# ---------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(str(text or ""))]


def _lang_score_from_tokens(tokens: list[str], original_text: str) -> dict[str, float]:
    scores = {"en": 0.0, "pt": 0.0, "de": 0.0}

    for token in tokens:
        for lang, marker_set in LANG_MARKERS.items():
            if token in marker_set:
                scores[lang] += 1.0

    lowered = str(original_text or "").lower()
    if any(ch in lowered for ch in SPECIAL_DE_CHARS):
        scores["de"] += 1.5
    if any(ch in lowered for ch in SPECIAL_PT_CHARS):
        scores["pt"] += 1.5

    return scores


def _detect_language(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return "unknown"

    toks = _tokens(t)
    if not toks:
        return "unknown"

    scores = _lang_score_from_tokens(toks, t)
    best_lang = max(scores, key=scores.get)
    best_score = scores[best_lang]
    if best_score < 1.0:
        return "unknown"

    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) >= 2 and sorted_scores[0] - sorted_scores[1] < 0.35:
        return "unknown"
    return best_lang


def _analyze_turn_language(text: str) -> dict[str, Any]:
    clean = SPACES_RE.sub(" ", str(text or "")).strip()
    if not clean:
        return {
            "primary": "unknown",
            "mixed": False,
            "lang_set": [],
            "word_counts": {"en": 0, "pt": 0, "de": 0},
            "total_words": 0,
        }

    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean) if p.strip()]
    if not pieces:
        pieces = [clean]

    lang_votes = {"en": 0, "pt": 0, "de": 0}
    word_counts = {"en": 0, "pt": 0, "de": 0}

    for piece in pieces:
        lang = _detect_language(piece)
        piece_words = len(_tokens(piece))
        if lang in _SUPPORTED_LANGS:
            lang_votes[lang] += 1
            word_counts[lang] += piece_words

    lang_set = [lang for lang, count in lang_votes.items() if count > 0]
    mixed = len(lang_set) > 1

    primary = "unknown"
    if lang_set:
        primary = max(lang_votes, key=lang_votes.get)

    total_words = sum(word_counts.values())
    return {
        "primary": primary,
        "mixed": mixed,
        "lang_set": lang_set,
        "word_counts": word_counts,
        "total_words": total_words,
    }


def _is_confusion_signal(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in CONFUSION_PATTERNS)


# ---------------------------------------------------------------------------
# Runtime language state helpers
# ---------------------------------------------------------------------------
def _resolve_language_key(key: str, runtime: dict[str, Any]) -> str:
    policy = runtime["policy"]
    k = str(key or "").strip().lower()
    if k == "l1":
        return runtime["l1_short"]
    if k == "l2":
        return runtime["l2_short"]
    if k in _SUPPORTED_LANGS:
        return k
    if policy.get("mode") == "auto":
        student_lang = runtime.get("last_student_lang", "unknown")
        if student_lang in _SUPPORTED_LANGS:
            return student_lang
    return runtime["l1_short"]


def _expected_language(runtime: dict[str, Any]) -> str:
    policy = runtime["policy"]

    if runtime.get("force_turns_remaining", 0) > 0:
        return _resolve_language_key(runtime.get("force_language_key", "l1"), runtime)

    mode = str(policy.get("mode") or "auto")
    if mode == "immersion":
        return runtime["l2_short"]

    if mode == "guided_bilingual":
        phase = runtime.get("guided_phase", "explain")
        key = policy.get("explain_language", "l1") if phase == "explain" else policy.get("practice_language", "l2")
        return _resolve_language_key(str(key), runtime)

    student_lang = runtime.get("last_student_lang", "unknown")
    if student_lang in _SUPPORTED_LANGS:
        return student_lang
    return runtime["l1_short"]


def _build_internal_control(runtime: dict[str, Any], reason: str) -> str:
    policy = runtime["policy"]
    mode = str(policy.get("mode") or "auto")
    expected = _expected_language(runtime)
    expected_label = _language_label(expected)

    l1_label = _language_label(runtime["policy"].get("l1", "en-US"))
    l2_label = _language_label(runtime["policy"].get("l2", "en-US"))

    control_parts = [
        "INTERNAL CONTROL: Language contract update.",
        f"Reason: {reason}.",
        f"Mode: {mode}.",
        f"L1={l1_label}, L2={l2_label}.",
        f"For the next tutor response, use {expected_label} only.",
        "Do not mix languages in one turn.",
        "In L2, keep slower and clearer pacing.",
    ]

    if mode == "guided_bilingual":
        phase = runtime.get("guided_phase", "explain")
        phase_lang = _language_label(runtime["policy"].get("l1", "en-US")) if phase == "explain" else _language_label(runtime["policy"].get("l2", "en-US"))
        control_parts.append(f"Guided phase: {phase}. You MUST respond ENTIRELY in {phase_lang}. This is non-negotiable. Every single word of your response must be in {phase_lang}.")

    if runtime.get("force_turns_remaining", 0) > 0:
        control_parts.append(
            f"Forced language lock active for {runtime.get('force_turns_remaining', 0)} turns."
        )

    return " ".join(control_parts)


async def _send_internal_control(
    session,
    runtime: dict[str, Any],
    metrics: dict[str, Any],
    slog,
    reason: str,
    *,
    force: bool = False,
) -> None:
    expected = _expected_language(runtime)
    signature = (
        expected,
        runtime.get("guided_phase"),
        runtime.get("force_language_key"),
        runtime.get("force_turns_remaining", 0),
        str(runtime["policy"].get("mode") or "auto"),
    )

    if (not force) and signature == runtime.get("last_control_signature"):
        return

    text = _build_internal_control(runtime, reason)
    await session.send_client_content(
        turns=types.Content(role="user", parts=[types.Part(text=text)]),
        turn_complete=False,
    )

    runtime["last_control_signature"] = signature
    metrics["control_prompts_sent"] += 1
    slog(
        "server",
        "internal_control_sent",
        reason=reason,
        expected_lang=expected,
        force=force,
        count=metrics["control_prompts_sent"],
    )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
def _avg(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _build_metric_snapshot(metrics: dict[str, Any]) -> dict[str, Any]:
    tutor_turns = int(metrics["tutor_turns"])
    single_turns = int(metrics["single_language_turns"])
    guided_expected = int(metrics["guided_expected_turns"])
    guided_matched = int(metrics["guided_matched_turns"])

    purity_rate = (single_turns / tutor_turns) * 100 if tutor_turns else 0.0
    guided_rate = (guided_matched / guided_expected) * 100 if guided_expected else 0.0

    l1_words = int(metrics["l1_words"])
    l2_words = int(metrics["l2_words"])
    l1_l2_total = l1_words + l2_words
    l2_ratio = (l2_words / l1_l2_total) * 100 if l1_l2_total else 0.0

    return {
        "turns": metrics["turn_completes"],
        "tutor_turns": tutor_turns,
        "purity_rate": round(purity_rate, 1),
        "mixed_turns": metrics["mixed_turns"],
        "guided_adherence": round(guided_rate, 1),
        "guided_expected_turns": guided_expected,
        "guided_matched_turns": guided_matched,
        "fallback_triggers": metrics["fallback_triggers"],
        "fallback_latency_avg_turns": round(_avg(metrics["fallback_latency_turns"]), 2),
        "fallback_latency_samples": len(metrics["fallback_latency_turns"]),
        "confusion_signals": metrics["confusion_signals"],
        "language_flips": metrics["language_flips"],
        "l1_words": l1_words,
        "l2_words": l2_words,
        "l2_ratio": round(l2_ratio, 1),
        "recap_triggers": metrics["recap_triggers"],
        "control_prompts_sent": metrics["control_prompts_sent"],
        "audio_chunks_out": metrics["audio_chunks_out"],
    }


async def _push_metric_snapshot(
    send_json,
    metrics: dict[str, Any],
    runtime: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    now = time.time()
    if not force and now - runtime["last_metric_push_at"] < METRIC_PUSH_MIN_GAP_S:
        return
    runtime["last_metric_push_at"] = now
    await send_json({"type": "language_metric", "data": _build_metric_snapshot(metrics)})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "index.html").read_text())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "poc": "03_multilingual"}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    session_id = f"poc3-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    profile_id = "luis"
    mode_override = ""
    try:
        raw_cfg = await asyncio.wait_for(websocket.receive_text(), timeout=6.0)
        cfg = json.loads(raw_cfg)
        if cfg.get("type") == "session_config":
            profile_id = str(cfg.get("profile_id") or "luis").strip().lower()
            mode_override = str(cfg.get("mode_override") or "").strip().lower()
    except Exception:
        logger.info("Session %s: no session_config received, using defaults", session_id)

    profile_name, language_policy = _build_profile_policy(profile_id, mode_override)
    language_contract = _build_language_contract(language_policy)

    runtime: dict[str, Any] = {
        "policy": language_policy,
        "l1_short": _language_short(language_policy.get("l1", "en-US")),
        "l2_short": _language_short(language_policy.get("l2", "en-US")),
        "guided_phase": "explain",
        "force_language_key": None,
        "force_turns_remaining": 0,
        "l2_streak": 0,
        "last_student_lang": "unknown",
        "last_tutor_lang": "unknown",
        "confusion_streak": 0,
        "confusion_grace_remaining": 0,
        "last_confusion_text": "",
        "last_confusion_at": 0.0,
        "last_control_signature": None,
        "last_metric_push_at": 0.0,
        "assistant_speaking": False,
        "current_turn_text_parts": [],
        "current_turn_transcript_parts": [],
        "last_student_transcript": "",
    }

    metrics: dict[str, Any] = {
        "turn_completes": 0,
        "tutor_turns": 0,
        "single_language_turns": 0,
        "mixed_turns": 0,
        "guided_expected_turns": 0,
        "guided_matched_turns": 0,
        "fallback_triggers": 0,
        "fallback_latency_turns": [],
        "fallback_pending_turn": None,
        "fallback_target_lang": "",
        "confusion_signals": 0,
        "language_flips": 0,
        "l1_words": 0,
        "l2_words": 0,
        "recap_triggers": 0,
        "control_prompts_sent": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
    }

    _, slog, close_logs = _create_session_log(session_id)
    send_lock = asyncio.Lock()

    async def send_json(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(payload))

    try:
        client = genai.Client()
        slog(
            "server",
            "session_start",
            profile_id=profile_id,
            profile_name=profile_name,
            mode=language_policy.get("mode"),
        )

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck"),
                )
            ),
            system_instruction=types.Content(
                parts=[
                    types.Part(
                        text=SYSTEM_PROMPT_TEMPLATE.format(
                            profile_name=profile_name,
                            language_contract=language_contract,
                        )
                    )
                ]
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=300,
                    silence_duration_ms=700,
                ),
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            await send_json(
                {
                    "type": "session_contract",
                    "data": {
                        "profile_id": profile_id,
                        "profile_name": profile_name,
                        "mode": language_policy.get("mode", "auto"),
                        "l1": _language_label(language_policy.get("l1", "en-US")),
                        "l2": _language_label(language_policy.get("l2", "en-US")),
                        "contract": language_contract,
                    },
                }
            )
            await _send_internal_control(session, runtime, metrics, slog, reason="session_start", force=True)
            await _push_metric_snapshot(send_json, metrics, runtime, force=True)

            forward_task = asyncio.create_task(
                _forward_browser_to_gemini(
                    websocket,
                    session,
                    session_id,
                    runtime,
                    metrics,
                    slog,
                ),
                name="browser_to_gemini",
            )
            receive_task = asyncio.create_task(
                _forward_gemini_to_browser(
                    websocket,
                    session,
                    session_id,
                    runtime,
                    metrics,
                    send_json,
                    slog,
                ),
                name="gemini_to_browser",
            )

            done, pending = await asyncio.wait(
                {forward_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc

    except Exception as exc:
        logger.exception("Session %s: error: %s", session_id, exc)
        try:
            await send_json({"type": "error", "data": str(exc)})
        except Exception:
            pass

    finally:
        snapshot = _build_metric_snapshot(metrics)
        _log_final_metrics(session_id, snapshot)
        slog("server", "session_end", **snapshot)
        close_logs()


# ---------------------------------------------------------------------------
# Browser -> Gemini
# ---------------------------------------------------------------------------
async def _forward_browser_to_gemini(
    websocket: WebSocket,
    session,
    session_id: str,
    runtime: dict[str, Any],
    metrics: dict[str, Any],
    slog,
):
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = message.get("type")

            if msg_type == "audio":
                encoded = message.get("data")
                if not encoded:
                    continue
                try:
                    audio_bytes = base64.b64decode(encoded)
                except binascii.Error:
                    continue

                metrics["audio_chunks_in"] += 1
                await session.send_realtime_input(
                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                )

            elif msg_type == "client_log":
                slog(
                    "client",
                    message.get("event", "log"),
                    text=message.get("text", ""),
                    **{k: v for k, v in message.items() if k not in ("type", "event", "text")},
                )

            elif msg_type == "activity_start":
                await session.send_realtime_input(activity_start=types.ActivityStart())
            elif msg_type == "activity_end":
                await session.send_realtime_input(activity_end=types.ActivityEnd())

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (forward)", session_id)
    except Exception as exc:
        logger.exception("Session %s: forward error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Gemini -> Browser
# ---------------------------------------------------------------------------
async def _forward_gemini_to_browser(
    websocket: WebSocket,
    session,
    session_id: str,
    runtime: dict[str, Any],
    metrics: dict[str, Any],
    send_json,
    slog,
):
    turn_index = 0

    try:
        while True:
            turn_index += 1
            turn_events = 0

            async for msg in session.receive():
                turn_events += 1

                if getattr(msg, "tool_call", None) is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                if getattr(server_content, "interrupted", False):
                    runtime["assistant_speaking"] = False
                    await send_json({"type": "interrupted", "data": {"source": "gemini"}})
                    slog("server", "gemini_interrupted")
                    continue

                turn_complete = bool(getattr(server_content, "turn_complete", False))

                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    for part in getattr(model_turn, "parts", None) or []:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is not None and inline_data.data:
                            runtime["assistant_speaking"] = True
                            metrics["audio_chunks_out"] += 1
                            encoded = base64.b64encode(inline_data.data).decode("utf-8")
                            await send_json({"type": "audio", "data": encoded})

                        text = getattr(part, "text", None)
                        if text:
                            clean_text = SPACES_RE.sub(" ", str(text)).strip()
                            if clean_text:
                                runtime["current_turn_text_parts"].append(clean_text)
                                slog("server", "tutor_text", text=clean_text)
                                await send_json({"type": "text", "data": clean_text})

                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        clean_student = SPACES_RE.sub(" ", str(transcript_text)).strip()
                        if clean_student and clean_student != runtime.get("last_student_transcript"):
                            runtime["last_student_transcript"] = clean_student
                            await send_json({"type": "input_transcript", "data": clean_student})
                            await _handle_student_transcript(
                                clean_student,
                                session,
                                runtime,
                                metrics,
                                send_json,
                                slog,
                            )

                output_transcription = getattr(server_content, "output_transcription", None)
                if output_transcription is not None:
                    transcript_text = getattr(output_transcription, "text", None)
                    if transcript_text:
                        clean_tutor = SPACES_RE.sub(" ", str(transcript_text)).strip()
                        if clean_tutor:
                            if not runtime["current_turn_transcript_parts"] or runtime["current_turn_transcript_parts"][-1] != clean_tutor:
                                runtime["current_turn_transcript_parts"].append(clean_tutor)
                            await send_json({"type": "output_transcript", "data": clean_tutor})

                if turn_complete:
                    metrics["turn_completes"] += 1
                    runtime["assistant_speaking"] = False

                    await _finalize_tutor_turn(
                        session,
                        runtime,
                        metrics,
                        send_json,
                        slog,
                    )

                    await send_json(
                        {
                            "type": "turn_complete",
                            "data": {"count": metrics["turn_completes"]},
                        }
                    )
                    await _push_metric_snapshot(send_json, metrics, runtime)

            if turn_events == 0:
                logger.info("Session %s: Gemini stream ended", session_id)
                return
            await asyncio.sleep(0)

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (receive)", session_id)
    except Exception as exc:
        logger.exception("Session %s: receive error: %s", session_id, exc)


async def _handle_student_transcript(
    text: str,
    session,
    runtime: dict[str, Any],
    metrics: dict[str, Any],
    send_json,
    slog,
) -> None:
    student_lang = _detect_language(text)
    if student_lang in _SUPPORTED_LANGS:
        runtime["last_student_lang"] = student_lang

    # Confusion detection with debounce for repeated partial chunks.
    now = time.time()
    is_confusion = _is_confusion_signal(text)
    normalized = text.strip().lower()

    if is_confusion:
        if normalized == runtime["last_confusion_text"] and (now - runtime["last_confusion_at"]) < 2.2:
            return

        runtime["last_confusion_text"] = normalized
        runtime["last_confusion_at"] = now
        runtime["confusion_streak"] += 1
        runtime["confusion_grace_remaining"] = 3
        metrics["confusion_signals"] += 1

        slog(
            "server",
            "confusion_signal",
            streak=runtime["confusion_streak"],
            count=metrics["confusion_signals"],
            lang=student_lang,
            text=text[:160],
        )
        await send_json(
            {
                "type": "language_event",
                "data": {
                    "event": "confusion_signal",
                    "streak": runtime["confusion_streak"],
                    "lang": student_lang,
                },
            }
        )

        confusion_cfg = runtime["policy"].get("confusion_fallback", {})
        threshold = _parse_int(confusion_cfg.get("after_confusions"), 2, minimum=1, maximum=5)
        fallback_turns = _parse_int(confusion_cfg.get("fallback_turns"), 2, minimum=1, maximum=6)
        fallback_key = str(confusion_cfg.get("fallback_language") or "l1").lower()

        if runtime["confusion_streak"] >= threshold and runtime.get("force_turns_remaining", 0) <= 0:
            runtime["confusion_streak"] = 0
            runtime["force_language_key"] = fallback_key
            runtime["force_turns_remaining"] = fallback_turns
            runtime["guided_phase"] = "explain"

            metrics["fallback_triggers"] += 1
            metrics["fallback_pending_turn"] = metrics["tutor_turns"] + 1
            metrics["fallback_target_lang"] = _resolve_language_key(fallback_key, runtime)

            await _send_internal_control(
                session,
                runtime,
                metrics,
                slog,
                reason="confusion_fallback",
                force=True,
            )

            await send_json(
                {
                    "type": "language_event",
                    "data": {
                        "event": "fallback_triggered",
                        "count": metrics["fallback_triggers"],
                        "forced_lang": metrics["fallback_target_lang"],
                        "fallback_turns": fallback_turns,
                    },
                }
            )
            slog(
                "server",
                "fallback_triggered",
                forced_lang=metrics["fallback_target_lang"],
                fallback_turns=fallback_turns,
                count=metrics["fallback_triggers"],
            )
    else:
        if runtime["confusion_streak"] > 0:
            runtime["confusion_grace_remaining"] -= 1
            if runtime["confusion_grace_remaining"] <= 0:
                runtime["confusion_streak"] = 0

    expected = _expected_language(runtime)
    if expected != runtime.get("last_announced_expected"):
        await _send_internal_control(
            session,
            runtime,
            metrics,
            slog,
            reason="student_language_update",
            force=False,
        )
        runtime["last_announced_expected"] = expected


async def _finalize_tutor_turn(
    session,
    runtime: dict[str, Any],
    metrics: dict[str, Any],
    send_json,
    slog,
) -> None:
    transcript_parts = [p for p in runtime["current_turn_transcript_parts"] if not p.startswith("INTERNAL CONTROL:")]
    text_parts = [p for p in runtime["current_turn_text_parts"] if not p.startswith("INTERNAL CONTROL:")]
    transcript_text = " ".join(transcript_parts).strip()
    part_text = " ".join(text_parts).strip()
    turn_text = transcript_text or part_text

    runtime["current_turn_transcript_parts"] = []
    runtime["current_turn_text_parts"] = []

    if not turn_text:
        return

    expected_lang = _expected_language(runtime)
    analysis = _analyze_turn_language(turn_text)
    primary = analysis["primary"]
    mixed = bool(analysis["mixed"])

    metrics["tutor_turns"] += 1

    if mixed:
        metrics["mixed_turns"] += 1
    else:
        metrics["single_language_turns"] += 1

    policy = runtime["policy"]
    mode = str(policy.get("mode") or "auto")

    if mode == "guided_bilingual":
        metrics["guided_expected_turns"] += 1
        if (not mixed) and (primary == expected_lang):
            metrics["guided_matched_turns"] += 1

    l1_short = runtime["l1_short"]
    l2_short = runtime["l2_short"]

    metrics["l1_words"] += int(analysis["word_counts"].get(l1_short, 0))
    metrics["l2_words"] += int(analysis["word_counts"].get(l2_short, 0))

    last_tutor_lang = runtime.get("last_tutor_lang", "unknown")
    if primary in _SUPPORTED_LANGS and last_tutor_lang in _SUPPORTED_LANGS and primary != last_tutor_lang:
        metrics["language_flips"] += 1
    if primary in _SUPPORTED_LANGS:
        runtime["last_tutor_lang"] = primary

    pending_turn = metrics.get("fallback_pending_turn")
    target_lang = str(metrics.get("fallback_target_lang") or "")
    if pending_turn is not None and target_lang in _SUPPORTED_LANGS and primary == target_lang:
        delta_turns = metrics["tutor_turns"] - int(pending_turn)
        metrics["fallback_latency_turns"].append(float(max(0, delta_turns)))
        metrics["fallback_pending_turn"] = None
        metrics["fallback_target_lang"] = ""

    if runtime.get("force_turns_remaining", 0) > 0:
        runtime["force_turns_remaining"] -= 1
        if runtime["force_turns_remaining"] <= 0:
            runtime["force_turns_remaining"] = 0
            runtime["force_language_key"] = None

    if primary == l2_short and not mixed:
        runtime["l2_streak"] += 1
    elif primary in _SUPPORTED_LANGS:
        runtime["l2_streak"] = 0

    max_l2 = _parse_int(policy.get("max_l2_turns_before_recap"), 5, minimum=1, maximum=8)
    if mode in {"immersion", "guided_bilingual"} and runtime["l2_streak"] >= max_l2 and runtime.get("force_turns_remaining", 0) == 0:
        runtime["l2_streak"] = 0
        runtime["force_language_key"] = "l1"
        runtime["force_turns_remaining"] = 1
        metrics["recap_triggers"] += 1

        await _send_internal_control(
            session,
            runtime,
            metrics,
            slog,
            reason="recap_after_l2_streak",
            force=True,
        )
        await send_json(
            {
                "type": "language_event",
                "data": {
                    "event": "recap_triggered",
                    "count": metrics["recap_triggers"],
                },
            }
        )

    if mode == "guided_bilingual" and runtime.get("force_turns_remaining", 0) == 0:
        runtime["guided_phase"] = "practice" if runtime.get("guided_phase") == "explain" else "explain"
        await _send_internal_control(
            session,
            runtime,
            metrics,
            slog,
            reason="guided_phase_switch",
            force=True,
        )

    slog(
        "server",
        "turn_language_eval",
        turn=metrics["tutor_turns"],
        expected=expected_lang,
        primary=primary,
        mixed=mixed,
        lang_set=analysis["lang_set"],
        text=turn_text[:220],
    )


# ---------------------------------------------------------------------------
# Final metrics summary
# ---------------------------------------------------------------------------
def _log_final_metrics(session_id: str, snapshot: dict[str, Any]) -> None:
    logger.info(
        "Session %s FINAL METRICS:\n"
        "  Turns=%d tutor_turns=%d\n"
        "  Purity=%.1f%% mixed=%d flips=%d\n"
        "  Guided adherence=%.1f%% (%d/%d)\n"
        "  Fallback triggers=%d latency_avg_turns=%.2f samples=%d\n"
        "  L2 ratio=%.1f%% (l1_words=%d l2_words=%d)\n"
        "  Recaps=%d controls=%d",
        session_id,
        snapshot["turns"],
        snapshot["tutor_turns"],
        snapshot["purity_rate"],
        snapshot["mixed_turns"],
        snapshot["language_flips"],
        snapshot["guided_adherence"],
        snapshot["guided_matched_turns"],
        snapshot["guided_expected_turns"],
        snapshot["fallback_triggers"],
        snapshot["fallback_latency_avg_turns"],
        snapshot["fallback_latency_samples"],
        snapshot["l2_ratio"],
        snapshot["l1_words"],
        snapshot["l2_words"],
        snapshot["recap_triggers"],
        snapshot["control_prompts_sent"],
    )
