"""Schema-driven integration tests.

These don't test "did the regex match this Arabic phrase". They test
"given a structured ``TurnUnderstanding`` from the LLM, does the engine
take the correct action?". That's the real production contract — the
engine consumes a schema, not raw text.

Each test installs a deterministic provider (scripted JSON) and walks
through the live flow code paths. No network, no Gemini, no
non-determinism.
"""

from __future__ import annotations

import asyncio
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


import agent  # noqa: E402
from call_scenario_tests import (  # noqa: E402
    SUBMISSIONS,
    bind_session,
    make_cfg,
    make_ctx,
    make_ud,
    patched_backend,
)
from core.understanding import (  # noqa: E402
    UnderstandingService,
    reset_default_service,
    set_default_service,
)
from core.understanding_mock import ScriptedProvider, script  # noqa: E402


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


def _install_scripted(*understandings: dict) -> ScriptedProvider:
    provider = script(*understandings)
    set_default_service(UnderstandingService(provider=provider))
    return provider


def _reset_understanding(ud: agent.UserData) -> None:
    """Clear the per-turn understanding cache before a new scripted turn."""
    ud.turn_understanding = None
    ud.turn_understanding_text = ""


# ---------------------------------------------------------------------------
# 1) Order extraction from LLM understanding
# ---------------------------------------------------------------------------


async def integration_llm_captures_order_with_quantity() -> None:
    """The real-call regression: 15 pizzas extracted from understanding."""
    cfg = make_cfg()
    cfg.menu_items.append({"name": "بيتزا مارجريتا", "price": 80.0, "available": True})
    ud = make_ud("integration-15-pizzas", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)

    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "order_items": [
            {"item_name": "بيتزا مارجريتا", "quantity": 15, "evidence": "محتاج منها 15"}
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "بيتزا مارجريتا محتاج منها 15 واحدة"

    captured = agent._should_capture_order_turn(
        "delivery", ud, "بيتزا مارجريتا محتاج منها 15 واحدة", cfg
    )
    _check("integration_15:captured", captured == ["بيتزا مارجريتا × 15"], str(captured))

    # Push through update_order so the engine validates + stores.
    msg = await flow.update_order(captured, ctx)
    items_dict = {
        item["name"]: item["qty"]
        for item in agent._build_order_items(ud.order or [], cfg.menu_items)
    }
    _check("integration_15:stored_qty", items_dict.get("بيتزا مارجريتا") == 15, str(items_dict))
    _check("integration_15:total", ud.order_total == 80 * 15, str(ud.order_total))


async def integration_llm_drops_unavailable_item() -> None:
    """The LLM proposes an item the menu doesn't have → engine drops it."""
    cfg = make_cfg()
    ud = make_ud("integration-unavailable", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)

    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "order_items": [
            {"item_name": "كافيار", "quantity": 1, "evidence": "كافيار"}
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "كافيار"

    captured = agent._should_capture_order_turn("delivery", ud, "كافيار", cfg)
    _check("integration_unavailable:dropped", captured == [], str(captured))
    _ = ctx  # unused but kept for symmetry


async def integration_llm_replace_clears_order() -> None:
    cfg = make_cfg()
    ud = make_ud("integration-replace", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)

    # Seed an order.
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "order_items": [{"item_name": "برجر كبير", "quantity": 1}],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "برجر كبير"
    captured = agent._should_capture_order_turn("delivery", ud, "برجر كبير", cfg)
    await flow.update_order(captured, ctx)

    # Clear the pending upsell so the replace mutation is treated as a
    # new order (not as an upsell decline). In a live call this happens
    # naturally via ``_handle_pending_upsell`` before the order intercept.
    ud.pending_upsell_item = None
    ud.pending_upsell_price = None

    # Now replace.
    _reset_understanding(ud)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "replace",
        "order_items": [{"item_name": "كولا", "quantity": 1}],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "لا، غير الطلب اعمله كولا"
    captured = agent._should_capture_order_turn(
        "delivery", ud, "لا، غير الطلب اعمله كولا", cfg
    )
    _check("integration_replace:captured", captured == ["كولا"], str(captured))


# ---------------------------------------------------------------------------
# 2) Intent routing from LLM understanding
# ---------------------------------------------------------------------------


def integration_llm_intent_routes_to_takeaway() -> None:
    cfg = make_cfg()
    ud = make_ud("integration-intent-takeaway", cfg)
    _install_scripted({
        "intent": "takeaway",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    intent = agent._guess_request_intent("هاجي اخده من المحل", cfg, ud)
    _check("intent_takeaway:via_llm", intent == "takeaway", intent)


def integration_llm_intent_routes_to_complaint() -> None:
    cfg = make_cfg()
    ud = make_ud("integration-intent-complaint", cfg)
    _install_scripted({
        "intent": "complaint",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    intent = agent._guess_request_intent("عندي مشكلة", cfg, ud)
    _check("intent_complaint:via_llm", intent == "complaint", intent)


def integration_llm_intent_low_confidence_falls_back() -> None:
    cfg = make_cfg()
    ud = make_ud("integration-intent-low", cfg)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "low",  # below the bridge threshold
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    # The text "احكي معاك" doesn't trigger any legacy delivery cue, so
    # the fall-back path returns "unknown".
    intent = agent._guess_request_intent("احكي معاك", cfg, ud)
    _check("intent_low_conf:fallback", intent == "unknown", intent)


def integration_llm_intent_delivery_unavailable_when_disabled() -> None:
    cfg = make_cfg(delivery_enabled=False)
    ud = make_ud("integration-intent-delivery-unavail", cfg)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": False,
        "is_denying": False,
    })
    intent = agent._guess_request_intent("عايز توصيل", cfg, ud)
    _check("intent_delivery_unavail:label", intent == "delivery_unavailable", intent)


# ---------------------------------------------------------------------------
# 3) Contact + address from LLM understanding
# ---------------------------------------------------------------------------


async def integration_llm_name_intercept() -> None:
    """The bridge yields a clean name from the LLM understanding.

    Asserts the bridge function rather than the full intercept method —
    the intercept calls ``self._say_and_stop`` which requires a live
    LiveKit activity context that the text harness can't synthesise.
    The bridge is the contract that matters for production correctness.
    """
    cfg = make_cfg()
    ud = make_ud("integration-name", cfg)
    _ = cfg, ud
    from core.understanding import get_or_extract_for_turn
    from core.understanding_bridge import name_from_understanding

    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "customer_name": "أحمد",
        "is_confirming": False,
        "is_denying": False,
    })
    understanding = get_or_extract_for_turn(ud, "اسمي أحمد", "delivery")
    name = name_from_understanding(understanding)
    _check("integration_name:value", name == "أحمد", str(name))


async def integration_llm_address_intercept_uses_zone() -> None:
    """Bridge yields the LLM address + zone the engine consumes."""
    cfg = make_cfg()
    ud = make_ud("integration-addr", cfg)
    from core.understanding import get_or_extract_for_turn
    from core.understanding_bridge import address_from_understanding

    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "delivery_address": "المعادي شارع 9 برج 12",
        "delivery_zone": "المعادي",
        "is_confirming": False,
        "is_denying": False,
    })
    understanding = get_or_extract_for_turn(ud, "المعادي شارع 9 برج 12", "delivery")
    result = address_from_understanding(understanding)
    _check("integration_addr:returned", result is not None, str(result))
    if result is not None:
        addr, zone = result
        _check("integration_addr:address", "المعادي" in addr, addr)
        _check("integration_addr:zone", zone == "المعادي", zone)


