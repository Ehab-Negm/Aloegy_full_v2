import asyncio
import sys
from types import SimpleNamespace

import agent
from call_scenario_tests import SUBMISSIONS, make_cfg, make_ud, patched_backend


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def ctx(flow: object, ud: agent.UserData) -> SimpleNamespace:
    return SimpleNamespace(userdata=ud, session=SimpleNamespace(current_agent=flow))


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


def payload_items(ud: agent.UserData) -> dict[str, dict]:
    return {item["name"]: item for item in agent._build_order_items(ud.order or [], ud.restaurant.menu_items)}


def qty(ud: agent.UserData, item: str) -> int:
    return int(payload_items(ud).get(item, {}).get("qty", 0))


async def complex_mixed_quantities_takeaway() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-mixed-qty", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير 2", "بطاطس 3", "كولا"], ctx(flow, ud))
    assert_true("burger qty", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("fries qty", qty(ud, "بطاطس") == 3, str(ud.order))
    assert_true("cola qty", qty(ud, "كولا") == 1, str(ud.order))
    assert_true("mixed total", ud.order_total == 165, str(ud.order_total))
    return 4


async def complex_prefix_quantities() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-prefix-qty", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["2 برجر كبير", "3 بطاطس"], ctx(flow, ud))
    assert_true("prefix burger qty", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("prefix fries qty", qty(ud, "بطاطس") == 3, str(ud.order))
    assert_true("prefix total", ud.order_total == 150, str(ud.order_total))
    return 3


async def complex_multiplier_symbols() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-mult-symbols", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير x 2", "بطاطس * 2"], ctx(flow, ud))
    assert_true("x burger qty", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("star fries qty", qty(ud, "بطاطس") == 2, str(ud.order))
    assert_true("symbol total", ud.order_total == 130, str(ud.order_total))
    return 3


async def complex_arabic_digit_quantities() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-ar-digits", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير ٢", "كولا ٣"], ctx(flow, ud))
    assert_true("arabic digit burger", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("arabic digit cola", qty(ud, "كولا") == 3, str(ud.order))
    assert_true("arabic digit total", ud.order_total == 135, str(ud.order_total))
    return 3


async def complex_duplicate_same_item_aggregates() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-aggregate", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير", "برجر كبير 2", "كولا", "كولا 2"], ctx(flow, ud))
    assert_true("aggregate burger qty", qty(ud, "برجر كبير") == 3, str(ud.order))
    assert_true("aggregate cola qty", qty(ud, "كولا") == 3, str(ud.order))
    assert_true("aggregate total", ud.order_total == 180, str(ud.order_total))
    return 3


async def complex_unknown_only_rejected() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-unknown-only", cfg)
    flow = ud.agents["takeaway"]
    msg = await flow.update_order(["سوشي 2", "قهوة"], ctx(flow, ud))
    assert_true("unknown order not saved", ud.order is None, str(ud.order))
    assert_true("unknown message helpful", "مش موجود" in msg, msg)
    return 2


