from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from nlp.arabic import AR_DIGITS, normalize_ar, normalized_phrase_present


_FILLER_STARTS = {"آ", "آآ", "آه", "اه", "أه", "امم", "ام", "ممم", "هم", "اهم"}
_NORMALIZED_FILLER_STARTS = {normalize_ar(value) for value in _FILLER_STARTS}

_NON_NAME_PATTERNS = {
    "آه لا",
    "اه لا",
    "آه لأ",
    "اه لأ",
    "آه مفيش",
    "اه مفيش",
    "آآ لا",
    "آآ لأ",
    "آآ مفيش",
    "آآ تمام",
    "لا مفيش",
    "مفيش",
    "لا خلاص",
    "خلاص",
    "تمام",
    "تمام كده",
    "لا تمام",
    "بس كده",
    "كده تمام",
    "اوكي",
    "أوكي",
    "ماشي",
    # Greetings the customer says back to the agent — never a name.
    "اهلا",
    "أهلا",
    "اهلاً",
    "أهلاً",
    "اهلا بيك",
    "أهلا بيك",
    "اهلاً بيك",
    "أهلاً بيك",
    "اهلا بيكي",
    "أهلا بيكي",
    "اهلا وسهلا",
    "أهلا وسهلا",
    "اهلين",
    "أهلين",
    "السلام عليكم",
    "وعليكم السلام",
    "وعليكم سلام",
    "صباح الخير",
    "صباح النور",
    "مساء الخير",
    "مساء النور",
    "ازيك",
    "إزيك",
    "ازيكم",
    "إزيكم",
    "عامل ايه",
    "عامل إيه",
    "اخبارك",
    "أخبارك",
    "حمدلله",
    "الحمدلله",
    "الحمد لله",
}

_NON_NAME_HINTS = {
    "حضرتك",
    "معايا",
    "سامعني",
    "لسه معايا",
    "موجود",
    "فين",
    "ليه",
    "ازاي",
    "اسمك",
    "اسمي ايه",
    "اسمي انا ايه",
    "اسمي أنا إيه",
    "قلتلك قبل كده",
    "قولتلك قبل كده",
    "انا قلتلك",
    "أنا قلتلك",
    "ما قلتلكش",
    "مقلتلكش",
    "مش قولتلك",
    "ناسي",
    "انت ناسي",
    "الحاجات دي",
    "إيه",
    "ايه",
    "ابنك",
    "ابني",
    "يا ابني",
    "يا ابنك",
}

_BLOCKED_NAME_TOKENS = {
    normalize_ar(word)
    for word in (
        "تمام",
        "صح",
        "ماشي",
        "أيوه",
        "ايوه",
        "عنوان",
        "شارع",
        "منطقة",
        "مدينه",
        "مدينة",
        "كويس",
        "حلو",
        "طيب",
        "خلاص",
        "حاضر",
        "اوكي",
        "أوكي",
        "شكرا",
        "بس",
        "فراخ",
        "لحمه",
        "لحمة",
        "جبنه",
        "جبنة",
        "بيتزا",
        "برجر",
        "كشري",
        "سلطه",
        "سلطة",
        "كبير",
        "كبيره",
        "كبيرة",
        "صغير",
        "صغيره",
        "صغيرة",
        "وسط",
        "لارج",
        "ميديم",
        "مشروب",
        "عصير",
        "كولا",
        "بيبسي",
        "مياه",
        "شاي",
        "قهوه",
        "قهوة",
        "حضرتك",
        "انا",
        "أنا",
        "إنا",
        "انى",
        "أني",
        "هو",
        "هي",
        "معايا",
        "اسمك",
        "اسمي",
        "ايه",
        "إيه",
        "ابني",
        "ابنك",
        # Greeting tokens — must never appear inside a captured name.
        "اهلا",
        "أهلا",
        "اهلاً",
        "أهلاً",
        "اهلين",
        "أهلين",
        "بيك",
        "بيكي",
        "وسهلا",
        "وسهلاً",
        "السلام",
        "عليكم",
        "وعليكم",
        "صباح",
        "مساء",
        "النور",
        "الخير",
        "ازيك",
        "إزيك",
        "ازيكم",
        "إزيكم",
        "اخبارك",
        "أخبارك",
        "الحمدلله",
        "حمدلله",
    )
}


def is_likely_non_name_response(
    text: str,
    *,
    looks_empty_answer: Callable[[str | None], bool],
) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if "؟" in cleaned or "?" in cleaned:
        return True

    normalized = normalize_ar(cleaned)
    if not normalized:
        return True

    for pattern in _NON_NAME_PATTERNS:
        normalized_pattern = normalize_ar(pattern)
        if normalized == normalized_pattern or normalized.startswith(normalized_pattern + " "):
            return True

    if any(normalized_phrase_present(normalized, hint) for hint in _NON_NAME_HINTS):
        return True

    first_token = cleaned.split()[0]
    if first_token in _FILLER_STARTS or normalize_ar(first_token) in _NORMALIZED_FILLER_STARTS:
        return True

    if looks_empty_answer(cleaned):
        return True
    return False


def extract_name_candidate(
    text: str,
    *,
    looks_empty_answer: Callable[[str | None], bool],
    is_phone_like_text: Callable[[str], bool],
    contains_any_hint: Callable[[str, set[str]], bool],
    intent_hint_groups: Sequence[set[str]],
) -> str | None:
    cleaned = re.sub(r"[.!؟،,]+", " ", (text or "").translate(AR_DIGITS)).strip()
    if not cleaned or is_phone_like_text(cleaned):
        return None
    if "؟" in (text or "") or "?" in (text or ""):
        return None

    for pattern in (
        r"^(?:انا|أنا)\s+(?:اسمي|اسمى)\s+",
        r"^(?:اسمي|اسمى|الاسم|اسم)\s+",
        r"^(?:انا|أنا)\s+",
        r"^(?:معاك|معاكي)\s+",
    ):
        updated = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        if updated != cleaned:
            cleaned = updated
            break

    cleaned = re.sub(
        r"\s+(?:يا\s*فندم|لو\s*سمحت|من\s*فضلك)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    normalized = normalize_ar(cleaned)
    if not normalized or looks_empty_answer(cleaned):
        return None
    if any(normalized_phrase_present(normalized, hint) for hint in _NON_NAME_HINTS):
        return None
    if any(contains_any_hint(normalized, hints) for hints in intent_hint_groups):
        return None

    tokens = cleaned.split()
    if not tokens or len(tokens) > 3:
        return None
    if any(re.search(r"\d", token) for token in tokens):
        return None
    if any(normalize_ar(token) in _BLOCKED_NAME_TOKENS for token in tokens):
        return None
    return cleaned
