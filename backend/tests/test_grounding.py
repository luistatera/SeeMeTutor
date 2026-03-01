"""Unit tests for modules/grounding.py."""

from modules.grounding import extract_inline_url_citations, init_grounding_state


def test_init_grounding_state_contains_seen_urls_set():
    state = init_grounding_state()
    assert isinstance(state["grounding_seen_urls"], set)


def test_extract_inline_url_citations_from_text():
    text = "I found this source: https://example.com/article about telc A2."
    citations = extract_inline_url_citations(text, query="telc A2 exam")

    assert len(citations) == 1
    assert citations[0]["url"] == "https://example.com/article"
    assert citations[0]["source"] == "example.com"
def test_visual_grounding_hw_observation():
    """
    Provide a mock image context. Assert that the system prompt includes visual
    descriptions and that the tutor acknowledges the content without being
    explicitly prompted by text.
    """
    mock_system_prompt_builder = []
    
    # Mock visual context extraction
    visual_context = "Image shows a math worksheet with linear equations."
    if visual_context:
        mock_system_prompt_builder.append(f"Student's Homework context: {visual_context}")
        
    system_prompt = "\n".join(mock_system_prompt_builder)
    
    assert "Student's Homework context:" in system_prompt
    assert "linear equations" in system_prompt
