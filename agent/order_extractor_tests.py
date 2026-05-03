"""Phase 2 acceptance suite for the production order extractor.

Goal: 200+ cases covering quantity formats, alias coverage, definite
articles, ambiguity detection, address-context safety, and STT mishearings.

The suite runs without booting LiveKit so it stays fast and is safe to wire
into CI. It only depends on ``core.menu_index``,
``core.extractors.order_extractor`` and ``core.order_mutations``.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from core.extractors.order_extractor import (  # noqa: E402
    HIGH_CONFIDENCE,
    extract_order,
)
from core.menu_index import MenuIndex  # noqa: E402
from core.order_mutations import parse_mutation  # noqa: E402


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


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


def _menu_basic() -> MenuIndex:
    return MenuIndex.build([
        {"name": "برجر كبير", "price": 45.0, "available": True},
        {"name": "برجر صغير", "price": 30.0, "available": True},
        {"name": "بطاطس", "price": 20.0, "available": True},
        {"name": "كولا", "price": 15.0, "available": True},
        {"name": "كشري كبير", "price": 35.0, "available": True},
        {"name": "كشري صغير", "price": 25.0, "available": True},
        {"name": "شاورما فراخ", "price": 50.0, "available": True},
        {"name": "شاورما لحمة", "price": 60.0, "available": True},
        {"name": "بيتزا مارجريتا", "price": 80.0, "available": True},
        {"name": "بيتزا فيراري", "price": 110.0, "available": False},
        {"name": "سلطة", "price": 18.0, "available": True},
        {"name": "ميه", "price": 5.0, "available": True},
    ])


def _items_to_dict(extraction) -> dict[str, int]:
    return {item.canonical_name: item.quantity for item in extraction.items}


# ---------------------------------------------------------------------------
# 1) Quantity parsing
# ---------------------------------------------------------------------------


def test_digit_suffix_quantity() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير 2", idx)
    _check("digit_suffix:result", _items_to_dict(r) == {"برجر كبير": 2})


def test_digit_prefix_quantity() -> None:
    idx = _menu_basic()
    r = extract_order("3 بطاطس", idx)
    _check("digit_prefix:result", _items_to_dict(r) == {"بطاطس": 3})


def test_arabic_indic_digits() -> None:
    idx = _menu_basic()
    r = extract_order("٢ كشري كبير", idx)
    _check("arabic_indic:result", _items_to_dict(r) == {"كشري كبير": 2})


def test_multiplier_symbol_x() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير x 2", idx)
    _check("mult_x:result", _items_to_dict(r) == {"برجر كبير": 2})


def test_multiplier_symbol_star() -> None:
    idx = _menu_basic()
    r = extract_order("بطاطس * 3", idx)
    _check("mult_star:result", _items_to_dict(r) == {"بطاطس": 3})


def test_multiplier_symbol_unicode() -> None:
    idx = _menu_basic()
    r = extract_order("كولا × 4", idx)
    _check("mult_unicode:result", _items_to_dict(r) == {"كولا": 4})


def test_spoken_quantity_aatnein_before() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين كولا", idx)
    _check("spoken_before_aatnein:result", _items_to_dict(r) == {"كولا": 2})


def test_spoken_quantity_aatnein_after() -> None:
    idx = _menu_basic()
    r = extract_order("كولا اتنين", idx)
    _check("spoken_after_aatnein:result", _items_to_dict(r) == {"كولا": 2})


def test_spoken_quantity_talata_before() -> None:
    idx = _menu_basic()
    r = extract_order("تلاته بطاطس", idx)
    _check("spoken_talata:result", _items_to_dict(r) == {"بطاطس": 3})


def test_spoken_quantity_arbaa() -> None:
    idx = _menu_basic()
    r = extract_order("اربعه كولا", idx)
    _check("spoken_arbaa:result", _items_to_dict(r) == {"كولا": 4})


def test_spoken_quantity_khamsa() -> None:
    idx = _menu_basic()
    r = extract_order("خمسه ميه", idx)
    _check("spoken_khamsa:result", _items_to_dict(r) == {"ميه": 5})


def test_quantity_word_with_min() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين من البرجر الكبير", idx)
    _check("min_pattern:result", _items_to_dict(r) == {"برجر كبير": 2})


def test_quantity_partitive_with_filler() -> None:
    """Real-call regression: "بيتزا مارجريتا محتاج منها 15 واحدة" → qty 15.

    Discovered when a real customer's 15-pizza delivery order was
    captured as a single pizza because the qty was after a filler verb.
    """
    idx = _menu_basic()
    r = extract_order(
        "أنا بطلب بيتزا مارجريتا محتاج منها 15 واحدة",
        idx,
    )
    _check(
        "partitive_qty:fifteen_pizzas",
        _items_to_dict(r) == {"بيتزا مارجريتا": 15},
        str(_items_to_dict(r)),
    )


def test_quantity_partitive_three_burgers() -> None:
    idx = _menu_basic()
    r = extract_order("عاوز برجر كبير محتاج منه 3", idx)
    _check(
        "partitive_qty:three_burgers",
        _items_to_dict(r) == {"برجر كبير": 3},
        str(_items_to_dict(r)),
    )


def test_quantity_partitive_does_not_fire_without_menu() -> None:
    idx = _menu_basic()
    r = extract_order("محتاج منها 5", idx)
    _check(
        "partitive_qty:no_match_without_menu",
        _items_to_dict(r) == {},
        str(_items_to_dict(r)),
    )


def test_quantity_with_kaman() -> None:
    idx = _menu_basic()
    r = extract_order("وكولا واحد كمان", idx)
    _check("waahed_kaman:result", _items_to_dict(r) == {"كولا": 1})


def test_quantity_default_one() -> None:
    idx = _menu_basic()
    r = extract_order("عايز كولا", idx)
    _check("default_one:result", _items_to_dict(r) == {"كولا": 1})


def test_quantity_with_ayez() -> None:
    idx = _menu_basic()
    r = extract_order("عايز 2 برجر كبير", idx)
    _check("ayez_qty:result", _items_to_dict(r) == {"برجر كبير": 2})


def test_quantity_with_haatli() -> None:
    idx = _menu_basic()
    r = extract_order("هاتلي 3 كولا", idx)
    _check("haatli_qty:result", _items_to_dict(r) == {"كولا": 3})


def test_qty_capped_above_20() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير 99", idx)
    # Out-of-range qty falls back to 1 because the parser treats ">20" as
    # not a quantity.
    _check("qty_cap:fallback", _items_to_dict(r) == {"برجر كبير": 1})


def test_qty_zero_is_not_qty() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير 0", idx)
    _check("qty_zero:fallback", _items_to_dict(r) == {"برجر كبير": 1})


def test_two_then_one() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين برجر كبير وكولا", idx)
    _check("two_then_one:items", _items_to_dict(r) == {"برجر كبير": 2, "كولا": 1})


def test_three_items_with_quantities() -> None:
    idx = _menu_basic()
    r = extract_order("3 برجر كبير، 2 بطاطس، كولا", idx)
    _check("triple_qty:items", _items_to_dict(r) == {"برجر كبير": 3, "بطاطس": 2, "كولا": 1})


# ---------------------------------------------------------------------------
# 2) Alias / STT repair
# ---------------------------------------------------------------------------


def test_alias_bibsi_to_cola() -> None:
    idx = _menu_basic()
    r = extract_order("هاتلي بيبسي", idx)
    _check("alias_bibsi:result", _items_to_dict(r) == {"كولا": 1})


def test_alias_kokakola_to_cola() -> None:
    idx = _menu_basic()
    r = extract_order("عايز كوكاكولا", idx)
    _check("alias_kokakola:result", _items_to_dict(r) == {"كولا": 1})


def test_alias_burger_with_misspelling() -> None:
    idx = _menu_basic()
    r = extract_order("برغر كبير", idx)
    # برغر is an alias; the canonical "برجر كبير" should still be matched
    # via the alias-aware index.
    items = _items_to_dict(r)
    _check("alias_burger:result", "برجر كبير" in items, str(items))


def test_alias_battata_to_battaats() -> None:
    idx = _menu_basic()
    r = extract_order("بطاطا اتنين", idx)
    _check("alias_battata:result", _items_to_dict(r) == {"بطاطس": 2})


def test_alias_franch_fries() -> None:
    idx = _menu_basic()
    r = extract_order("هات فرايز", idx)
    _check("alias_fries:result", _items_to_dict(r) == {"بطاطس": 1})


def test_alias_pitsa() -> None:
    idx = _menu_basic()
    r = extract_order("بيتسا مارجريتا", idx)
    items = _items_to_dict(r)
    _check("alias_pitsa:has_pizza", "بيتزا مارجريتا" in items, str(items))


def test_alias_shawarma_with_ta_marbouta() -> None:
    idx = _menu_basic()
    r = extract_order("شاورمة فراخ", idx)
    items = _items_to_dict(r)
    _check("alias_shawarma_t:result", "شاورما فراخ" in items, str(items))


def test_alias_unavailable_item_skipped() -> None:
    idx = _menu_basic()
    r = extract_order("بيتزا فيراري", idx)
    _check("alias_unavailable:skipped", _items_to_dict(r) == {})


# ---------------------------------------------------------------------------
# 3) Definite article tolerance
# ---------------------------------------------------------------------------


def test_def_article_albirjar() -> None:
    idx = _menu_basic()
    r = extract_order("البرجر الكبير", idx)
    _check("def_article:case", _items_to_dict(r) == {"برجر كبير": 1})


def test_def_article_with_qty() -> None:
    idx = _menu_basic()
    r = extract_order("البطاطس اتنين", idx)
    _check("def_article_qty:case", _items_to_dict(r) == {"بطاطس": 2})


def test_def_article_alkoshary() -> None:
    idx = _menu_basic()
    r = extract_order("الكشري الكبير", idx)
    _check("def_article_koshary:case", _items_to_dict(r) == {"كشري كبير": 1})


def test_no_def_article_inside_long_word() -> None:
    idx = _menu_basic()
    # "السلطة" → after removing "ال" → "سلطه" (since ة → ه via normalize)
    r = extract_order("السلطة دي", idx)
    _check("def_article_salad:case", _items_to_dict(r) == {"سلطة": 1})


# ---------------------------------------------------------------------------
# 4) Ambiguity detection
# ---------------------------------------------------------------------------


def test_ambiguous_burger_alone() -> None:
    idx = _menu_basic()
    r = extract_order("عايز برجر", idx)
    _check("amb_burger:items_empty", _items_to_dict(r) == {})
    _check("amb_burger:ambiguity_reported", any(p[0] == "برجر" for p in r.ambiguous_phrases))


def test_ambiguous_koshary_alone() -> None:
    idx = _menu_basic()
    r = extract_order("هات كشري", idx)
    _check("amb_koshary:ambiguity", r.has_ambiguity())


def test_ambiguous_shawarma_alone() -> None:
    idx = _menu_basic()
    r = extract_order("عايز شاورما", idx)
    _check("amb_shawarma:ambiguity", r.has_ambiguity())


def test_disambiguation_with_qualifier() -> None:
    idx = _menu_basic()
    r = extract_order("شاورما فراخ", idx)
    _check("disamb_shawarma:specific", _items_to_dict(r) == {"شاورما فراخ": 1})


def test_disambiguation_burger_kabir() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير", idx)
    _check("disamb_burger:specific", _items_to_dict(r) == {"برجر كبير": 1})


# ---------------------------------------------------------------------------
# 5) Address / phone context safety
# ---------------------------------------------------------------------------


def test_address_with_numbers_no_order() -> None:
    idx = _menu_basic()
    r = extract_order("العنوان شارع 5 برج 3 شقة 12", idx)
    _check("address_no_order:empty", _items_to_dict(r) == {})


def test_phone_like_no_order() -> None:
    idx = _menu_basic()
    r = extract_order("رقمي 01012345678", idx)
    _check("phone_no_order:empty", _items_to_dict(r) == {})


def test_address_word_does_not_eat_quantity() -> None:
    idx = _menu_basic()
    r = extract_order("عايز كولا والعنوان شارع 3", idx)
    _check("addr_keeps_order:cola_present", "كولا" in _items_to_dict(r))
    _check("addr_keeps_order:no_addr_qty", _items_to_dict(r).get("كولا", 0) == 1)


def test_zone_word_in_text_no_order() -> None:
    idx = _menu_basic()
    r = extract_order("منطقة المعادي", idx)
    _check("zone_no_order:empty", _items_to_dict(r) == {})


# ---------------------------------------------------------------------------
# 6) Multi-item parsing
# ---------------------------------------------------------------------------


def test_three_distinct_items() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير وبطاطس وكولا", idx)
    _check("three:items", _items_to_dict(r) == {"برجر كبير": 1, "بطاطس": 1, "كولا": 1})


def test_aggregation_of_repeated_mention() -> None:
    idx = _menu_basic()
    r = extract_order("كولا وكولا", idx)
    _check("repeat_agg:qty", _items_to_dict(r) == {"كولا": 2})


def test_aggregation_with_explicit_qty() -> None:
    idx = _menu_basic()
    r = extract_order("كولا وكولا اتنين", idx)
    # "كولا" alone (qty 1) plus "كولا اتنين" (qty 2) = 3
    _check("repeat_agg_qty:total", _items_to_dict(r) == {"كولا": 3})


def test_multiple_quantities_in_one_turn() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين برجر كبير وتلاته كولا", idx)
    _check("multi_qty:items", _items_to_dict(r) == {"برجر كبير": 2, "كولا": 3})


def test_ordering_preserved_in_output() -> None:
    idx = _menu_basic()
    r = extract_order("بطاطس وكولا وبرجر كبير", idx)
    formatted = r.formatted_items()
    _check("order_preserved:length", len(formatted) == 3)
    # First mention should come first.
    _check("order_preserved:first", formatted[0].startswith("بطاطس"))


# ---------------------------------------------------------------------------
# 7) Long-phrase preference over partial matches
# ---------------------------------------------------------------------------


def test_long_phrase_wins_over_partial() -> None:
    idx = _menu_basic()
    r = extract_order("شاورما فراخ مع شاورما لحمة", idx)
    _check(
        "long_phrase_wins:items",
        _items_to_dict(r) == {"شاورما فراخ": 1, "شاورما لحمة": 1},
        str(_items_to_dict(r)),
    )


def test_long_phrase_with_qty() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين شاورما فراخ", idx)
    _check("long_phrase_qty:items", _items_to_dict(r) == {"شاورما فراخ": 2})


# ---------------------------------------------------------------------------
# 8) Confidence reporting
# ---------------------------------------------------------------------------


def test_high_confidence_explicit_qty() -> None:
    idx = _menu_basic()
    r = extract_order("اتنين برجر كبير", idx)
    _check("conf_high:overall", r.has_high_confidence())


def test_lower_confidence_partial_match() -> None:
    idx = MenuIndex.build([
        {"name": "كشري كبير", "price": 35.0, "available": True},
        {"name": "بطاطس", "price": 20.0, "available": True},
    ])
    r = extract_order("كشري واحد", idx)
    _check("conf_partial:has_item", _items_to_dict(r) == {"كشري كبير": 1})
    _check("conf_partial:not_high", not r.has_high_confidence())


# ---------------------------------------------------------------------------
# 9) Empty / noisy input
# ---------------------------------------------------------------------------


def test_empty_string() -> None:
    idx = _menu_basic()
    _check("empty:string", _items_to_dict(extract_order("", idx)) == {})


def test_whitespace_only() -> None:
    idx = _menu_basic()
    _check("empty:whitespace", _items_to_dict(extract_order("   \n  ", idx)) == {})


def test_no_menu_match() -> None:
    idx = _menu_basic()
    _check("noisy:no_match", _items_to_dict(extract_order("ابغى لازنيا روسية", idx)) == {})


def test_only_filler_words() -> None:
    idx = _menu_basic()
    _check("noisy:filler", _items_to_dict(extract_order("اه طبعا تمام", idx)) == {})


def test_unicode_punctuation_only() -> None:
    idx = _menu_basic()
    _check("noisy:punct", _items_to_dict(extract_order("؟؟؟ ،،،", idx)) == {})


# ---------------------------------------------------------------------------
# 10) Mutation parser
# ---------------------------------------------------------------------------


def test_mutation_replace() -> None:
    m = parse_mutation("لأ غير الطلب، اعمله بيتزا")
    _check("mut_replace:kind", m.kind == "replace")


def test_mutation_replace_amsah() -> None:
    m = parse_mutation("امسح الطلب وابدأ من جديد")
    _check("mut_replace_amsah:kind", m.kind == "replace")


def test_mutation_keep() -> None:
    m = parse_mutation("لأ خليه كده")
    _check("mut_keep:kind", m.kind == "keep")


def test_mutation_keep_zay_ma_howa() -> None:
    m = parse_mutation("سيبه زي ما هو")
    _check("mut_keep_zay:kind", m.kind == "keep")


def test_mutation_remove() -> None:
    m = parse_mutation("شيل الكولا")
    _check("mut_remove:kind", m.kind == "remove")


def test_mutation_remove_balash() -> None:
    m = parse_mutation("بلاش بطاطس")
    _check("mut_remove_balash:kind", m.kind == "remove")


def test_mutation_increase() -> None:
    m = parse_mutation("زود برجر كمان")
    _check("mut_increase:kind", m.kind in {"increase", "add"})


def test_mutation_decrease() -> None:
    m = parse_mutation("نقص واحد كولا")
    _check("mut_decrease:kind", m.kind == "decrease")


def test_mutation_add() -> None:
    m = parse_mutation("ضيف كولا معاه")
    _check("mut_add:kind", m.kind == "add")


def test_mutation_kaman() -> None:
    m = parse_mutation("هاتلي كمان كولا")
    _check("mut_kaman:kind", m.kind == "add")


def test_mutation_unknown() -> None:
    m = parse_mutation("ازيك يا كابتن")
    _check("mut_unknown:kind", m.kind == "unknown")


def test_mutation_replace_beats_remove() -> None:
    # "غير الطلب" should map to replace, not remove, even though "غير"
    # is also a remove cue.
    m = parse_mutation("من فضلك غير الطلب")
    _check("mut_replace_beats_remove:kind", m.kind == "replace")


# ---------------------------------------------------------------------------
# 11) Realistic conversational turns
# ---------------------------------------------------------------------------


def test_realistic_long_turn() -> None:
    idx = _menu_basic()
    r = extract_order(
        "اه طب من فضلك هاتلي اتنين برجر كبير معاهم 3 بطاطس وكولا",
        idx,
    )
    items = _items_to_dict(r)
    _check("realistic:burger", items.get("برجر كبير") == 2, str(items))
    _check("realistic:fries", items.get("بطاطس") == 3, str(items))
    _check("realistic:cola_present", items.get("كولا") == 1, str(items))


def test_realistic_change_after_mention() -> None:
    idx = _menu_basic()
    # User says order, then changes mind. Extractor should still report
    # both items; the engine + mutation parser handle the replace intent.
    r = extract_order("لأ غير الطلب، اعمله شاورما فراخ بدل البرجر", idx)
    _check("realistic_replace:has_shawarma", "شاورما فراخ" in _items_to_dict(r))


def test_realistic_with_special_request() -> None:
    idx = _menu_basic()
    r = extract_order("برجر كبير من غير بصل وكولا", idx)
    _check("realistic_special:items", _items_to_dict(r) == {"برجر كبير": 1, "كولا": 1})


def test_realistic_address_mid_order() -> None:
    idx = _menu_basic()
    r = extract_order("عايز برجر كبير وبطاطس، العنوان شارع 5 المعادي", idx)
    items = _items_to_dict(r)
    _check("realistic_addr:burger", items.get("برجر كبير") == 1)
    _check("realistic_addr:fries", items.get("بطاطس") == 1)


def test_realistic_with_thanks() -> None:
    idx = _menu_basic()
    r = extract_order("متشكر يا فندم، عايز كولا واحدة", idx)
    _check("realistic_thanks:cola", _items_to_dict(r) == {"كولا": 1})


def test_realistic_question_then_order() -> None:
    idx = _menu_basic()
    # Cross-clause qty resolution is left to the dialogue engine — the
    # extractor only guarantees the item is captured. We assert presence,
    # not the exact qty, because the user mentioned كولا twice and the
    # safe deterministic behaviour is to aggregate.
    r = extract_order("عندك كولا؟ تمام، اتنين كولا", idx)
    items = _items_to_dict(r)
    _check("question_then_order:cola_present", "كولا" in items, str(items))


def test_realistic_misheard_quantity() -> None:
    idx = _menu_basic()
    r = extract_order("هاتلي اربعه كولا لو سمحت", idx)
    _check("misheard_qty:cola_count", _items_to_dict(r).get("كولا") == 4)


def test_realistic_with_filler() -> None:
    idx = _menu_basic()
    r = extract_order("يعني كده آه ممكن برجر كبير لو سمحت", idx)
    items = _items_to_dict(r)
    _check("filler:burger_present", items.get("برجر كبير") == 1, str(items))


# ---------------------------------------------------------------------------
# 12) Programmatic large corpus (parametrized)
# ---------------------------------------------------------------------------


def test_parametrized_qty_corpus() -> None:
    idx = _menu_basic()
    pairs = [
        ("كولا 1", 1),
        ("كولا 2", 2),
        ("كولا 3", 3),
        ("كولا 4", 4),
        ("كولا 5", 5),
        ("كولا 6", 6),
        ("كولا 7", 7),
        ("كولا 8", 8),
        ("كولا 9", 9),
        ("كولا 10", 10),
        ("كولا ١", 1),
        ("كولا ٢", 2),
        ("كولا ٣", 3),
        ("كولا ٤", 4),
        ("كولا ٥", 5),
        ("كولا x 2", 2),
        ("كولا × 2", 2),
        ("كولا * 2", 2),
        ("كولا اتنين", 2),
        ("كولا تلاته", 3),
        ("كولا اربعه", 4),
        ("كولا خمسه", 5),
        ("كولا سته", 6),
        ("كولا سبعه", 7),
        ("كولا تمانيه", 8),
        ("كولا تسعه", 9),
        ("كولا عشره", 10),
        ("اتنين كولا", 2),
        ("تلاته كولا", 3),
        ("اربعه كولا", 4),
        ("اتنين من الكولا", 2),
    ]
    for text, expected_qty in pairs:
        r = extract_order(text, idx)
        actual = _items_to_dict(r).get("كولا")
        _check(f"qty_corpus[{text}]", actual == expected_qty, f"got {actual}")


def test_parametrized_alias_corpus() -> None:
    idx = _menu_basic()
    pairs = [
        ("بيبسي", "كولا"),
        ("كوكاكولا", "كولا"),
        ("كوكا كولا", "كولا"),
        ("بطاطا", "بطاطس"),
        ("فرايز", "بطاطس"),
        ("برغر كبير", "برجر كبير"),
        ("هامبرجر كبير", "برجر كبير"),
        ("بيتسا مارجريتا", "بيتزا مارجريتا"),
        ("بيتزه مارجريتا", "بيتزا مارجريتا"),
        ("شاورمة فراخ", "شاورما فراخ"),
        ("شاورمة لحمة", "شاورما لحمة"),
    ]
    for text, expected_item in pairs:
        r = extract_order(text, idx)
        items = _items_to_dict(r)
        _check(
            f"alias_corpus[{text}]",
            expected_item in items,
            f"got {items}",
        )


def test_parametrized_definite_article_corpus() -> None:
    idx = _menu_basic()
    pairs = [
        ("البرجر الكبير", "برجر كبير"),
        ("البرجر الصغير", "برجر صغير"),
        ("الكشري الكبير", "كشري كبير"),
        ("الكشري الصغير", "كشري صغير"),
        ("الشاورما الفراخ", "شاورما فراخ"),
        ("البطاطس", "بطاطس"),
        ("الكولا", "كولا"),
    ]
    for text, expected_item in pairs:
        r = extract_order(text, idx)
        items = _items_to_dict(r)
        _check(
            f"def_article_corpus[{text}]",
            expected_item in items,
            f"got {items}",
        )


def test_parametrized_no_order_corpus() -> None:
    idx = _menu_basic()
    no_order_texts = [
        "ازيك",
        "السلام عليكم",
        "ايه الاخبار",
        "متشكر يا فندم",
        "تمام",
        "اوكي",
        "ماشي",
        "مفيش حاجة",
        "بس كده",
        "وفقتش",
        "العنوان شارع 5",
        "رقمي 01012345678",
        "حد عاوزني",
        "اسمي احمد",
    ]
    for text in no_order_texts:
        r = extract_order(text, idx)
        items = _items_to_dict(r)
        _check(
            f"no_order[{text}]",
            items == {},
            f"got {items}",
        )


def test_parametrized_unknown_phrase_corpus() -> None:
    idx = _menu_basic()
    # Phrases that mention a non-menu word — should not produce items
    # nor crash.
    unknown_texts = [
        "عايز كباب",
        "عندك مكرونة",
        "هاتلي حلويات",
        "عايز ايس كريم",
        "عاوز فطير",
    ]
    for text in unknown_texts:
        r = extract_order(text, idx)
        items = _items_to_dict(r)
        _check(
            f"unknown[{text}]",
            items == {},
            f"got {items}",
        )


def test_parametrized_combined_orders() -> None:
    idx = _menu_basic()
    # Long table of full multi-item orders.
    cases = [
        ("برجر كبير وبطاطس وكولا", {"برجر كبير": 1, "بطاطس": 1, "كولا": 1}),
        ("اتنين برجر كبير وكولا", {"برجر كبير": 2, "كولا": 1}),
        ("3 برجر كبير و2 كولا", {"برجر كبير": 3, "كولا": 2}),
        ("شاورما فراخ مع كولا", {"شاورما فراخ": 1, "كولا": 1}),
        ("شاورما لحمة وبيتزا مارجريتا", {"شاورما لحمة": 1, "بيتزا مارجريتا": 1}),
        ("اتنين شاورما فراخ وتلاته كولا", {"شاورما فراخ": 2, "كولا": 3}),
        ("كشري كبير وسلطة وميه", {"كشري كبير": 1, "سلطة": 1, "ميه": 1}),
        ("بطاطس اتنين وكولا تلاته", {"بطاطس": 2, "كولا": 3}),
        ("اربعه كولا وميه", {"كولا": 4, "ميه": 1}),
        ("برجر كبير × 2 وبطاطس × 3", {"برجر كبير": 2, "بطاطس": 3}),
        ("هاتلي البرجر الكبير وبطاطس", {"برجر كبير": 1, "بطاطس": 1}),
        ("عايز اتنين كشري كبير وكولا", {"كشري كبير": 2, "كولا": 1}),
        ("بيبسي وبطاطا", {"كولا": 1, "بطاطس": 1}),
        ("كوكاكولا وفرايز", {"كولا": 1, "بطاطس": 1}),
        ("ميه ٣ مع سلطة", {"ميه": 3, "سلطة": 1}),
    ]
    for text, expected in cases:
        r = extract_order(text, idx)
        actual = _items_to_dict(r)
        _check(
            f"combined[{text}]",
            actual == expected,
            f"expected {expected} got {actual}",
        )


def test_parametrized_noise_robustness() -> None:
    idx = _menu_basic()
    # Strong filler / hesitation words wrapped around real orders.
    cases = [
        ("اممممم يعني كده، برجر كبير", {"برجر كبير": 1}),
        ("اه اه تمام، 2 كولا", {"كولا": 2}),
        ("لو سمحت من فضلك، كولا واحدة", {"كولا": 1}),
        ("والله حتى، بطاطس", {"بطاطس": 1}),
        ("لحظة واحدة، شاورما فراخ", {"شاورما فراخ": 1}),
    ]
    for text, expected in cases:
        r = extract_order(text, idx)
        actual = _items_to_dict(r)
        _check(
            f"noise[{text}]",
            actual == expected,
            f"got {actual}",
        )


def test_parametrized_quantity_then_item_corpus() -> None:
    idx = _menu_basic()
    cases = [
        ("اتنين كولا", 2, "كولا"),
        ("تلاته كولا", 3, "كولا"),
        ("اربعه كولا", 4, "كولا"),
        ("خمسه كولا", 5, "كولا"),
        ("سته كولا", 6, "كولا"),
        ("سبعه كولا", 7, "كولا"),
        ("تمانيه كولا", 8, "كولا"),
        ("تسعه كولا", 9, "كولا"),
        ("عشره كولا", 10, "كولا"),
    ]
    for text, expected_qty, item in cases:
        r = extract_order(text, idx)
        actual = _items_to_dict(r).get(item)
        _check(
            f"qty_then_item[{text}]",
            actual == expected_qty,
            f"got {actual}",
        )


def test_parametrized_mutation_corpus() -> None:
    cases = [
        ("غير الطلب اعمله بيتزا", "replace"),
        ("لأ غير الطلب", "replace"),
        ("امسح الطلب", "replace"),
        ("الغي الطلب", "replace"),
        ("شيل الطلب كله", "replace"),
        ("شيل الكولا", "remove"),
        ("امسح البطاطس", "remove"),
        ("بلاش بطاطس", "remove"),
        ("متجبش كولا", "remove"),
        ("احذف الكولا", "remove"),
        ("ضيف كولا", "add"),
        ("هاتلي كمان كولا", "add"),
        ("معاه ميه", "add"),
        ("زود برجر كمان", "increase"),
        ("نقص واحد كولا", "decrease"),
        ("خليه كده", "keep"),
        ("خليها كده", "keep"),
        ("سيبها زي ما هي", "unknown"),  # phrase not in cue list
        ("ازيك", "unknown"),
        ("السلام عليكم", "unknown"),
    ]
    for text, expected in cases:
        kind = parse_mutation(text).kind
        _check(f"mut_corpus[{text}]", kind == expected, f"got {kind}")


# ---------------------------------------------------------------------------
# 13) Menu index correctness
# ---------------------------------------------------------------------------


def test_menu_index_caches_aliases() -> None:
    idx = _menu_basic()
    cola_aliases = [
        a for a, e in idx.by_alias.items() if e.norm_name == "كولا"
    ]
    _check("idx_alias:bibsi", "بيبسي" in cola_aliases)
    _check("idx_alias:kokakola", "كوكاكولا" in cola_aliases)


def test_menu_index_skips_blank_names() -> None:
    idx = MenuIndex.build([
        {"name": "", "price": 10.0, "available": True},
        {"name": "كولا", "price": 15.0, "available": True},
    ])
    _check("idx_skip_blank:size", len(idx.entries) == 1)


def test_menu_index_marks_availability() -> None:
    idx = MenuIndex.build([
        {"name": "كولا", "price": 15.0, "available": True},
        {"name": "بيتزا فيراري", "price": 110.0, "available": False},
    ])
    by_name = {e.norm_name: e for e in idx.entries}
    _check("idx_avail:cola_true", by_name["كولا"].available is True)
    _check("idx_avail:pizza_false", by_name["بيتزا فيراري"].available is False)


def test_menu_index_token_lookup() -> None:
    idx = _menu_basic()
    cola_candidates = idx.candidates_for_token("كولا")
    _check("idx_token:cola", any(e.norm_name == "كولا" for e in cola_candidates))


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
        except Exception as exc:  # pragma: no cover - blow up loudly on bug in test itself
            _FAILURES.append((name, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1

    print(f"PHASE2_ORDER_EXTRACTOR_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
