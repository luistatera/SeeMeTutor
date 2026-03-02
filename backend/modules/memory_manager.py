"""
Memory management helpers.

Keeps deterministic memory logic independent from transport/runtime code:
- transcript window maintenance
- checkpoint summary generation
- typed memory cell extraction/validation
- retrieval ranking + token-budgeted recall payload
"""

from __future__ import annotations

import re
import time
from typing import Any

VALID_MEMORY_CELL_TYPES = {"fact", "plan", "preference", "decision", "task", "risk"}
_WORD_RE = re.compile(r"[A-Za-z0-9À-ÿ']+")


def init_memory_state(
    *,
    checkpoint_interval_s: int = 300,
    recall_budget_tokens: int = 500,
    recall_max_cells: int = 6,
) -> dict[str, Any]:
    """Return default memory-related keys for runtime_state."""
    return {
        "memory_transcript_window": [],
        "memory_last_checkpoint_at": 0.0,
        "memory_checkpoint_interval_s": max(60, int(checkpoint_interval_s)),
        "memory_checkpoint_count": 0,
        "memory_cells_saved": 0,
        "memory_recall_budget_tokens": max(120, int(recall_budget_tokens)),
        "memory_recall_max_cells": max(1, int(recall_max_cells)),
        "memory_recall_count": 0,
        "memory_budget_violations": 0,
        "memory_last_recall": None,
        "memory_last_checkpoint_reason": "",
    }


def estimate_token_count(text: str) -> int:
    """Heuristic token estimate suitable for guardrails."""
    words = len(_WORD_RE.findall(str(text or "")))
    if words <= 0:
        return 0
    # Loose heuristic: average ~1.3 tokens per word in short educational text.
    return int(round(words * 1.3))


def append_transcript_piece(
    runtime_state: dict,
    *,
    role: str,
    text: str,
    at: float | None = None,
    max_items: int = 80,
) -> None:
    """Append transcript event into bounded in-memory window."""
    clean_role = str(role or "").strip().lower()
    clean_text = str(text or "").strip()
    if clean_role not in {"student", "tutor"} or not clean_text:
        return
    bucket = runtime_state.setdefault("memory_transcript_window", [])
    bucket.append(
        {
            "role": clean_role,
            "text": clean_text,
            "timestamp": float(at or time.time()),
        }
    )
    if len(bucket) > max_items:
        del bucket[0 : len(bucket) - max_items]


def _latest_transcripts(runtime_state: dict, *, role: str, limit: int) -> list[str]:
    window = runtime_state.get("memory_transcript_window", [])
    if not isinstance(window, list):
        return []
    out: list[str] = []
    for item in reversed(window):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").lower() != role:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    out.reverse()
    return out


def _extract_open_questions(student_turns: list[str], limit: int = 3) -> list[str]:
    questions: list[str] = []
    for text in reversed(student_turns):
        token = str(text or "").strip()
        if not token:
            continue
        if token.endswith("?") or any(w in token.lower() for w in ("why", "how", "what", "can you")):
            questions.append(token)
        if len(questions) >= limit:
            break
    questions.reverse()
    return questions


