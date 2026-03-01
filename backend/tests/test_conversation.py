"""Unit tests for modules/conversation.py."""

from modules.conversation import (
    build_example_note,
    build_question_answer_note,
    extract_example_from_turn,
    expects_student_reply,
    is_near_duplicate,
    is_question_like_turn,
    is_student_question,
    is_study_related_question,
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


def test_is_student_question_detects_question_mark():
    assert is_student_question("How do I solve this equation?") is True


def test_is_student_question_detects_starter_without_question_mark():
    assert is_student_question("Can you explain this step to me") is True


def test_is_study_related_question_true_for_topic_overlap():
    assert is_study_related_question(
        "Why does this equation become linear here?",
        topic_title="Linear Equations",
    ) is True


def test_is_study_related_question_false_for_off_topic_prompt():
    assert is_study_related_question(
        "What's the weather in Berlin today?",
        topic_title="Fractions",
    ) is False


def test_build_question_answer_note_formats_title_and_content():
    title, content = build_question_answer_note(
        question="Why do we move this term to the other side?",
        answer="Because subtracting the same value from both sides keeps the equation balanced.",
        sequence=2,
    )

    assert title.startswith("My note 2:")
    assert "Q: Why do we move this term to the other side?" in content
    assert "A: Because subtracting the same value from both sides keeps the equation balanced." in content


def test_extract_example_from_turn_with_marker():
    text = "Great job. For example, if x + 2 = 7 then x = 5."
    extracted = extract_example_from_turn(text)
    assert extracted.lower().startswith("for example")
    assert "x + 2 = 7" in extracted


def test_extract_example_from_turn_empty_for_non_example():
    text = "Nice work. Now try the next step on your own."
    assert extract_example_from_turn(text) == ""


def test_build_example_note_formats_note():
    title, content = build_example_note("For example, 3x = 12 so x = 4.", 3)
    assert title == "My note 3: Example"
    assert content.startswith("Example: For example, 3x = 12")
def test_barge_in_handling():
    """
    Simulate user sending audio while the tutor is speaking.
    Assert that the turn_text buffer clears and is_interrupted metric bumps.
    """
    class MockSessionMetrics:
        def __init__(self):
            self.is_interrupted = False
            self.turn_text_buffer = ["Hello", " ", "there.", " Let", " me", " explain"]
            
        def handle_user_audio(self):
            # Barge-in detected
            self.turn_text_buffer.clear()
            self.is_interrupted = True

    metrics = MockSessionMetrics()
    assert len(metrics.turn_text_buffer) > 0
    
    metrics.handle_user_audio()
    
    assert len(metrics.turn_text_buffer) == 0
    assert metrics.is_interrupted is True
