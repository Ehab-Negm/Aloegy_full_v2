"""Dialog and response generation helpers."""

import random as _random
import re
from typing import TYPE_CHECKING

from nlp.arabic import (
    contains_normalized_phrase as _contains_normalized_phrase,
    normalize_ar as _normalize_ar,
    normalized_phrase_present as _normalized_phrase_present,
)
from utils.money import money2ar, num2ar
from utils.voice import _voice_safe_text

if TYPE_CHECKING:
    from state.user_data import UserData
    from backend.config import RestaurantConfig

def _extract_zone_from_address(address: str, delivery_zones: list[str] | None) -> str:
    """Match the address against known delivery zones; fall back to the last
    comma/dash-separated segment, or the full trimmed address."""
    if not delivery_zones:
        return address.strip().split(",")[-1].strip() if "," in address else address.strip()
    addr_norm = _normalize_ar(address)
    for z in delivery_zones:
        if _normalize_ar(z) in addr_norm:
            return z
    parts = re.split(r"[،,\-]", address)
    return parts[-1].strip() if len(parts) > 1 else address.strip()


NEGATIVE_WORDS = {
    "لا", "لأ", "لاا", "مفيش", "مفيش طلب", "مفيش حاجه", "مفيش حاجة",
    "خلاص", "بس كده", "تمام كده", "لا تمام", "لا شكرا", "لا شكرًا",
    "ولا حاجه", "ولا حاجة", "no", "none",
    "آه لا", "اه لا", "آه لأ", "اه لأ", "لا مفيش", "لا خلاص", "لا كده تمام",
    "لا تمام كده", "آه مفيش", "اه مفيش",
}

_NEGATIVE_FORMS = frozenset(_normalize_ar(word) for word in NEGATIVE_WORDS)

POSITIVE_CONFIRMATION_WORDS = {
    "صح", "صح كده", "ايوه", "أيوه", "ماشي", "مظبوط", "تمام", "تمام كده",
    "تمام يا فندم", "أوكي", "اوكي", "yes", "أه", "اه", "آه", "اه تمام", "آه تمام",
    "اه ماشي", "آه ماشي", "اه أيوه", "آه أيوه",
}

UPSELL_ACCEPT_WORDS = {
    "ضيف", "ضيفها", "ضيفه", "ضيفهم", "حط", "حطها", "حطه", "حطهم",
    "زود", "زودها", "زوده", "زودهم", "هات", "هاتها", "هاته", "هاتهم",
    "عايزها", "عايزه", "عاوزها", "عاوزه", "ماشي ضيفها", "تمام ضيفها",
    "ايوه ضيفها", "أيوه ضيفها", "ايوه حطها", "أيوه حطها",
    "هضيف", "هضيفها", "هحط", "هحطها", "هزود", "هزودها", "اضيف", "أضيف",
    "نضيف", "نضيفها", "نضيفه", "نضيفهم", "نحط", "نحطها", "نحطه", "نحطهم",
    "نزود", "نزودها", "نزوده", "نزودهم", "نجيب", "نجيبها", "نجيبه",
    "خليها", "خليه", "خليهم", "خليها معاه", "خليها معاها",
    "ok", "اوكي", "أوكي",
}

UPSELL_ITEM_REQUEST_WORDS = {
    "عايز", "عاوزه", "عاوز", "ممكن", "هات", "ضيف", "حط", "زود",
}

UPSELL_REJECTION_WORDS = {
    "بلاش", "مش عايز", "مش عاوز", "مش محتاج", "مش عايزها", "مش عاوزها",
    "لا بلاش", "لا مش عايز", "لا مش عاوز", "لا شكرا", "لا شكرًا",
    "لا ميرسي", "لا مرسي", "مش دلوقتي", "خلينا كده", "كفاية كده",
}

THANKS_WORDS = {
    "شكرا", "شكرا جدا", "شكرا جزيلا", "متشكر", "متشكره", "ميرسي",
    "تسلم", "تسلمي", "thanks", "thank you",
}

ADDRESS_DETAIL_WORDS = {
    "شارع", "عمارة", "عماره", "برج", "بلوك", "دور", "شقة", "شقه", "فيلا",
    "بناية", "بنايه", "ميدان", "أمام", "امام", "قدام", "جنب", "خلف",
}

