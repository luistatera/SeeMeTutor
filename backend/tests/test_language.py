"""Unit tests for modules/language.py."""

from modules.language import (
    build_language_metric_snapshot,
    detect_language,
    finalize_tutor_turn,
    handle_student_transcript,
    init_language_state,
    append_tutor_text_part,
)


class TestDetectLanguage:
    def test_detects_english(self):
        assert detect_language("What is your answer?") == "en"

    def test_detects_portuguese(self):
        assert detect_language("Nao entendi, pode explicar?") == "pt"

    def test_detects_german(self):
        assert detect_language("Ich verstehe das nicht, bitte.") == "de"

    def test_empty_returns_unknown(self):
        assert detect_language("") == "unknown"


class TestHandleStudentTranscript:
    def test_confusion_triggers_fallback(self):
        policy = {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "de-DE",
            "confusion_fallback": {
                "after_confusions": 1,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        }
        state = init_language_state(policy, preferred_language="de")

        update = handle_student_transcript("I don't understand this step.", state)

        events = update["events"]
        assert any(e.get("event") == "confusion_signal" for e in events)
        assert any(e.get("event") == "fallback_triggered" for e in events)
        assert update["control_prompt"] is not None
        assert state["language_force_language_key"] == "l1"
        assert state["language_force_turns_remaining"] == 2

    def test_repeated_partial_confusion_is_debounced(self):
        policy = {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "de-DE",
            "confusion_fallback": {
                "after_confusions": 3,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        }
        state = init_language_state(policy, preferred_language="de")

        first = handle_student_transcript("I don't understand", state)
        second = handle_student_transcript("I don't understand", state)

        assert len(first["events"]) >= 1
        assert second["events"] == []
        assert state["language_metrics"]["confusion_signals"] == 1


class TestFinalizeTutorTurn:
    def test_guided_mode_switches_phase(self):
        policy = {
            "mode": "guided_bilingual",
            "l1": "en-US",
            "l2": "de-DE",
            "explain_language": "l1",
            "practice_language": "l2",
        }
        state = init_language_state(policy, preferred_language="en")
        append_tutor_text_part(state, "Let's break this down together.", source="transcript")

        result = finalize_tutor_turn(state)

        assert result["control_prompt"] is not None
        assert state["language_guided_phase"] == "practice"
        assert state["language_metrics"]["tutor_turns"] == 1
        assert state["language_metrics"]["guided_expected_turns"] == 1

    def test_immersion_triggers_recap_after_l2_streak(self):
        policy = {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "de-DE",
            "max_l2_turns_before_recap": 1,
        }
        state = init_language_state(policy, preferred_language="de")
        append_tutor_text_part(state, "Ich verstehe das gut.", source="transcript")

        result = finalize_tutor_turn(state)

        assert any(e.get("event") == "recap_triggered" for e in result["events"])
        assert result["control_prompt"] is not None
        assert state["language_force_language_key"] == "l1"
        assert state["language_force_turns_remaining"] == 1

    def test_metric_snapshot_computed(self):
        policy = {
            "mode": "guided_bilingual",
            "l1": "en-US",
            "l2": "de-DE",
        }
        state = init_language_state(policy, preferred_language="en")
        append_tutor_text_part(state, "This is in English.", source="text")
        finalize_tutor_turn(state)

        snapshot = build_language_metric_snapshot(state)
        assert snapshot["tutor_turns"] == 1
        assert "purity_rate" in snapshot
