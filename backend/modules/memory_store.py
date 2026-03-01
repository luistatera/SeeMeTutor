"""
Firestore persistence helpers for memory checkpoints and recall cells.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from modules.memory_manager import validate_memory_cell

logger = logging.getLogger(__name__)

_ROOT_COLLECTION = "student_memory"


def _root_ref(firestore_client: Any, student_id: str):
    return firestore_client.collection(_ROOT_COLLECTION).document(student_id)


def _stable_cell_doc_id(cell: dict[str, Any]) -> str:
    """Build deterministic cell id so upserts stay stable across process restarts."""
    topic_id = str(cell.get("topic_id") or "topic")
    cell_type = str(cell.get("cell_type") or "fact")
    text = str(cell.get("text") or "")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"{topic_id}--{cell_type}--{digest}"


async def save_checkpoint(
    firestore_client: Any,
    *,
    student_id: str,
    session_id: str,
    checkpoint: dict[str, Any],
) -> str | None:
    """Persist one checkpoint document; returns checkpoint_id or None."""
    if not firestore_client:
        return None
    sid = str(student_id or "").strip().lower()
    if not sid:
        return None
    payload = dict(checkpoint or {})
    payload["student_id"] = sid
    payload["session_id"] = str(session_id or "")
    payload["updated_at"] = time.time()
    checkpoint_id = f"{int(payload['updated_at'] * 1000)}-{payload.get('reason', 'checkpoint')}"
    try:
        await _root_ref(firestore_client, sid).collection("checkpoints").document(checkpoint_id).set(payload, merge=True)
        return checkpoint_id
    except Exception:
        logger.warning("Failed to persist memory checkpoint", exc_info=True)
        return None


async def upsert_memory_cells(
    firestore_client: Any,
    *,
    student_id: str,
    cells: list[dict[str, Any]],
) -> int:
    """Persist validated memory cells; returns number of saved cells."""
    if not firestore_client:
        return 0
    sid = str(student_id or "").strip().lower()
    if not sid:
        return 0
    saved = 0
    ref = _root_ref(firestore_client, sid).collection("cells")
    for cell in cells:
        if not validate_memory_cell(cell):
            continue
        payload = dict(cell)
        payload["student_id"] = sid
        payload["updated_at"] = time.time()
        doc_id = _stable_cell_doc_id(payload)
        try:
            await ref.document(doc_id).set(payload, merge=True)
            saved += 1
        except Exception:
            logger.warning("Failed to upsert memory cell", exc_info=True)
    return saved


async def load_recent_memory_cells(
    firestore_client: Any,
    *,
    student_id: str,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Load recent memory cells for one student."""
    if not firestore_client:
        return []
    sid = str(student_id or "").strip().lower()
    if not sid:
        return []
    out: list[dict[str, Any]] = []
    try:
        async for snap in _root_ref(firestore_client, sid).collection("cells").stream():
            payload = snap.to_dict() or {}
            payload["id"] = snap.id
            out.append(payload)
    except Exception:
        logger.warning("Failed to load memory cells", exc_info=True)
        return []
    out.sort(
        key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0),
        reverse=True,
    )
    if len(out) > max(1, int(limit)):
        out = out[: max(1, int(limit))]
    return out


async def load_recent_checkpoint(
    firestore_client: Any,
    *,
    student_id: str,
    max_age_seconds: int = 24 * 60 * 60,
) -> dict[str, Any] | None:
    """Load latest non-expired checkpoint for one student."""
    if not firestore_client:
        return None
    sid = str(student_id or "").strip().lower()
    if not sid:
        return None
    now = time.time()
    min_ts = now - max(60, int(max_age_seconds))
    checkpoints: list[dict[str, Any]] = []
    try:
        async for snap in _root_ref(firestore_client, sid).collection("checkpoints").stream():
            payload = snap.to_dict() or {}
            payload["id"] = snap.id
            checkpoints.append(payload)
    except Exception:
        logger.warning("Failed to load recent checkpoint", exc_info=True)
        return None
    checkpoints.sort(
        key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0),
        reverse=True,
    )
    for payload in checkpoints[:3]:
        updated_at = float(payload.get("updated_at") or payload.get("created_at") or 0.0)
        if updated_at >= min_ts:
            return payload
    return None
