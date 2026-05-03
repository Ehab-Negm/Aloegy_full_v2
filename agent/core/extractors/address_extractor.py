"""Deterministic address extractor with confidence scoring.

A turn becomes a candidate address when it includes typical landmark
words (شارع / عمارة / دور / شقة) or names a delivery zone configured
by the restaurant. The extractor returns a structured ``AddressCapture``
that the dialogue engine uses to decide between immediate capture,
confirmation, or LLM fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nlp.arabic import normalize_ar
from nlp.phone_extract import is_phone_like_text


HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6
LOW_CONFIDENCE = 0.35


_ADDRESS_DETAIL_TOKENS: tuple[str, ...] = (
    "شارع",
    "ش",
    "ميدان",
    "كوبري",
    "عماره",
    "عمارة",
    "بنايه",
    "بناية",
    "برج",
    "شقه",
    "شقة",
    "دور",
    "بلوك",
    "محله",
    "محلة",
    "حي",
    "منطقه",
    "منطقة",
    "كومباوند",
    "ابراج",
    "مدينه",
    "مدينة",
    "زهراء",
)


_NUMERIC_HINT = re.compile(r"\d")


@dataclass(frozen=True)
class AddressCapture:
    value: str | None
    zone: str | None = None
    confidence: float = 0.0
    reason: str = ""

    def is_high_confidence(self) -> bool:
        return self.value is not None and self.confidence >= HIGH_CONFIDENCE


def _detect_zone(text_norm: str, zones: tuple[str, ...] | None) -> str | None:
    if not zones:
        return None
    for zone in zones:
        zone_norm = normalize_ar(zone)
        if not zone_norm:
            continue
        if zone_norm in text_norm:
            return zone
        # Allow "ال" prefix to match a zone written without the article,
        # and vice versa.
        if len(zone_norm) > 3 and zone_norm.startswith("ال"):
            stripped = zone_norm[2:]
            if stripped in text_norm:
                return zone
        else:
            article_form = "ال" + zone_norm
            if article_form in text_norm:
                return zone
    return None


def _norm_with_optional_article(text: str) -> str:
    """Normalize and drop "ال" before each token so "الشارع" matches "شارع"."""
    norm = normalize_ar(text)
    if not norm:
        return ""
    out_tokens: list[str] = []
    for token in norm.split():
        if len(token) > 3 and token.startswith("ال"):
            out_tokens.append(token[2:])
        else:
            out_tokens.append(token)
    return " ".join(out_tokens)


def extract_address(
    text: str,
    *,
    delivery_zones: tuple[str, ...] | None = None,
) -> AddressCapture:
    """Detect a delivery address in the turn.

    Confidence:
    - 0.95: zone match + landmark word + a digit (street number / floor).
    - 0.85: zone match OR landmark word + digit.
    - 0.6 : at least one address detail word with no zone or number.
    - 0.0 : empty / no address signal.

    Note: a phone-only turn typically lacks landmark words and so
    naturally yields no_signal. We don't reject phone-like input up
    front because short address turns ("شارع 5") look phone-like under
    the heuristic in ``nlp.phone_extract``.
    """
    raw = (text or "").strip()
    if not raw:
        return AddressCapture(value=None, confidence=0.0, reason="empty")

    norm = _norm_with_optional_article(raw)
    if not norm:
        return AddressCapture(value=None, confidence=0.0, reason="empty_after_normalize")

    has_landmark = any(
        f" {normalize_ar(token)} " in f" {norm} "
        for token in _ADDRESS_DETAIL_TOKENS
    )
    has_digit = bool(_NUMERIC_HINT.search(raw))
    zone = _detect_zone(norm, delivery_zones)

    if not has_landmark and not zone:
        # No address signal — and we should rebuff phone-only turns now
        # so callers don't treat a phone number as an address. We use
        # the local phone-like check instead of returning early so we
        # only run it when nothing else matched.
        if is_phone_like_text(raw):
            return AddressCapture(value=None, confidence=0.0, reason="phone_like")

    if zone and has_landmark and has_digit:
        return AddressCapture(value=raw, zone=zone, confidence=0.95, reason="zone+landmark+digit")
    if zone and (has_landmark or has_digit):
        return AddressCapture(value=raw, zone=zone, confidence=0.9, reason="zone+detail")
    if has_landmark and has_digit:
        return AddressCapture(value=raw, zone=None, confidence=0.85, reason="landmark+digit")
    if zone:
        return AddressCapture(value=raw, zone=zone, confidence=0.75, reason="zone_only")
    if has_landmark:
        return AddressCapture(value=raw, zone=None, confidence=0.65, reason="landmark_only")

    return AddressCapture(value=None, confidence=0.0, reason="no_signal")


__all__ = [
    "AddressCapture",
    "HIGH_CONFIDENCE",
    "LOW_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "extract_address",
]