async def complex_partial_unknown_saves_valid() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-partial-unknown", cfg)
    flow = ud.agents["takeaway"]
    msg = await flow.update_order(["برجر كبير 2", "سوشي"], ctx(flow, ud))
    assert_true("valid saved despite unknown", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("unknown mentioned", "مش في المنيو" in msg or "مش موجود" in msg, msg)
    assert_true("partial total", ud.order_total == 90, str(ud.order_total))
    return 3


async def complex_unavailable_item_not_saved() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-unavailable", cfg)
    flow = ud.agents["takeaway"]
    msg = await flow.update_order(["برجر كبير", "مياه 2"], ctx(flow, ud))
    assert_true("available saved", qty(ud, "برجر كبير") == 1, str(ud.order))
    assert_true("unavailable not saved", qty(ud, "مياه") == 0, str(ud.order))
    assert_true("unavailable mentioned", "مياه" in msg, msg)
    return 3


async def complex_delivery_min_order_rejects_small() -> int:
    cfg = make_cfg()
    cfg.min_order = 100
    ud = make_ud("complex-min-small", cfg)
    flow = ud.agents["delivery"]
    msg = await flow.update_order(["برجر كبير"], ctx(flow, ud))
    assert_true("small delivery not saved", ud.order is None, str(ud.order))
    assert_true("min order message", "أقل طلب" in msg, msg)
    return 2


async def complex_delivery_min_order_accepts_large() -> int:
    cfg = make_cfg()
    cfg.min_order = 100
    ud = make_ud("complex-min-large", cfg)
    flow = ud.agents["delivery"]
    await flow.update_order(["برجر كبير 2", "كولا"], ctx(flow, ud))
    assert_true("large delivery saved", qty(ud, "برجر كبير") == 2 and qty(ud, "كولا") == 1, str(ud.order))
    assert_true("large total", ud.order_total == 105, str(ud.order_total))
    return 2


async def complex_incremental_add_preserves_quantities() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-incremental-qty", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير 2"], ctx(flow, ud))
    ud.last_user_message = "ضيف بطاطس اتنين وكولا"
    await flow.update_order(["بطاطس 2", "كولا"], ctx(flow, ud))
    assert_true("incremental burger kept", qty(ud, "برجر كبير") == 2, str(ud.order))
    assert_true("incremental fries added", qty(ud, "بطاطس") == 2, str(ud.order))
    assert_true("incremental cola added", qty(ud, "كولا") == 1, str(ud.order))
    assert_true("incremental total", ud.order_total == 145, str(ud.order_total))
    return 4


async def complex_duplicate_incremental_tool_call_no_double() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-dup-incremental", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "ضيف كولا اتنين"
    await flow.update_order(["كولا 2"], c)
    await flow.update_order(["كولا 2"], c)
    assert_true("duplicate incremental no double", qty(ud, "كولا") == 2, str(ud.order))
    assert_true("duplicate total stable", ud.order_total == 75, str(ud.order_total))
    return 2


async def complex_replace_multi_item_order() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-replace-multi", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير 2", "كولا"], ctx(flow, ud))
    ud.last_user_message = "لا بدل كله بطاطس 3"
    await flow.update_order(["بطاطس 3"], ctx(flow, ud))
    assert_true("replace removed burger", qty(ud, "برجر كبير") == 0, str(ud.order))
    assert_true("replace fries qty", qty(ud, "بطاطس") == 3, str(ud.order))
    assert_true("replace total", ud.order_total == 60, str(ud.order_total))
    return 3


async def complex_remove_item_via_replacement() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-remove-via-replace", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير", "كولا", "بطاطس"], ctx(flow, ud))
    ud.last_user_message = "شيل الكولا وخليه برجر وبطاطس بس"
    await flow.update_order(["برجر كبير", "بطاطس"], ctx(flow, ud))
    assert_true("removed cola", qty(ud, "كولا") == 0, str(ud.order))
    assert_true("kept burger", qty(ud, "برجر كبير") == 1, str(ud.order))
    assert_true("kept fries", qty(ud, "بطاطس") == 1, str(ud.order))
    return 3


async def complex_no_menu_raw_order_captured() -> int:
    cfg = make_cfg(menu=False)
    ud = make_ud("complex-no-menu", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["طبق عائلي 2", "مشروب كبير"], ctx(flow, ud))
    assert_true("raw order captured", ud.order == ["طبق عائلي 2", "مشروب كبير"], str(ud.order))
    assert_true("raw order not validated", not ud.order_validated)
    return 2


async def complex_payload_has_qty_and_price() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-payload", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير 2", "بطاطس"], ctx(flow, ud))
    items = payload_items(ud)
    assert_true("payload burger qty", items["برجر كبير"]["qty"] == 2, str(items))
    assert_true("payload burger price", items["برجر كبير"]["price"] == 45, str(items))
    assert_true("payload fries qty", items["بطاطس"]["qty"] == 1, str(items))
    return 3


