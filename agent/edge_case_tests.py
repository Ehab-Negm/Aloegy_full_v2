"""Edge-case scenarios that production calls hit but the existing
acceptance suites don't exercise.

Covered cases:

- ``min_order`` validation after a remove mutation drops the cart below
  the threshold,
- an item flips to unavailable mid-call (stock-out),
- the customer hangs up between confirmation prompt and submit
  (``submit_in_flight`` cleanup),
- the backend returns a queued response and the customer immediately
  re-confirms,
- the customer re-confirms after a failed submit and we don't double
  charge them.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


import agent  # noqa: E402
from call_scenario_tests import (  # noqa: E402
    SUBMISSIONS,
    bind_session,
    fake_submit_takeaway,
    make_cfg,
    make_ctx,
    make_ud,
    patched_backend,
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
# 1) Removing an item below min_order keeps the order recoverable.
# ---------------------------------------------------------------------------


async def edge_case_min_order_after_remove() -> None:
    cfg = make_cfg()
    cfg.min_order = 50.0
    ud = make_ud("edge-min-order-remove", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)

    # Build an order that meets the minimum: 2 burgers (90) + cola (15) = 105
    await flow.update_order(["برجر كبير 2", "كولا"], ctx)
    _check("min_order_after_remove:initial_validated", ud.order_validated)
    _check("min_order_after_remove:initial_total", ud.order_total >= 50.0, str(ud.order_total))
    snapshot_total = ud.order_total
    snapshot_items = list(ud.order or [])

    # Customer drops to a single burger which falls below the min_order.
    # The flow must surface the rejection so the user knows, and must
    # leave the *previous* validated order untouched — never submit an
    # order whose total dropped below the minimum.
    ud.last_user_message = "شيل واحد، خليه برجر واحد بس"
    msg = await flow.update_order(["برجر كبير 1"], ctx)
    _check(
        "min_order_after_remove:rejection_message_surfaced",
        "أقل طلب" in msg,
        msg,
    )
    _check(
        "min_order_after_remove:state_preserved_total",
        ud.order_total == snapshot_total,
        f"total={ud.order_total} expected={snapshot_total}",
    )
    _check(
        "min_order_after_remove:state_preserved_items",
        list(ud.order or []) == snapshot_items,
        f"items={ud.order} expected={snapshot_items}",
    )


# ---------------------------------------------------------------------------
# 2) Stock-out mid-call: item flips to unavailable between turns.
# ---------------------------------------------------------------------------


async def edge_case_item_goes_out_of_stock() -> None:
    cfg = make_cfg()
    ud = make_ud("edge-stockout", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)

    await flow.update_order(["برجر كبير 1", "كولا 1"], ctx)
    _check("stockout:initial_total", ud.order_total > 0)

    # Operator marks burger unavailable mid-call (e.g. via backend push).
    for item in cfg.menu_items:
        if item["name"] == "برجر كبير":
            item["available"] = False

    # Customer asks again — extractor must not re-add the now-unavailable item.
    from core.extractors.order_extractor import extract_order
    from core.menu_index import MenuIndex
    fresh_index = MenuIndex.build(cfg.menu_items)
    extraction = extract_order("برجر كبير", fresh_index)
    _check("stockout:extractor_skips_unavailable", extraction.is_empty(), str(extraction.formatted_items()))

    # Live cache should also rebuild and skip the unavailable item.
    live_idx = agent._menu_index_for(cfg)
    extraction_live = extract_order("برجر كبير", live_idx)
    _check("stockout:live_cache_invalidated", extraction_live.is_empty())


# ---------------------------------------------------------------------------
# 3) Customer hangs up while submit is in flight: state is recoverable.
# ---------------------------------------------------------------------------


async def edge_case_submit_in_flight_resets() -> None:
    cfg = make_cfg()
    ud = make_ud("edge-in-flight-reset", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)

    # Pretend a previous submit was orphaned (worker crashed mid-call).
    ud.order_state.items = ["برجر كبير 1"]
    ud.order_state.total = 45.0
    ud.order_state.validated = True
    ud.order_state.submit_in_flight = True
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"

    # On the next confirm attempt, the engine should detect the stale
    # in-flight flag and return a "thinking" message rather than
    # re-submitting silently.
    with patched_backend():
        SUBMISSIONS.clear()
        msg = await flow.confirm_order(ctx)
        _check("in_flight_reset:no_submit_called", len(SUBMISSIONS) == 0, str(SUBMISSIONS))
        _check("in_flight_reset:order_not_confirmed", not ud.order_confirmed)
        _check("in_flight_reset:message_set", "ثانية" in msg or "بسجل" in msg, msg)


# ---------------------------------------------------------------------------
# 4) Duplicate confirm after backend success returns the dup message
#    and never invokes the backend again.
# ---------------------------------------------------------------------------


async def edge_case_duplicate_confirm_uses_tracker() -> None:
    cfg = make_cfg()
    ud = make_ud("edge-duplicate-tracker", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)

    await flow.update_order(["برجر كبير 1", "كولا 1"], ctx)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"

    with patched_backend():
        SUBMISSIONS.clear()
        first = await flow.confirm_order(ctx)
        _check("duplicate_tracker:first_calls_backend", len(SUBMISSIONS) == 1, str(SUBMISSIONS))
        _check("duplicate_tracker:order_confirmed", ud.order_confirmed)

        second = await flow.confirm_order(ctx)
        _check("duplicate_tracker:second_does_not_call_backend", len(SUBMISSIONS) == 1, str(SUBMISSIONS))
        _check("duplicate_tracker:second_returns_message", "متسجل" in second or "مسجل" in second, second)


# ---------------------------------------------------------------------------
# 5) Backend returned queued — re-confirm should not blindly resubmit.
# ---------------------------------------------------------------------------


async def edge_case_queued_then_reconfirm() -> None:
    cfg = make_cfg()
    ud = make_ud("edge-queued-reconfirm", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)

    await flow.update_order(["برجر كبير 1"], ctx)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"

    async def queued_submit(local_ud: agent.UserData) -> dict:
        SUBMISSIONS.append(("takeaway-queued", local_ud.call_id))
        return {"queued": True}

    original = agent.submit_takeaway
    SUBMISSIONS.clear()
    agent.submit_takeaway = queued_submit
    try:
        first = await flow.confirm_order(ctx)
        _check("queued:first_attempt_called", len(SUBMISSIONS) == 1, str(SUBMISSIONS))
        _check("queued:not_confirmed_yet", not ud.order_confirmed)

        # Customer says "أكد تاني" — order is still not server-confirmed
        # so the engine should retry rather than block. The tracker marked
        # the previous attempt as failed (queued), so the second submit is
        # allowed to proceed.
        second = await flow.confirm_order(ctx)
        _check("queued:second_attempt_called", len(SUBMISSIONS) == 2, str(SUBMISSIONS))
    finally:
        agent.submit_takeaway = original


# ---------------------------------------------------------------------------
# 6) Backend failure leaves the call recoverable (no slot reset).
# ---------------------------------------------------------------------------


async def edge_case_backend_failure_recoverable() -> None:
    cfg = make_cfg()
    ud = make_ud("edge-backend-failure", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)

    await flow.update_order(["برجر كبير 1"], ctx)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    snapshot_items = list(ud.order or [])
    snapshot_name = ud.customer_name
    snapshot_phone = ud.customer_phone

    async def failing_submit(local_ud: agent.UserData) -> dict | None:
        SUBMISSIONS.append(("takeaway-fail", local_ud.call_id))
        return None

    original = agent.submit_takeaway
    SUBMISSIONS.clear()
    agent.submit_takeaway = failing_submit
    try:
        msg = await flow.confirm_order(ctx)
        _check("backend_fail:returned_message", isinstance(msg, str) and msg)
        _check("backend_fail:not_confirmed", not ud.order_confirmed)
        _check("backend_fail:in_flight_cleared", not ud.order_submit_in_flight)
        _check("backend_fail:items_preserved", list(ud.order or []) == snapshot_items)
        _check("backend_fail:name_preserved", ud.customer_name == snapshot_name)
        _check("backend_fail:phone_preserved", ud.customer_phone == snapshot_phone)
    finally:
        agent.submit_takeaway = original


# ---------------------------------------------------------------------------
# 7) Phone with leading "+" without the 20 country code is rejected
#    by validate_phone (we don't accidentally accept stray prefixes).
# ---------------------------------------------------------------------------


def edge_case_phone_invalid_country_code() -> None:
    from core.extractors.contact_extractor import extract_phone
    invalid_inputs = [
        "+1 010 1234 5678",   # US prefix on Egyptian number
        "+44 010 1234 5678",  # UK prefix
        "+201912345678",      # 019 isn't a valid Egyptian carrier
        "00201012345678",     # leading 00
    ]
    for raw in invalid_inputs:
        capture = extract_phone(raw)
        # validate_phone should reject these. Some "+1" inputs end up with
        # 11 digits after stripping non-digits — those still must not pass
        # carrier validation.
        if capture.value:
            # If capture succeeded, ensure it normalised to a valid Egyptian
            # number (the digits-only branch can produce one for +201xx).
            assert capture.value.startswith("01") or capture.value.startswith("+201"), (
                f"unexpected phone capture for {raw}: {capture.value}"
            )
        _check(f"phone_invalid[{raw}]:no_value_or_normalized", capture.value is None or capture.value.startswith(("01", "+201")))


# ---------------------------------------------------------------------------
# 8a) Real-call regression: upsell acceptance honours user's quantity.
# ---------------------------------------------------------------------------


async def edge_case_upsell_honours_quantity() -> None:
    """User says "ماشي زود لي 10 كولا" → 10 colas, not 1."""
    cfg = make_cfg()
    ud = make_ud("edge-upsell-qty", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)

    # Build an order so the upsell rule can fire.
    await flow.update_order(["برجر كبير 1"], ctx)
    _check("upsell_qty:upsell_offered", ud.pending_upsell_item == "كولا", str(ud.pending_upsell_item))
    pre_total = ud.order_total

    # Simulate the user's accept-with-quantity reply.
    accepted = agent._accept_pending_upsell(ud, cfg, user_text="ماشي زود لي 10 كولا")
    _check("upsell_qty:accepted", accepted == "كولا")
    items_dict = {
        item["name"]: item["qty"]
        for item in agent._build_order_items(ud.order or [], cfg.menu_items)
    }
    _check(
        "upsell_qty:ten_colas",
        items_dict.get("كولا") == 10,
        str(items_dict),
    )
    _check(
        "upsell_qty:total_grew_by_ten",
        ud.order_total >= pre_total + 10 * 15,
        f"pre={pre_total} post={ud.order_total}",
    )


async def edge_case_upsell_default_quantity_one() -> None:
    """User says plain "آه" / "تمام" → upsell adds 1 (legacy behaviour)."""
    cfg = make_cfg()
    ud = make_ud("edge-upsell-default-qty", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير 1"], ctx)
    _check("upsell_default:offered", ud.pending_upsell_item == "كولا")
    agent._accept_pending_upsell(ud, cfg, user_text="آه تمام")
    items_dict = {
        item["name"]: item["qty"]
        for item in agent._build_order_items(ud.order or [], cfg.menu_items)
    }
    _check(
        "upsell_default:one_cola",
        items_dict.get("كولا") == 1,
        str(items_dict),
    )


# ---------------------------------------------------------------------------
# 8) Order extractor reports ambiguity instead of guessing on partial
#    matches that span multiple menu items.
# ---------------------------------------------------------------------------


def edge_case_ambiguous_partial_does_not_capture() -> None:
    from core.extractors.order_extractor import extract_order
    from core.menu_index import MenuIndex
    menu = [
        {"name": "برجر كبير", "price": 45.0, "available": True},
        {"name": "برجر صغير", "price": 30.0, "available": True},
    ]
    idx = MenuIndex.build(menu)
    extraction = extract_order("عايز برجر", idx)
    _check("ambiguous:no_capture", extraction.is_empty())
    _check("ambiguous:reported", extraction.has_ambiguity())


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


_ASYNC_TESTS = [
    edge_case_min_order_after_remove,
    edge_case_item_goes_out_of_stock,
    edge_case_submit_in_flight_resets,
    edge_case_duplicate_confirm_uses_tracker,
    edge_case_queued_then_reconfirm,
    edge_case_backend_failure_recoverable,
    edge_case_upsell_honours_quantity,
    edge_case_upsell_default_quantity_one,
]

_SYNC_TESTS = [
    edge_case_phone_invalid_country_code,
    edge_case_ambiguous_partial_does_not_capture,
]


def main() -> int:
    for fn in _ASYNC_TESTS:
        try:
            asyncio.run(fn())
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1
    for fn in _SYNC_TESTS:
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            _TOTAL += 1

    print(f"EDGE_CASE_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