_ACK_PHRASES = [
    "تمام يا فندم", "حاضر يا فندم", "ماشي يا فندم", "تمام",
    "أكيد", "حاضر", "طبعاً", "ماشي",
]
_ACK_GOT_IT = ["معايا", "سجلت", "أخدت", "حلو"]
_NEXT_NAME = [
    "اسمك إيه يا فندم؟", "الاسم إيه يا فندم؟", "ممكن اسم حضرتك؟",
    "والاسم إيه؟", "اسمك إيه؟",
]
_NEXT_PHONE = [
    "ورقم موبايلك؟", "رقم الموبايل يا فندم؟", "ورقم حضرتك؟",
    "ممكن رقم الموبايل؟", "ورقمك إيه؟", "والموبايل؟",
]
_NEXT_SPECIAL = [
    "في أي طلب خاص في التحضير؟", "حابب تضيف أي ملاحظة على الطلب؟",
    "عندك أي طلب خاص؟", "في حاجة معينة في التحضير؟",
]
_NEXT_ADDRESS = [
    "عنوانك إيه يا فندم؟", "العنوان إيه يا فندم؟", "ممكن العنوان؟",
    "فين هنوصلك؟", "العنوان إيه؟",
]
_EMPTY_TAIL_WORDS = {
    "تمام", "خلاص", "كده", "بس", "شكرا", "شكرًا", "ميرسي", "متشكر",
    "يا", "فندم", "لو", "سمحت", "حضرتك", "اوكي", "أوكي", "ماشي", "حاضر",
    "مفيش", "حاجه", "حاجة", "خاصه", "خاصة", "طلب", "طلبات", "ملاحظه", "ملاحظة",
    "مش", "محتاج", "محتاجه", "محتاجة", "عايز", "عاوز", "عايزه", "عاوزه",
    "عادي", "ابدا", "أبدا", "ابدًا", "نهائي", "نهائيا", "خالص",
}
_SPECIAL_REQUEST_HINTS = {
    "من غير", "بدون", "سخنه", "سخنة", "حار", "بارد", "صوص", "شطه", "شطة",
    "كاتشب", "مايونيز", "جبنه", "جبنة", "بصل", "طماطم", "مخلل", "تقطيع",
    "مقطعه", "مقطعة", "مقرمشه", "مقرمشة", "زياده", "زيادة", "على جنب",
    "خلي", "خليها", "خليه", "تكون", "استواء", "رفيعه", "رفيعة", "ناشف",
    "طرية", "طريه", "زيادة جبنة", "من غير بصل", "من غير طماطم",
}
_NEGATE_SPECIAL_PHRASES = {
    "مفيش طلبات", "مفيش طلب خاص", "مفيش حاجة خاصة", "مفيش ملاحظات",
    "مفيش طلبات خاصة", "لا مفيش", "من غير طلبات", "لا عادي",
    "لا مفيش حاجة", "كده وبس", "بس كده", "خلاص كده",
    "خلينا كده", "كفاية كده", "كفايه كده",
}


def _looks_empty_answer(text: str | None) -> bool:
    normalized = _normalize_ar(text or "")
    if not normalized:
        return True
    for word in _NEGATIVE_FORMS:
        if normalized == word:
            return True
        if normalized.startswith(f"{word} "):
            tail = normalized[len(word):].strip()
            if not tail:
                return True
            if all(token in _EMPTY_TAIL_WORDS for token in tail.split()):
                return True
    return False


def _ack() -> str:
    return _random.choice(_ACK_PHRASES)

def _ack_got(thing: str) -> str:
    return f"{_random.choice(_ACK_GOT_IT)} {thing}"

def _ask_name() -> str:
    return _random.choice(_NEXT_NAME)

def _ask_phone() -> str:
    return _random.choice(_NEXT_PHONE)

def _ask_special() -> str:
    return _random.choice(_NEXT_SPECIAL)

def _ask_address() -> str:
    return _random.choice(_NEXT_ADDRESS)


def _format_order_item(name: str, qty: int) -> str:
    return name if qty <= 1 else f"{name} × {qty}"


def _is_thanks_message(text: str) -> bool:
    return _contains_normalized_phrase(text, THANKS_WORDS)


