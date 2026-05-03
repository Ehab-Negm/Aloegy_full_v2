"""Deterministic reservation extractor: time + guest count.

The extractor handles the formats Egyptian customers typically dictate:

- "بكره الساعة 8 مساء"
- "النهاردة 9 بليل"
- "يوم الجمعة الساعة 7"
- "بعد كده بساعة"
- "اربع ضيوف", "5 شخص", "هنبقى 6"

The result includes a confidence score so the dialogue engine knows
whether to confirm, capture silently, or ask again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nlp.arabic import SPOKEN_DIGIT_MAP, normalize_ar


HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6


_DAY_TOKENS: tuple[str, ...] = (
    "النهارده",
    "النهاردة",
    "اليوم",
    "بكره",
    "بكرة",
    "بعد بكره",
    "بعد بكرة",
    "السبت",
    "الاحد",
    "الأحد",
    "الاتنين",
    "الإتنين",
    "التلات",
    "الثلاث",
    "الاربع",
    "الأربع",
    "الخميس",
    "الجمعه",
    "الجمعة",
)

_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?"  # 8, 8:30, 8.30
    r"\s*(?:صباحا|الصبح|صبح|مساءا|مساء|بليل|الليل|بالظهر|ضهر|عصرا|بعد الظهر)?",
    re.IGNORECASE,
)

_GUESTS_PATTERN = re.compile(
    r"(?<!\d)(?P<num>\d{1,2})(?!\d)\s*(?:ضيف|ضيوف|شخص|اشخاص|أشخاص|نفر|راس)",
)


@dataclass(frozen=True)
class ReservationTimeCapture:
    raw: str | None
    confidence: float = 0.0
    reason: str = ""

    def is_high_confidence(self) -> bool:
        return self.raw is not None and self.confidence >= HIGH_CONFIDENCE


@dataclass(frozen=True)
class GuestsCapture:
    count: int | None
    confidence: float = 0.0
    reason: str = ""

    def is_high_confidence(self) -> bool:
        return self.count is not None and self.confidence >= HIGH_CONFIDENCE


def _spoken_digit_to_int(token: str) -> int | None:
    mapped = SPOKEN_DIGIT_MAP.get(normalize_ar(token))
    if mapped and mapped.isdigit():
        return int(mapped)
    return None


def extract_reservation_time(text: str) -> ReservationTimeCapture:
    raw = (text or "").strip()
    if not raw:
        return ReservationTimeCapture(raw=None, confidence=0.0, reason="empty")

    norm = normalize_ar(raw)
    if not norm:
        return ReservationTimeCapture(raw=None, confidence=0.0, reason="empty_after_normalize")

    has_day = any(normalize_ar(day) in norm for day in _DAY_TOKENS)
    time_match = _TIME_PATTERN.search(raw)
    has_explicit_time = bool(time_match and time_match.group("hour"))

    if has_day and has_explicit_time:
        return ReservationTimeCapture(raw=raw, confidence=0.95, reason="day+time")
    if has_explicit_time:
        return ReservationTimeCapture(raw=raw, confidence=0.85, reason="time_only")
    if has_day:
        return ReservationTimeCapture(raw=raw, confidence=0.7, reason="day_only")

    return ReservationTimeCapture(raw=None, confidence=0.0, reason="no_signal")


def extract_guests_count(text: str) -> GuestsCapture:
    raw = (text or "").strip()
    if not raw:
        return GuestsCapture(count=None, confidence=0.0, reason="empty")

    match = _GUESTS_PATTERN.search(normalize_ar(raw))
    if match:
        try:
            count = int(match.group("num"))
        except ValueError:
            count = None
        if count and 1 <= count <= 30:
            return GuestsCapture(count=count, confidence=0.95, reason="explicit_unit")

    norm = normalize_ar(raw)
    tokens = norm.split()
    for token in tokens:
        spoken = _spoken_digit_to_int(token)
        if spoken is not None and any(unit in tokens for unit in ("ضيف", "ضيوف", "شخص", "اشخاص", "نفر")):
            if 1 <= spoken <= 30:
                return GuestsCapture(count=spoken, confidence=0.9, reason="spoken+unit")

    digit_match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", raw)
    if digit_match:
        try:
            value = int(digit_match.group(0))
        except ValueError:
            value = 0
        if 1 <= value <= 30:
            return GuestsCapture(count=value, confidence=0.55, reason="bare_digit")

    return GuestsCapture(count=None, confidence=0.0, reason="no_signal")


__all__ = [
    "GuestsCapture",
    "HIGH_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "ReservationTimeCapture",
    "extract_guests_count",
    "extract_reservation_time",
]
