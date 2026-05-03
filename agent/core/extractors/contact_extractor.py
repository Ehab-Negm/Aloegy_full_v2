"""Deterministic contact (name + phone) extractor with confidence scoring.

This wraps the existing ``nlp.name_extract`` / ``nlp.phone_extract``
helpers in a confidence-aware API that the dialogue engine can use:

- ``extract_name(text)`` -> ``ContactCapture(value, confidence, ...)``
- ``extract_phone(text)`` -> ``ContactCapture(...)``

Confidence tiers:

- HIGH    >= 0.85 — capture immediately.
- MEDIUM  >= 0.6  — confirm before saving ("اسم حضرتك أحمد، صح؟").
- LOW     <  0.6  — reprompt or fall back to LLM.

The extractor never mutates ``UserData``. Decisions about whether to
treat a capture as a confirmed slot live in ``submission_policy`` /
``DialogueEngine`` so confirmed slots remain immutable.
"""

from __future__ import annotations

from dataclasses import dataclass

from nlp.arabic import contains_normalized_phrase, normalize_ar
from nlp.name_extract import extract_name_candidate, is_likely_non_name_response
from nlp.phone_extract import (
    is_phone_like_text,
    phone_digits_only,
    validate_phone,
)


HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6
LOW_CONFIDENCE = 0.35


@dataclass(frozen=True)
class ContactCapture:
    value: str | None
    confidence: float = 0.0
    reason: str = ""

    def is_high_confidence(self) -> bool:
        return self.value is not None and self.confidence >= HIGH_CONFIDENCE

    def needs_clarification(self) -> bool:
        return (
            self.value is not None
            and MEDIUM_CONFIDENCE <= self.confidence < HIGH_CONFIDENCE
        )


_EMPTY_ANSWER_TOKENS = {"لا", "لأ", "مفيش", "مفيش حاجه", "مفيش حاجة"}


def _looks_empty_answer(text: str | None) -> bool:
    if not text:
        return True
    norm = normalize_ar(text)
    if not norm:
        return True
    return norm in {normalize_ar(t) for t in _EMPTY_ANSWER_TOKENS}


# Hint groups copied from agent.py so the extractor stays standalone.
_NAME_INTENT_HINT_GROUPS: tuple[set[str], ...] = (
    {"توصيل", "دليفري"},
    {"تيكاواي", "استلام"},
    {"اوردر", "طلب", "اطلب"},
    {"المنيو", "المتاح"},
    {"حجز", "ترابيزه", "ترابيزة"},
    {"شكوى", "مشكله", "مشكلة"},
)


def _contains_any_hint(normalized_text: str, hints: set[str]) -> bool:
    return any(normalize_ar(hint) in normalized_text for hint in hints)


def extract_name(text: str) -> ContactCapture:
    """Detect a probable customer name in the turn.

    Confidence rubric:
    - 0.95: explicit "اسمي ..." prefix.
    - 0.85: short single-or-two-token answer that passes blocklist checks.
    - 0.6 : short token but appears alongside an intent hint (medium —
            engine should confirm).
    - 0.0 : not a name at all (filler, denial, phone-like, intent words).
    """
    raw = (text or "").strip()
    if not raw:
        return ContactCapture(value=None, confidence=0.0, reason="empty")

    if is_likely_non_name_response(raw, looks_empty_answer=_looks_empty_answer):
        return ContactCapture(value=None, confidence=0.0, reason="non_name_response")

    candidate = extract_name_candidate(
        raw,
        looks_empty_answer=_looks_empty_answer,
        is_phone_like_text=is_phone_like_text,
        contains_any_hint=_contains_any_hint,
        intent_hint_groups=_NAME_INTENT_HINT_GROUPS,
    )
    if not candidate:
        return ContactCapture(value=None, confidence=0.0, reason="no_candidate")

    norm = normalize_ar(raw)
    explicit_marker = any(
        normalize_ar(prefix) in norm
        for prefix in ("اسمي", "انا اسمي", "أنا اسمي", "الاسم")
    )
    if explicit_marker:
        return ContactCapture(value=candidate, confidence=0.95, reason="explicit_marker")

    tokens = candidate.split()
    if len(tokens) <= 2:
        return ContactCapture(value=candidate, confidence=0.85, reason="short_clean")

    return ContactCapture(value=candidate, confidence=0.7, reason="long_candidate")


def extract_phone(text: str) -> ContactCapture:
    """Detect a phone number in the turn.

    Validates against Egyptian carrier prefixes (010 / 011 / 012 / 015).
    """
    if not text:
        return ContactCapture(value=None, confidence=0.0, reason="empty")

    digits = phone_digits_only(text)
    if not digits:
        return ContactCapture(value=None, confidence=0.0, reason="no_digits")

    candidate = validate_phone(digits)
    if candidate:
        return ContactCapture(value=candidate, confidence=0.95, reason="validated")

    if len(digits) >= 11:
        return ContactCapture(value=None, confidence=0.0, reason="invalid_full_length")

    if len(digits) >= 7:
        return ContactCapture(
            value=None,
            confidence=0.5,
            reason="partial_phone",
        )

    return ContactCapture(value=None, confidence=0.0, reason="too_short")


def is_explicit_denial(text: str) -> bool:
    """Detect "مفيش / لا / no" style denials so the engine doesn't loop."""
    norm = normalize_ar(text)
    if not norm:
        return False
    denials = {"لا", "لأ", "مفيش", "no", "نو"}
    return norm in {normalize_ar(d) for d in denials} or contains_normalized_phrase(
        text, {"مفيش حاجه", "مفيش حاجة", "بلاش"}
    )


__all__ = [
    "ContactCapture",
    "HIGH_CONFIDENCE",
    "LOW_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "extract_name",
    "extract_phone",
    "is_explicit_denial",
]
