"""
Conversation-flow helpers for natural dialogue behavior.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_SPACES_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")


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
