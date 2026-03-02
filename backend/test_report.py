"""
Automated test report capture + PRD scorecard for migration validation.

Collects runtime events during each session and writes a structured JSON file
to backend/test_results/. The saved report includes:
- raw counters/events (audio, video, tools, guardrails, latency, etc.)
- derived metrics
- per-POC pass/fail/not_tested scorecards
- overall product-level PRD metric summary
"""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "test_results"

# ---------------------------------------------------------------------------
# Registry — one report per active session
# ---------------------------------------------------------------------------
_reports: dict[str, "SessionReport"] = {}


def create_report(session_id: str, student_id: str) -> "SessionReport":
    report = SessionReport(session_id, student_id)
    _reports[session_id] = report
    return report


def get_report(session_id: str) -> "SessionReport | None":
    return _reports.get(session_id)


def remove_report(session_id: str) -> "SessionReport | None":
    return _reports.pop(session_id, None)


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _round_or_none(value: float | int | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _check_status_pass_fail_not_tested(
    value: float | int | None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> str:
    if value is None:
        return "not_tested"
    val = float(value)
    if min_value is not None and val < float(min_value):
        return "fail"
    if max_value is not None and val > float(max_value):
        return "fail"
    return "pass"


def _status_from_checks(checks: list[dict]) -> str:
    if not checks:
        return "not_tested"

    statuses = [str(c.get("status", "not_tested")) for c in checks]
    if "fail" in statuses:
        return "fail"
    if all(s == "not_tested" for s in statuses):
        return "not_tested"
    if "not_tested" in statuses:
        return "partial"
    return "pass"


class SessionReport:
    def __init__(self, session_id: str, student_id: str):
        self._whiteboard_note_queued_at: dict[str, float] = {}
        self.data = {
            "session_id": session_id,
            "student_id": student_id,
            "started_at": time.time(),
            "ended_at": None,
            "duration_seconds": None,
            "ended_reason": None,
            "connection": {
                "ws_connected": True,
                "backlog_context_sent": False,
            },
            "run_config": {
                "compression_enabled": None,
                "compression_field": None,
                "resumption_enabled": None,
                "resumption_field": None,
                "resumption_requested": False,
            },
            "audio": {
                "chunks_in": 0,
                "chunks_out": 0,
                "first_response_ms": None,
                "latency_samples_ms": [],
            },
            "video": {
                "frames_in": 0,
            },
            "latency": {
                "events": [],
                "latest_report": {},
                "reports": [],
            },
            "language": {
                "events": [],
                "latest_metric": {},
                "metrics_history": [],
            },
            "resilience": {
                "stream_retry_attempts": 0,
                "stream_reconnect_successes": 0,
                "stream_reconnect_failures": 0,
                "retry_backoff_seconds": [],
                "session_resume_attempts": 0,
                "session_resume_successes": 0,
                "session_resume_fallbacks": 0,
                "resumption_handles_saved": 0,
                "events": [],
            },
            "compression": {
                "events": 0,
                "last_token_estimate": 0,
                "trigger_tokens": 0,
                "target_tokens": 0,
                "events_detail": [],
            },
            "memory": {
                "recall_checks": 0,
                "checkpoints_saved": 0,
                "cells_saved": 0,
                "recalls_applied": 0,
                "recall_candidates_last": 0,
                "recall_candidates_total": 0,
                "recall_selected_total": 0,
                "last_recall_token_estimate": 0,
                "recall_token_estimates": [],
                "checkpoint_attempts": 0,
                "checkpoint_skipped": 0,
                "checkpoint_failed": 0,
                "checkpoint_reasons": {},
                "checkpoint_skip_reasons": {},
                "checkpoint_failure_reasons": {},
                "budget_violations": 0,
                "events": [],
            },
            "tools": {
                "calls": [],
                "total_calls": 0,
                "unique_tools_used": [],
            },
            "phases": {
                "transitions": [],
                "final_phase": "greeting",
            },
            "whiteboard": {
                "notes_created": 0,
                "notes_duplicate_skipped": 0,
                "status_updates": 0,
                "clear_events": 0,
                "notes_delivered": 0,
                "delivery_latency_ms": [],
                "sync_speaking_count": 0,
                "sync_deadline_count": 0,
            },
            "interruptions": {
                "count": 0,
                "stale_filtered": 0,
                "barge_in_received": 0,
                "barge_in_while_speaking": 0,
                "barge_in_ignored": 0,
            },
            "turns": {
                "count": 0,
                "audio_chunks_per_turn": [],
            },
            "conversation": {
                "awaiting_reply_prompts": 0,
                "suspected_repetition_loops": 0,
            },
            "idle": {
                "checkin_1_count": 0,
                "checkin_2_count": 0,
                "away_activated_count": 0,
                "away_resumed_count": 0,
            },
            "proactive": {
                "poke_count": 0,
                "nudge_count": 0,
            },
            "transcripts": {
                "student": [],
                "tutor": [],
            },
            "guardrails": {
                "refusals_total": 0,
                "answer_leaks": 0,
                "content_flags": 0,
                "prompt_injections": 0,
                "drift_reinforcements": 0,
                "events": [],
            },
            "screen_share": {
                "source_switches": 0,
                "stop_sharing_count": 0,
                "switch_log": [],
            },
            "mastery": {
                "verifications_completed": 0,
                "premature_mastery_blocked": 0,
                "step_failures": 0,
                "verified_note_ids": [],
                "blocked_note_ids": [],
                "step_failure_details": [],
            },
            "errors": [],
            "manual": {},
            "prd_scorecard": {},
        }

    # --- Connection ---

    def record_backlog_sent(self):
        self.data["connection"]["backlog_context_sent"] = True

    def record_run_config(self, meta: dict | None, *, resumption_requested: bool = False):
        payload = meta or {}
        run_cfg = self.data["run_config"]
        run_cfg["compression_enabled"] = bool(payload.get("compression_enabled"))
        run_cfg["compression_field"] = payload.get("compression_field")
        run_cfg["resumption_enabled"] = bool(payload.get("resumption_enabled"))
        run_cfg["resumption_field"] = payload.get("resumption_field")
        run_cfg["resumption_requested"] = bool(resumption_requested)

    # --- Audio ---

    def record_audio_in(self):
        self.data["audio"]["chunks_in"] += 1

    def record_audio_out(self):
        self.data["audio"]["chunks_out"] += 1

    def record_first_response_latency(self, ms: float):
        self.data["audio"]["latency_samples_ms"].append(round(float(ms), 1))
        if self.data["audio"]["first_response_ms"] is None:
            self.data["audio"]["first_response_ms"] = round(float(ms), 1)

    # --- Video ---

    def record_video_in(self):
        self.data["video"]["frames_in"] += 1

    # --- Latency ---

    def record_latency_report(self, report_payload: dict):
        snapshot = copy.deepcopy(report_payload or {})
        self.data["latency"]["latest_report"] = snapshot
        self.data["latency"]["reports"].append(snapshot)

    def record_latency_event(self, event_type: str, ms: float):
        """Record a single latency measurement and update latest_report stats."""
        self.data["latency"]["events"].append({
            "type": str(event_type),
            "ms": round(float(ms), 1),
            "timestamp": time.time(),
        })

    def _recompute_latency_report(self):
        """Build latency.latest_report from accumulated events."""
        by_type: dict[str, list[float]] = {}
        for ev in self.data["latency"]["events"]:
            t = str(ev.get("type", ""))
            if t:
                by_type.setdefault(t, []).append(float(ev.get("ms", 0)))
        metrics: dict[str, Any] = {}
        for metric_type, samples in by_type.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            avg = sum(sorted_s) / n
            p95_idx = max(0, int(round(0.95 * (n - 1))))
            metrics[metric_type] = {
                "avg": round(avg, 1),
                "p95": round(sorted_s[p95_idx], 1),
                "count": n,
                "min": round(sorted_s[0], 1),
                "max": round(sorted_s[-1], 1),
            }
        if metrics:
            payload = {"metrics": metrics}
            self.data["latency"]["latest_report"] = payload

    def record_language_event(self, metric_snapshot: dict):
        """Record a language purity metric snapshot."""
        snapshot = copy.deepcopy(metric_snapshot or {})
        snapshot["timestamp"] = time.time()
        self.data["language"]["events"].append(snapshot)
        self.data["language"]["latest_metric"] = copy.deepcopy(metric_snapshot or {})
        self.data["language"]["metrics_history"].append(copy.deepcopy(metric_snapshot or {}))

    # --- Resilience ---

    def record_stream_retry_attempt(self, attempt: int, reason: str):
        self.data["resilience"]["stream_retry_attempts"] += 1
        self.data["resilience"]["events"].append({
            "type": "stream_retry_attempt",
            "attempt": int(attempt),
            "reason": str(reason),
            "timestamp": time.time(),
        })

    def record_stream_retry_backoff(self, attempt: int, backoff_seconds: float):
        delay = round(float(backoff_seconds), 3)
        self.data["resilience"]["retry_backoff_seconds"].append(delay)
        self.data["resilience"]["events"].append({
            "type": "stream_retry_backoff",
            "attempt": int(attempt),
            "backoff_seconds": delay,
            "timestamp": time.time(),
        })

    def record_stream_reconnect_success(self, attempt: int):
        self.data["resilience"]["stream_reconnect_successes"] += 1
        self.data["resilience"]["events"].append({
            "type": "stream_reconnect_success",
            "attempt": int(attempt),
            "timestamp": time.time(),
        })

    def record_stream_reconnect_failure(self, attempt: int, reason: str):
        self.data["resilience"]["stream_reconnect_failures"] += 1
        self.data["resilience"]["events"].append({
            "type": "stream_reconnect_failure",
            "attempt": int(attempt),
            "reason": str(reason),
            "timestamp": time.time(),
        })


    def record_context_compression(
        self,
        token_estimate: int,
        trigger_tokens: int,
        *,
        target_tokens: int = 0,
    ):
        self.data["compression"]["events"] += 1
        self.data["compression"]["last_token_estimate"] = int(token_estimate)
        self.data["compression"]["trigger_tokens"] = int(trigger_tokens)
        self.data["compression"]["target_tokens"] = int(target_tokens)
        self.data["compression"]["events_detail"].append({
            "token_estimate": int(token_estimate),
            "trigger_tokens": int(trigger_tokens),
            "target_tokens": int(target_tokens),
            "timestamp": time.time(),
        })

    def record_memory_recall_applied(
        self,
        *,
        selected_count: int,
        token_estimate: int,
        candidate_count: int = 0,
    ):
        self.data["memory"]["recall_checks"] += 1
        selected = int(max(0, int(selected_count)))
        candidates = int(max(0, int(candidate_count)))
        if selected > 0:
            self.data["memory"]["recalls_applied"] += 1
        self.data["memory"]["recall_selected_total"] += selected
        self.data["memory"]["recall_candidates_last"] = candidates
        self.data["memory"]["recall_candidates_total"] += candidates
        self.data["memory"]["last_recall_token_estimate"] = int(token_estimate)
        self.data["memory"]["recall_token_estimates"].append(int(max(0, int(token_estimate))))
        self.data["memory"]["events"].append({
            "type": "memory_recall_applied",
            "selected_count": selected,
            "candidate_count": candidates,
            "token_estimate": int(token_estimate),
            "timestamp": time.time(),
        })

    def record_memory_checkpoint_attempt(self, *, reason: str):
        self.data["memory"]["checkpoint_attempts"] += 1
        self.data["memory"]["events"].append({
            "type": "memory_checkpoint_attempt",
            "reason": str(reason),
            "timestamp": time.time(),
        })

    def record_memory_checkpoint_skipped(self, *, reason: str):
        self.data["memory"]["checkpoint_skipped"] += 1
        reasons = self.data["memory"]["checkpoint_skip_reasons"]
        key = str(reason or "unknown")
        reasons[key] = int(reasons.get(key, 0)) + 1
        self.data["memory"]["events"].append({
            "type": "memory_checkpoint_skipped",
            "reason": key,
            "timestamp": time.time(),
        })

    def record_memory_checkpoint_failure(self, *, reason: str, error: str):
        self.data["memory"]["checkpoint_failed"] += 1
        reasons = self.data["memory"]["checkpoint_failure_reasons"]
        key = str(reason or "unknown")
        reasons[key] = int(reasons.get(key, 0)) + 1
        self.data["memory"]["events"].append({
            "type": "memory_checkpoint_failed",
            "reason": key,
            "error": str(error),
            "timestamp": time.time(),
        })

    def record_memory_checkpoint(self, *, saved_cells: int, reason: str):
        self.data["memory"]["checkpoints_saved"] += 1
        self.data["memory"]["cells_saved"] += int(max(0, int(saved_cells)))
        reasons = self.data["memory"]["checkpoint_reasons"]
        key = str(reason or "unknown")
        reasons[key] = int(reasons.get(key, 0)) + 1
        self.data["memory"]["events"].append({
            "type": "memory_checkpoint_saved",
            "saved_cells": int(saved_cells),
            "reason": key,
            "timestamp": time.time(),
        })

    def record_memory_budget_violation(self, *, token_estimate: int, budget_tokens: int):
        self.data["memory"]["budget_violations"] += 1
        self.data["memory"]["events"].append({
            "type": "memory_budget_violation",
            "token_estimate": int(token_estimate),
            "budget_tokens": int(budget_tokens),
            "timestamp": time.time(),
        })

    # --- Tools ---

    def record_tool_call(
        self,
        name: str,
        args: dict,
        result_status: str,
        duration_ms: float,
    ):
        self.data["tools"]["calls"].append({
            "name": name,
            "args": args,
            "result_status": result_status,
            "duration_ms": round(float(duration_ms), 1),
            "timestamp": time.time(),
        })
        self.data["tools"]["total_calls"] += 1
        if name not in self.data["tools"]["unique_tools_used"]:
            self.data["tools"]["unique_tools_used"].append(name)

    # --- Phases ---

    def record_phase_transition(self, from_phase: str, to_phase: str):
        self.data["phases"]["transitions"].append({
            "from": from_phase,
            "to": to_phase,
            "timestamp": time.time(),
        })
        self.data["phases"]["final_phase"] = to_phase

    # --- Whiteboard ---

    def record_whiteboard_note_created(self):
        self.data["whiteboard"]["notes_created"] += 1

    def record_whiteboard_note_queued(self, note_id: str):
        if note_id:
            self._whiteboard_note_queued_at[str(note_id)] = time.time()

    def record_whiteboard_note_delivered(self, note_id: str | None, sync_mode: str):
        self.data["whiteboard"]["notes_delivered"] += 1
        mode = str(sync_mode or "").lower()
        if mode == "speech":
            self.data["whiteboard"]["sync_speaking_count"] += 1
        elif mode == "deadline":
            self.data["whiteboard"]["sync_deadline_count"] += 1

        if note_id:
            queued_at = self._whiteboard_note_queued_at.pop(str(note_id), None)
            if queued_at is not None:
                latency_ms = (time.time() - queued_at) * 1000
                self.data["whiteboard"]["delivery_latency_ms"].append(round(latency_ms, 1))

    def record_whiteboard_duplicate_skipped(self):
        self.data["whiteboard"]["notes_duplicate_skipped"] += 1

    def record_whiteboard_status_update(self):
        self.data["whiteboard"]["status_updates"] += 1

    def record_whiteboard_clear(self):
        self.data["whiteboard"]["clear_events"] += 1

    # --- Mastery Verification ---

    def record_mastery_verified(self, note_id: str):
        self.data["mastery"]["verifications_completed"] += 1
        self.data["mastery"]["verified_note_ids"].append(note_id)

    def record_premature_mastery_blocked(self, note_id: str):
        self.data["mastery"]["premature_mastery_blocked"] += 1
        if note_id not in self.data["mastery"]["blocked_note_ids"]:
            self.data["mastery"]["blocked_note_ids"].append(note_id)

    def record_mastery_step_failed(self, note_id: str, step: str):
        self.data["mastery"]["step_failures"] += 1
        self.data["mastery"]["step_failure_details"].append(
            {"note_id": note_id, "step": step, "at": time.time()}
        )

    # --- Interruptions ---

    def record_interruption(self):
        self.data["interruptions"]["count"] += 1

    def record_stale_interruption(self):
        self.data["interruptions"]["stale_filtered"] += 1

    def record_barge_in(self, while_speaking: bool):
        """Track a client-side barge-in message from the frontend.

        Args:
            while_speaking: True if tutor was actively speaking when the
                barge-in arrived (= successful cutoff), False if tutor was
                already silent (echo / stale VAD spike).
        """
        self.data["interruptions"]["barge_in_received"] += 1
        if while_speaking:
            self.data["interruptions"]["barge_in_while_speaking"] += 1
        else:
            self.data["interruptions"]["barge_in_ignored"] += 1

    # --- Turns ---

    def record_turn_complete(self, audio_chunks_this_turn: int):
        self.data["turns"]["count"] += 1
        self.data["turns"]["audio_chunks_per_turn"].append(int(audio_chunks_this_turn))

    # --- Conversation ---

    def record_awaiting_reply_prompt(self):
        self.data["conversation"]["awaiting_reply_prompts"] += 1

    def record_repetition_suspected(self):
        self.data["conversation"]["suspected_repetition_loops"] += 1

    # --- Idle ---

    def record_idle_checkin(self, stage: int):
        if stage == 1:
            self.data["idle"]["checkin_1_count"] += 1
        elif stage == 2:
            self.data["idle"]["checkin_2_count"] += 1

    def record_away_activated(self):
        self.data["idle"]["away_activated_count"] += 1

    # --- Proactive ---

    def record_proactive_poke(self):
        self.data["proactive"]["poke_count"] += 1

    def record_proactive_nudge(self):
        self.data["proactive"]["nudge_count"] += 1

    # --- Transcripts ---

    def record_student_transcript(self, text: str):
        self.data["transcripts"]["student"].append({
            "text": text,
            "timestamp": time.time(),
        })

    def record_tutor_transcript(self, text: str):
        self.data["transcripts"]["tutor"].append({
            "text": text,
            "timestamp": time.time(),
        })

    # --- Guardrails ---

    def record_guardrail_event(self, guardrail: str, severity: str, source: str):
        normalized_guardrail = str(guardrail or "")
        self.data["guardrails"]["events"].append({
            "guardrail": normalized_guardrail,
            "severity": severity,
            "source": source,
            "timestamp": time.time(),
        })
        if normalized_guardrail in (
            "off_topic",
            "cheat_request",
            "content_moderation",
            "inappropriate",
            "prompt_injection",
            "drift",
        ):
            self.data["guardrails"]["refusals_total"] += 1
        if normalized_guardrail == "answer_leak":
            self.data["guardrails"]["answer_leaks"] += 1
        if normalized_guardrail == "content_moderation":
            self.data["guardrails"]["content_flags"] += 1
        if normalized_guardrail == "prompt_injection":
            self.data["guardrails"]["prompt_injections"] += 1

    # --- Grounding ---

    # --- Screen share ---

    def record_source_switch(self, old_source: str, new_source: str):
        self.data["screen_share"]["source_switches"] += 1
        self.data["screen_share"]["switch_log"].append({
            "from": old_source,
            "to": new_source,
            "timestamp": time.time(),
        })

    def record_stop_sharing(self, old_source: str):
        self.data["screen_share"]["stop_sharing_count"] += 1
        self.data["screen_share"]["switch_log"].append({
            "from": old_source,
            "to": "none",
            "timestamp": time.time(),
        })

    # --- Errors ---

    def record_error(self, error: str):
        self.data["errors"].append({
            "error": error,
            "timestamp": time.time(),
        })

    # --- PRD scorecard helpers ---

    def _check(
        self,
        check_id: str,
        metric: str,
        target: str,
        actual: Any,
        status: str,
        source: str,
        notes: str = "",
    ) -> dict[str, Any]:
        return {
            "id": check_id,
            "metric": metric,
            "target": target,
            "actual": actual,
            "status": status,
            "source": source,
            "notes": notes,
        }

    def _question_turn_stats(self) -> tuple[int, int, float | None, int]:
        tutor_turns = self.data["transcripts"]["tutor"]
        if not tutor_turns:
            return 0, 0, None, 0

        total = 0
        question_turns = 0
        max_streak = 0
        streak = 0
        for item in tutor_turns:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            total += 1
            is_question = text.endswith("?")
            if is_question:
                question_turns += 1
                streak += 1
                if streak > max_streak:
                    max_streak = streak
            else:
                streak = 0

        ratio = _safe_ratio(question_turns * 100.0, total)
        return total, question_turns, _round_or_none(ratio, 1), max_streak

    def _latency_metric_stats(self, metric: str) -> dict[str, Any]:
        latest = self.data.get("latency", {}).get("latest_report", {}) or {}
        metrics = latest.get("metrics", {}) if isinstance(latest, dict) else {}
        payload = metrics.get(metric, {}) if isinstance(metrics, dict) else {}
        return payload if isinstance(payload, dict) else {}

    def _build_prd_scorecard(self) -> dict[str, Any]:
        student_turns = len(self.data["transcripts"]["student"])
        tutor_turn_count = int(self.data["turns"]["count"])
        proactive_total = int(self.data["proactive"]["poke_count"]) + int(self.data["proactive"]["nudge_count"])
        total_tutor_text_turns, question_turns, question_ratio, question_streak_max = self._question_turn_stats()
        language_latest = self.data["language"].get("latest_metric", {}) or {}

        interruption_stats = self._latency_metric_stats("interruption_stop")
        response_stats = self._latency_metric_stats("response_start")
        turn_gap_stats = self._latency_metric_stats("turn_to_turn")
        first_byte_stats = self._latency_metric_stats("first_byte")

        student_heard_rate = _safe_ratio(float(tutor_turn_count), float(student_turns))
        if student_heard_rate is not None and student_heard_rate > 1.0:
            student_heard_rate = 1.0

        socratic_compliance = None
        if tutor_turn_count > 0:
            leaks = int(self.data["guardrails"]["answer_leaks"])
            socratic_compliance = ((tutor_turn_count - leaks) / tutor_turn_count) * 100.0

        whiteboard_latency_samples = self.data["whiteboard"]["delivery_latency_ms"]
        whiteboard_p95 = None
        if whiteboard_latency_samples:
            sorted_samples = sorted(float(v) for v in whiteboard_latency_samples)
            idx = max(0, int(round(0.95 * (len(sorted_samples) - 1))))
            whiteboard_p95 = sorted_samples[idx]

        pocs: dict[str, dict[str, Any]] = {}

        # ------------------------------------------------------------------
        # POC 00 — Onboarding
        # ------------------------------------------------------------------
        p00_checks = [
            self._check(
                "P00.context_retention",
                "Context retention baseline",
                "100%",
                100.0 if self.data["connection"]["backlog_context_sent"] else 0.0,
                "pass" if self.data["connection"]["backlog_context_sent"] else "fail",
                "connection.backlog_context_sent",
                "Proxy for profile/context injection on session start.",
            ),
            self._check(
                "P00.onboarding_dropoff",
                "Onboarding drop-off",
                "<10%",
                None,
                "not_tested",
                "manual",
                "Requires frontend onboarding funnel instrumentation (post-event POC 00).",
            ),
        ]
        pocs["poc_00_onboarding"] = {
            "status": _status_from_checks(p00_checks),
            "checks": p00_checks,
        }

        # ------------------------------------------------------------------
        # POC 01 — Interruption Handling
        # ------------------------------------------------------------------
        p01_checks = [
            self._check(
                "P01.interruption_stop_p95",
                "Gemini interruption stop p95",
                "<=500ms",
                interruption_stats.get("p95"),
                _check_status_pass_fail_not_tested(interruption_stats.get("p95"), max_value=500),
                "latency.latest_report.metrics.interruption_stop.p95",
            ),
            self._check(
                "P01.student_heard_rate",
                "Student-heard rate proxy",
                ">=90%",
                _round_or_none(student_heard_rate * 100.0 if student_heard_rate is not None else None, 1),
                _check_status_pass_fail_not_tested(
                    student_heard_rate * 100.0 if student_heard_rate is not None else None,
                    min_value=90,
                ),
                "turns.count / transcripts.student",
                "Proxy metric: tutor turns relative to student transcripts.",
            ),
            self._check(
                "P01.interruptions_observed",
                "Interruption events observed in run",
                ">=1 (when testing interruption scenario)",
                self.data["interruptions"]["count"],
                _check_status_pass_fail_not_tested(
                    self.data["interruptions"]["count"] if self.data["interruptions"]["count"] > 0 else None,
                    min_value=1,
                ),
                "interruptions.count",
            ),
            self._check(
                "P01.barge_in_received",
                "Client-side barge-in messages from frontend",
                ">=1 (when testing interruption scenario)",
                self.data["interruptions"]["barge_in_received"],
                _check_status_pass_fail_not_tested(
                    self.data["interruptions"]["barge_in_received"] if self.data["interruptions"]["barge_in_received"] > 0 else None,
                    min_value=1,
                ),
                "interruptions.barge_in_received",
                "Total barge-in messages sent by frontend VAD.",
            ),
            self._check(
                "P01.barge_in_while_speaking",
                "Barge-ins that cut off active tutor speech",
                ">=1 (when testing interruption scenario)",
                self.data["interruptions"]["barge_in_while_speaking"],
                _check_status_pass_fail_not_tested(
                    self.data["interruptions"]["barge_in_while_speaking"] if self.data["interruptions"]["barge_in_while_speaking"] > 0 else None,
                    min_value=1,
                ),
                "interruptions.barge_in_while_speaking",
                "Barge-ins that arrived while tutor was actively speaking (successful cutoffs).",
            ),
        ]
        pocs["poc_01_interruption"] = {
            "status": _status_from_checks(p01_checks),
            "checks": p01_checks,
        }

        # ------------------------------------------------------------------
        # POC 02 — Proactive Vision
        # ------------------------------------------------------------------
        proactive_actual = proactive_total if self.data["video"]["frames_in"] > 0 else None
        p02_checks = [
            self._check(
                "P02.proactive_trigger_count",
                "Proactive triggers with visual input",
                ">=1",
                proactive_actual,
                _check_status_pass_fail_not_tested(proactive_actual, min_value=1),
                "proactive.poke_count + proactive.nudge_count",
            ),
            self._check(
                "P02.question_turn_ratio",
                "Question-ending turn ratio",
                "35-50%",
                question_ratio,
                _check_status_pass_fail_not_tested(question_ratio, min_value=35, max_value=50),
                "transcripts.tutor",
                "Only meaningful for longer tutoring sessions.",
            ),
            self._check(
                "P02.question_streak_max",
                "Max consecutive question turns",
                "<=2",
                question_streak_max if total_tutor_text_turns > 0 else None,
                _check_status_pass_fail_not_tested(
                    question_streak_max if total_tutor_text_turns > 0 else None,
                    max_value=2,
                ),
                "transcripts.tutor",
            ),
            self._check(
                "P02.repetition_loop_suspected",
                "Suspected repetitive tutor prompt loops",
                "0",
                self.data["conversation"]["suspected_repetition_loops"],
                _check_status_pass_fail_not_tested(
                    self.data["conversation"]["suspected_repetition_loops"],
                    max_value=0,
                ),
                "conversation.suspected_repetition_loops",
            ),
        ]
        pocs["poc_02_proactive_vision"] = {
            "status": _status_from_checks(p02_checks),
            "checks": p02_checks,
        }

        # ------------------------------------------------------------------
        # POC 03 — Multilingual (auto-detect, respond in student's language)
        # ------------------------------------------------------------------
        p03_checks = [
            self._check(
                "P03.language_purity_rate",
                "Language purity rate",
                ">=98%",
                language_latest.get("purity_rate"),
                _check_status_pass_fail_not_tested(language_latest.get("purity_rate"), min_value=98),
                "language.latest_metric.purity_rate",
            ),
        ]
        pocs["poc_03_multilingual"] = {
            "status": _status_from_checks(p03_checks),
            "checks": p03_checks,
        }

        # ------------------------------------------------------------------
        # POC 04 — Whiteboard Sync
        # ------------------------------------------------------------------
        p04_checks = [
            self._check(
                "P04.whiteboard_usage_rate",
                "Notes created",
                ">=1 note/session",
                self.data["whiteboard"]["notes_created"],
                _check_status_pass_fail_not_tested(
                    self.data["whiteboard"]["notes_created"]
                    if tutor_turn_count > 0 else None,
                    min_value=1,
                ),
                "whiteboard.notes_created",
            ),
            self._check(
                "P04.note_delivery_latency_p95",
                "Whiteboard delivery latency p95",
                "<=500ms",
                _round_or_none(whiteboard_p95, 1),
                _check_status_pass_fail_not_tested(whiteboard_p95, max_value=500),
                "whiteboard.delivery_latency_ms",
            ),
            self._check(
                "P04.audio_continuity_proxy",
                "Audio continuity while using whiteboard",
                "audio_out > 0 when notes created",
                self.data["audio"]["chunks_out"] if self.data["whiteboard"]["notes_created"] > 0 else None,
                _check_status_pass_fail_not_tested(
                    self.data["audio"]["chunks_out"] if self.data["whiteboard"]["notes_created"] > 0 else None,
                    min_value=1,
                ),
                "audio.chunks_out",
            ),
        ]
        pocs["poc_04_whiteboard_sync"] = {
            "status": _status_from_checks(p04_checks),
            "checks": p04_checks,
        }

        # ------------------------------------------------------------------
        # POC 06 — Session Resilience
        # ------------------------------------------------------------------
        retries = int(self.data["resilience"]["stream_retry_attempts"])
        successes = int(self.data["resilience"]["stream_reconnect_successes"])
        reconnect_success_rate = _safe_ratio(float(successes) * 100.0, float(retries))
        resume_attempts = int(self.data["resilience"]["session_resume_attempts"])
        resume_successes = int(self.data["resilience"]["session_resume_successes"])
        resume_success_rate = _safe_ratio(float(resume_successes) * 100.0, float(resume_attempts))
        p06_checks = [
            self._check(
                "P06.reconnect_success_rate",
                "Backend stream reconnect success rate",
                "100% (for transient drops)",
                _round_or_none(reconnect_success_rate, 1) if retries > 0 else None,
                _check_status_pass_fail_not_tested(
                    reconnect_success_rate if retries > 0 else None,
                    min_value=100,
                    max_value=100,
                ),
                "resilience.stream_reconnect_successes / stream_retry_attempts",
            ),
            self._check(
                "P06.retry_attempt_cap",
                "Retry attempts capped",
                "<=3",
                retries if retries > 0 else None,
                _check_status_pass_fail_not_tested(
                    retries if retries > 0 else None,
                    max_value=3,
                ),
                "resilience.stream_retry_attempts",
            ),
            self._check(
                "P06.session_resumption_success_rate",
                "Session resumption success rate",
                "100% when resumption is attempted",
                _round_or_none(resume_success_rate, 1) if resume_attempts > 0 else None,
                _check_status_pass_fail_not_tested(
                    resume_success_rate if resume_attempts > 0 else None,
                    min_value=100,
                    max_value=100,
                ),
                "resilience.session_resume_successes / session_resume_attempts",
            ),
        ]
        pocs["poc_06_session_resilience"] = {
            "status": _status_from_checks(p06_checks),
            "checks": p06_checks,
        }

        # ------------------------------------------------------------------
        # POC 07 — Latency Instrumentation & Budget
        # ------------------------------------------------------------------
        p07_checks = [
            self._check(
                "P07.response_start_avg",
                "Response start avg latency",
                "<=500ms",
                response_stats.get("avg"),
                _check_status_pass_fail_not_tested(response_stats.get("avg"), max_value=500),
                "latency.latest_report.metrics.response_start.avg",
            ),
            self._check(
                "P07.response_start_p95",
                "Response start p95 latency",
                "<=800ms",
                response_stats.get("p95"),
                _check_status_pass_fail_not_tested(response_stats.get("p95"), max_value=800),
                "latency.latest_report.metrics.response_start.p95",
            ),
            self._check(
                "P07.interruption_stop_p95",
                "Interruption stop p95 latency",
                "<=400ms",
                interruption_stats.get("p95"),
                _check_status_pass_fail_not_tested(interruption_stats.get("p95"), max_value=400),
                "latency.latest_report.metrics.interruption_stop.p95",
            ),
            self._check(
                "P07.turn_to_turn_p95",
                "Turn-to-turn p95 latency",
                "<=2500ms",
                turn_gap_stats.get("p95"),
                _check_status_pass_fail_not_tested(turn_gap_stats.get("p95"), max_value=2500),
                "latency.latest_report.metrics.turn_to_turn.p95",
            ),
            self._check(
                "P07.first_byte_p95",
                "First-byte p95 latency",
                "<=5000ms",
                first_byte_stats.get("p95"),
                _check_status_pass_fail_not_tested(first_byte_stats.get("p95"), max_value=5000),
                "latency.latest_report.metrics.first_byte.p95",
            ),
        ]
        pocs["poc_07_latency"] = {
            "status": _status_from_checks(p07_checks),
            "checks": p07_checks,
        }

        # ------------------------------------------------------------------
        # POC 08 — Tool Action Moment (post-event)
        # ------------------------------------------------------------------
        p08_checks = [
            self._check(
                "P08.a2a_summary_pipeline",
                "A2A summary pipeline",
                "implemented + measured",
                None,
                "not_tested",
                "manual",
                "Post-event feature; requires reflection agent pipeline instrumentation.",
            ),
        ]
        pocs["poc_08_tool_action_moment"] = {
            "status": _status_from_checks(p08_checks),
            "checks": p08_checks,
        }

        # ------------------------------------------------------------------
        # POC 09 — Safety & Scope Guardrails
        # ------------------------------------------------------------------
        p09_checks = [
            self._check(
                "P09.direct_answer_leak_rate",
                "Direct answer leak count",
                "0",
                self.data["guardrails"]["answer_leaks"],
                _check_status_pass_fail_not_tested(
                    self.data["guardrails"]["answer_leaks"],
                    max_value=0,
                ),
                "guardrails.answer_leaks",
            ),
            self._check(
                "P09.socratic_compliance_rate",
                "Socratic compliance rate",
                ">=90%",
                _round_or_none(socratic_compliance, 1),
                _check_status_pass_fail_not_tested(socratic_compliance, min_value=90),
                "(turns.count - guardrails.answer_leaks) / turns.count",
            ),
            self._check(
                "P09.prompt_injection_detection",
                "Prompt-injection detection events",
                ">=1 when injection test is run",
                self.data["guardrails"]["prompt_injections"]
                if self.data["guardrails"]["prompt_injections"] > 0 else None,
                _check_status_pass_fail_not_tested(
                    self.data["guardrails"]["prompt_injections"]
                    if self.data["guardrails"]["prompt_injections"] > 0 else None,
                    min_value=1,
                ),
                "guardrails.prompt_injections",
            ),
        ]
        pocs["poc_09_safety_guardrails"] = {
            "status": _status_from_checks(p09_checks),
            "checks": p09_checks,
        }

        # ------------------------------------------------------------------
        # POC 10 — Screen Share Toggle
        # ------------------------------------------------------------------
        source_switches = int(self.data["screen_share"]["source_switches"])
        p10_checks = [
            self._check(
                "P10.source_switch_count",
                "Source switches observed",
                ">=1 when screen-share flow tested",
                source_switches if source_switches > 0 else None,
                _check_status_pass_fail_not_tested(
                    source_switches if source_switches > 0 else None,
                    min_value=1,
                ),
                "screen_share.source_switches",
            ),
            self._check(
                "P10.switch_session_continuity",
                "Switching without fatal backend errors",
                "errors == 0",
                len(self.data["errors"]) if source_switches > 0 else None,
                _check_status_pass_fail_not_tested(
                    len(self.data["errors"]) if source_switches > 0 else None,
                    max_value=0,
                ),
                "errors",
            ),
            self._check(
                "P10.stop_sharing_path",
                "Stop sharing events",
                ">=1 when stop-sharing tested",
                self.data["screen_share"]["stop_sharing_count"]
                if source_switches > 0 else None,
                _check_status_pass_fail_not_tested(
                    self.data["screen_share"]["stop_sharing_count"]
                    if source_switches > 0 else None,
                    min_value=1,
                ),
                "screen_share.stop_sharing_count",
            ),
        ]
        pocs["poc_10_screen_share_toggle"] = {
            "status": _status_from_checks(p10_checks),
            "checks": p10_checks,
        }

        # ------------------------------------------------------------------
        # POC 11 — Idle Orchestration
        # ------------------------------------------------------------------
        p11_checks = [
            self._check(
                "P11.gentle_check_nag_limit",
                "Gentle check count",
                "<=1",
                self.data["idle"]["checkin_1_count"],
                _check_status_pass_fail_not_tested(self.data["idle"]["checkin_1_count"], max_value=1),
                "idle.checkin_1_count",
            ),
            self._check(
                "P11.options_check_nag_limit",
                "Options check count",
                "<=1",
                self.data["idle"]["checkin_2_count"],
                _check_status_pass_fail_not_tested(self.data["idle"]["checkin_2_count"], max_value=1),
                "idle.checkin_2_count",
            ),
            self._check(
                "P11.away_resume_flow_observed",
                "Away/resume flow observed",
                "away_activated >=1 and away_resumed >=1 when tested",
                (
                    f"{self.data['idle']['away_activated_count']}/"
                    f"{self.data['idle']['away_resumed_count']}"
                    if self.data["idle"]["away_activated_count"] > 0
                    else None
                ),
                _check_status_pass_fail_not_tested(
                    self.data["idle"]["away_resumed_count"]
                    if self.data["idle"]["away_activated_count"] > 0 else None,
                    min_value=1,
                ),
                "idle.away_activated_count + idle.away_resumed_count",
            ),
        ]
        pocs["poc_11_idle_orchestration"] = {
            "status": _status_from_checks(p11_checks),
            "checks": p11_checks,
        }

        # ------------------------------------------------------------------
        # POC 12 — Final Student Report (post-event)
        # ------------------------------------------------------------------
        p12_checks = [
            self._check(
                "P12.final_report_pipeline",
                "Final student report generation",
                "implemented + measured",
                None,
                "not_tested",
                "manual",
                "Post-event feature; not part of current live backend runtime path.",
            ),
        ]
        pocs["poc_12_final_student_report"] = {
            "status": _status_from_checks(p12_checks),
            "checks": p12_checks,
        }

        # ------------------------------------------------------------------
        # POC 13 — Memory Management
        # ------------------------------------------------------------------
        memory_checkpoints = int(self.data["memory"]["checkpoints_saved"])
        memory_cells_saved = int(self.data["memory"]["cells_saved"])
        memory_recalls_applied = int(self.data["memory"]["recalls_applied"])
        memory_checkpoint_attempts = int(self.data["memory"]["checkpoint_attempts"])
        memory_checkpoint_failed = int(self.data["memory"]["checkpoint_failed"])
        memory_budget_violations = int(self.data["memory"]["budget_violations"])
        memory_checkpoint_failure_rate = _safe_ratio(
            float(memory_checkpoint_failed) * 100.0,
            float(memory_checkpoint_attempts),
        )
        p13_checks = [
            self._check(
                "P13.memory_recall_applied",
                "Memory recall applied at session start",
                ">=1 when prior memory exists",
                memory_recalls_applied if memory_recalls_applied > 0 else None,
                _check_status_pass_fail_not_tested(
                    memory_recalls_applied if memory_recalls_applied > 0 else None,
                    min_value=1,
                ),
                "memory.recalls_applied",
            ),
            self._check(
                "P13.checkpoints_saved",
                "Checkpoint summaries saved",
                ">=1 in long session",
                memory_checkpoints if tutor_turn_count > 0 else None,
                _check_status_pass_fail_not_tested(
                    memory_checkpoints if tutor_turn_count > 0 else None,
                    min_value=1,
                ),
                "memory.checkpoints_saved",
            ),
            self._check(
                "P13.cells_saved",
                "Typed memory cells persisted",
                ">=1 when checkpoints saved",
                memory_cells_saved if memory_checkpoints > 0 else None,
                _check_status_pass_fail_not_tested(
                    memory_cells_saved if memory_checkpoints > 0 else None,
                    min_value=1,
                ),
                "memory.cells_saved",
            ),
            self._check(
                "P13.memory_budget_violations",
                "Memory injection budget violations",
                "0",
                memory_budget_violations,
                _check_status_pass_fail_not_tested(memory_budget_violations, max_value=0),
                "memory.budget_violations",
            ),
            self._check(
                "P13.checkpoint_failure_rate",
                "Checkpoint persistence failure rate",
                "0%",
                _round_or_none(memory_checkpoint_failure_rate, 1)
                if memory_checkpoint_attempts > 0 else None,
                _check_status_pass_fail_not_tested(
                    memory_checkpoint_failure_rate if memory_checkpoint_attempts > 0 else None,
                    max_value=0,
                ),
                "memory.checkpoint_failed / memory.checkpoint_attempts",
            ),
        ]
        pocs["poc_13_memory_management"] = {
            "status": _status_from_checks(p13_checks),
            "checks": p13_checks,
        }

        # ------------------------------------------------------------------
        # POC 14 — Mastery Verification Protocol
        # ------------------------------------------------------------------
        mastery_verified = int(self.data["mastery"]["verifications_completed"])
        mastery_blocked = int(self.data["mastery"]["premature_mastery_blocked"])
        mastery_step_failures = int(self.data["mastery"]["step_failures"])
        p14_checks = [
            self._check(
                "P14.mastery_verifications",
                "3-step mastery verifications completed",
                ">=1 when exercises mastered",
                mastery_verified if mastery_verified > 0 else None,
                _check_status_pass_fail_not_tested(
                    mastery_verified if mastery_verified > 0 else None,
                    min_value=1,
                ),
                "mastery.verifications_completed",
            ),
            self._check(
                "P14.premature_mastery_blocked",
                "Premature mastery attempts blocked",
                "tracked (informational)",
                mastery_blocked,
                "pass" if mastery_blocked >= 0 else "not_tested",
                "mastery.premature_mastery_blocked",
                "Counts times tutor tried to mark mastered without verification.",
            ),
            self._check(
                "P14.mastery_protocol_active",
                "Mastery protocol tool called",
                ">=1 when tutoring phase active",
                (mastery_verified + mastery_blocked + mastery_step_failures)
                if (mastery_verified + mastery_blocked + mastery_step_failures) > 0
                else None,
                _check_status_pass_fail_not_tested(
                    (mastery_verified + mastery_blocked + mastery_step_failures)
                    if (mastery_verified + mastery_blocked + mastery_step_failures) > 0
                    else None,
                    min_value=1,
                ),
                "mastery.*",
                "At least one mastery-related tool call observed.",
            ),
        ]
        pocs["poc_14_mastery_verification"] = {
            "status": _status_from_checks(p14_checks),
            "checks": p14_checks,
        }

        # ------------------------------------------------------------------
        # POC 99 — Hero Flow Rehearsal (integration)
        # ------------------------------------------------------------------
        reconnect_check_status = "not_tested"
        reconnect_actual = None
        if retries > 0:
            reconnect_actual = successes
            reconnect_check_status = "pass" if successes > 0 else "fail"

        p99_checks = [
            self._check(
                "P99.proactive_vision_checkpoint",
                "Checklist: proactive vision",
                "triggered",
                proactive_total,
                "pass" if proactive_total > 0 else "fail",
                "proactive.*",
            ),
            self._check(
                "P99.whiteboard_checkpoint",
                "Checklist: whiteboard note",
                ">=1 note",
                self.data["whiteboard"]["notes_created"],
                "pass" if self.data["whiteboard"]["notes_created"] > 0 else "fail",
                "whiteboard.notes_created",
            ),
            self._check(
                "P99.interruption_checkpoint",
                "Checklist: interruption handled",
                ">=1 interruption or barge-in",
                self.data["interruptions"]["count"] + self.data["interruptions"]["barge_in_while_speaking"],
                "pass" if (self.data["interruptions"]["count"] > 0 or self.data["interruptions"]["barge_in_while_speaking"] > 0) else "fail",
                "interruptions.count + interruptions.barge_in_while_speaking",
            ),
            self._check(
                "P99.action_moment_checkpoint",
                "Checklist: action moment (3+ exchanges)",
                "turns >= 3",
                tutor_turn_count,
                "pass" if tutor_turn_count >= 3 else "fail",
                "turns.count",
            ),
            self._check(
                "P99.reconnect_checkpoint",
                "Checklist: reconnect survived",
                ">=1 successful reconnect",
                reconnect_actual,
                reconnect_check_status,
                "resilience.stream_reconnect_successes",
            ),
        ]
        checklist_completed = sum(1 for c in p99_checks if c["status"] == "pass")
        pocs["poc_99_hero_flow_rehearsal"] = {
            "status": _status_from_checks(p99_checks),
            "checks": p99_checks,
            "checklist_completed": checklist_completed,
            "checklist_total": len(p99_checks),
        }

        # ------------------------------------------------------------------
        # Overall summary
        # ------------------------------------------------------------------
        all_checks = [c for entry in pocs.values() for c in entry.get("checks", [])]
        passed = sum(1 for c in all_checks if c.get("status") == "pass")
        failed = sum(1 for c in all_checks if c.get("status") == "fail")
        not_tested = sum(1 for c in all_checks if c.get("status") == "not_tested")
        auto_checks = sum(1 for c in all_checks if c.get("source") != "manual")
        auto_passed = sum(
            1 for c in all_checks if c.get("source") != "manual" and c.get("status") == "pass"
        )
        auto_failed = sum(
            1 for c in all_checks if c.get("source") != "manual" and c.get("status") == "fail"
        )
        auto_not_tested = sum(
            1 for c in all_checks if c.get("source") != "manual" and c.get("status") == "not_tested"
        )
        auto_pass_rate = _safe_ratio(float(auto_passed) * 100.0, float(auto_checks))
        stream_retry_success_rate = _safe_ratio(float(successes) * 100.0, float(retries))
        resume_success_rate_percent = _safe_ratio(float(resume_successes) * 100.0, float(resume_attempts))
        retry_backoffs = self.data["resilience"].get("retry_backoff_seconds", [])
        retry_backoff_avg = None
        if isinstance(retry_backoffs, list) and retry_backoffs:
            retry_backoff_avg = sum(float(v) for v in retry_backoffs) / len(retry_backoffs)

        recall_token_estimates = self.data["memory"].get("recall_token_estimates", [])
        recall_avg_tokens = None
        if isinstance(recall_token_estimates, list) and recall_token_estimates:
            recall_avg_tokens = sum(float(v) for v in recall_token_estimates) / len(recall_token_estimates)

        memory_checkpoint_attempts = int(self.data["memory"]["checkpoint_attempts"])
        memory_checkpoint_failed = int(self.data["memory"]["checkpoint_failed"])
        checkpoint_success_rate = _safe_ratio(
            float(self.data["memory"]["checkpoints_saved"]) * 100.0,
            float(memory_checkpoint_attempts),
        )
        memory_avg_cells_per_checkpoint = _safe_ratio(
            float(self.data["memory"]["cells_saved"]),
            float(self.data["memory"]["checkpoints_saved"]),
        )

        poc_status_counts = {
            "pass": 0,
            "partial": 0,
            "fail": 0,
            "not_tested": 0,
        }
        for entry in pocs.values():
            st = str(entry.get("status", "not_tested"))
            poc_status_counts[st] = poc_status_counts.get(st, 0) + 1

        return {
            "version": "v1",
            "generated_at": time.time(),
            "pocs": pocs,
            "summary": {
                "checks_passed": passed,
                "checks_failed": failed,
                "checks_not_tested": not_tested,
                "checks_total": len(all_checks),
                "auto_checks_total": auto_checks,
                "auto_checks_passed": auto_passed,
                "auto_checks_failed": auto_failed,
                "auto_checks_not_tested": auto_not_tested,
                "auto_pass_rate_percent": _round_or_none(auto_pass_rate, 1),
                "poc_status_counts": poc_status_counts,
            },
            "derived_metrics": {
                "student_turns": student_turns,
                "tutor_turns": tutor_turn_count,
                "question_turns": question_turns,
                "question_turn_ratio_percent": question_ratio,
                "max_question_streak": question_streak_max,
                "student_heard_rate_percent": _round_or_none(
                    student_heard_rate * 100.0 if student_heard_rate is not None else None, 1
                ),
                "socratic_compliance_percent": _round_or_none(socratic_compliance, 1),
                "whiteboard_delivery_p95_ms": _round_or_none(whiteboard_p95, 1),
                "proactive_trigger_total": proactive_total,
                "compression_events": int(self.data["compression"]["events"]),
                "compression_last_token_estimate": int(self.data["compression"]["last_token_estimate"]),
                "memory_checkpoints_saved": int(self.data["memory"]["checkpoints_saved"]),
                "memory_cells_saved": int(self.data["memory"]["cells_saved"]),
                "memory_avg_cells_per_checkpoint": _round_or_none(memory_avg_cells_per_checkpoint, 2),
                "memory_checkpoint_attempts": memory_checkpoint_attempts,
                "memory_checkpoint_failed": memory_checkpoint_failed,
                "memory_checkpoint_success_rate_percent": _round_or_none(checkpoint_success_rate, 1),
                "memory_recalls_applied": int(self.data["memory"]["recalls_applied"]),
                "memory_recall_checks": int(self.data["memory"]["recall_checks"]),
                "memory_recall_avg_tokens": _round_or_none(recall_avg_tokens, 1),
                "session_resume_attempts": int(self.data["resilience"]["session_resume_attempts"]),
                "session_resume_successes": int(self.data["resilience"]["session_resume_successes"]),
                "session_resume_success_rate_percent": _round_or_none(resume_success_rate_percent, 1),
                "stream_retry_success_rate_percent": _round_or_none(stream_retry_success_rate, 1),
                "stream_retry_backoff_avg_seconds": _round_or_none(retry_backoff_avg, 3),
            },
        }

    # --- Finalize & Save ---

    def finalize(self, ended_reason: str):
        now = time.time()
        self.data["ended_at"] = now
        self.data["duration_seconds"] = round(now - self.data["started_at"], 1)
        self.data["ended_reason"] = ended_reason
        self._recompute_latency_report()
        self.data["prd_scorecard"] = self._build_prd_scorecard()

    def save(self) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        session_id = self.data["session_id"]
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{session_id[:8]}.json"
        path = OUTPUT_DIR / filename
        with open(path, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        logger.info("Test report saved: %s", path)
        return path
