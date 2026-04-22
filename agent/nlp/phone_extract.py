from __future__ import annotations

import re

from nlp.arabic import AR_DIGITS, spoken_words_to_digits


PHONE_PREFIXES = ("010", "011", "012", "015")


def validate_phone(phone: str) -> str | None:
    cleaned = (phone or "").translate(AR_DIGITS)
    cleaned = re.sub(r"[^\d+]", "", cleaned)
    if re.fullmatch(r"201[0125]\d{8}", cleaned):
        cleaned = "+" + cleaned
    return cleaned if re.fullmatch(r"(01[0125]\d{8}|\+201[0125]\d{8})", cleaned) else None


def phone_digits_only(phone: str) -> str:
    spoken = spoken_words_to_digits(phone, phone_mode=True)
    if spoken:
        return spoken
    return re.sub(r"\D", "", (phone or "").translate(AR_DIGITS))


def is_phone_like_text(text: str) -> bool:
    translated = (text or "").translate(AR_DIGITS)
    digits = re.sub(r"\D", "", translated)
    spoken_digits = spoken_words_to_digits(text or "", phone_mode=True)
    all_digits = spoken_digits or digits
    if not all_digits:
        return False
    # Spoken number words need a higher threshold than raw digit chunks; otherwise
    # ordinary order phrases like "2 كفتة و 3 كباب" can be misrouted as phone input.
    if spoken_digits and len(spoken_digits) >= 5:
        return True
    non_phone = re.sub(r"[\d\s()+\-]", "", translated).strip()
    return len(non_phone) <= 4


def merge_phone_digits(buffered: str, incoming: str) -> str:
    if not buffered:
        return incoming
    if not incoming:
        return buffered
    if len(incoming) >= 5 and (
        incoming.startswith("01") or incoming.startswith("20") or incoming.startswith("201")
    ):
        return incoming
    return buffered + incoming


def local_phone_digits(digits: str) -> str:
    normalized = re.sub(r"\D", "", (digits or "").translate(AR_DIGITS))
    if normalized.startswith("20") and len(normalized) >= 12:
        normalized = "0" + normalized[2:]
    return normalized


def is_plausible_partial_phone_digits(digits: str) -> bool:
    if not digits:
        return False
    local_digits = local_phone_digits(digits)
    if not local_digits:
        return False
    if len(local_digits) >= 11:
        return bool(validate_phone(local_digits))
    if any(prefix.startswith(local_digits) for prefix in PHONE_PREFIXES):
        return True
    if len(local_digits) >= 3 and any(local_digits.startswith(prefix) for prefix in PHONE_PREFIXES):
        return True
    # Accept any 3+ digit chunk even without a 01X prefix — it may be a middle
    # segment of a phone number spoken in pieces (e.g. STT catches "788 950"
    # while the leading "012" went to a previous turn). Final validate_phone()
    # still gates commitment once the full number is assembled.
    if len(local_digits) >= 3:
        return True
    return False