async def complex_confirm_missing_phone_does_not_submit() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-missing-phone", cfg)
    flow = ud.agents["takeaway"]
    ud.order = ["برجر كبير 2", "بطاطس"]
    ud.order_validated = True
    ud.order_total = 110
    ud.customer_name = "أحمد"
    msg = await flow.confirm_order(ctx(flow, ud))
    assert_true("missing phone not confirmed", not ud.order_confirmed, msg)
    assert_true("missing phone asks phone", "موبايل" in msg or "رقم" in msg, msg)
    assert_true("missing phone no submit", ("takeaway", ud.call_id) not in SUBMISSIONS)
    return 3


async def complex_delivery_confirm_missing_address() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-missing-address", cfg)
    flow = ud.agents["delivery"]
    ud.order = ["برجر كبير 2"]
    ud.order_validated = True
    ud.order_total = 90
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = await flow.confirm_delivery(ctx(flow, ud))
    assert_true("missing address not confirmed", not ud.order_confirmed, msg)
    assert_true("missing address asks address", "العنوان" in msg or "المنطقة" in msg, msg)
    return 2


async def complex_ready_order_add_no_contact_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-ready-add", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "ضيف بطاطس 2"
    msg = await flow.update_order(["بطاطس 2"], c)
    assert_true("ready add no phone ask", "موبايل" not in msg and "رقم" not in msg, msg)
    assert_true("ready add no name ask", "اسمك" not in msg, msg)
    return 2


async def complex_upsell_not_offered_when_in_order() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-upsell-already", cfg)
    flow = ud.agents["takeaway"]
    msg = await flow.update_order(["برجر كبير", "كولا"], ctx(flow, ud))
    assert_true("no pending upsell for existing cola", ud.pending_upsell_item is None, str(ud.pending_upsell_item))
    assert_true("no upsell wording", "أزودلك" not in msg, msg)
    return 2


async def complex_upsell_accept_updates_total() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-upsell-accept", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير"], ctx(flow, ud))
    accepted = agent._accept_pending_upsell(ud, cfg)
    assert_true("upsell accepted item", accepted == "كولا", str(accepted))
    assert_true("upsell order includes cola", qty(ud, "كولا") == 1, str(ud.order))
    assert_true("upsell total", ud.order_total == 60, str(ud.order_total))
    return 3


async def complex_total_delivery_includes_fee() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-delivery-total", cfg)
    ud.order = ["برجر كبير 2", "كولا"]
    ud.order_validated = True
    ud.order_total = 105
    msg = agent._order_total_user_message("delivery", ud, cfg)
    assert_true("delivery total includes fee", "مية وعشرين" in msg or "مائة وعشرين" in msg or "120" in msg, msg)
    assert_true("delivery fee mentioned", "التوصيل" in msg, msg)
    return 2


async def complex_empty_order_rejected() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-empty", cfg)
    flow = ud.agents["takeaway"]
    msg = await flow.update_order([], ctx(flow, ud))
    assert_true("empty order not saved", ud.order is None, str(ud.order))
    assert_true("empty order message", "فاضي" in msg, msg)
    return 2


async def complex_order_quantities_not_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-qty-not-phone", cfg)
    flow = ud.agents["takeaway"]
    await flow.update_order(["برجر كبير 2", "بطاطس 3", "كولا 4"], ctx(flow, ud))
    assert_true("qty not phone", ud.customer_phone is None, str(ud.customer_phone))
    assert_true("qty total correct", ud.order_total == 210, str(ud.order_total))
    return 2


async def complex_delivery_full_confirm_complex_payload() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-delivery-confirm", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير 2", "بطاطس", "كولا"], c)
    await flow.update_delivery_address("مدينة نصر شارع عباس العقاد عمارة 12 الدور 3 شقة 4", "مدينة نصر", c)
    await agent.update_name("أحمد", c)
    await agent.update_phone("01012345678", c)
    msg = await flow.confirm_delivery(c)
    assert_true("complex delivery confirmed", ud.order_confirmed, msg)
    assert_true("complex delivery one submit", ("delivery", ud.call_id) in SUBMISSIONS)
    return 2


