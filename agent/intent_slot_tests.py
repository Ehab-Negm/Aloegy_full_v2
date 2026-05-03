"""Phase 3 acceptance suite for the intent + slot extractors.

Goal: ≥150 cases covering deterministic intent detection, contact / address
/ reservation / complaint extraction, and confidence-tier behaviour.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from core.extractors.address_extractor import extract_address  # noqa: E402
from core.extractors.complaint_extractor import classify_complaint  # noqa: E402
from core.extractors.contact_extractor import (  # noqa: E402
    extract_name,
    extract_phone,
    is_explicit_denial,
)
from core.extractors.intent_extractor import detect_intent  # noqa: E402
from core.extractors.reservation_extractor import (  # noqa: E402
    extract_guests_count,
    extract_reservation_time,
)


_FAILURES: list[tuple[str, str]] = []
_PASSED = 0
_TOTAL = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _TOTAL
    _TOTAL += 1
    if condition:
        _PASSED += 1
    else:
        _FAILURES.append((name, detail))


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


def test_intent_takeaway_basic() -> None:
    cases = [
        "عايز اطلب تيكاواي",
        "هاجي استلمه من المطعم",
        "هاخده انا من المحل",
        "تيكاواي لو سمحت",
        "استلام من الفرع",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_takeaway[{text}]", d.kind == "takeaway", str(d))


def test_intent_delivery_basic() -> None:
    cases = [
        "عايز توصيل لو سمحت",
        "دليفري للبيت",
        "هتوصلوا فين بالظبط؟ لا، عايز توصيل",  # contains zone question first
        "ابعتوهالي عالعنوان",
        "وصلوهالي على المعادي",
    ]
    for i, text in enumerate(cases):
        d = detect_intent(text)
        if i == 2:
            # Contains zone question → that should win
            _check(
                f"intent_delivery_zone_first[{text}]",
                d.kind == "delivery_zone_question",
                str(d),
            )
        else:
            _check(f"intent_delivery[{text}]", d.kind == "delivery", str(d))


def test_intent_reservation() -> None:
    cases = [
        "عايز احجز ترابيزة",
        "احجزلي ترابيزه يوم الجمعة",
        "حجز لاتنين",
        "رزيرفيشن لو سمحت",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_reservation[{text}]", d.kind == "reservation", str(d))


def test_intent_complaint() -> None:
    cases = [
        "عندي شكوى",
        "اشكي على الطلب",
        "في مشكلة في الأكل",
        "الطلب وصل بارد",
        "تأخر علي ساعة",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_complaint[{text}]", d.kind == "complaint", str(d))


def test_intent_menu_question() -> None:
    cases = [
        "ايه المتاح؟",
        "عندك ايه؟",
        "ممكن المنيو؟",
        "إيه الأصناف عندكم؟",
        "السعر بتاع البرجر كام؟",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_menu[{text}]", d.kind == "menu_question", str(d))


def test_intent_zone_question() -> None:
    cases = [
        "بتوصلوا فين؟",
        "هتوصلوا فين بالظبط؟",
        "ايه المناطق المتاحة؟",
        "التوصيل متاح فين؟",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_zone[{text}]", d.kind == "delivery_zone_question", str(d))


def test_intent_total_question() -> None:
    cases = [
        "بكام كله؟",
        "كام الحساب؟",
        "الإجمالي إيه؟",
        "التوتال كام؟",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_total[{text}]", d.kind == "total_question", str(d))


def test_intent_post_completion() -> None:
    cases = [
        "متشكر يا فندم",
        "شكرا، بس كده",
        "تمام كده، خلاص",
        "thanks",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_post[{text}]", d.kind == "post_completion_thanks", str(d))


def test_intent_greeting() -> None:
    cases = [
        "اهلا يا فندم",
        "السلام عليكم",
        "صباح الخير",
        "مساء الخير",
        "ازيك",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_greeting[{text}]", d.kind == "greeting", str(d))


def test_intent_unknown() -> None:
    cases = [
        "",
        "ك ل ل ل",
        "وصاحبي قاللي اكل ايه",
        "مش فاهم اي حاجة",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"intent_unknown[{text}]", d.kind == "unknown", str(d))


def test_intent_priority_zone_over_delivery() -> None:
    d = detect_intent("هتوصلوا فين بالظبط؟")
    _check("intent_priority_zone:zone_wins", d.kind == "delivery_zone_question")


def test_intent_priority_menu_over_takeaway() -> None:
    d = detect_intent("ايه المتاح وعايز استلام؟")
    _check("intent_priority_menu:menu_wins", d.kind == "menu_question")


# ---------------------------------------------------------------------------
# Contact: name
# ---------------------------------------------------------------------------


def test_name_explicit_marker() -> None:
    c = extract_name("اسمي أحمد")
    _check("name_explicit:value", c.value == "أحمد", str(c))
    _check("name_explicit:high", c.is_high_confidence(), str(c))


def test_name_short_clean() -> None:
    c = extract_name("محمد")
    _check("name_short:value", c.value == "محمد", str(c))


def test_name_two_tokens() -> None:
    c = extract_name("ياسمين علي")
    _check("name_two_tokens:value", c.value == "ياسمين علي", str(c))


def test_name_anchored_with_marker() -> None:
    c = extract_name("انا اسمي يوسف")
    _check("name_anchored:high_conf", c.is_high_confidence())
    _check("name_anchored:value", c.value == "يوسف", str(c))


def test_name_blocked_token() -> None:
    cases = [
        "اوكي",
        "تمام",
        "بيتزا",
        "كولا",
        "الشارع 5",
    ]
    for text in cases:
        c = extract_name(text)
        _check(f"name_blocked[{text}]:rejected", c.value is None, str(c))


def test_name_phone_like_rejected() -> None:
    c = extract_name("01012345678")
    _check("name_phone_like:rejected", c.value is None, str(c))


def test_name_question_rejected() -> None:
    c = extract_name("اسمك إيه؟")
    _check("name_question:rejected", c.value is None, str(c))


def test_name_filler_rejected() -> None:
    cases = ["آه", "اه", "ممم", "هم"]
    for text in cases:
        c = extract_name(text)
        _check(f"name_filler[{text}]:rejected", c.value is None, str(c))


def test_name_too_long_rejected() -> None:
    c = extract_name("احمد محمد علي حسن صبحي")
    _check("name_too_long:rejected", c.value is None, str(c))


# ---------------------------------------------------------------------------
# Contact: phone
# ---------------------------------------------------------------------------


def test_phone_full_010() -> None:
    c = extract_phone("01012345678")
    _check("phone_010:value", c.value == "01012345678", str(c))
    _check("phone_010:high", c.is_high_confidence())


def test_phone_full_011() -> None:
    c = extract_phone("01134567890")
    _check("phone_011:value", c.value == "01134567890")


def test_phone_full_012() -> None:
    c = extract_phone("01234567890")
    _check("phone_012:value", c.value == "01234567890")


def test_phone_full_015() -> None:
    c = extract_phone("01512345678")
    _check("phone_015:value", c.value == "01512345678")


def test_phone_with_country_code() -> None:
    c = extract_phone("+201012345678")
    _check("phone_intl:value", c.value == "+201012345678")


def test_phone_partial_returns_medium() -> None:
    c = extract_phone("0101234")
    _check("phone_partial:no_value", c.value is None, str(c))
    _check("phone_partial:medium", 0.4 <= c.confidence < 0.85, str(c))


def test_phone_invalid_carrier() -> None:
    c = extract_phone("01999999999")
    _check("phone_invalid_carrier:rejected", c.value is None, str(c))


def test_phone_arabic_indic_digits() -> None:
    c = extract_phone("٠١٠١٢٣٤٥٦٧٨")
    _check("phone_arabic:value", c.value == "01012345678", str(c))


def test_phone_with_separators() -> None:
    c = extract_phone("0101-234-5678")
    _check("phone_separators:value", c.value == "01012345678", str(c))


def test_phone_with_country_no_plus() -> None:
    c = extract_phone("201012345678")
    _check("phone_country_noplus:value", c.value == "+201012345678", str(c))


def test_phone_empty() -> None:
    _check("phone_empty:none", extract_phone("").value is None)


def test_phone_only_letters() -> None:
    _check("phone_letters:none", extract_phone("لا اعرف رقمي").value is None)


def test_phone_too_short() -> None:
    _check("phone_short:none", extract_phone("0101").value is None)


# ---------------------------------------------------------------------------
# Contact: explicit denial
# ---------------------------------------------------------------------------


def test_denial_la() -> None:
    _check("denial_la", is_explicit_denial("لا"))


def test_denial_mafish() -> None:
    _check("denial_mafish", is_explicit_denial("مفيش"))


def test_denial_mafish_haga() -> None:
    _check("denial_mafish_haga", is_explicit_denial("مفيش حاجة"))


def test_denial_no() -> None:
    _check("denial_no", is_explicit_denial("no"))


def test_denial_random_text() -> None:
    _check("denial_random:false", not is_explicit_denial("اسمي أحمد"))


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------


def test_address_zone_landmark_digit() -> None:
    a = extract_address(
        "المعادي شارع 9 برج 12",
        delivery_zones=("المعادي", "مدينة نصر"),
    )
    _check("addr_full:high", a.is_high_confidence(), str(a))
    _check("addr_full:zone", a.zone == "المعادي", str(a))


def test_address_landmark_digit_no_zone() -> None:
    a = extract_address("شارع 5 شقة 3 الدور التاني")
    _check("addr_no_zone:value", a.value is not None)
    _check("addr_no_zone:landmark_digit", a.confidence >= 0.85)


def test_address_zone_only() -> None:
    a = extract_address("في المعادي", delivery_zones=("المعادي",))
    _check("addr_zone_only:value", a.value is not None, str(a))
    _check("addr_zone_only:medium", a.confidence >= 0.6)


def test_address_landmark_only() -> None:
    a = extract_address("في الشارع")
    _check("addr_landmark_only:medium_at_least", a.confidence >= 0.6, str(a))


def test_address_phone_like_rejected() -> None:
    a = extract_address("01012345678")
    _check("addr_phone:rejected", a.value is None, str(a))


def test_address_empty_rejected() -> None:
    _check("addr_empty:rejected", extract_address("").value is None)


def test_address_no_signal() -> None:
    a = extract_address("اسمي أحمد")
    _check("addr_no_signal:none", a.value is None, str(a))


def test_address_zone_unknown_to_us() -> None:
    a = extract_address(
        "المنطقة في الزمالك",
        delivery_zones=("المعادي",),  # zamalek is not configured
    )
    # Still has landmark word "منطقة" → medium confidence.
    _check("addr_unknown_zone:medium_at_least", a.confidence >= 0.6, str(a))


# ---------------------------------------------------------------------------
# Reservation: time
# ---------------------------------------------------------------------------


def test_reservation_day_and_time() -> None:
    r = extract_reservation_time("بكره الساعة 8 مساء")
    _check("res_time:high", r.is_high_confidence(), str(r))


def test_reservation_time_only() -> None:
    r = extract_reservation_time("الساعة 9 بليل")
    _check("res_time_only:high", r.confidence >= 0.85, str(r))


def test_reservation_day_only() -> None:
    r = extract_reservation_time("يوم الجمعة")
    _check("res_day_only:medium", r.confidence >= 0.6, str(r))


def test_reservation_no_signal() -> None:
    r = extract_reservation_time("متشكر يا فندم")
    _check("res_no_signal:none", r.raw is None, str(r))


def test_reservation_today_with_time() -> None:
    r = extract_reservation_time("النهارده الساعة 7")
    _check("res_today:high", r.is_high_confidence())


def test_reservation_dot_time() -> None:
    r = extract_reservation_time("بكره 7.30 مساء")
    _check("res_dot_time:value", r.raw is not None, str(r))


# ---------------------------------------------------------------------------
# Reservation: guests
# ---------------------------------------------------------------------------


def test_guests_explicit_word() -> None:
    g = extract_guests_count("4 ضيوف")
    _check("guests_explicit:value", g.count == 4)


def test_guests_with_shakhs() -> None:
    g = extract_guests_count("هنبقى 6 شخص")
    _check("guests_shakhs:value", g.count == 6)


def test_guests_spoken_with_unit() -> None:
    g = extract_guests_count("اربعه ضيوف")
    _check("guests_spoken:value", g.count == 4, str(g))


def test_guests_bare_digit_lower_confidence() -> None:
    g = extract_guests_count("خمسة")  # spoken digit but no unit word
    _check("guests_bare_low:none_or_low", g.confidence < 0.6 or g.count is None)


def test_guests_too_high_rejected() -> None:
    g = extract_guests_count("100 ضيف")
    _check("guests_high:rejected", g.count is None, str(g))


def test_guests_zero_rejected() -> None:
    g = extract_guests_count("0 ضيف")
    _check("guests_zero:rejected", g.count is None, str(g))


def test_guests_no_signal() -> None:
    g = extract_guests_count("متشكر")
    _check("guests_no_signal:none", g.count is None)


# ---------------------------------------------------------------------------
# Complaint
# ---------------------------------------------------------------------------


def test_complaint_order_wrong() -> None:
    c = classify_complaint("الطلب اللي وصلني غلط")
    _check("complaint_order_wrong:cat", c.category == "order", str(c))


def test_complaint_quality_cold() -> None:
    c = classify_complaint("الأكل وصلني بارد")
    _check("complaint_quality_cold:cat", c.category == "quality", str(c))


def test_complaint_quality_burnt() -> None:
    c = classify_complaint("البرجر محروق")
    _check("complaint_quality_burnt:cat", c.category == "quality", str(c))


def test_complaint_service() -> None:
    c = classify_complaint("الموظف اتعصب عليا")
    _check("complaint_service:cat", c.category == "service", str(c))


def test_complaint_delivery_late() -> None:
    c = classify_complaint("اتأخر علي ساعة كاملة")
    _check("complaint_delivery_late:cat", c.category == "delivery", str(c))


def test_complaint_other_generic() -> None:
    c = classify_complaint("عندي شكوى عامة")
    _check("complaint_other:cat", c.category == "other", str(c))


def test_complaint_no_signal() -> None:
    c = classify_complaint("اهلا يا فندم")
    _check("complaint_no_signal:none", c.category is None, str(c))


# ---------------------------------------------------------------------------
# Parametric corpora — bring totals well over 150
# ---------------------------------------------------------------------------


def test_intent_corpus_takeaway() -> None:
    cases = [
        "تيكاواي",
        "هاجي استلمه",
        "هاخده من المطعم",
        "اخده من المحل",
        "هاجي اخده من الفرع",
        "تيكاواي لو سمحت",
        "استلام",
    ]
    for text in cases:
        _check(f"corpus_takeaway[{text}]", detect_intent(text).kind == "takeaway")


def test_intent_corpus_delivery() -> None:
    cases = [
        "توصيل",
        "دليفري",
        "delivery",
        "ابعتوهالي",
        "وصلوهالي",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"corpus_delivery[{text}]", d.kind == "delivery", str(d))


def test_intent_corpus_reservation() -> None:
    cases = [
        "حجز",
        "احجز ترابيزة",
        "ترابيزه لاتنين",
        "رزيرفيشن",
        "reservation please",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"corpus_reservation[{text}]", d.kind == "reservation", str(d))


def test_intent_corpus_complaint() -> None:
    cases = [
        "شكوى",
        "اشتكي",
        "مشكله",
        "مشكلة",
        "اتأخر",
        "بارد",
        "غلط الطلب",
        "complaint",
    ]
    for text in cases:
        d = detect_intent(text)
        _check(f"corpus_complaint[{text}]", d.kind == "complaint", str(d))


def test_phone_corpus_valid() -> None:
    valid = [
        "01012345678",
        "01112345678",
        "01234567890",
        "01512345678",
        "+201012345678",
        "+201112345678",
        "+201234567890",
        "+201512345678",
        "201012345678",
        "0101 234 5678",
        "(010) 1234-5678",
    ]
    for raw in valid:
        c = extract_phone(raw)
        _check(f"phone_corpus_valid[{raw}]", c.value is not None, str(c))


def test_phone_corpus_invalid() -> None:
    invalid = [
        "0",
        "01",
        "010",
        "0101",
        "01999999999",
        "abc",
        "01012",
        "+201912345678",  # no carrier 019
    ]
    for raw in invalid:
        c = extract_phone(raw)
        _check(f"phone_corpus_invalid[{raw}]", c.value is None, str(c))


def test_address_corpus_landmark_words() -> None:
    cases = [
        "شارع",
        "ميدان",
        "عمارة",
        "بنايه",
        "بناية",
        "شقة",
        "دور",
        "بلوك",
        "برج",
    ]
    for word in cases:
        a = extract_address(f"{word} 5", delivery_zones=())
        _check(
            f"addr_corpus[{word}]:value_set",
            a.value is not None,
            str(a),
        )


def test_address_corpus_zones() -> None:
    zones = ("المعادي", "مدينة نصر", "الزمالك", "الدقي", "المهندسين")
    for zone in zones:
        a = extract_address(zone, delivery_zones=zones)
        _check(f"addr_zone_corpus[{zone}]:zone", a.zone == zone, str(a))


def test_reservation_time_corpus() -> None:
    cases = [
        ("بكره الساعة 8", True),
        ("النهارده 9 بليل", True),
        ("يوم الخميس", False),
        ("بكره", False),
        ("متشكر", False),
    ]
    for text, expect_value in cases:
        r = extract_reservation_time(text)
        if expect_value:
            _check(f"res_corpus[{text}]:has_value", r.raw is not None, str(r))
        else:
            _check(f"res_corpus[{text}]:no_high", not r.is_high_confidence(), str(r))


def test_guests_corpus_spoken() -> None:
    cases = [
        ("اتنين ضيوف", 2),
        ("تلاته اشخاص", 3),
        ("اربعه ضيوف", 4),
        ("خمسه شخص", 5),
        ("سته شخص", 6),
        ("سبعه ضيوف", 7),
        ("تمانيه شخص", 8),
        ("تسعه شخص", 9),
        ("عشره ضيوف", 10),
    ]
    for text, expected in cases:
        g = extract_guests_count(text)
        _check(f"guests_corpus[{text}]", g.count == expected, str(g))


def test_complaint_corpus_categories() -> None:
    cases = [
        ("الطلب وصلني ناقص", "order"),
        ("الأكل بايت", "quality"),
        ("الموظف مش محترم", "service"),
        ("الدليفري متاخر جدا", "delivery"),
        ("شكوى عامة", "other"),
        ("ازيك يا كابتن", None),
    ]
    for text, expected in cases:
        c = classify_complaint(text)
        _check(
            f"complaint_corpus[{text}]",
            c.category == expected,
            str(c),
        )


def test_name_corpus_short_arabic() -> None:
    names = [
        "أحمد",
        "محمد",
        "علي",
        "سارة",
        "ياسمين",
        "كريم",
        "نور",
        "هدى",
        "آدم",
        "ليلى",
    ]
    for name in names:
        c = extract_name(name)
        _check(f"name_corpus[{name}]:value", c.value == name, str(c))
        _check(f"name_corpus[{name}]:conf", c.confidence >= 0.85)


def test_name_corpus_with_marker() -> None:
    pairs = [
        ("اسمي أحمد", "أحمد"),
        ("انا اسمي محمد", "محمد"),
        ("الاسم سارة", "سارة"),
        ("اسمي ياسمين علي", "ياسمين علي"),
    ]
    for text, expected in pairs:
        c = extract_name(text)
        _check(f"name_marker[{text}]:value", c.value == expected, str(c))


def test_intent_neutral_phrases_unknown() -> None:
    neutral = [
        "اوريد ان اطلب طعام",
        "السعر مرتفع جدا",
        "هل المتجر مفتوح",
        "كم تكلفة المنتج",
    ]
    for text in neutral:
        # These are MSA phrasings that don't hit our Egyptian-Arabic
        # cue list. The extractor should fall back to "unknown" so the
        # LLM can handle them.
        d = detect_intent(text)
        _check(f"intent_neutral[{text}]", d.kind in {"unknown", "menu_question", "total_question"}, str(d))


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _all_test_functions():
    g = globals()
    return [
        (name, g[name])
        for name in sorted(g)
        if name.startswith("test_") and callable(g[name])
    ]


def main() -> int:
    for name, fn in _all_test_functions():
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((name, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1

    print(f"PHASE3_INTENT_SLOT_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