def _compress_sentences(turns: list[str], *, limit: int = 3) -> list[str]:
    out: list[str] = []
    for text in turns:
        clean = " ".join(str(text or "").split())
        if not clean:
            continue
        if len(clean) > 140:
            clean = clean[:137].rstrip() + "..."
        if clean not in out:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def build_checkpoint_summary(runtime_state: dict, *, reason: str) -> dict[str, Any]:
    """Build deterministic pedagogical checkpoint from runtime state."""
    topic_title = str(runtime_state.get("topic_title") or "Current Topic")
    topic_id = str(runtime_state.get("topic_id") or "unknown-topic")
    track_id = str(runtime_state.get("track_id") or "unknown-track")
    student_id = str(runtime_state.get("student_id") or "")
    tutor_turns = _latest_transcripts(runtime_state, role="tutor", limit=6)
    student_turns = _latest_transcripts(runtime_state, role="student", limit=6)
    key_points = _compress_sentences(tutor_turns, limit=3)
    open_questions = _extract_open_questions(student_turns, limit=3)
    latest_student = student_turns[-1] if student_turns else ""
    next_step = (
        "Continue with one small practice step, then invite the student to explain their reasoning."
    )
    if open_questions:
        next_step = "Address the latest open question before introducing new material."

    summary_lines = [f"Topic: {topic_title}."]
    if key_points:
        summary_lines.append(f"Key points: {' | '.join(key_points)}")
    if open_questions:
        summary_lines.append(f"Open questions: {' | '.join(open_questions)}")
    elif latest_student:
        summary_lines.append(f"Latest student input: {latest_student}")

    summary_text = " ".join(summary_lines).strip()
    return {
        "student_id": student_id,
        "track_id": track_id,
        "topic_id": topic_id,
        "topic_title": topic_title,
        "reason": str(reason or "interval"),
        "created_at": time.time(),
        "summary_text": summary_text,
        "key_points": key_points,
        "open_questions": open_questions,
        "next_step": next_step,
    }


