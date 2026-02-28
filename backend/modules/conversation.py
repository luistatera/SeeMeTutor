"""
Conversation-flow helpers for natural dialogue behavior.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_SPACES_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_WORD_RE = re.compile(r"[a-z0-9]+")

_QUESTION_STARTERS = {
    "what",
    "why",
    "how",
    "when",
    "where",
    "who",
    "which",
    "can",
    "could",
    "would",
    "should",
    "do",
    "does",
    "did",
    "is",
    "are",
    "am",
    "was",
    "were",
    "will",
    "explain",
    "tell",
    "help",
}

_TOPIC_STOPWORDS = {
    "current",
    "topic",
    "with",
    "from",
    "that",
    "this",
    "your",
    "study",
    "learning",
    "about",
    "and",
    "the",
    "for",
}

_EDUCATIONAL_HINT_WORDS = {
    "homework",
    "exercise",
    "problem",
    "equation",
    "formula",
    "grammar",
    "translate",
    "translation",
    "meaning",
    "definition",
    "solve",
    "calculate",
    "proof",
    "verb",
    "noun",
    "sentence",
    "math",
    "science",
    "history",
    "geography",
    "biology",
    "chemistry",
    "physics",
    "exam",
    "test",
    "lesson",
    "class",
}

_OFF_TOPIC_HINT_WORDS = {
    "weather",
    "movie",
    "movies",
    "music",
    "song",
    "songs",
    "tiktok",
    "instagram",
    "youtube",
    "football",
    "basketball",
    "shopping",
    "restaurant",
    "vacation",
    "travel",
    "celebrity",
}

_EXAMPLE_MARKERS = (
    "example:",
    "for example",
    "for instance",
    "e.g.",
    "eg.",
    "let's say",
    "lets say",
    "suppose",
    "imagine",
)


def _tokens(text: str) -> list[str]:
    candidate = normalize_for_similarity(text)
    if not candidate:
        return []
    return _WORD_RE.findall(candidate)


def _truncate_sentence(text: str, max_chars: int) -> str:
    candidate = _SPACES_RE.sub(" ", str(text or "")).strip()
    if not candidate:
        return ""
    if len(candidate) <= max_chars:
        return candidate
    trimmed = candidate[: max_chars - 3].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return f"{trimmed}..."


def normalize_for_similarity(text: str) -> str:
    candidate = str(text or "").strip().lower()
    if not candidate:
        return ""
    candidate = _NON_ALNUM_RE.sub(" ", candidate)
    candidate = _SPACES_RE.sub(" ", candidate).strip()
    return candidate


def is_near_duplicate(a: str, b: str, *, threshold: float = 0.86) -> bool:
    na = normalize_for_similarity(a)
    nb = normalize_for_similarity(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ratio = SequenceMatcher(None, na, nb).ratio()
    return ratio >= float(threshold)


def expects_student_reply(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    if "?" in candidate:
        return True
    # Language-agnostic fallback: short trailing pause invitation.
    if candidate.endswith("...") or candidate.endswith("…"):
        tokens = normalize_for_similarity(candidate).split()
        return len(tokens) <= 10
    return False


def is_question_like_turn(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False

    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+", candidate) if p.strip()]
    if not pieces:
        pieces = [candidate]

    question_count = sum(1 for piece in pieces if piece.endswith("?"))
    if question_count == 0:
        return False
    declarative_count = max(0, len(pieces) - question_count)
    if question_count > declarative_count:
        return True
    if question_count == declarative_count == 1:
        declarative = next((p for p in pieces if not p.endswith("?")), "")
        # Heuristic: "hint-like" setup sentence followed by a question.
        if ":" in declarative:
            return False
        return True
    return False


def is_student_question(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    if "?" in candidate:
        return True

    words = _tokens(candidate)
    if len(words) < 3:
        return False
    if words[0] in _QUESTION_STARTERS:
        return True
    first_two = " ".join(words[:2])
    return first_two in {"can you", "could you", "would you", "do i", "is it", "what is", "how do"}


def is_study_related_question(text: str, topic_title: str | None = None) -> bool:
    if not is_student_question(text):
        return False

    words = set(_tokens(text))
    if not words:
        return False

    has_off_topic_hint = bool(words & _OFF_TOPIC_HINT_WORDS)
    has_educational_hint = bool(words & _EDUCATIONAL_HINT_WORDS)
    if has_off_topic_hint and not has_educational_hint:
        return False

    topic_words = {
        token
        for token in _tokens(topic_title or "")
        if len(token) >= 4 and token not in _TOPIC_STOPWORDS
    }
    if topic_words and words.intersection(topic_words):
        return True

    if has_educational_hint:
        return True

    # Math-like fallback for concise symbolic questions ("is x=5?", "2+3?")
    if re.search(r"\d", str(text or "")) and re.search(r"[=+\-*/^]", str(text or "")):
        return True

    return False


def build_question_answer_note(question: str, answer: str, sequence: int) -> tuple[str, str]:
    q_line = _truncate_sentence(question, 140)
    a_line = _truncate_sentence(answer, 220)
    title_focus = _truncate_sentence(str(question or "").rstrip("?.! "), 44) or "Study question"
    note_index = max(1, int(sequence or 1))
    title = f"My note {note_index}: {title_focus}"
    content = f"Q: {q_line or '-'}\nA: {a_line or '-'}"
    return title, content


def extract_example_from_turn(text: str) -> str:
    candidate = _SPACES_RE.sub(" ", str(text or "")).strip()
    if not candidate or len(candidate) < 16:
        return ""

    lowered = candidate.lower()
    for marker in _EXAMPLE_MARKERS:
        idx = lowered.find(marker)
        if idx >= 0:
            excerpt = candidate[idx:]
            return _truncate_sentence(excerpt, 230)

    # Math-like worked example fallback: short symbolic line.
    if re.search(r"\d", candidate) and re.search(r"[=+\-*/^]", candidate):
        return _truncate_sentence(candidate, 230)

    return ""


def build_example_note(example_text: str, sequence: int) -> tuple[str, str]:
    note_index = max(1, int(sequence or 1))
    line = _truncate_sentence(example_text, 220)
    title = f"My note {note_index}: Example"
    content = f"Example: {line or '-'}"
    return title, content
