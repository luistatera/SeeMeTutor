"""Unit tests for memory management helpers."""

from modules.memory_manager import (
    append_transcript_piece,
    build_checkpoint_summary,
    build_hidden_memory_context,
    build_recall_payload,
    extract_cells_from_checkpoint,
    init_memory_state,
)


def test_init_memory_state_defaults():
    state = init_memory_state(checkpoint_interval_s=120, recall_budget_tokens=320, recall_max_cells=4)
    assert state["memory_checkpoint_interval_s"] == 120
    assert state["memory_recall_budget_tokens"] == 320
    assert state["memory_recall_max_cells"] == 4


def test_checkpoint_summary_uses_recent_turns():
    runtime_state = {
        "student_id": "student-1",
        "track_id": "track-1",
        "topic_id": "fractions",
        "topic_title": "Fractions",
    }
    for line in (
        ("student", "How do I simplify this fraction?"),
        ("tutor", "Find the greatest common divisor first."),
        ("student", "So I divide numerator and denominator by 3?"),
        ("tutor", "Exactly. Then check if it can be reduced again."),
    ):
        append_transcript_piece(runtime_state, role=line[0], text=line[1])

    checkpoint = build_checkpoint_summary(runtime_state, reason="interval_turn")
    assert checkpoint["topic_id"] == "fractions"
    assert "Fractions" in checkpoint["summary_text"]
    assert checkpoint["key_points"]


def test_extract_cells_from_checkpoint_returns_typed_cells():
    checkpoint = {
        "topic_id": "fractions",
        "summary_text": "Topic: Fractions. Key points: simplify by common divisor.",
        "next_step": "Practice two simplification examples.",
        "open_questions": ["How do I know if it's fully simplified?"],
        "key_points": ["Simplify numerator and denominator by the same divisor."],
    }
    cells = extract_cells_from_checkpoint(
        checkpoint,
        source_session_id="sess-001",
        tutor_preferences={"speech_pace": "slow"},
    )
    assert cells
    assert any(cell["cell_type"] == "plan" for cell in cells)
    assert all(cell["topic_id"] == "fractions" for cell in cells)


def test_recall_payload_respects_budget():
    cells = [
        {
            "cell_type": "fact",
            "text": "Fractions can be simplified by dividing numerator and denominator by the same non-zero integer.",
            "topic_id": "fractions",
            "salience": 0.9,
            "source_session_id": "s1",
            "created_at": 1.0,
            "updated_at": 1.0,
        },
        {
            "cell_type": "plan",
            "text": "Practice 5 short simplification exercises before moving to addition.",
            "topic_id": "fractions",
            "salience": 0.8,
            "source_session_id": "s1",
            "created_at": 2.0,
            "updated_at": 2.0,
        },
    ]
    payload = build_recall_payload(cells, topic_id="fractions", budget_tokens=50, max_cells=5)
    assert payload["selected_count"] >= 1
    assert payload["token_estimate"] <= 50


def test_hidden_memory_context_supports_summary_only():
    text = build_hidden_memory_context({"summary": "Student struggled with equivalent fractions."})
    assert "MEMORY RECALL" in text
    assert "equivalent fractions" in text