# ---------------------------------------------------------------------------
# 4) Confirmation flag from LLM understanding
# ---------------------------------------------------------------------------


async def integration_llm_confirm_after_summary() -> None:
    cfg = make_cfg()
    ud = make_ud("integration-confirm", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    ud.order_state.items = ["برجر كبير × 1"]
    ud.order_state.total = 45.0
    ud.order_state.validated = True
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"

    _install_scripted({
        "intent": "confirming",
        "intent_confidence": "high",
        "mutation": "none",
        "is_confirming": True,
        "is_denying": False,
    })
    with patched_backend():
        SUBMISSIONS.clear()
        msg = await flow.confirm_order(ctx)
        _check("integration_confirm:submitted", len(SUBMISSIONS) == 1, str(SUBMISSIONS))
        _check("integration_confirm:accepted", ud.order_confirmed)


# ---------------------------------------------------------------------------
# 5) Fallback to legacy when no provider
# ---------------------------------------------------------------------------


def integration_no_provider_falls_back_to_legacy() -> None:
    reset_default_service()
    # Pretend env disables LLM understanding.
    import os
    prev = os.environ.get("LLM_UNDERSTANDING_ENABLED")
    os.environ["LLM_UNDERSTANDING_ENABLED"] = "0"
    try:
        cfg = make_cfg()
        ud = make_ud("integration-no-provider", cfg)
        # "تيكاواي" matches the legacy hint set, so the engine should
        # still route to takeaway even with no LLM.
        intent = agent._guess_request_intent("تيكاواي لو سمحت", cfg, ud)
        _check("integration_no_provider:legacy", intent == "takeaway", intent)
    finally:
        if prev is None:
            os.environ.pop("LLM_UNDERSTANDING_ENABLED", None)
        else:
            os.environ["LLM_UNDERSTANDING_ENABLED"] = prev
        reset_default_service()


# ---------------------------------------------------------------------------
# 6) Greeter prefill respects LLM "no name" signal
# ---------------------------------------------------------------------------


def integration_greeter_prefill_skips_name_when_llm_says_none() -> None:
    """Real-call regression: "ألو" should NOT be captured as a name.

    Pre-fix the legacy ``_extract_name_candidate`` claimed any short
    bare token as a name, even when the LLM (correctly) returned
    ``customer_name: null``. The fix routes prefill through the
    bridge first.
    """
    cfg = make_cfg()
    ud = make_ud("integration-prefill-no-name", cfg)
    flow = ud.agents["greeter"]
    bind_session(flow, ud)
    _install_scripted({
        "intent": "greeting",
        "intent_confidence": "high",
        "mutation": "none",
        "customer_name": None,
        "is_confirming": False,
        "is_denying": False,
    })
    flow._capture_prefill_contact("ألو")
    _check(
        "prefill_no_name:not_captured",
        ud.customer_name is None,
        f"got name={ud.customer_name}",
    )


def integration_greeter_prefill_takes_name_from_llm() -> None:
    """Bridge captures the name when the LLM provides one."""
    cfg = make_cfg()
    ud = make_ud("integration-prefill-llm-name", cfg)
    flow = ud.agents["greeter"]
    bind_session(flow, ud)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "customer_name": "محمد",
        "is_confirming": False,
        "is_denying": False,
    })
    flow._capture_prefill_contact("اسمي محمد عايز توصيل")
    _check(
        "prefill_llm_name:captured",
        ud.customer_name == "محمد",
        f"got name={ud.customer_name}",
    )