def _cell_payload(
    *,
    cell_type: str,
    text: str,
    topic_id: str,
    source_session_id: str,
    salience: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    return {
        "cell_type": cell_type,
        "text": text,
        "topic_id": topic_id,
        "salience": max(0.0, min(1.0, float(salience))),
        "source_session_id": source_session_id,
        "created_at": now,
        "updated_at": now,
        "metadata": metadata or {},
    }


def extract_cells_from_checkpoint(
    checkpoint: dict[str, Any],
    *,
    source_session_id: str,
    tutor_preferences: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert one checkpoint into typed memory cells."""
    topic_id = str(checkpoint.get("topic_id") or "unknown-topic")
    summary = str(checkpoint.get("summary_text") or "").strip()
    next_step = str(checkpoint.get("next_step") or "").strip()
    questions = checkpoint.get("open_questions") if isinstance(checkpoint.get("open_questions"), list) else []
    key_points = checkpoint.get("key_points") if isinstance(checkpoint.get("key_points"), list) else []

    cells: list[dict[str, Any]] = []
    if summary:
        cells.append(
            _cell_payload(
                cell_type="fact",
                text=summary,
                topic_id=topic_id,
                source_session_id=source_session_id,
                salience=0.85,
            )
        )
    if next_step:
        cells.append(
            _cell_payload(
                cell_type="plan",
                text=next_step,
                topic_id=topic_id,
                source_session_id=source_session_id,
                salience=0.8,
            )
        )
    for question in questions[:2]:
        q = str(question or "").strip()
        if not q:
            continue
        cells.append(
            _cell_payload(
                cell_type="task",
                text=f"Resolve student question: {q}",
                topic_id=topic_id,
                source_session_id=source_session_id,
                salience=0.78,
            )
        )
    for point in key_points[:2]:
        p = str(point or "").strip()
        if not p:
            continue
        cells.append(
            _cell_payload(
                cell_type="fact",
                text=p,
                topic_id=topic_id,
                source_session_id=source_session_id,
                salience=0.72,
            )
        )
    if isinstance(tutor_preferences, dict) and tutor_preferences:
        compact = ", ".join(
            f"{k}={v}" for k, v in sorted(tutor_preferences.items()) if str(v or "").strip()
        )
        if compact:
            cells.append(
                _cell_payload(
                    cell_type="preference",
                    text=f"Tutor preferences: {compact}",
                    topic_id=topic_id,
                    source_session_id=source_session_id,
                    salience=0.55,
                )
            )
    return [cell for cell in cells if validate_memory_cell(cell)]


def validate_memory_cell(cell: dict[str, Any]) -> bool:
    """Validate memory cell schema for safe persistence."""
    if not isinstance(cell, dict):
        return False
    cell_type = str(cell.get("cell_type") or "").strip().lower()
    text = str(cell.get("text") or "").strip()
    topic_id = str(cell.get("topic_id") or "").strip()
    source_session_id = str(cell.get("source_session_id") or "").strip()
    if cell_type not in VALID_MEMORY_CELL_TYPES:
        return False
    if not text or len(text) < 8:
        return False
    if not topic_id or not source_session_id:
        return False
    return True


def rank_memory_cells(
    cells: list[dict[str, Any]],
    *,
    topic_id: str,
    now_ts: float | None = None,
) -> list[dict[str, Any]]:
    """Rank memory cells by salience, topic match, and recency."""
    now = float(now_ts or time.time())
    target_topic = str(topic_id or "").strip()
    ranked: list[tuple[float, dict[str, Any]]] = []
    for cell in cells:
        if not validate_memory_cell(cell):
            continue
        salience = float(cell.get("salience") or 0.5)
        updated_at = float(cell.get("updated_at") or cell.get("created_at") or now)
        age_h = max(0.0, (now - updated_at) / 3600.0)
        recency = max(0.0, 1.0 - min(1.0, age_h / 72.0))
        topic_boost = 0.2 if target_topic and str(cell.get("topic_id")) == target_topic else 0.0
        score = (salience * 0.65) + (recency * 0.35) + topic_boost
        ranked.append((score, cell))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [cell for _, cell in ranked]


def build_recall_payload(
    cells: list[dict[str, Any]],
    *,
    topic_id: str,
    budget_tokens: int,
    max_cells: int,
) -> dict[str, Any]:
    """Select top cells under token budget and build recall payload."""
    selected: list[dict[str, Any]] = []
    used_tokens = 0
    limit = max(1, int(max_cells))
    budget = max(80, int(budget_tokens))

    ranked = rank_memory_cells(cells, topic_id=topic_id)
    candidate_count = len(ranked)
    for cell in ranked:
        text = str(cell.get("text") or "").strip()
        if not text:
            continue
        cost = max(1, estimate_token_count(text))
        if selected and (used_tokens + cost) > budget:
            continue
        if (used_tokens + cost) > budget:
            break
        selected.append(cell)
        used_tokens += cost
        if len(selected) >= limit:
            break

    summary_parts = [str(item.get("text") or "").strip() for item in selected[:3]]
    compact_summary = " ".join(part for part in summary_parts if part).strip()
    if compact_summary and len(compact_summary) > 320:
        compact_summary = compact_summary[:317].rstrip() + "..."

    return {
        "topic_id": str(topic_id or ""),
        "selected_cells": selected,
        "selected_count": len(selected),
        "token_estimate": used_tokens,
        "candidate_count": candidate_count,
        "dropped_count": max(0, candidate_count - len(selected)),
        "budget_utilization_percent": round((used_tokens / budget) * 100.0, 1) if budget > 0 else 0.0,
        "summary": compact_summary,
        "budget_tokens": budget,
    }


def build_hidden_memory_context(recall_payload: dict[str, Any]) -> str:
    """Create hidden prompt block for memory recall injection."""
    if not isinstance(recall_payload, dict):
        return ""
    cells = recall_payload.get("selected_cells")
    if (not isinstance(cells, list) or not cells):
        summary_only = str(recall_payload.get("summary") or "").strip()
        if not summary_only:
            return ""
        return (
            "[MEMORY RECALL — CONTEXT ONLY, DO NOT SPEAK]\n"
            f"Recent checkpoint summary: {summary_only}\n"
            "[IMPORTANT: Background memory only. Do not produce a standalone response to this memory block.]"
        )
    lines: list[str] = [
        "[MEMORY RECALL — CONTEXT ONLY, DO NOT SPEAK]",
        "Use these recalled memory anchors to maintain continuity. "
        "If current student input conflicts, ask to confirm instead of asserting.",
    ]
    for idx, cell in enumerate(cells[:8], start=1):
        ctype = str(cell.get("cell_type") or "fact")
        topic_id = str(cell.get("topic_id") or "")
        text = str(cell.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{idx}. ({ctype}) [{topic_id}] {text}")
    lines.append(
        "[IMPORTANT: Background memory only. Do not produce a standalone response to this memory block.]"
    )
    return "\n".join(lines)
