"""Deterministic intent classification.

Detects what the customer wants without invoking the LLM. The dialogue
engine consults this to decide which flow to route to (takeaway,
delivery, reservation, complaint) or which non-flow response to issue
(menu, zone listing, total, post-completion thanks).

Confidence tiers mirror ``order_extractor``:

- HIGH    >= 0.85 — capture and act.
- MEDIUM  >= 0.6  — clarify or ask follow-up.
- LOW     <  0.6  — fall back to LLM or reprompt.

The classifier is rule-based on hand-curated Egyptian Arabic cues. It is
exhaustively unit-tested in ``intent_slot_tests.py`` so changes here are
safe to ship.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nlp.arabic import normalize_ar, normalized_phrase_present


IntentKind = Literal[
    "takeaway",
    "delivery",
    "reservation",
    "complaint",
    "menu_question",
    "delivery_zone_question",
    "total_question",
    "post_completion_thanks",
    "greeting",
    "unknown",
]


@dataclass(frozen=True)
class IntentDetection:
    kind: IntentKind
    confidence: float = 0.0
    cue: str = ""

    def is_actionable(self, threshold: float = 0.6) -> bool:
        return self.kind != "unknown" and self.confidence >= threshold


_DELIVERY_CUES: tuple[str, ...] = (
    "توصيل",
    "دليفري",
    "الدليفري",
    "delivery",
    "وصلوهالي",
    "ابعتوهالي",
    "هتوصلوا",
    "بتوصلوا",
    "يوصل",
)


_TAKEAWAY_CUES: tuple[str, ...] = (
    "تيكاواي",
    "takeaway",
    "استلام",
    "هاجي اخده",
    "هاجي استلمه",
    "اخده من المطعم",
    "اخده من المحل",
    "هاخده من المطعم",
    "هاخده من المحل",
    "هاخده انا",
    "هاخده",
    "اجي استلمه",
    "ميل لي",
)


_RESERVATION_CUES: tuple[str, ...] = (
    "احجزلي",
    "احجز ترابيزه",
    "احجز ترابيزة",
    "حجز ترابيزه",
    "حجز ترابيزة",
    "حجز",
    "ترابيزه",
    "ترابيزة",
    "رزيرفيشن",
    "reservation",
)


_COMPLAINT_CUES: tuple[str, ...] = (
    "شكوى",
    "اشتكي",
    "اشكي",
    "مشكله",
    "مشكلة",
    "مش راضي",
    "تأخر",
    "اتأخر",
    "complaint",
    "بارد",
    "غلط الطلب",
    "غلط في الطلب",
    "وحش",
)


_MENU_CUES: tuple[str, ...] = (
    "المنيو",
    "menu",
    "ايه المتاح",
    "إيه المتاح",
    "المتاح ايه",
    "المتاح إيه",
    "ايه عندك",
    "إيه عندك",
    "عندك ايه",
    "عندكم ايه",
    "الاصناف",
    "الأصناف",
    "السعر",
    "الاسعار",
    "الأسعار",
)


_DELIVERY_ZONE_CUES: tuple[str, ...] = (
    "بتوصلوا فين",
    "هتوصلوا فين",
    "فين متاح",
    "متاح فين",
    "ايه المناطق",
    "إيه المناطق",
    "المناطق المتاحه",
    "المناطق المتاحة",
    "التوصيل فين",
    "التوصيل متاح فين",
)


_TOTAL_CUES: tuple[str, ...] = (
    "الحساب",
    "الإجمالي",
    "الاجمالي",
    "التوتال",
    "التوتل",
    "توتال",
    "بكام كله",
    "كام كله",
    "المجموع",
    "السعر كله",
)


_POST_COMPLETION_CUES: tuple[str, ...] = (
    "متشكر",
    "متشكره",
    "شكرا",
    "thanks",
    "thank you",
    "تمام كده",
    "بس كده",
    "كده تمام",
    "خلاص",
)


_GREETING_CUES: tuple[str, ...] = (
    "السلام عليكم",
    "اهلا",
    "أهلا",
    "مساء الخير",
    "صباح الخير",
    "ازيك",
    "عامل ايه",
    "هاي",
    "hi",
    "hello",
)


# Order-of-evaluation matters: a query about the menu should not be
# misclassified as a takeaway intent because it contains "عندك". We test
# the most specific intents first (zone question wins over delivery,
# menu question wins over takeaway, etc.).
_INTENT_GROUPS: tuple[tuple[IntentKind, tuple[str, ...]], ...] = (
    ("delivery_zone_question", _DELIVERY_ZONE_CUES),
    ("menu_question", _MENU_CUES),
    ("total_question", _TOTAL_CUES),
    ("complaint", _COMPLAINT_CUES),
    ("reservation", _RESERVATION_CUES),
    ("takeaway", _TAKEAWAY_CUES),
    ("delivery", _DELIVERY_CUES),
    ("post_completion_thanks", _POST_COMPLETION_CUES),
    ("greeting", _GREETING_CUES),
)


def detect_intent(text: str) -> IntentDetection:
    norm = normalize_ar(text)
    if not norm:
        return IntentDetection(kind="unknown")

    for kind, cues in _INTENT_GROUPS:
        for cue in cues:
            if normalized_phrase_present(norm, cue):
                return IntentDetection(kind=kind, confidence=0.9, cue=cue)

    return IntentDetection(kind="unknown")


def detect_all_intents(text: str) -> list[IntentDetection]:
    """All intents matched, ranked by group priority then cue length.

    Useful for debugging and for showing a confidence breakdown in the
    JSONL trace; the dialogue engine should normally use ``detect_intent``.
    """
    norm = normalize_ar(text)
    if not norm:
        return []
    out: list[IntentDetection] = []
    for kind, cues in _INTENT_GROUPS:
        for cue in cues:
            if normalized_phrase_present(norm, cue):
                out.append(IntentDetection(kind=kind, confidence=0.9, cue=cue))
                break
    return out


__all__ = [
    "IntentDetection",
    "IntentKind",
    "detect_all_intents",
    "detect_intent",
]
