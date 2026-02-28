"""Unit tests for modules/search_intent.py."""

from modules.search_intent import (
    build_force_search_control_prompt,
    detect_explicit_search_request,
    extract_search_query,
    is_likely_educational_search,
)


def test_detect_explicit_search_request_english():
    runtime_state = {
        "search_intent_policy": {
            "request_patterns": [r"\b(search|google|look\s*up|lookup)\b"],
        }
    }
    assert detect_explicit_search_request("Can you search for telc A2 exam fee?", runtime_state) is True


def test_detect_explicit_search_request_german():
    runtime_state = {
        "search_intent_policy": {
            "request_patterns": [r"\b(suche|such\s+nach|suchen|recherchier|google)\b"],
        }
    }
    assert detect_explicit_search_request("Kannst du nach quadratische Formel suchen?", runtime_state) is True


def test_extract_search_query_sanitizes_prefix():
    query = extract_search_query("Please can you search for quadratic formula")
    assert query == "Please can you search for quadratic formula"


def test_is_likely_educational_search_true_for_exam_topic():
    runtime_state = {"topic_title": "German A2", "track_title": "Languages"}
    assert is_likely_educational_search("Search for German A2 exam dates", runtime_state) is True


def test_is_likely_educational_search_false_for_shopping():
    runtime_state = {"topic_title": "Math", "track_title": "STEM"}
    assert is_likely_educational_search("Search iPhone price on Amazon", runtime_state) is False


def test_build_force_search_control_prompt_mentions_google_search_and_url():
    prompt = build_force_search_control_prompt("search for telc C1 exam price")
    assert "google_search" in prompt
    assert "URL" in prompt
