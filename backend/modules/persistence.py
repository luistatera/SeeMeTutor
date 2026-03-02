"""
Persistence helpers — memory recall/checkpoints, session metrics, command logging.

Extracted from main.py (Step 5 of refactor).  Every async function takes
``firestore_client`` as an explicit parameter — no module-level mutable state.
"""

import logging
import time
from typing import Any, Callable, Awaitable

from fastapi import WebSocket

from modules.memory_manager import (
    build_checkpoint_summary,
    build_recall_payload,
    extract_cells_from_checkpoint,
)
from modules.memory_store import (
    load_recent_checkpoint,
    load_recent_memory_cells,
    save_checkpoint,
    upsert_memory_cells,
)
from modules.search_intent import SEARCH_INTENT_SIGNAL_WINDOW_S

logger = logging.getLogger(__name__)

SendJsonFn = Callable[[WebSocket, dict], Awaitable[None]]


# ---------------------------------------------------------------------------
# Memory recall (bootstrap)
# ---------------------------------------------------------------------------

async def _load_memory_recall(
    student_id: str,
    topic_id: str,
    firestore_client: Any | None,
    *,
    recall_budget_tokens: int,
    recall_max_cells: int,
    checkpoint_max_age_s: int,
) -> dict:
    """Load ranked memory recall payload for session bootstrap."""
    if not firestore_client:
        return {}
    sid = str(student_id or "").strip().lower()
    if not sid:
        return {}
    try:
        cells = await load_recent_memory_cells(
            firestore_client,
            student_id=sid,
            limit=80,
        )
        recall_payload = build_recall_payload(
            cells,
            topic_id=topic_id,
            budget_tokens=recall_budget_tokens,
            max_cells=recall_max_cells,
        )
        latest_checkpoint = await load_recent_checkpoint(
            firestore_client,
            student_id=sid,
            max_age_seconds=checkpoint_max_age_s,
        )
        if latest_checkpoint:
            recall_payload["latest_checkpoint"] = latest_checkpoint
            if not str(recall_payload.get("summary") or "").strip():
                recall_payload["summary"] = str(latest_checkpoint.get("summary_text") or "").strip()
        return recall_payload
    except Exception:
        logger.warning("Failed to load memory recall for student '%s'", sid, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# Memory checkpoint persistence
# ---------------------------------------------------------------------------

async def _persist_memory_checkpoint(
    runtime_state: dict,
    session_id: str,
    *,
    reason: str,
    firestore_client: Any | None,
    checkpoint_interval_s: int,
    send_json: SendJsonFn | None = None,
    websocket: WebSocket | None = None,
    report: Any | None = None,
    force: bool = False,
) -> dict | None:
    """
    Create + persist checkpoint and derived memory cells.

    Returns payload with checkpoint_id and saved cell count when persisted.
    """
    if report:
        report.record_memory_checkpoint_attempt(reason=str(reason or ""))

    if not firestore_client:
        if report:
            report.record_memory_checkpoint_skipped(reason="firestore_unavailable")
        return None
    student_id = str(runtime_state.get("student_id") or "").strip().lower()
    if not student_id:
        if report:
            report.record_memory_checkpoint_skipped(reason="missing_student_id")
        return None

    now = time.time()
    interval_s = max(60, int(runtime_state.get("memory_checkpoint_interval_s", checkpoint_interval_s)))
    last_at = float(runtime_state.get("memory_last_checkpoint_at", 0.0))
    if (not force) and last_at > 0 and (now - last_at) < interval_s:
        if report:
            report.record_memory_checkpoint_skipped(reason="interval_guardrail")
        return None

    checkpoint = build_checkpoint_summary(runtime_state, reason=reason)
    if not checkpoint.get("summary_text"):
        if report:
            report.record_memory_checkpoint_skipped(reason="empty_summary")
        return None

    checkpoint_id = await save_checkpoint(
        firestore_client,
        student_id=student_id,
        session_id=session_id,
        checkpoint=checkpoint,
    )
    if not checkpoint_id:
        if report:
            report.record_memory_checkpoint_failure(
                reason=str(reason or ""),
                error="checkpoint_save_failed",
            )
        return None

    try:
        derived_cells = extract_cells_from_checkpoint(
            checkpoint,
            source_session_id=session_id,
            tutor_preferences=runtime_state.get("tutor_preferences"),
        )
        saved_cells = await upsert_memory_cells(
            firestore_client,
            student_id=student_id,
            cells=derived_cells,
        )
    except Exception as exc:
        if report:
            report.record_memory_checkpoint_failure(
                reason=str(reason or ""),
                error=f"cell_upsert_failed:{type(exc).__name__}",
            )
        return None

    runtime_state["memory_last_checkpoint_at"] = now
    runtime_state["memory_checkpoint_count"] = int(runtime_state.get("memory_checkpoint_count", 0)) + 1
    runtime_state["memory_cells_saved"] = int(runtime_state.get("memory_cells_saved", 0)) + int(saved_cells)
    runtime_state["memory_last_checkpoint_reason"] = str(reason or "")
    payload = {
        "checkpoint_id": checkpoint_id,
        "reason": str(reason or ""),
        "saved_cells": int(saved_cells),
        "topic_id": str(checkpoint.get("topic_id") or ""),
        "topic_title": str(checkpoint.get("topic_title") or ""),
        "summary": str(checkpoint.get("summary_text") or ""),
        "created_at": checkpoint.get("created_at"),
    }
    if websocket is not None and send_json is not None:
        await send_json(websocket, {"type": "memory_checkpoint", "data": payload})
    if report:
        report.record_memory_checkpoint(saved_cells=saved_cells, reason=str(reason or ""))
    return payload


# ---------------------------------------------------------------------------
# Session metrics summary
# ---------------------------------------------------------------------------

async def _persist_session_metrics_summary(
    session_id: str,
    report: Any | None,
    firestore_client: Any | None,
) -> None:
    """Persist compact session telemetry for longitudinal analysis in Firestore."""
    if not firestore_client or report is None:
        return

    report_data = report.data if isinstance(report.data, dict) else {}
    scorecard = report_data.get("prd_scorecard", {}) if isinstance(report_data, dict) else {}
    score_summary = scorecard.get("summary", {}) if isinstance(scorecard, dict) else {}
    derived = scorecard.get("derived_metrics", {}) if isinstance(scorecard, dict) else {}
    pocs = scorecard.get("pocs", {}) if isinstance(scorecard, dict) else {}
    p99 = pocs.get("poc_99_hero_flow_rehearsal", {}) if isinstance(pocs, dict) else {}
    run_config = report_data.get("run_config", {}) if isinstance(report_data, dict) else {}
    resilience = report_data.get("resilience", {}) if isinstance(report_data, dict) else {}
    memory = report_data.get("memory", {}) if isinstance(report_data, dict) else {}
    compression = report_data.get("compression", {}) if isinstance(report_data, dict) else {}

    telemetry_payload = {
        "version": "v1",
        "updated_at": time.time(),
        "run_config": {
            "compression_enabled": bool(run_config.get("compression_enabled")),
            "compression_field": run_config.get("compression_field"),
            "resumption_enabled": bool(run_config.get("resumption_enabled")),
            "resumption_field": run_config.get("resumption_field"),
            "resumption_requested": bool(run_config.get("resumption_requested")),
        },
        "proof_signals": {
            "compression_events": int(compression.get("events", 0)),
            "memory_recalls_applied": int(memory.get("recalls_applied", 0)),
            "memory_checkpoints_saved": int(memory.get("checkpoints_saved", 0)),
            "session_resume_successes": int(resilience.get("session_resume_successes", 0)),
            "auto_pass_rate_percent": score_summary.get("auto_pass_rate_percent"),
            "hero_flow_checklist_completed": p99.get("checklist_completed"),
            "hero_flow_checklist_total": p99.get("checklist_total"),
        },
        "resilience": {
            "stream_retry_attempts": int(resilience.get("stream_retry_attempts", 0)),
            "stream_reconnect_successes": int(resilience.get("stream_reconnect_successes", 0)),
            "stream_reconnect_failures": int(resilience.get("stream_reconnect_failures", 0)),
            "session_resume_attempts": int(resilience.get("session_resume_attempts", 0)),
            "session_resume_successes": int(resilience.get("session_resume_successes", 0)),
            "session_resume_fallbacks": int(resilience.get("session_resume_fallbacks", 0)),
            "retry_backoff_seconds": resilience.get("retry_backoff_seconds", []),
        },
        "memory": {
            "recall_checks": int(memory.get("recall_checks", 0)),
            "recalls_applied": int(memory.get("recalls_applied", 0)),
            "recall_candidates_total": int(memory.get("recall_candidates_total", 0)),
            "recall_selected_total": int(memory.get("recall_selected_total", 0)),
            "last_recall_token_estimate": int(memory.get("last_recall_token_estimate", 0)),
            "recall_avg_tokens": derived.get("memory_recall_avg_tokens"),
            "checkpoint_attempts": int(memory.get("checkpoint_attempts", 0)),
            "checkpoint_saved": int(memory.get("checkpoints_saved", 0)),
            "checkpoint_skipped": int(memory.get("checkpoint_skipped", 0)),
            "checkpoint_failed": int(memory.get("checkpoint_failed", 0)),
            "checkpoint_reasons": memory.get("checkpoint_reasons", {}),
            "checkpoint_skip_reasons": memory.get("checkpoint_skip_reasons", {}),
            "checkpoint_failure_reasons": memory.get("checkpoint_failure_reasons", {}),
            "cells_saved": int(memory.get("cells_saved", 0)),
            "budget_violations": int(memory.get("budget_violations", 0)),
        },
        "compression": {
            "events": int(compression.get("events", 0)),
            "last_token_estimate": int(compression.get("last_token_estimate", 0)),
            "trigger_tokens": int(compression.get("trigger_tokens", 0)),
            "target_tokens": int(compression.get("target_tokens", 0)),
        },
        "scorecard": {
            "checks_passed": score_summary.get("checks_passed"),
            "checks_failed": score_summary.get("checks_failed"),
            "checks_not_tested": score_summary.get("checks_not_tested"),
            "auto_checks_total": score_summary.get("auto_checks_total"),
            "auto_checks_passed": score_summary.get("auto_checks_passed"),
            "auto_checks_failed": score_summary.get("auto_checks_failed"),
            "auto_pass_rate_percent": score_summary.get("auto_pass_rate_percent"),
            "poc_status_counts": score_summary.get("poc_status_counts", {}),
        },
        "derived_metrics": {
            "compression_events": derived.get("compression_events"),
            "memory_checkpoints_saved": derived.get("memory_checkpoints_saved"),
            "memory_cells_saved": derived.get("memory_cells_saved"),
            "memory_checkpoint_success_rate_percent": derived.get("memory_checkpoint_success_rate_percent"),
            "memory_recall_avg_tokens": derived.get("memory_recall_avg_tokens"),
            "session_resume_success_rate_percent": derived.get("session_resume_success_rate_percent"),
            "stream_retry_success_rate_percent": derived.get("stream_retry_success_rate_percent"),
            "stream_retry_backoff_avg_seconds": derived.get("stream_retry_backoff_avg_seconds"),
        },
    }
    await firestore_client.collection("sessions").document(session_id).set(
        {"telemetry": telemetry_payload},
        merge=True,
    )


# ---------------------------------------------------------------------------
# Command event logging
# ---------------------------------------------------------------------------

async def _log_command_event(
    session_id: str,
    runtime_state: dict,
    payload: dict,
    firestore_client: Any | None,
) -> None:
    """Persist voice command telemetry for debugging command behavior."""
    if not isinstance(payload, dict):
        payload = {}

    command_event = {
        "timestamp": time.time(),
        "source": "voice",
        "student_id": runtime_state.get("student_id"),
        "track_id": runtime_state.get("track_id"),
        "topic_id": runtime_state.get("topic_id"),
        "command_id": str(payload.get("command_id", "unknown")),
        "spoken_text": str(payload.get("spoken_text", "")),
        "normalized_text": str(payload.get("normalized_text", "")),
        "match_status": str(payload.get("match_status", "unknown")),
        "action_status": str(payload.get("action_status", "unknown")),
        "detail": str(payload.get("detail", "")),
        "attempt": int(payload.get("attempt", 0) or 0),
        "intent": str(payload.get("intent", "")),
    }
    intent = str(command_event.get("intent") or "").strip().lower()
    if intent == "search_request":
        runtime_state["search_intent_signal_until"] = (
            time.time() + float(SEARCH_INTENT_SIGNAL_WINDOW_S)
        )
    logger.info(
        "CMD session=%s command=%s match=%s action=%s detail=%s attempt=%s normalized='%s'",
        session_id,
        command_event["command_id"],
        command_event["match_status"],
        command_event["action_status"],
        command_event["detail"],
        command_event["attempt"],
        command_event["normalized_text"],
    )

    if not firestore_client:
        return

    try:
        await firestore_client.collection("sessions").document(session_id).collection("commands").add(command_event)
    except Exception:
        logger.warning("Session %s: failed to persist command event", session_id, exc_info=True)
