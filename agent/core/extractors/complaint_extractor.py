"""Deterministic complaint type classifier.

Categorizes a complaint turn into one of:

- ``order``     — wrong items, missing items, wrong quantity.
- ``quality``   — taste, freshness, temperature.
- ``service``   — staff behaviour, rudeness.
- ``delivery``  — late delivery, lost order, driver issues.
- ``other``     — generic dissatisfaction.

The categorization feeds into the complaint flow so the engine can
ask the right follow-up question without an LLM round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nlp.arabic import contains_normalized_phrase, normalize_ar


ComplaintCategory = Literal["order", "quality", "service", "delivery", "other"]


HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6


@dataclass(frozen=True)
class ComplaintCapture:
    text: str | None
    category: ComplaintCategory | None
    confidence: float = 0.0
    reason: str = ""


_ORDER_CUES: tuple[str, ...] = (
    "غلط في الطلب",
    "غلط الطلب",
    "الطلب غلط",
    "وصلني غلط",
    "ناقص",
    "ناقصني",
    "مكنش بالطلب",
    "غير اللي طلبته",
    "غير اللي طلبتها",
    "اتبعتلي حاجه غلط",
    "اتبعتلي حاجة غلط",
)


_QUALITY_CUES: tuple[str, ...] = (
    "بارد",
    "بارده",
    "باردة",
    "وحش",
    "وحشه",
    "وحشة",
    "مش طازه",
    "بايت",
    "ريحته",
    "طعمه",
    "نيء",
    "محروق",
    "ناشف",
    "مش لذيذ",
)


_SERVICE_CUES: tuple[str, ...] = (
    "الكاشير",
    "الويتر",
    "الموظف",
    "الموظفه",
    "اتعصب",
    "بيرد بأسلوب",
    "اسلوب الموظف",
    "أسلوب الموظف",
    "مش محترم",
)


_DELIVERY_CUES: tuple[str, ...] = (
    "اتأخر",
    "تأخر",
    "تأخرتوا",
    "اتأخرت",
    "السواق",
    "الدليفري",
    "ضاع الطلب",
    "محدش وصل",
    "متاخر",
    "متأخر",
)


_GROUPS: tuple[tuple[ComplaintCategory, tuple[str, ...]], ...] = (
    # Service cues like "مش محترم" are more specific than quality cues
    # like "وحش", so service must win when both fire.
    ("delivery", _DELIVERY_CUES),
    ("order", _ORDER_CUES),
    ("service", _SERVICE_CUES),
    ("quality", _QUALITY_CUES),
)


def classify_complaint(text: str) -> ComplaintCapture:
    raw = (text or "").strip()
    if not raw:
        return ComplaintCapture(text=None, category=None, confidence=0.0, reason="empty")

    norm = normalize_ar(raw)
    if not norm:
        return ComplaintCapture(text=None, category=None, confidence=0.0, reason="empty_after_normalize")

    for category, cues in _GROUPS:
        for cue in cues:
            if contains_normalized_phrase(raw, {cue}):
                return ComplaintCapture(
                    text=raw,
                    category=category,
                    confidence=0.9,
                    reason=f"cue:{cue}",
                )

    generic_markers = {
        normalize_ar(word)
        for word in ("شكوى", "اشتكي", "مشكله", "مشكلة")
    }
    if any(marker in norm for marker in generic_markers):
        return ComplaintCapture(
            text=raw,
            category="other",
            confidence=0.65,
            reason="generic_complaint_marker",
        )

    return ComplaintCapture(text=None, category=None, confidence=0.0, reason="no_signal")


__all__ = [
    "ComplaintCapture",
    "ComplaintCategory",
    "HIGH_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "classify_complaint",
]
