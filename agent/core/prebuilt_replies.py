"""Prebuilt voice replies for the deterministic fast path.

The dialogue engine's hottest replies (slot questions, simple
acknowledgements, "already submitted" guards) are short and almost
identical across calls. Computing them on every turn is wasted work and
adds avoidable latency before TTS streaming can start.

Phase 2.2 — variation pools. Each category now exposes 5–8 phrasings;
``question_for`` / ``repeat_for`` pick one per call so the agent stops
sounding like a tape loop. The first variant of each pool stays the
canonical one (kept exported as ``ASK_*`` / ``REPEAT_*`` for callers
that still want a stable single string — e.g. golden tests).

Acceptance: ``benchmark_engine_tests.py`` ensures the deterministic
engine decision p95 is under 30 ms and the deterministic turn p95
before TTS is under 100 ms.
"""

from __future__ import annotations

import random as _random
from typing import Final


# ---- Slot questions (variant pools) ----------------------------------------
# 5–8 variants per category. First entry is the canonical phrasing kept
# for backwards-compatible exports below.

_QUESTION_VARIANTS: dict[str, tuple[str, ...]] = {
    "name": (
        "ممكن اسم حضرتك؟",
        "اسم حضرتك إيه يا فندم؟",
        "تحت أي اسم نسجل الطلب؟",
        "اسم حضرتك لو سمحت؟",
        "ممكن أعرف اسم حضرتك؟",
        "خد بالك بس، اسم حضرتك؟",
    ),
    "phone": (
        "ممكن رقم الموبايل؟",
        "رقم الموبايل لو سمحت؟",
        "موبايل حضرتك إيه؟",
        "ممكن نمرة موبايل للتواصل؟",
        "ممكن أعرف رقم الموبايل بتاع حضرتك؟",
        "ممكن رقم موبايل نتواصل بيه؟",
    ),
    "order": (
        "تحب تطلب إيه؟",
        "حضرتك تحب تطلب إيه يا فندم؟",
        "تحب تطلب إيه من المنيو؟",
        "إيه اللي تحب تطلبه؟",
        "تحب تطلب إيه دلوقتي؟",
        "خد راحتك، تحب تطلب إيه؟",
    ),
    "address": (
        "ممكن العنوان؟",
        "العنوان لو سمحت؟",
        "ممكن أعرف عنوان التوصيل؟",
        "العنوان والمنطقة يا فندم؟",
        "ممكن العنوان بالتفصيل؟",
        "العنوان فين يا فندم؟",
    ),
    "reservation_time": (
        "عايز تحجز إمتى يا فندم؟",
        "الحجز يكون إمتى؟",
        "أي يوم وأي ساعة مناسبين لحضرتك؟",
        "إمتى تحب تيجي؟",
        "حضرتك عايز الحجز يوم إيه والساعة كام؟",
        "ميعاد الحجز اللي يناسبك؟",
    ),
    "guests": (
        "كام شخص هتكونوا؟",
        "هتكونوا كام واحد يا فندم؟",
        "الحجز لكام نفر؟",
        "عددكم كام؟",
        "ترابيزة لكام شخص؟",
    ),
    "complaint": (
        "احكيلي الشكوى يا فندم.",
        "إيه المشكلة لو سمحت؟",
        "حضرتك تواجه مشكلة في إيه؟",
        "احكيلي اللي حصل، أنا سامعك.",
        "قولّي تفاصيل الشكوى وأنا هساعدك.",
    ),
    "complaint_type": (
        "المشكلة في الطلب ولا الجودة ولا الخدمة ولا التوصيل؟",
        "المشكلة دي بخصوص إيه — الطلب، الأكل، الخدمة، ولا التوصيل؟",
        "نوع الشكوى إيه يا فندم؟",
        "المشكلة كانت في الطلب نفسه ولا في الخدمة؟",
    ),
    "post_completion": (
        "تحب حاجة تانية يا فندم؟",
        "في حاجة كمان أقدر أعملها لحضرتك؟",
        "تحب تضيف حاجة على الطلب؟",
        "محتاج حاجة تانية؟",
        "كده تمام يا فندم ولا في حاجة كمان؟",
    ),
    "unknown": (
        "ممكن توضحلي أكتر؟",
        "ممكن تعيد كلام حضرتك تاني؟",
        "معلش، مش فاهم قصد حضرتك بالظبط، توضحلي؟",
        "ممكن تشرحلي أكتر شوية؟",
        "أنا مش لاقطها صح، ممكن تعيدها؟",
    ),
}


