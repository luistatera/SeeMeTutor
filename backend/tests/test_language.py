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
        state = init_language_state(
            {
                "l1": "en-US",
                "l2": "de-DE",
                "detection_patterns": {
                    "en": [r"\bwhat\b"],
                    "de": [r"\bich\b"],
                },
            },
            preferred_language="en",
        )
        assert detect_language(
            "What is your answer?",
            candidate_langs={"en", "de"},
            runtime_state=state,
        ) == "en"

    def test_detects_portuguese(self):
        state = init_language_state(
            {
                "l1": "pt-BR",
                "l2": "en-US",
                "detection_patterns": {
                    "pt": [r"\bnao\b"],
                    "en": [r"\bwhat\b"],
                },
            },
            preferred_language="pt",
        )
        assert detect_language(
            "Nao entendi, pode explicar?",
            candidate_langs={"pt", "en"},
            runtime_state=state,
        ) == "pt"

    def test_detects_german(self):
        state = init_language_state(
            {
                "l1": "de-DE",
                "l2": "en-US",
                "detection_patterns": {
                    "de": [r"\bich\b"],
                    "en": [r"\bwhat\b"],
                },
            },
            preferred_language="de",
        )
        assert detect_language(
            "Ich verstehe das nicht, bitte.",
            candidate_langs={"de", "en"},
            runtime_state=state,
        ) == "de"

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
                "signal_patterns": [r"\bi\s+don't\s+understand\b"],
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
                "signal_patterns": [r"\bi\s+don't\s+understand\b"],
            },
        }
        state = init_language_state(policy, preferred_language="de")

        first = handle_student_transcript("I don't understand", state)
        second = handle_student_transcript("I don't understand", state)

        assert len(first["events"]) >= 1
        assert second["events"] == []
        assert state["language_metrics"]["confusion_signals"] == 1

    def test_guided_mode_student_language_update_does_not_force_control_prompt(self):
        policy = {
            "mode": "guided_bilingual",
            "l1": "en-US",
            "l2": "de-DE",
            "detection_patterns": {
                "de": [r"\bich\b"],
                "en": [r"\bwhat\b"],
            },
        }
        state = init_language_state(policy, preferred_language="en")

        update = handle_student_transcript("Ich verstehe das.", state)

        assert update["student_language"] == "de"
        assert update["expected_language"] == "de"
        assert update["control_prompt"] is None


class TestFinalizeTutorTurn:
    def test_guided_mode_switches_phase_after_min_turns(self):
        policy = {
            "mode": "guided_bilingual",
            "l1": "en-US",
            "l2": "de-DE",
            "explain_language": "l1",
            "practice_language": "l2",
            "guided_phase_min_turns": 2,
        }
        state = init_language_state(policy, preferred_language="en")
        append_tutor_text_part(state, "This is your first strategy step.", source="transcript")

        first = finalize_tutor_turn(state)
        append_tutor_text_part(state, "This is your second strategy step.", source="transcript")
        second = finalize_tutor_turn(state)

        assert first["control_prompt"] is None
        assert second["control_prompt"] is not None
        assert state["language_guided_phase"] == "practice"
        assert state["language_metrics"]["tutor_turns"] == 2
        assert state["language_metrics"]["guided_expected_turns"] == 2

    def test_immersion_triggers_recap_after_l2_streak(self):
        policy = {
            "mode": "immersion",
            "l1": "en-US",
            "l2": "de-DE",
            "max_l2_turns_before_recap": 1,
            "recap_policy": {
                "min_l2_streak": 1,
                "base_l2_streak": 1
            },
            "detection_patterns": {
                "de": [r"\b[Ii]ch\b"]
            }
        }
        state = init_language_state(policy, preferred_language="de")
        append_tutor_text_part(state, "Ich verstehe das gut.", source="transcript")

        result = finalize_tutor_turn(state)

        assert any(e.get("event") == "recap_triggered" for e in result["events"])
        assert result["control_prompt"] is not None
        assert state["language_force_language_key"] == "l1"
        assert state["language_force_turns_remaining"] == 1

    def test_generic_l1_locale_is_preserved_in_runtime(self):
        policy = {
            "mode": "guided_bilingual",
            "l1": "es-MX",
            "l2": "de-DE",
        }
        state = init_language_state(policy, preferred_language="es-MX")
        assert state["language_l1_short"] == "es"
        assert "es" in state["language_session_langs"]

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