async def complex_takeaway_duplicate_confirm_after_complex_order() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-dup-confirm", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير 2", "بطاطس 2", "كولا"], c)
    await agent.update_name("أحمد", c)
    await agent.update_phone("01012345678", c)
    await flow.confirm_order(c)
    before = len([x for x in SUBMISSIONS if x == ("takeaway", ud.call_id)])
    await flow.confirm_order(c)
    after = len([x for x in SUBMISSIONS if x == ("takeaway", ud.call_id)])
    assert_true("complex duplicate confirm no second submit", before == 1 and after == 1, f"{before}/{after}")
    return 1


async def complex_unknown_after_existing_incremental_keeps_existing() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-unknown-incremental", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "ضيف سوشي"
    msg = await flow.update_order(["سوشي"], c)
    assert_true("unknown add keeps existing", ud.order == ["برجر كبير"], str(ud.order))
    assert_true("unknown add message", "مش موجود" in msg or "مش في المنيو" in msg, msg)
    return 2


async def complex_replace_with_partial_unknown_saves_valid_replacement() -> int:
    cfg = make_cfg()
    ud = make_ud("complex-replace-partial-unknown", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير", "كولا"], c)
    ud.last_user_message = "لا خليها بطاطس وسوشي"
    msg = await flow.update_order(["بطاطس", "سوشي"], c)
    assert_true("replacement valid saved", ud.order == ["بطاطس"], str(ud.order))
    assert_true("replacement removed old", qty(ud, "برجر كبير") == 0 and qty(ud, "كولا") == 0, str(ud.order))
    assert_true("replacement unknown mentioned", "سوشي" in msg, msg)
    return 3


async def complex_delivery_add_after_min_rejection_can_succeed() -> int:
    cfg = make_cfg()
    cfg.min_order = 100
    ud = make_ud("complex-min-retry", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "خلاص خليه برجرين وكولا"
    await flow.update_order(["برجر كبير 2", "كولا"], c)
    assert_true("retry after min saved", qty(ud, "برجر كبير") == 2 and qty(ud, "كولا") == 1, str(ud.order))
    assert_true("retry total", ud.order_total == 105, str(ud.order_total))
    return 2


TESTS = [
    complex_mixed_quantities_takeaway,
    complex_prefix_quantities,
    complex_multiplier_symbols,
    complex_arabic_digit_quantities,
    complex_duplicate_same_item_aggregates,
    complex_unknown_only_rejected,
    complex_partial_unknown_saves_valid,
    complex_unavailable_item_not_saved,
    complex_delivery_min_order_rejects_small,
    complex_delivery_min_order_accepts_large,
    complex_incremental_add_preserves_quantities,
    complex_duplicate_incremental_tool_call_no_double,
    complex_replace_multi_item_order,
    complex_remove_item_via_replacement,
    complex_no_menu_raw_order_captured,
    complex_payload_has_qty_and_price,
    complex_confirm_missing_phone_does_not_submit,
    complex_delivery_confirm_missing_address,
    complex_ready_order_add_no_contact_repeat,
    complex_upsell_not_offered_when_in_order,
    complex_upsell_accept_updates_total,
    complex_total_delivery_includes_fee,
    complex_empty_order_rejected,
    complex_order_quantities_not_phone,
    complex_delivery_full_confirm_complex_payload,
    complex_takeaway_duplicate_confirm_after_complex_order,
    complex_unknown_after_existing_incremental_keeps_existing,
    complex_replace_with_partial_unknown_saves_valid_replacement,
    complex_delivery_add_after_min_rejection_can_succeed,
]


async def main() -> int:
    failures: list[str] = []
    checks = 0
    SUBMISSIONS.clear()
    with patched_backend():
        for index, test in enumerate(TESTS, start=1):
            try:
                count = await test()
                checks += count
                print(f"{index:02d}. {test.__name__}: PASS ({count} checks)")
            except Exception as exc:
                failures.append(f"{test.__name__}: {exc}")
                print(f"{index:02d}. {test.__name__}: FAIL - {exc}")
    print(f"COMPLEX_ORDER_TESTS_PASSED: {len(TESTS) - len(failures)}/{len(TESTS)}")
    print(f"COMPLEX_ORDER_CHECKS: {checks}")
    if failures:
        print("FAILED_COMPLEX_ORDER_TESTS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
