"""
Automated test report capture for migration validation.

Collects events during a session and dumps a structured JSON file
at session end. Each migration step has expected outcomes that can
be compared against the captured report.

Usage in main.py:
    report = create_report(session_id, student_id)
    # ... during session, call report.record_*() methods ...
    report.finalize("student_ended")
    report.save()  # writes to backend/test_results/

Usage in agent.py tools:
    from test_report import get_report
    report = get_report(session_id)
    if report:
        report.record_tool_call(...)
"""

import json
import logging
import os
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# SessionReport
# ---------------------------------------------------------------------------
class SessionReport:
    def __init__(self, session_id: str, student_id: str):
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
            },
            "interruptions": {
                "count": 0,
                "stale_filtered": 0,
            },
            "turns": {
                "count": 0,
                "audio_chunks_per_turn": [],
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
        self.data["audio"]["latency_samples_ms"].append(round(ms, 1))
        if self.data["audio"]["first_response_ms"] is None:
            self.data["audio"]["first_response_ms"] = round(ms, 1)

    # --- Video ---

    def record_video_in(self):
        self.data["video"]["frames_in"] += 1

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
            "duration_ms": round(duration_ms, 1),
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
        self.data["turns"]["audio_chunks_per_turn"].append(audio_chunks_this_turn)

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
        self.data["guardrails"]["events"].append({
            "guardrail": guardrail,
            "severity": severity,
            "source": source,
            "timestamp": time.time(),
        })
        if guardrail in ("off_topic", "cheat_request", "content_moderation"):
            self.data["guardrails"]["refusals_total"] += 1
        if guardrail == "answer_leak":
            self.data["guardrails"]["answer_leaks"] += 1
        if guardrail == "content_moderation":
            self.data["guardrails"]["content_flags"] += 1

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

    # --- Finalize & Save ---

    def finalize(self, ended_reason: str):
        now = time.time()
        self.data["ended_at"] = now
        self.data["duration_seconds"] = round(now - self.data["started_at"], 1)
        self.data["ended_reason"] = ended_reason

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
