"""
Prompt capture helpers for Gemini text prompts.

Prompts are aggregated into a single JSON file per session.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "backend" / "test_results"
_WRITE_LOCK = threading.Lock()


def _resolve_output_dir() -> Path:
    return _DEFAULT_OUTPUT_DIR


def _sanitize_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(session_id or "").strip())
    if not cleaned:
        return "unknown"
    return cleaned[:64]


def _extract_text_parts(content: Any) -> list[str]:
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    text_parts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            text_parts.append(text)
    return text_parts


def _session_file_path(session_id: str) -> Path:
    output_dir = _resolve_output_dir()
    session_tag = _sanitize_session_id(session_id)
    return output_dir / f"session_prompts_{session_tag}.json"


def _load_session_payload(path: Path, session_id: str, now_iso: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "session_id": str(session_id),
            "created_at": now_iso,
            "updated_at": now_iso,
            "prompt_count": 0,
            "prompts": [],
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read existing prompt capture file %s; resetting", path, exc_info=True)
        return {
            "session_id": str(session_id),
            "created_at": now_iso,
            "updated_at": now_iso,
            "prompt_count": 0,
            "prompts": [],
        }

    if not isinstance(payload, dict):
        return {
            "session_id": str(session_id),
            "created_at": now_iso,
            "updated_at": now_iso,
            "prompt_count": 0,
            "prompts": [],
        }

    prompts = payload.get("prompts")
    if not isinstance(prompts, list):
        prompts = []

    return {
        "session_id": str(payload.get("session_id") or session_id),
        "created_at": str(payload.get("created_at") or now_iso),
        "updated_at": str(payload.get("updated_at") or now_iso),
        "prompt_count": int(payload.get("prompt_count") or len(prompts)),
        "prompts": prompts,
    }


def capture_prompt_text(
    prompt_text: str,
    *,
    session_id: str,
    source: str,
    role: str = "user",
    runtime_state: dict | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Persist one prompt text payload without sending anything to the queue."""
    text = str(prompt_text or "")
    if not text.strip():
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {
        "captured_at": now_iso,
        "source": str(source or ""),
        "role": str(role or "user"),
        "text_parts": [text],
        "prompt_text": text,
    }
    if isinstance(extra, dict) and extra:
        entry["meta"] = extra

    session_key = str(session_id or "")
    try:
        with _WRITE_LOCK:
            path = _session_file_path(session_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = _load_session_payload(path, session_key, now_iso)
            prompts = payload.get("prompts", [])
            if not isinstance(prompts, list):
                prompts = []
            assigned_index = len(prompts) + 1
            entry["prompt_index"] = assigned_index
            prompts.append(entry)
            payload["prompts"] = prompts
            payload["prompt_count"] = len(prompts)
            payload["updated_at"] = now_iso

            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(path)
    except Exception:
        logger.warning("Failed to persist prompt capture for session %s", session_key, exc_info=True)
        return None

    output_path = str(path)
    if isinstance(runtime_state, dict):
        runtime_state["prompt_capture_index"] = int(entry["prompt_index"])
        runtime_state["prompt_capture_last_file"] = output_path
    return output_path


def send_content_with_prompt_capture(
    queue: Any,
    content: Any,
    *,
    session_id: str,
    source: str,
    runtime_state: dict | None = None,
) -> None:
    """
    Send content to Gemini and persist a JSON prompt snapshot when text exists.

    The queue send happens first; capture is only written after successful send.
    """
    queue.send_content(content)

    text_parts = _extract_text_parts(content)
    if not text_parts:
        return
    capture_prompt_text(
        "\n".join(text_parts),
        session_id=session_id,
        source=source,
        role=str(getattr(content, "role", "") or "user"),
        runtime_state=runtime_state,
    )
