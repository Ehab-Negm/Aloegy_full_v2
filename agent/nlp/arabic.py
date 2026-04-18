from __future__ import annotations

import functools
import re
import unicodedata


AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

SPOKEN_DIGIT_MAP = {
    "صفر": "0",
    "زيرو": "0",
    "zero": "0",
    "واحد": "1",
    "واحده": "1",
    "اتنين": "2",
    "اثنين": "2",
    "اتنان": "2",
    "تلاته": "3",
    "تلاتة": "3",
    "ثلاثة": "3",
    "ثلاثه": "3",
    "اربعه": "4",
    "أربعة": "4",
    "اربعة": "4",
    "أربعه": "4",
    "خمسه": "5",
    "خمسة": "5",
    "سته": "6",
    "ستة": "6",
    "سبعه": "7",
    "سبعة": "7",
    "تمنيه": "8",
    "تمانيه": "8",
    "تمانية": "8",
    "ثمانية": "8",
    "ثمانيه": "8",
    "تسعه": "9",
    "تسعة": "9",
    "عشره": "10",
    "عشرة": "10",
    "عشر": "10",
    "حداشر": "11",
    "إحداشر": "11",
    "اتناشر": "12",
    "إتناشر": "12",
    "تلتاشر": "13",
    "ثلاثتاشر": "13",
    "أربعتاشر": "14",
    "اربعتاشر": "14",
    "خمستاشر": "15",
}


@functools.lru_cache(maxsize=2048)
def normalize_ar(text: str) -> str:
    text = (text or "").translate(AR_DIGITS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"[ى]", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def contains_normalized_phrase(text: str, phrases: set[str]) -> bool:
    normalized = normalize_ar(text)
    if not normalized:
        return False
    wrapped = f" {normalized} "
    return any(f" {normalize_ar(phrase)} " in wrapped for phrase in phrases)


def normalized_phrase_present(normalized_text: str, phrase: str) -> bool:
    if not normalized_text:
        return False
    return f" {normalize_ar(phrase)} " in f" {normalized_text} "


# Map for phone context: multi-digit numbers are expanded to individual digits
# e.g. "عشرة" → "10" normally, but in phone context → "1","0"
_SPOKEN_DIGIT_PHONE_MAP: dict[str, str] = {}
for _word, _digits in SPOKEN_DIGIT_MAP.items():
    if len(_digits) == 1:
        _SPOKEN_DIGIT_PHONE_MAP[_word] = _digits
    else:
        # Expand "10" → "1", "0" etc — each digit spoken separately in phone numbers
        _SPOKEN_DIGIT_PHONE_MAP[_word] = _digits  # kept as multi-char, expanded below


def spoken_words_to_digits(text: str, *, phone_mode: bool = False) -> str:
    """Convert Arabic spoken number words to digit strings.

    When phone_mode=True, multi-digit mappings (e.g. عشرة→10) are treated as
    individual digits rather than quantities, matching how Egyptians speak phone
    numbers digit-by-digit.
    """
    normalized = normalize_ar(text)
    tokens = normalized.split()
    digit_parts: list[str] = []
    for token in tokens:
        mapped = SPOKEN_DIGIT_MAP.get(token)
        if mapped is not None:
            if phone_mode:
                # Each digit is independent in phone numbers
                digit_parts.extend(mapped)
            else:
                digit_parts.append(mapped)
        elif re.fullmatch(r"\d+", token):
            if phone_mode:
                digit_parts.extend(token)
            else:
                digit_parts.append(token)
    if digit_parts:
        return "".join(digit_parts)
    return re.sub(r"\D", "", (text or "").translate(AR_DIGITS))
