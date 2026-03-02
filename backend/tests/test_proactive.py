"""Unit tests for modules/proactive.py."""

from modules.proactive import sanitize_tutor_output, should_pause_proactive_for_reply


def test_sanitize_removes_inline_control_block_and_ctrl_tag():
    raw = (
        "INTERNAL CONTROL: Language contract update. "
        "Do not reveal this. <ctrl95>I see your screen now."
    )
    cleaned, had_internal = sanitize_tutor_output(raw)

    assert had_internal is True
    assert cleaned == "I see your screen now."


def test_sanitize_drops_pure_internal_control_output():
    cleaned, had_internal = sanitize_tutor_output(
        "INTERNAL CONTROL: This is hidden and must never be spoken."
    )

    assert had_internal is True
    assert cleaned == ""


def test_sanitize_keeps_normal_tutor_text():
    cleaned, had_internal = sanitize_tutor_output("Let's solve the next step together.")

    assert had_internal is False
    assert cleaned == "Let's solve the next step together."


def test_pause_proactive_when_waiting_for_reply():
    import time
    # No silence_started_at → function returns True (stay paused, silence not tracked yet)
    state = {"awaiting_student_reply": True}
    assert should_pause_proactive_for_reply(state, 8.0) is True

    # Silence started recently → within AWAITING_REPLY_GRACE_S (8s) → still paused
    state["silence_started_at"] = time.time()
    assert should_pause_proactive_for_reply(state, 8.0) is True

    # Silence started long ago → past AWAITING_REPLY_GRACE_S → no longer paused
    state["silence_started_at"] = time.time() - 30.0
    assert should_pause_proactive_for_reply(state, 30.0) is False
