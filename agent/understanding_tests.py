"""Schema-driven tests for ``core.understanding``.

These exercise the orchestration layer (parsing, caching, fallback)
plus the ``programmatic_provider`` mock. They never touch the network.

Real-LLM smoke tests live in ``understanding_smoke.py`` and are gated
by the ``LIVE_LLM_TESTS=1`` env var so CI does not burn quota.
"""

from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from core.understanding import (  # noqa: E402
    OrderItemMention,
    TurnContext,
    TurnUnderstanding,
    UnderstandingService,
    parse_understanding,
)
from core.understanding_mock import (  # noqa: E402
    ScriptedProvider,
    programmatic_provider,
    script,
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


_MENU = (
    {"name": "بيتزا مارجريتا", "price": 80.0, "available": True},
    {"name": "برجر كبير", "price": 45.0, "available": True},
    {"name": "كولا", "price": 20.0, "available": True},
    {"name": "بطاطس", "price": 20.0, "available": True},
)


def _ctx(text: str, *, flow: str = "delivery") -> TurnContext:
    return TurnContext(
        user_text=text,
        flow=flow,
        menu_items=_MENU,
        delivery_zones=("المعادي", "مدينة نصر"),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_minimal() -> None:
    raw = json.dumps({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_minimal:intent", u.intent == "delivery")
    _check("parse_minimal:confidence", u.intent_confidence == "high")
    _check("parse_minimal:items_empty", u.order_items == ())


def test_parse_with_items() -> None:
    raw = json.dumps({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "add",
        "order_items": [
            {"item_name": "بيتزا مارجريتا", "quantity": 15, "evidence": "محتاج منها 15"},
            {"item_name": "كولا", "quantity": 2},
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_items:count", len(u.order_items) == 2)
    _check("parse_items:first_qty", u.order_items[0].quantity == 15)
    _check("parse_items:first_name", u.order_items[0].item_name == "بيتزا مارجريتا")
    _check("parse_items:second_default_evidence", u.order_items[1].evidence == "")


def test_parse_invalid_intent_falls_back_to_unknown() -> None:
    raw = json.dumps({
        "intent": "totally_made_up",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_invalid_intent:unknown", u.intent == "unknown")


def test_parse_invalid_quantity_normalized_to_one() -> None:
    raw = json.dumps({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "add",
        "order_items": [
            {"item_name": "كولا", "quantity": -3},
            {"item_name": "بطاطس", "quantity": "two"},
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_invalid_qty:cola_one", u.order_items[0].quantity == 1)
    _check("parse_invalid_qty:fries_one", u.order_items[1].quantity == 1)


def test_parse_malformed_json_returns_fallback() -> None:
    u = parse_understanding("{not json}")
    _check("parse_malformed:unknown", u.intent == "unknown")
    _check("parse_malformed:has_error", u.error.startswith("parse:"))


def test_parse_empty_returns_fallback() -> None:
    u = parse_understanding("")
    _check("parse_empty:unknown", u.intent == "unknown")
    _check("parse_empty:error", u.error == "empty")


def test_parse_skips_invalid_items() -> None:
    raw = json.dumps({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "add",
        "order_items": [
            {"item_name": "", "quantity": 1},  # empty name → drop
            "not_a_dict",
            {"item_name": "كولا", "quantity": 2},
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_skip_invalid:count", len(u.order_items) == 1)
    _check("parse_skip_invalid:kept_cola", u.order_items[0].item_name == "كولا")


def test_parse_complaint_category_validated() -> None:
    raw = json.dumps({
        "intent": "complaint",
        "intent_confidence": "high",
        "mutation": "none",
        "complaint_text": "الأكل بارد",
        "complaint_category": "quality",
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_complaint:category", u.complaint_category == "quality")
    _check("parse_complaint:text", u.complaint_text == "الأكل بارد")


def test_parse_complaint_invalid_category_dropped() -> None:
    raw = json.dumps({
        "intent": "complaint",
        "intent_confidence": "high",
        "mutation": "none",
        "complaint_category": "evil",
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_complaint_bad_cat:none", u.complaint_category is None)


def test_parse_guests_count_clamped() -> None:
    raw = json.dumps({
        "intent": "reservation",
        "intent_confidence": "high",
        "mutation": "none",
        "guests_count": 1000,  # absurd → reject
        "is_confirming": False,
        "is_denying": False,
    })
    u = parse_understanding(raw)
    _check("parse_guests_clamp:none", u.guests_count is None)


# ---------------------------------------------------------------------------
# Service: cache + fallback
# ---------------------------------------------------------------------------


def test_service_cache_hit_returns_cached() -> None:
    provider = script({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    svc = UnderstandingService(provider=provider)
    ctx = _ctx("هاتلي بيبسي")
    first = svc.extract(ctx)
    _check("cache:first_llm", first.source == "llm", str(first))
    second = svc.extract(ctx)
    _check("cache:second_cache", second.source == "cache", str(second))
    _check("cache:provider_called_once", len(provider.calls) == 1)


def test_service_cache_misses_on_different_text() -> None:
    provider = script(
        {"intent": "delivery", "intent_confidence": "high", "mutation": "none",
         "is_confirming": False, "is_denying": False},
        {"intent": "takeaway", "intent_confidence": "high", "mutation": "none",
         "is_confirming": False, "is_denying": False},
    )
    svc = UnderstandingService(provider=provider)
    a = svc.extract(_ctx("توصيل"))
    b = svc.extract(_ctx("استلام"))
    _check("cache_miss:a_intent", a.intent == "delivery")
    _check("cache_miss:b_intent", b.intent == "takeaway")


def test_service_provider_failure_returns_fallback() -> None:
    def boom(_ctx: TurnContext) -> str:
        raise TimeoutError("provider timeout")

    svc = UnderstandingService(provider=boom)
    u = svc.extract(_ctx("هاتلي كولا"))
    _check("provider_fail:unknown", u.intent == "unknown")
    _check("provider_fail:source", u.source == "provider_error")
    _check("provider_fail:error_set", "TimeoutError" in u.error)


def test_service_no_provider_returns_no_provider_source() -> None:
    svc = UnderstandingService(provider=None)
    u = svc.extract(_ctx("هاتلي كولا"))
    _check("no_provider:unknown", u.intent == "unknown")
    _check("no_provider:source", u.source == "no_provider")


def test_service_empty_text_short_circuits() -> None:
    provider = ScriptedProvider()  # would raise if called
    svc = UnderstandingService(provider=provider)
    u = svc.extract(_ctx(""))
    _check("empty_text:unknown", u.intent == "unknown")
    _check("empty_text:source", u.source == "empty")
    _check("empty_text:no_provider_call", len(provider.calls) == 0)


def test_service_records_extraction_latency() -> None:
    provider = script({
        "intent": "delivery", "intent_confidence": "high", "mutation": "none",
        "is_confirming": False, "is_denying": False,
    })
    svc = UnderstandingService(provider=provider)
    u = svc.extract(_ctx("توصيل"))
    _check("latency:non_negative", u.extraction_ms >= 0)


def test_service_cache_eviction_lru_ish() -> None:
    """Cache holds at most ``cache_size`` entries."""
    def echo(ctx: TurnContext) -> str:
        return json.dumps({
            "intent": "delivery", "intent_confidence": "high", "mutation": "none",
            "is_confirming": False, "is_denying": False,
        })
    svc = UnderstandingService(provider=echo, cache_size=3)
    for i in range(5):
        svc.extract(_ctx(f"text_{i}"))
    _check("cache_evict:size_capped", len(svc.cache) == 3)


# ---------------------------------------------------------------------------
# Programmatic mock — sanity checks
# ---------------------------------------------------------------------------


def test_mock_intent_takeaway() -> None:
    raw = programmatic_provider(_ctx("تيكاواي لو سمحت", flow="greeter"))
    u = parse_understanding(raw)
    _check("mock_takeaway:intent", u.intent == "takeaway")


def test_mock_intent_delivery() -> None:
    raw = programmatic_provider(_ctx("عايز توصيل لو سمحت", flow="greeter"))
    u = parse_understanding(raw)
    _check("mock_delivery:intent", u.intent == "delivery")


def test_mock_intent_complaint() -> None:
    raw = programmatic_provider(_ctx("عندي شكوى الأكل بارد", flow="greeter"))
    u = parse_understanding(raw)
    _check("mock_complaint:intent", u.intent == "complaint")
    _check("mock_complaint:category", u.complaint_category == "quality")


def test_mock_orders_with_quantity() -> None:
    raw = programmatic_provider(_ctx("بيتزا مارجريتا محتاج منها 15 واحدة", flow="delivery"))
    u = parse_understanding(raw)
    _check(
        "mock_qty:fifteen_pizzas",
        any(item.quantity == 15 and "بيتزا" in item.item_name for item in u.order_items),
        str(u.order_items),
    )


def test_mock_phone_capture() -> None:
    raw = programmatic_provider(_ctx("01012345678", flow="delivery"))
    u = parse_understanding(raw)
    _check("mock_phone:digits", u.customer_phone_digits == "01012345678")


def test_mock_address_with_zone() -> None:
    raw = programmatic_provider(_ctx("المعادي شارع 9 برج 12", flow="delivery"))
    u = parse_understanding(raw)
    _check("mock_addr:zone", u.delivery_zone == "المعادي")
    _check("mock_addr:value_set", bool(u.delivery_address))


def test_mock_mutation_replace() -> None:
    raw = programmatic_provider(_ctx("لأ غير الطلب اعمله بطاطس", flow="delivery"))
    u = parse_understanding(raw)
    _check("mock_mut_replace:kind", u.mutation == "replace")


def test_mock_mutation_add() -> None:
    raw = programmatic_provider(_ctx("ضيف معاه كولا", flow="delivery"))
    u = parse_understanding(raw)
    _check("mock_mut_add:kind", u.mutation == "add")


def test_mock_confirming() -> None:
    raw = programmatic_provider(_ctx("أكد", flow="delivery"))
    u = parse_understanding(raw)
    _check("mock_confirm:flag", u.is_confirming)


def test_mock_reservation_time() -> None:
    raw = programmatic_provider(_ctx("بكره الساعة 8 مساء", flow="reservation"))
    u = parse_understanding(raw)
    _check("mock_res_time:set", u.reservation_time is not None)


def test_mock_guests_count() -> None:
    raw = programmatic_provider(_ctx("هنبقى 4 ضيوف", flow="reservation"))
    u = parse_understanding(raw)
    _check("mock_guests:count", u.guests_count == 4)


# ---------------------------------------------------------------------------
# Latency fast-path: trivial turns must skip the LLM call entirely
# ---------------------------------------------------------------------------


def test_fast_path_skips_provider_for_digits() -> None:
    from core.understanding import (
        UnderstandingService,
        get_or_extract_for_turn,
        reset_default_service,
        set_default_service,
    )
    from core.understanding_mock import ScriptedProvider

    class _FakeUD:
        call_id = "fast-digits"
        last_agent_message = ""
        pending_upsell_item = ""
        turn_understanding = None
        turn_understanding_text = ""
        restaurant = type("Cfg", (), {"menu_items": [], "delivery_zones": []})()

    provider = ScriptedProvider()  # no script — would raise if called
    set_default_service(UnderstandingService(provider=provider))
    try:
        u = get_or_extract_for_turn(_FakeUD(), "01012345678", "delivery")
        _check("fast_path_digits:source", u.source == "fast_path", str(u))
        _check("fast_path_digits:no_provider_call", len(provider.calls) == 0)
        _check("fast_path_digits:phone_set", u.customer_phone_digits == "01012345678")
    finally:
        reset_default_service()


def test_fast_path_skips_provider_for_yes() -> None:
    from core.understanding import (
        UnderstandingService,
        get_or_extract_for_turn,
        reset_default_service,
        set_default_service,
    )
    from core.understanding_mock import ScriptedProvider

    class _FakeUD:
        call_id = "fast-yes"
        last_agent_message = ""
        pending_upsell_item = ""
        turn_understanding = None
        turn_understanding_text = ""
        restaurant = type("Cfg", (), {"menu_items": [], "delivery_zones": []})()

    provider = ScriptedProvider()
    set_default_service(UnderstandingService(provider=provider))
    try:
        u = get_or_extract_for_turn(_FakeUD(), "أيوه", "delivery")
        _check("fast_path_yes:confirming", u.is_confirming)
        _check("fast_path_yes:no_provider_call", len(provider.calls) == 0)
        u_no = get_or_extract_for_turn(_FakeUD(), "لا", "delivery")
        _check("fast_path_no:denying", u_no.is_denying)
    finally:
        reset_default_service()


def test_fast_path_runs_provider_for_complex_turn() -> None:
    """Non-trivial turns still hit the LLM."""
    from core.understanding import (
        UnderstandingService,
        get_or_extract_for_turn,
        reset_default_service,
        set_default_service,
    )
    from core.understanding_mock import script

    class _FakeUD:
        call_id = "complex-turn"
        last_agent_message = ""
        pending_upsell_item = ""
        turn_understanding = None
        turn_understanding_text = ""
        restaurant = type("Cfg", (), {"menu_items": [], "delivery_zones": []})()

    provider = script({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    set_default_service(UnderstandingService(provider=provider))
    try:
        u = get_or_extract_for_turn(
            _FakeUD(),
            "كنت محتاج أطلب أوردر توصيل لو سمحت",
            "greeter",
        )
        _check("fast_path_complex:provider_called", len(provider.calls) == 1)
        _check("fast_path_complex:source", u.source == "llm")
    finally:
        reset_default_service()


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
    print(f"UNDERSTANDING_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
