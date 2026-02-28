"""Tests for unified PRD scorecard generation in test_report.py."""

from test_report import SessionReport


def test_finalize_builds_prd_scorecard_structure():
    report = SessionReport("session-123", "student-abc")
    report.record_backlog_sent()
    report.finalize("student_ended")

    conversation = report.data["conversation"]
    assert conversation["awaiting_reply_prompts"] == 0
    assert conversation["suspected_repetition_loops"] == 0

    scorecard = report.data["prd_scorecard"]
    assert isinstance(scorecard, dict)
    assert "pocs" in scorecard
    assert "summary" in scorecard
    assert "poc_01_interruption" in scorecard["pocs"]
    assert "poc_99_hero_flow_rehearsal" in scorecard["pocs"]


def test_latency_and_language_checks_can_pass():
    report = SessionReport("session-234", "student-def")
    report.record_backlog_sent()
    report.record_turn_complete(3)
    report.record_student_transcript("Can you explain this?")
    report.record_tutor_transcript("Let's do this together.")

    report.record_latency_report({
        "metrics": {
            "response_start": {"avg": 320, "p95": 510},
            "interruption_stop": {"p95": 180},
            "turn_to_turn": {"p95": 1200},
            "first_byte": {"p95": 2200},
        },
        "turns": 1,
        "alerts": 0,
    })
    report.record_language_metric({
        "purity_rate": 99.0,
        "guided_adherence": 98.0,
        "guided_expected_turns": 2,
        "fallback_latency_turns": [1.0],
        "l2_ratio": 75.0,
    })

    report.finalize("student_ended")
    pocs = report.data["prd_scorecard"]["pocs"]

    assert pocs["poc_03_multilingual"]["status"] == "pass"
    assert pocs["poc_07_latency"]["status"] == "pass"


def test_whiteboard_delivery_latency_is_captured():
    report = SessionReport("session-345", "student-ghi")
    report.record_backlog_sent()
    report.record_audio_out()
    report.record_turn_complete(2)

    report.record_whiteboard_note_created()
    report.record_whiteboard_note_queued("note-1")
    report.record_whiteboard_note_delivered("note-1", "speech")

    report.finalize("student_ended")

    whiteboard = report.data["whiteboard"]
    assert whiteboard["notes_created"] == 1
    assert whiteboard["notes_delivered"] == 1
    assert len(whiteboard["delivery_latency_ms"]) == 1