def _is_positive_confirmation(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized or _is_thanks_message(text):
        return False
    if _contains_normalized_phrase(text, POSITIVE_CONFIRMATION_WORDS):
        return len(normalized.split()) <= 4
    return False


def _is_explicit_upsell_acceptance(text: str, item_name: str | None) -> bool:
    normalized = _normalize_ar(text)
    if not normalized or _is_thanks_message(text) or _looks_empty_answer(text):
        return False

    if _contains_normalized_phrase(text, UPSELL_ACCEPT_WORDS):
        return True

    item_normalized = _normalize_ar(item_name or "")
    if not item_normalized:
        return False

    mentions_item = _normalized_phrase_present(normalized, item_normalized)
    if not mentions_item:
        return False

    if normalized == item_normalized:
        return True
    if _contains_normalized_phrase(text, POSITIVE_CONFIRMATION_WORDS | UPSELL_ITEM_REQUEST_WORDS):
        return True
    return False


def _is_explicit_upsell_rejection(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    if _looks_empty_answer(text):
        return True
    if _contains_normalized_phrase(text, UPSELL_REJECTION_WORDS):
        return True
    _REJECTION_PREFIXES = {_normalize_ar(w) for w in ("لا", "لأ", "لاا")}
    first_token = normalized.split()[0] if normalized else ""
    if first_token in _REJECTION_PREFIXES and len(normalized.split()) > 1:
        return True
    return False


def _upsell_reply_negates_special(text: str) -> bool:
    if _extract_special_request_candidate(text):
        return False
    normalized = _normalize_ar(text or "")
    return any(_normalize_ar(phrase) in normalized for phrase in _NEGATE_SPECIAL_PHRASES)


def _extract_special_request_candidate(text: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    if not raw or _looks_empty_answer(raw) or _is_thanks_message(raw):
        return None

    cleaned = re.sub(
        r"^\s*(?:بس|وبس|ولو|لو|طيب|طب|يعني|معلش|ممكن|حاضر|تمام يا فندم|تمام|ماشي|أيوه|ايوه)\b[\s،,]*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip(" ،,.")
    if not cleaned or _looks_empty_answer(cleaned):
        return None

    normalized = _normalize_ar(cleaned)
    for hint in _SPECIAL_REQUEST_HINTS:
        norm_hint = _normalize_ar(hint)
        if " " in norm_hint:
            if norm_hint in normalized:
                return cleaned
        elif _normalized_phrase_present(normalized, norm_hint):
            return cleaned
    return None


def _extract_special_request_after_upsell_reply(text: str, item_name: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    if not raw:
        return None

    cleaned = raw
    if item_name:
        cleaned = re.sub(re.escape(item_name), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:هضيف|هضيفها|هحط|هحطها|هزود|هزودها|ضيف|ضيفها|حط|حطها|زود|زودها|هات|هاتها|"
        r"أيوه|ايوه|تمام|ماشي|حاضر|لا|لأ|شكرا|شكرًا|ميرسي|لو سمحت|يا فندم|وبس|بس)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ،,.")
    return _extract_special_request_candidate(cleaned)


def _address_seems_specific(address: str) -> bool:
    normalized = _normalize_ar(address)
    if not normalized:
        return False

    raw = (address or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    has_number = bool(re.search(r"\d", raw))
    detail_hits = sum(1 for word in ADDRESS_DETAIL_WORDS if _normalize_ar(word) in normalized)

    if has_number and detail_hits >= 1:
        return True
    if detail_hits >= 2:
        return True
    return False


def _join_user_phrases(*parts: str) -> str:
    cleaned_parts = [re.sub(r"\s+", " ", (part or "")).strip(" .") for part in parts if (part or "").strip()]
    if not cleaned_parts:
        return ""
    text = ". ".join(cleaned_parts)
    if not text.endswith(("؟", ".", "!", "؟.")):
        text += "."
    return text


def _clean_followup_note(note: str) -> str:
    return re.sub(r"\s+", " ", (note or "")).strip(" .")


def _followup_after_name(flow: str, ud: "UserData") -> str:
    if flow == "complaint":
        return _ask_phone() if not ud.customer_phone else "تحب حاجة تانية؟"
    return _ask_phone()


def _followup_after_special_request(flow: str) -> str:
    if flow == "takeaway":
        return _ask_name()
    if flow == "delivery":
        return _ask_address()
    return ""


def _special_request_followup_message(flow: str, ud: "UserData", *, accepted_item: str | None = None) -> str:
    next_question = _followup_after_special_request(flow)
    if accepted_item:
        return _voice_safe_text(
            _join_user_phrases(f"تمام يا فندم، ضفت {accepted_item} وسجلت الملاحظة على الطلب", next_question),
            max_chars=180,
        )
    return _voice_safe_text(
        _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", next_question),
        max_chars=180,
    )


def _complaint_followup_question(ud: "UserData") -> str:
    from agent import _complaint_next_missing_slot
    missing = _complaint_next_missing_slot(ud)
    if missing == "الاسم":
        return _ask_name()
    if missing == "رقم الموبايل":
        return _ask_phone()
    return "تحب حاجة تانية؟"


def _phone_capture_short_reply(ud: "UserData", partial_digits: str) -> str:
    if not partial_digits:
        return "تمام يا فندم"
    from utils.money import phone2ar
    return f"معايا {phone2ar(partial_digits)} كمل يا فندم"


def _phone_capture_failure_reply(ud: "UserData") -> str:
    if ud.phone_capture_failures >= 3:
        return "للأسف الرقم لسه مش واضح، هنتواصل معاك بعدين لتأكيده."
    return "معلش الرقم مش واضح، ممكن تقوله تاني رقم رقم؟"


def _takeaway_confirmation_prompt(ud: "UserData") -> str:
    from agent import _spoken_order_items
    total_part = f"، الإجمالي {money2ar(ud.order_total)} جنيه" if ud.order_validated and ud.order_total > 0 else ""
    return f"{_spoken_order_items(ud.order)}{total_part} باسم {ud.customer_name}، صح؟"


def _delivery_confirmation_prompt(ud: "UserData") -> str:
    from agent import _spoken_order_items
    total = ud.order_total
    fee = getattr(ud, '_delivery_fee', 0.0)
    if ud.restaurant and hasattr(ud.restaurant, 'delivery_fee'):
        fee = float(ud.restaurant.delivery_fee or 0)
        total += fee
    total_part = f"، الإجمالي {money2ar(total)} جنيه" if ud.order_validated and total > 0 else ""
    if fee > 0 and ud.order_validated:
        total_part += " شامل التوصيل"
    return f"{_spoken_order_items(ud.order)}{total_part} لعنوان {ud.delivery_address} باسم {ud.customer_name}، صح؟"


def _reservation_confirmation_prompt(ud: "UserData") -> str:
    return f"حجز {num2ar(ud.guests_count or 0)} ضيوف يوم {ud.reservation_time} باسم {ud.customer_name}، صح؟"


def _is_confirmation_prompt(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return False
    normalized = _normalize_ar(cleaned)
    return cleaned.endswith("صح؟") or (" باسم " in f" {normalized} " and " صح " in f" {normalized} ")


def _followup_after_phone(flow: str, ud: "UserData") -> str:
    from agent import _is_takeaway_ready_for_confirmation, _is_delivery_ready_for_confirmation, _is_reservation_ready_for_confirmation
    if flow == "takeaway":
        return _takeaway_confirmation_prompt(ud) if _is_takeaway_ready_for_confirmation(ud) else "تحب تطلب إيه؟"
    if flow == "delivery":
        return _delivery_confirmation_prompt(ud) if _is_delivery_ready_for_confirmation(ud) else "تحب تطلب إيه؟"
    if flow == "reservation":
        return _reservation_confirmation_prompt(ud) if _is_reservation_ready_for_confirmation(ud, ud.restaurant) else "عايز تحجز إمتى يا فندم؟"
    if flow == "complaint":
        return "تحب حاجة تانية يا فندم؟"
    return ""

__all__ = [
    "_looks_empty_answer",
    "_ack",
    "_ack_got",
    "_ask_name",
    "_ask_phone",
    "_ask_special",
    "_ask_address",
    "_format_order_item",
    "_is_thanks_message",
    "_is_positive_confirmation",
    "_is_explicit_upsell_acceptance",
    "_is_explicit_upsell_rejection",
    "_upsell_reply_negates_special",
    "_extract_special_request_candidate",
    "_extract_special_request_after_upsell_reply",
    "_address_seems_specific",
    "_join_user_phrases",
    "_clean_followup_note",
    "_followup_after_name",
    "_followup_after_special_request",
    "_special_request_followup_message",
    "_complaint_followup_question",
    "_phone_capture_short_reply",
    "_phone_capture_failure_reply",
    "_takeaway_confirmation_prompt",
    "_delivery_confirmation_prompt",
    "_reservation_confirmation_prompt",
    "_is_confirmation_prompt",
    "_followup_after_phone",
]