# ---------------------------------------------------------------------------
# 7) Hallucination resistance
# ---------------------------------------------------------------------------


async def integration_llm_hallucinates_unavailable_item() -> None:
    """LLM invents an item not on the menu — engine refuses to add it."""
    cfg = make_cfg()
    ud = make_ud("integration-hallucination", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "order_items": [
            {"item_name": "بيتزا فاخرة بالكافيار", "quantity": 5}
        ],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "بيتزا فاخرة بالكافيار"
    captured = agent._should_capture_order_turn("delivery", ud, "بيتزا فاخرة بالكافيار", cfg)
    _check("integration_halluc:dropped", captured == [], str(captured))
    _ = ctx


async def integration_llm_hallucinates_negative_quantity() -> None:
    """LLM returns qty=-3 — schema parser normalizes to 1."""
    cfg = make_cfg()
    ud = make_ud("integration-neg-qty", cfg)
    _install_scripted({
        "intent": "delivery",
        "intent_confidence": "high",
        "mutation": "none",
        "order_items": [{"item_name": "كولا", "quantity": -3}],
        "is_confirming": False,
        "is_denying": False,
    })
    ud.last_user_message = "كولا"
    captured = agent._should_capture_order_turn("delivery", ud, "كولا", cfg)
    _check("integration_neg_qty:normalized", captured == ["كولا"], str(captured))


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


_ASYNC_TESTS = [
    integration_llm_captures_order_with_quantity,
    integration_llm_drops_unavailable_item,
    integration_llm_replace_clears_order,
    integration_llm_name_intercept,
    integration_llm_address_intercept_uses_zone,
    integration_llm_confirm_after_summary,
    integration_llm_hallucinates_unavailable_item,
    integration_llm_hallucinates_negative_quantity,
]

_SYNC_TESTS = [
    integration_llm_intent_routes_to_takeaway,
    integration_llm_intent_routes_to_complaint,
    integration_llm_intent_low_confidence_falls_back,
    integration_llm_intent_delivery_unavailable_when_disabled,
    integration_no_provider_falls_back_to_legacy,
    integration_greeter_prefill_skips_name_when_llm_says_none,
    integration_greeter_prefill_takes_name_from_llm,
]


def main() -> int:
    for fn in _ASYNC_TESTS:
        try:
            asyncio.run(fn())
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1
        finally:
            reset_default_service()
    for fn in _SYNC_TESTS:
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            _TOTAL += 1
        finally:
            reset_default_service()

    print(f"UNDERSTANDING_INTEGRATION_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