# ---- Repeat-guard fallbacks (variant pools) --------------------------------

_REPEAT_VARIANTS: dict[str, tuple[str, ...]] = {
    "name": (
        "لسه محتاج الاسم عشان أسجل الطلب صح.",
        "محتاج اسم حضرتك علشان أكمل الطلب.",
        "لو سمحت اسم حضرتك؟ مش لاقطه.",
        "علشان أسجل صح، ممكن الاسم تاني؟",
    ),
    "phone": (
        "لسه محتاج رقم موبايل صحيح للتواصل.",
        "ممكن نمرة الموبايل تاني؟ مش لاقطها كاملة.",
        "محتاج رقم موبايل علشان نقدر نتواصل مع حضرتك.",
        "النمرة دي مش طالعة معايا صح، ممكن تعيدها؟",
    ),
    "order": (
        "لسه محتاج أعرف الطلب عشان أكمله.",
        "ممكن تقولي الطلب تاني يا فندم؟",
        "علشان نكمل، عايز تطلب إيه بالظبط؟",
        "محتاج أعرف الطلب لحضرتك علشان أسجله.",
    ),
    "address": (
        "لسه محتاج العنوان والمنطقة عشان التوصيل.",
        "ممكن العنوان كامل بالمنطقة؟",
        "علشان نوصل صح، محتاج العنوان مفصّل.",
        "العنوان مش واضح ليا، ممكن تعيده؟",
    ),
    "reservation_time": (
        "لسه محتاج يوم وساعة الحجز.",
        "ممكن يوم وميعاد الحجز تاني؟",
        "محتاج اليوم والساعة بالظبط علشان أحجز.",
    ),
    "guests": (
        "لسه محتاج أعرف عدد الأشخاص.",
        "ممكن العدد تاني يا فندم؟",
        "هتكونوا كام واحد علشان أحجز ترابيزة مناسبة؟",
    ),
    "branch": (
        "لسه محتاج أعرف الفرع المناسب.",
        "أي فرع يناسب حضرتك؟",
        "محتاج أعرف الفرع علشان أكمل الحجز.",
    ),
    "complaint": (
        "لسه محتاج تفاصيل الشكوى.",
        "ممكن تحكيلي اللي حصل بالتفصيل؟",
        "علشان أساعد حضرتك، محتاج التفاصيل.",
    ),
    "complaint_type": (
        "لسه محتاج أعرف نوع المشكلة.",
        "المشكلة بالظبط في إيه يا فندم؟",
        "علشان أحوّلها صح، نوع الشكوى إيه؟",
    ),
}


# ---- Acknowledgements (variant pools) --------------------------------------

_ACK_OK_VARIANTS: tuple[str, ...] = (
    "تمام يا فندم.",
    "ماشي يا فندم.",
    "حاضر يا فندم.",
    "اوكي.",
    "تمام.",
    "ماشي.",
)

_ACK_GOT_IT_VARIANTS: tuple[str, ...] = (
    "ماشي، استلمت.",
    "تمام، اتسجلت.",
    "حاضر، خدت بالي.",
    "تمام، خلصت.",
    "ماشي، اتظبطت.",
)

_ACK_THINKING_VARIANTS: tuple[str, ...] = (
    "ثانية واحدة يا فندم.",
    "لحظة بس يا فندم.",
    "ثواني وأرجعلك.",
    "ثانية بس.",
    "ثواني يا فندم.",
)


# ---- Stable single-string exports (back-compat for golden tests) -----------

ASK_NAME: Final = _QUESTION_VARIANTS["name"][0]
ASK_PHONE: Final = _QUESTION_VARIANTS["phone"][0]
ASK_ORDER: Final = _QUESTION_VARIANTS["order"][0]
ASK_ADDRESS: Final = _QUESTION_VARIANTS["address"][0]
ASK_RESERVATION_TIME: Final = _QUESTION_VARIANTS["reservation_time"][0]
ASK_GUESTS: Final = _QUESTION_VARIANTS["guests"][0]
ASK_COMPLAINT: Final = _QUESTION_VARIANTS["complaint"][0]
ASK_COMPLAINT_TYPE: Final = _QUESTION_VARIANTS["complaint_type"][0]
ASK_POST_COMPLETION: Final = _QUESTION_VARIANTS["post_completion"][0]
ASK_CLARIFICATION: Final = _QUESTION_VARIANTS["unknown"][0]

