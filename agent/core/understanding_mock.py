"""Deterministic mock provider for ``core.understanding`` tests.

Tests must never reach the real LLM — both for cost and reproducibility.
This module provides:

- ``ScriptedProvider``: returns a queued JSON for each call, ideal for
  scenario tests where you want to assert the engine handles a specific
  ``TurnUnderstanding``.
- ``programmatic_provider``: a tiny rule-based provider that is good
  enough for the existing acceptance suites (which were written against
  a deterministic engine). It lets the suites run without rewriting
  every assertion.

The programmatic mock is **not** a smart NLU — it's a small set of
patterns that map common turns to plausible JSON. The point is to keep
the existing test surface green while we migrate the production path
to a real LLM provider.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass, field
from typing import Iterable

from core.understanding import Provider, TurnContext


# ---------------------------------------------------------------------------
# 1) Scripted provider — pure replay
# ---------------------------------------------------------------------------


@dataclass
class ScriptedProvider:
    """Return queued JSON strings, one per call.

    Raises ``RuntimeError`` if the script runs out so a missing test
    setup is loud rather than silent.
    """

    script: list[str] = field(default_factory=list)
    calls: list[TurnContext] = field(default_factory=list)

    def __call__(self, ctx: TurnContext) -> str:
        self.calls.append(ctx)
        if not self.script:
            raise RuntimeError("ScriptedProvider exhausted")
        return self.script.pop(0)

    def queue(self, *jsons: str) -> "ScriptedProvider":
        self.script.extend(jsons)
        return self


def script(*understandings: dict) -> ScriptedProvider:
    """Convenience: build a ``ScriptedProvider`` from dicts."""
    return ScriptedProvider(script=[_json.dumps(u, ensure_ascii=False) for u in understandings])


# ---------------------------------------------------------------------------
# 2) Programmatic provider — keeps the existing tests green
# ---------------------------------------------------------------------------


# Egyptian Arabic spoken-digit map borrowed from nlp.arabic. Imported
# lazily so this module stays import-cheap.
def _spoken_digit_map() -> dict[str, str]:
    from nlp.arabic import SPOKEN_DIGIT_MAP
    return SPOKEN_DIGIT_MAP


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def programmatic_provider(ctx: TurnContext) -> str:
    """Tiny rule engine that approximates an LLM for unit tests.

    It only needs to produce JSON that matches the schema; the engine
    consumes it. We don't try to be exhaustive — this is a stand-in,
    not a product.
    """
    text = (ctx.user_text or "").translate(_AR_DIGITS)
    norm = _normalize(text)
    payload: dict = {
        "intent": "unknown",
        "intent_confidence": "low",
        "order_items": [],
        "mutation": "none",
        "customer_name": None,
        "customer_phone_digits": None,
        "delivery_address": None,
        "delivery_zone": None,
        "reservation_time": None,
        "guests_count": None,
        "complaint_text": None,
        "complaint_category": None,
        "is_confirming": False,
        "is_denying": False,
    }

    # Intent guess — kept very small. The real provider does this much
    # better; this just keeps existing scripted scenarios working.
    if any(cue in norm for cue in ("توصيل", "دليفري", "وصلوهالي", "يوصل")):
        payload["intent"] = "delivery"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("تيكاواي", "استلام", "هاجي اخده", "اخده من المطعم", "اخده من المحل")):
        payload["intent"] = "takeaway"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("حجز", "احجز", "ترابيزه")):
        payload["intent"] = "reservation"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("شكوى", "اشتكي", "مشكله", "مشكلة", "اتاخر", "اتأخر", "بارد")):
        payload["intent"] = "complaint"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("المنيو", "المتاح", "ايه عندك", "عندك ايه", "عندكم ايه", "الاصناف")):
        payload["intent"] = "menu_question"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("بتوصلوا فين", "متاح فين", "المناطق المتاحه", "المناطق المتاحة")):
        payload["intent"] = "delivery_zone_question"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("بكام كله", "الحساب", "الاجمالي", "التوتال")):
        payload["intent"] = "total_question"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("اهلا", "السلام عليكم", "صباح", "مساء", "ازيك")):
        payload["intent"] = "greeting"
        payload["intent_confidence"] = "high"
    elif any(cue in norm for cue in ("متشكر", "تمام كده", "بس كده", "خلاص", "thanks")):
        payload["intent"] = "post_completion_thanks"
        payload["intent_confidence"] = "high"

    # Mutation guess.
    if any(cue in norm for cue in ("غير الطلب", "بدل الطلب", "امسح الطلب", "غير ", "بدل ")):
        payload["mutation"] = "replace"
    elif any(cue in norm for cue in ("ضيف", "كمان", "هاتلي كمان", "وزود", "معاه")):
        payload["mutation"] = "add"
    elif any(cue in norm for cue in ("شيل", "بلاش", "احذف", "متجبش", "امسح ال")):
        payload["mutation"] = "remove"
    elif "زود" in norm:
        payload["mutation"] = "increase"
    elif any(cue in norm for cue in ("نقص", "خفف", "قلل")):
        payload["mutation"] = "decrease"
    elif any(cue in norm for cue in ("خليه كده", "كده تمام", "زي ما هو")):
        payload["mutation"] = "keep"

    # Yes / no on confirmation.
    if any(cue in norm for cue in ("اكد", "أكد", "ايوه اكد", "تمام اكدها", "اوكي اكدها")):
        payload["is_confirming"] = True
    if norm in {"لا", "لأ"} or any(
        cue in norm for cue in ("لا غير", "لا مش", "لا متاكد", "لا مفيش")
    ):
        payload["is_denying"] = True

    # Phone.
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 11:
        payload["customer_phone_digits"] = digits[:14]
    elif _looks_phone_like(text):
        payload["customer_phone_digits"] = digits or None

    # Order items + quantities — match against menu canonical names.
    order_items = _extract_order_items(text, norm, ctx)
    if order_items:
        payload["order_items"] = order_items

    # Address.
    if _looks_like_address(norm, ctx):
        payload["delivery_address"] = text.strip()
        payload["delivery_zone"] = _detect_zone(norm, ctx.delivery_zones) or None

    # Reservation time + guests.
    if any(cue in norm for cue in ("بكره", "النهارده", "الجمعه", "الجمعة", "الخميس", "السبت", "الاحد", "الاتنين", "التلات", "الاربع")) or re.search(r"\b(?:[01]?\d|2[0-3])(?::\d{2})?\b", text):
        if re.search(r"\d", text) or any(t in norm for t in ("بكره", "النهارده", "الجمعه", "الجمعة")):
            payload["reservation_time"] = text.strip()

    guests_match = re.search(
        r"(?<!\d)(\d{1,2})(?!\d)\s*(?:ضيف|ضيوف|شخص|اشخاص|أشخاص|نفر)",
        norm,
    )
    if guests_match:
        try:
            value = int(guests_match.group(1))
            if 1 <= value <= 30:
                payload["guests_count"] = value
        except ValueError:
            pass
    else:
        digit_map = _spoken_digit_map()
        tokens = norm.split()
        for token in tokens:
            mapped = digit_map.get(token)
            if mapped and mapped.isdigit() and any(t in tokens for t in ("ضيف", "ضيوف", "شخص", "اشخاص", "نفر")):
                value = int(mapped)
                if 1 <= value <= 30:
                    payload["guests_count"] = value
                    break

    # Complaint category.
    if payload["intent"] == "complaint":
        payload["complaint_text"] = text.strip()
        if any(cue in norm for cue in ("بارد", "وحش", "محروق", "ناشف")):
            payload["complaint_category"] = "quality"
        elif any(cue in norm for cue in ("اتاخر", "اتأخر", "السواق", "الدليفري", "متاخر", "متأخر")):
            payload["complaint_category"] = "delivery"
        elif any(cue in norm for cue in ("الموظف", "الكاشير", "محترم", "اسلوب")):
            payload["complaint_category"] = "service"
        elif any(cue in norm for cue in ("ناقص", "غلط الطلب", "وصلني غلط")):
            payload["complaint_category"] = "order"
        else:
            payload["complaint_category"] = "other"

    # Name detection — narrow: only after explicit markers.
    name_match = re.search(r"(?:انا|أنا|اسمي|اسمى|الاسم)\s+([؀-ۿ]+(?:\s+[؀-ۿ]+){0,1})", text)
    if name_match:
        candidate = name_match.group(1).strip()
        if candidate and len(candidate.split()) <= 2:
            payload["customer_name"] = candidate

    return _json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers (intentionally tiny — replicate just enough for the mock)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    from nlp.arabic import normalize_ar
    return normalize_ar(text)


def _looks_phone_like(text: str) -> bool:
    from nlp.phone_extract import is_phone_like_text
    return is_phone_like_text(text)


def _looks_like_address(norm: str, ctx: TurnContext) -> bool:
    landmark_words = {
        "شارع", "ميدان", "كوبري", "عماره", "عمارة", "بنايه", "بناية",
        "برج", "شقه", "شقة", "دور", "بلوك", "حي", "منطقه", "منطقة",
        "كومباوند",
    }
    if any(f" {w} " in f" {norm} " for w in landmark_words):
        return True
    if ctx.delivery_zones and any(_normalize(zone) in norm for zone in ctx.delivery_zones):
        return True
    return False


def _detect_zone(norm: str, zones: Iterable[str]) -> str | None:
    for zone in zones or ():
        zone_norm = _normalize(zone)
        if zone_norm and zone_norm in norm:
            return zone
    return None


def _extract_order_items(text: str, norm: str, ctx: TurnContext) -> list[dict]:
    """Best-effort menu match — leverages the existing extractor.

    The mock delegates to ``core.extractors.order_extractor`` so the
    behaviour matches what we already test extensively.
    """
    from core.extractors.order_extractor import extract_order
    from core.menu_index import MenuIndex

    index = MenuIndex.build(ctx.menu_items)
    extraction = extract_order(text, index)
    return [
        {
            "item_name": item.canonical_name,
            "quantity": item.quantity,
            "evidence": item.source_phrase or "",
        }
        for item in extraction.items
    ]


__all__ = [
    "ScriptedProvider",
    "programmatic_provider",
    "script",
]
