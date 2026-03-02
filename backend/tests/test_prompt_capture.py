"""Unit tests for prompt capture helper."""

from __future__ import annotations

import json

import modules.prompt_capture as prompt_capture
from modules.prompt_capture import (
    capture_prompt_text,
    send_content_with_prompt_capture,
)


class _FakePart:
    def __init__(self, text: str | None = None):
        self.text = text


class _FakeContent:
    def __init__(self, role: str, parts: list[_FakePart]):
        self.role = role
        self.parts = parts


class _FakeQueue:
    def __init__(self):
        self.sent: list[_FakeContent] = []

    def send_content(self, content: _FakeContent) -> None:
        self.sent.append(content)


def test_send_content_with_prompt_capture_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_capture, "_DEFAULT_OUTPUT_DIR", tmp_path)
    queue = _FakeQueue()
    state: dict = {}
    content = _FakeContent(role="user", parts=[_FakePart("test prompt text")])

    send_content_with_prompt_capture(
        queue,
        content,
        session_id="session-abc",
        source="unit_test",
        runtime_state=state,
    )

    files = list(tmp_path.glob("session_prompts_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-abc"
    assert payload["prompt_count"] == 1
    assert isinstance(payload["prompts"], list)
    assert len(payload["prompts"]) == 1
    assert payload["prompts"][0]["source"] == "unit_test"
    assert payload["prompts"][0]["prompt_text"] == "test prompt text"
    assert payload["prompts"][0]["text_parts"] == ["test prompt text"]
    assert payload["prompts"][0]["prompt_index"] == 1
    assert state["prompt_capture_index"] == 1
    assert state["prompt_capture_last_file"].endswith("session_prompts_session-abc.json")
    assert len(queue.sent) == 1


def test_send_content_with_prompt_capture_skips_non_text_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_capture, "_DEFAULT_OUTPUT_DIR", tmp_path)
    queue = _FakeQueue()
    content = _FakeContent(role="user", parts=[_FakePart(None), _FakePart("")])

    send_content_with_prompt_capture(
        queue,
        content,
        session_id="session-abc",
        source="unit_test_non_text",
        runtime_state={},
    )

    files = list(tmp_path.glob("session_prompts_*.json"))
    assert len(files) == 0
    assert len(queue.sent) == 1


def test_capture_prompt_text_writes_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_capture, "_DEFAULT_OUTPUT_DIR", tmp_path)
    state: dict = {}

    out = capture_prompt_text(
        "spoken transcript text",
        session_id="session-xyz",
        source="student_audio_transcript",
        role="user",
        runtime_state=state,
        extra={"derived_from": "audio_input_transcription"},
    )

    assert out is not None
    files = list(tmp_path.glob("session_prompts_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-xyz"
    assert payload["prompt_count"] == 1
    assert payload["prompts"][0]["source"] == "student_audio_transcript"
    assert payload["prompts"][0]["prompt_text"] == "spoken transcript text"
    assert payload["prompts"][0]["meta"]["derived_from"] == "audio_input_transcription"


def test_capture_prompt_text_appends_to_single_file_per_session(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_capture, "_DEFAULT_OUTPUT_DIR", tmp_path)
    state: dict = {}

    p1 = capture_prompt_text(
        "first prompt",
        session_id="same-session",
        source="source_a",
        role="user",
        runtime_state=state,
    )
    p2 = capture_prompt_text(
        "second prompt",
        session_id="same-session",
        source="source_b",
        role="user",
        runtime_state=state,
    )

    assert p1 == p2
    files = list(tmp_path.glob("session_prompts_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["prompt_count"] == 2
    assert payload["prompts"][0]["prompt_index"] == 1
    assert payload["prompts"][1]["prompt_index"] == 2
    assert payload["prompts"][1]["prompt_text"] == "second prompt"
    assert state["prompt_capture_index"] == 2