REPEAT_NAME: Final = _REPEAT_VARIANTS["name"][0]
REPEAT_PHONE: Final = _REPEAT_VARIANTS["phone"][0]
REPEAT_ORDER: Final = _REPEAT_VARIANTS["order"][0]
REPEAT_ADDRESS: Final = _REPEAT_VARIANTS["address"][0]
REPEAT_RESERVATION_TIME: Final = _REPEAT_VARIANTS["reservation_time"][0]
REPEAT_GUESTS: Final = _REPEAT_VARIANTS["guests"][0]
REPEAT_BRANCH: Final = _REPEAT_VARIANTS["branch"][0]
REPEAT_COMPLAINT: Final = _REPEAT_VARIANTS["complaint"][0]
REPEAT_COMPLAINT_TYPE: Final = _REPEAT_VARIANTS["complaint_type"][0]

ACK_OK: Final = _ACK_OK_VARIANTS[0]
ACK_GOT_IT: Final = _ACK_GOT_IT_VARIANTS[0]
ACK_THINKING: Final = _ACK_THINKING_VARIANTS[0]


# ---- Submission outcomes ---------------------------------------------------

ALREADY_SUBMITTED_TAKEAWAY = "الطلب متسجل خلاص. في حاجة تانية؟"
ALREADY_SUBMITTED_DELIVERY = "طلب التوصيل متسجل خلاص. في حاجة تانية؟"
ALREADY_SUBMITTED_RESERVATION = "الحجز متسجل خلاص يا فندم."
ALREADY_LOGGED_COMPLAINT = "الشكوى اتسجلت خلاص."
SUBMIT_IN_FLIGHT = "ثانية واحدة يا فندم، بسجل الطلب دلوقتي."


# ---- Lookup helpers --------------------------------------------------------

def question_for(category: str) -> str:
    """Pick a phrasing for ``category`` at random (5–8 variants per slot)."""
    pool = _QUESTION_VARIANTS.get(category) or _QUESTION_VARIANTS["unknown"]
    return _random.choice(pool)


def repeat_for(category: str) -> str:
    """Pick a repeat-guard phrasing for ``category`` at random."""
    pool = _REPEAT_VARIANTS.get(category)
    if not pool:
        return question_for(category)
    return _random.choice(pool)


def ack_ok() -> str:
    return _random.choice(_ACK_OK_VARIANTS)


def ack_got_it() -> str:
    return _random.choice(_ACK_GOT_IT_VARIANTS)


def ack_thinking() -> str:
    return _random.choice(_ACK_THINKING_VARIANTS)


__all__ = [
    "ACK_GOT_IT",
    "ACK_OK",
    "ACK_THINKING",
    "ALREADY_LOGGED_COMPLAINT",
    "ALREADY_SUBMITTED_DELIVERY",
    "ALREADY_SUBMITTED_RESERVATION",
    "ALREADY_SUBMITTED_TAKEAWAY",
    "ASK_ADDRESS",
    "ASK_CLARIFICATION",
    "ASK_COMPLAINT",
    "ASK_COMPLAINT_TYPE",
    "ASK_GUESTS",
    "ASK_NAME",
    "ASK_ORDER",
    "ASK_PHONE",
    "ASK_POST_COMPLETION",
    "ASK_RESERVATION_TIME",
    "REPEAT_ADDRESS",
    "REPEAT_BRANCH",
    "REPEAT_COMPLAINT",
    "REPEAT_COMPLAINT_TYPE",
    "REPEAT_GUESTS",
    "REPEAT_NAME",
    "REPEAT_ORDER",
    "REPEAT_PHONE",
    "REPEAT_RESERVATION_TIME",
    "SUBMIT_IN_FLIGHT",
    "ack_got_it",
    "ack_ok",
    "ack_thinking",
    "question_for",
    "repeat_for",
]
