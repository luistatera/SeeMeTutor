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
            "grounding": {
                "events": 0,
                "citations_sent": 0,
                "search_queries": [],
            },
            "screen_share": {
                "source_switches": 0,
                "stop_sharing_count": 0,
                "switch_log": [],
            },
            "errors": [],
            "manual": {},
            "prd_scorecard": {},
        }

    # --- Connection ---

    def record_backlog_sent(self):
        self.data["connection"]["backlog_context_sent"] = True

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

    def record_latency_event(self, metric: str, value_ms: float, is_alert: bool):
        self.data["latency"]["events"].append({
            "metric": metric,
            "value_ms": round(float(value_ms), 1),
            "is_alert": bool(is_alert),
            "timestamp": time.time(),
        })

    def record_latency_report(self, report_payload: dict):
        snapshot = copy.deepcopy(report_payload or {})
        self.data["latency"]["latest_report"] = snapshot
        self.data["latency"]["reports"].append(snapshot)

    # --- Language ---

    def record_language_event(self, event: dict):
        self.data["language"]["events"].append({
            "event": copy.deepcopy(event),
            "timestamp": time.time(),
        })

    def record_language_metric(self, metric_payload: dict):
        snapshot = copy.deepcopy(metric_payload or {})
        self.data["language"]["latest_metric"] = snapshot
        self.data["language"]["metrics_history"].append(snapshot)

    # --- Resilience ---

    def record_stream_retry_attempt(self, attempt: int, reason: str):
        self.data["resilience"]["stream_retry_attempts"] += 1
        self.data["resilience"]["events"].append({
            "type": "stream_retry_attempt",
            "attempt": int(attempt),
            "reason": str(reason),
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

    # --- Interruptions ---

    def record_interruption(self):
        self.data["interruptions"]["count"] += 1

    def record_stale_interruption(self):
        self.data["interruptions"]["stale_filtered"] += 1

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

    def record_away_resumed(self):
        self.data["idle"]["away_resumed_count"] += 1

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

    def record_guardrail_reinforcement(self):
        self.data["guardrails"]["drift_reinforcements"] += 1

    # --- Grounding ---

    def record_grounding_citation(self, source: str, query: str):
        self.data["grounding"]["events"] += 1
        self.data["grounding"]["citations_sent"] += 1
        if query:
            self.data["grounding"]["search_queries"].append(query)

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
        # POC 03 — Multilingual
        # ------------------------------------------------------------------
        guided_expected = language_latest.get("guided_expected_turns")
        guided_adherence = language_latest.get("guided_adherence")
        fallback_latency_turns = language_latest.get("fallback_latency_turns", [])
        fallback_latency_avg = None
        if isinstance(fallback_latency_turns, list) and fallback_latency_turns:
            fallback_latency_avg = sum(float(v) for v in fallback_latency_turns) / len(fallback_latency_turns)

        p03_checks = [
            self._check(
                "P03.language_purity_rate",
                "Language purity rate",
                ">=98%",
                language_latest.get("purity_rate"),
                _check_status_pass_fail_not_tested(language_latest.get("purity_rate"), min_value=98),
                "language.latest_metric.purity_rate",
            ),
            self._check(
                "P03.guided_bilingual_adherence",
                "Guided bilingual adherence",
                ">=95%",
                guided_adherence if isinstance(guided_expected, int) and guided_expected > 0 else None,
                _check_status_pass_fail_not_tested(
                    guided_adherence if isinstance(guided_expected, int) and guided_expected > 0 else None,
                    min_value=95,
                ),
                "language.latest_metric.guided_adherence",
            ),
            self._check(
                "P03.fallback_trigger_latency",
                "Fallback trigger latency (turns)",
                "<=1 turn",
                _round_or_none(fallback_latency_avg, 2),
                _check_status_pass_fail_not_tested(fallback_latency_avg, max_value=1),
                "language.latest_metric.fallback_latency_turns",
            ),
            self._check(
                "P03.l2_distribution",
                "L2 word ratio",
                ">=70%",
                language_latest.get("l2_ratio"),
                _check_status_pass_fail_not_tested(language_latest.get("l2_ratio"), min_value=70),
                "language.latest_metric.l2_ratio",
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
        # POC 05 — Search Grounding
        # ------------------------------------------------------------------
        grounding_events = int(self.data["grounding"]["events"])
        citations_sent = int(self.data["grounding"]["citations_sent"])
        citation_render_rate = _safe_ratio(float(citations_sent) * 100.0, float(grounding_events))
        p05_checks = [
            self._check(
                "P05.grounding_event_count",
                "Grounding events on factual/search turns",
                ">=1 when factual search tested",
                grounding_events if grounding_events > 0 else None,
                _check_status_pass_fail_not_tested(
                    grounding_events if grounding_events > 0 else None,
                    min_value=1,
                ),
                "grounding.events",
            ),
            self._check(
                "P05.citation_render_rate",
                "Citation render rate",
                "100%",
                _round_or_none(citation_render_rate, 1) if grounding_events > 0 else None,
                _check_status_pass_fail_not_tested(
                    citation_render_rate if grounding_events > 0 else None,
                    min_value=100,
                    max_value=100,
                ),
                "grounding.citations_sent / grounding.events",
            ),
            self._check(
                "P05.search_queries_logged",
                "Grounding search query logs",
                ">=1 query when grounding occurs",
                len(self.data["grounding"]["search_queries"]) if grounding_events > 0 else None,
                _check_status_pass_fail_not_tested(
                    len(self.data["grounding"]["search_queries"]) if grounding_events > 0 else None,
                    min_value=1,
                ),
                "grounding.search_queries",
            ),
        ]
        pocs["poc_05_search_grounding"] = {
            "status": _status_from_checks(p05_checks),
            "checks": p05_checks,
        }

        # ------------------------------------------------------------------
        # POC 06 — Session Resilience
        # ------------------------------------------------------------------
        retries = int(self.data["resilience"]["stream_retry_attempts"])
        successes = int(self.data["resilience"]["stream_reconnect_successes"])
        reconnect_success_rate = _safe_ratio(float(successes) * 100.0, float(retries))
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
        # POC 13 — Memory Management (post-event)
        # ------------------------------------------------------------------
        p13_checks = [
            self._check(
                "P13.memory_recall_pipeline",
                "Long-horizon memory recall",
                "implemented + measured",
                None,
                "not_tested",
                "manual",
                "Post-event feature; requires memory ingestion + retrieval pipeline.",
            ),
        ]
        pocs["poc_13_memory_management"] = {
            "status": _status_from_checks(p13_checks),
            "checks": p13_checks,
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
                ">=1 interruption",
                self.data["interruptions"]["count"],
                "pass" if self.data["interruptions"]["count"] > 0 else "fail",
                "interruptions.count",
            ),
            self._check(
                "P99.grounding_checkpoint",
                "Checklist: search citation shown",
                ">=1 citation",
                self.data["grounding"]["citations_sent"],
                "pass" if self.data["grounding"]["citations_sent"] > 0 else "fail",
                "grounding.citations_sent",
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
            },
        }

    # --- Finalize & Save ---

    def finalize(self, ended_reason: str):
        now = time.time()
        self.data["ended_at"] = now
        self.data["duration_seconds"] = round(now - self.data["started_at"], 1)
        self.data["ended_reason"] = ended_reason
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
