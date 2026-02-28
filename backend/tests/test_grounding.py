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
    assert citations[0]["query"] == "telc A2 exam"
