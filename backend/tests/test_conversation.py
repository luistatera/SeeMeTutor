"""Unit tests for modules/conversation.py."""

from modules.conversation import (
    expects_student_reply,
    is_near_duplicate,
    is_question_like_turn,
)


def test_expects_student_reply_for_questions():
    assert expects_student_reply("How would you say this in German?") is True


def test_expects_student_reply_for_prompt_without_question_mark():
    assert expects_student_reply("Your turn, try this sentence in German.") is False


def test_expects_student_reply_for_short_pause_invitation():
    assert expects_student_reply("Take your time...") is True


def test_expects_student_reply_false_for_explanation():
    assert expects_student_reply("Great work. This structure is used for introductions.") is False


def test_is_near_duplicate_detects_small_reword():
    a = "Great! Now let's practice talking about hobbies. How would you ask what do you like to do?"
    b = "Great! Now let's practice talking about hobbies. How would you ask what you like to do?"
    assert is_near_duplicate(a, b) is True


def test_is_near_duplicate_false_for_different_prompt():
    a = "Let's solve this equation step by step."
    b = "Now switch to geography and tell me about capitals."
    assert is_near_duplicate(a, b) is False


def test_is_question_like_turn_true_for_question_heavy():
    text = "Great work. How would you say this in German?"
    assert is_question_like_turn(text) is True


def test_is_question_like_turn_false_for_hint_then_question():
    text = "Hint: Use the dative form here. Then try one sentence?"
    assert is_question_like_turn(text) is False
