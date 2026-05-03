from __future__ import annotations

import asyncio
import sys

import agent
from call_scenario_tests import SUBMISSIONS, make_cfg, make_ud, patched_backend
from complex_order_tests import qty
from conversation_turn_tests import FakeChatContext, bind_all, user_turn
from deterministic_pipeline import run_pipeline
from repeated_question_tests import asks_address, asks_name, asks_order, asks_phone


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


def current_flow(session) -> str:
    return session.current_agent.__class__.__name__.lower()


def make_livekit_log_cfg() -> agent.RestaurantConfig:
    cfg = make_cfg()
    cfg.menu_items = [
        {"name": "شاورما فراخ", "price": 95, "available": True},
        {"name": "شاورما لحمة", "price": 95, "available": True},
        {"name": "بيتزا مارجريتا", "price": 120, "available": True},
        {"name": "كولا", "price": 20, "available": True},
    ]
    cfg.upsell_rules = [{"item": "كولا", "price": 20}]
    return cfg


async def messy_delivery_laf_w_dawaran() -> int:
    cfg = make_cfg()
    ud = make_ud("real-messy-delivery", cfg)
    session, said = bind_all(ud, "greeter")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "السلام عليكم، بتوصلوا فين يا باشا؟")
    assert_true("zones answered", "مدينة نصر" in msg and "المعادي" in msg, msg)
    assert_true("still greeter after side question", current_flow(session) == "greeter")

    await user_turn(session, chat, said, "طيب عايز دليفري")
    assert_true("routed to delivery", current_flow(session) == "delivery")

    msg = await user_turn(session, chat, said, "قبل ما اطلب، المنيو فيه ايه؟")
    assert_true("menu answered", "برجر" in msg and "بطاطس" in msg, msg)
    assert_true("menu answer asks order", asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "هاتلي اتنين برجر كبير وبطاطس وكولا")
    assert_true("order captured with qty", qty(ud, "برجر كبير") == 2 and qty(ud, "بطاطس") == 1 and qty(ud, "كولا") == 1)
    assert_true("asks address after order", asks_address(msg), msg)

    msg = await user_turn(session, chat, said, "لا شيل البطاطس بس")
    assert_true("fries removed", qty(ud, "بطاطس") == 0, str(ud.order))
    assert_true("keeps other items", qty(ud, "برجر كبير") == 2 and qty(ud, "كولا") == 1, str(ud.order))
    assert_true("after remove still asks address", asks_address(msg), msg)

    msg = await user_turn(session, chat, said, "طب الحساب كام كده؟")
    assert_true("total answered", "إجمالي" in msg or "جنيه" in msg, msg)
    assert_true("total did not reset order", qty(ud, "برجر كبير") == 2 and qty(ud, "كولا") == 1, str(ud.order))

    msg = await user_turn(session, chat, said, "العنوان مدينة نصر شارع مصطفى النحاس عمارة ٧ الدور الرابع")
    assert_true("address captured", ud.delivery_address and "مصطفى" in ud.delivery_address, str(ud.delivery_address))
    assert_true("after address asks name", asks_name(msg), msg)

    msg = await user_turn(session, chat, said, "اسمي أحمد علي")
    assert_true("name captured", ud.customer_name == "أحمد علي", str(ud.customer_name))
    assert_true("after name asks phone", asks_phone(msg), msg)

    await user_turn(session, chat, said, "الرقم اهو 010123")
    msg = await user_turn(session, chat, said, "45678")
    assert_true("chunked phone captured", ud.customer_phone == "01012345678", str(ud.customer_phone))
    assert_true("ready confirmation after chunked phone", "صح" in msg or "أكد" in msg or "تأكيد" in msg or "الإجمالي" in msg, msg)

    msg = await user_turn(session, chat, said, "أكد")
    assert_true("delivery submitted", ud.order_confirmed and ("delivery", ud.call_id) in SUBMISSIONS, msg)
    assert_true("no slot reask after submit", not asks_order(msg) and not asks_address(msg) and not asks_phone(msg), msg)
    return 18


async def delivery_to_takeaway_mid_call_keeps_memory() -> int:
    cfg = make_cfg()
    ud = make_ud("real-flow-change", cfg)
    session, said = bind_all(ud, "delivery")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "عايز برجر كبير وكولا دليفري")
    assert_true("delivery order captured", qty(ud, "برجر كبير") == 1 and qty(ud, "كولا") == 1, str(ud.order))
    assert_true("delivery asks address", asks_address(msg), msg)

    await user_turn(session, chat, said, "لا خليها تيكاواي، انا هعدي اخدها")
    assert_true("switched to takeaway", current_flow(session) == "takeaway")
    assert_true("order survived handoff", qty(ud, "برجر كبير") == 1 and qty(ud, "كولا") == 1, str(ud.order))
    assert_true("no address stored after takeaway switch", ud.delivery_address is None)

    msg = await user_turn(session, chat, said, "اسمي سارة ورقمي 01012345678")
    assert_true("name captured same turn as phone", ud.customer_name == "سارة", str(ud.customer_name))
    assert_true("phone captured same turn as name", ud.customer_phone == "01012345678", str(ud.customer_phone))
    assert_true("takeaway ready confirmation", "صح" in msg or "أكد" in msg or "تأكيد" in msg, msg)

    msg = await user_turn(session, chat, said, "تمام كده اكد")
    assert_true("takeaway submitted", ud.order_confirmed and ("takeaway", ud.call_id) in SUBMISSIONS, msg)
    return 9


async def reservation_customer_talks_over_slots() -> int:
    cfg = make_cfg()
    ud = make_ud("real-reservation", cfg)
    session, said = bind_all(ud, "greeter")
    chat = FakeChatContext()

    await user_turn(session, chat, said, "مساء الخير، عايز احجز ترابيزة")
    assert_true("routed reservation", current_flow(session) == "reservation")

    msg = await user_turn(session, chat, said, "بكرة الساعة 8 بالليل كده ينفع؟")
    assert_true("reservation time captured", bool(ud.reservation_time), str(ud.reservation_time))
    assert_true("asks guests after time", "كام" in msg or "شخص" in msg, msg)

    msg = await user_turn(session, chat, said, "احنا ٤ أفراد في فرع المعادي")
    assert_true("guests captured", ud.guests_count == 4, str(ud.guests_count))
    assert_true("branch captured from same turn", ud.selected_branch == "المعادي", str(ud.selected_branch))
    assert_true("asks contact after reservation slots", asks_name(msg) or ("الاسم" in msg and "موبايل" in msg), msg)

    msg = await user_turn(session, chat, said, "الاسم نور ورقمي 01012345678")
    assert_true("reservation name captured", ud.customer_name == "نور", str(ud.customer_name))
    assert_true("reservation phone captured", ud.customer_phone == "01012345678", str(ud.customer_phone))
    assert_true("reservation ready confirmation", "صح" in msg or "أكد" in msg or "تأكيد" in msg, msg)

    msg = await user_turn(session, chat, said, "ايوه")
    assert_true("reservation submitted", ud.reservation_confirmed and ("reservation", ud.call_id) in SUBMISSIONS, msg)
    return 11


async def cancellation_after_order_does_not_submit() -> int:
    cfg = make_cfg()
    ud = make_ud("real-cancel", cfg)
    session, said = bind_all(ud, "takeaway")
    chat = FakeChatContext()

    await user_turn(session, chat, said, "عايز برجر كبير وبطاطس")
    assert_true("order captured before cancel", bool(ud.order), str(ud.order))

    msg = await user_turn(session, chat, said, "لا خلاص الغي الطلب معلش")
    assert_true("cancel message spoken", "اتلغى" in msg or "تمام" in msg, msg)
    assert_true("not submitted after cancel", not ud.order_confirmed and ("takeaway", ud.call_id) not in SUBMISSIONS)
    assert_true("session marked transitional", bool(ud.session_transitional_state))
    return 4


async def greeter_menu_then_order_before_mode_no_reask() -> int:
    cfg = make_cfg()
    ud = make_ud("real-greeter-order-before-mode", cfg)
    session, said = bind_all(ud, "greeter")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "محتاج اطلب اوردر ايه المتاح حاليا؟")
    assert_true("greeter menu asks mode not order", "دليفري" in msg and "تيكاواي" in msg, msg)
    assert_true("still greeter after menu", current_flow(session) == "greeter")

    msg = await user_turn(session, chat, said, "ماشي هطلب برجر كبير")
    assert_true("greeter captured pre-mode order", qty(ud, "برجر كبير") == 1, str(ud.order))
    assert_true("greeter asks mode after order", "دليفري" in msg and "تيكاواي" in msg, msg)
    assert_true("no route before mode", current_flow(session) == "greeter")

    msg = await user_turn(session, chat, said, "هطلب تلاتة")
    assert_true("quantity-only updates pending order", qty(ud, "برجر كبير") == 3, str(ud.order))
    assert_true("still asks mode not order", "دليفري" in msg and "تيكاواي" in msg and not asks_order(msg), msg)

    await user_turn(session, chat, said, "توصيل ماشي")
    assert_true("routes delivery after mode", current_flow(session) == "delivery")
    assert_true("order survives mode route", qty(ud, "برجر كبير") == 3, str(ud.order))
    return 9


async def greeter_unavailable_item_stays_fast() -> int:
    cfg = make_cfg()
    ud = make_ud("real-greeter-unavailable", cfg)
    session, said = bind_all(ud, "greeter")
    chat = FakeChatContext()

    await user_turn(session, chat, said, "ايه المتاح حاليا؟")
    msg = await user_turn(session, chat, said, "ماشي هطلب شاورما لحمة")
    assert_true("unavailable item rejected fast", "مش" in msg and "المنيو" in msg, msg)
    assert_true("unavailable not stored as order", not ud.order, str(ud.order))

    msg = await user_turn(session, chat, said, "هطلب تلاتة")
    assert_true("quantity-only asks item", "تلاتة من إيه" in msg and "دليفري" in msg, msg)
    assert_true("still greeter after unavailable loop", current_flow(session) == "greeter")
    return 5


async def delivery_real_call_notes_zone_and_chunked_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("real-notes-zone-phone", cfg)
    session, said = bind_all(ud, "delivery")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "عايز برجر كبير")
    assert_true("order captured", qty(ud, "برجر كبير") == 1, str(ud.order))
    assert_true("upsell offered", "كولا" in msg, msg)

    msg = await user_turn(session, chat, said, "ماشي ضيف كولا")
    assert_true("upsell accepted", qty(ud, "كولا") == 1, str(ud.order))
    assert_true("asks special after upsell", "طلب خاص" in msg or "ملاحظة" in msg, msg)

    msg = await user_turn(session, chat, said, "اه اه يعني البرجر ما يكونش عليه اي كاتشب خالص")
    assert_true("special request captured", ud.special_requests and "كاتشب" in ud.special_requests, str(ud.special_requests))
    assert_true("after special asks address", asks_address(msg) or "العنوان" in msg, msg)

    msg = await user_turn(session, chat, said, "لا تمام")
    assert_true("empty ack does not clear special", ud.special_requests and "كاتشب" in ud.special_requests, str(ud.special_requests))
    assert_true("empty ack continues missing slot", asks_address(msg) or "العنوان" in msg, msg)

    msg = await user_turn(session, chat, said, "العنوان شبين الكوم شارع سعد زغلول برج الراشد")
    assert_true("unsupported zone rejected", "مش بنوصل" in msg and "مدينة نصر" in msg, msg)
    assert_true("unsupported address not stored", not ud.delivery_address, str(ud.delivery_address))

    await user_turn(session, chat, said, "ماشي 0155")
    msg = await user_turn(session, chat, said, "8950484")
    assert_true("chunked phone captured out of order", ud.customer_phone == "01558950484", str(ud.customer_phone))
    assert_true("still asks address after phone", asks_address(msg) or "العنوان" in msg, msg)
    return 15


async def name_question_not_captured_as_customer_name() -> int:
    cfg = make_cfg()
    ud = make_ud("real-name-protest", cfg)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45

    result = run_pipeline(text="اه تمام الاسم ايه يا ابني", flow="delivery", ud=ud)
    assert_true("name protest not captured", not ud.customer_name, str(ud.customer_name))
    assert_true("name protest not slot captured", result.decision_reason != "slot_captured:name", result.decision_reason)
    return 2


async def livekit_log_regressions_do_not_forget_order_or_fake_name() -> int:
    cfg = make_livekit_log_cfg()

    assert_true("bare ana is not a name", agent._extract_name_candidate("أنا.") is None)

    ud = make_ud("real-livekit-regression", cfg)
    result = run_pipeline(
        text="محتاج اطلب ساندوتش شاورما فراخ وساندوتش شاورما لحمة وآآ.",
        flow="delivery",
        ud=ud,
    )
    assert_true("first split order captured", result.decision_reason == "slot_captured:order", result.decision_reason)
    assert_true("shawarma chicken kept", qty(ud, "شاورما فراخ") == 1, str(ud.order))
    assert_true("shawarma meat kept", qty(ud, "شاورما لحمة") == 1, str(ud.order))

    result = run_pipeline(
        text="بيتزا مارجريتا محتاج منها خمسة.",
        flow="delivery",
        ud=ud,
    )
    assert_true("second split order captured", result.decision_reason == "slot_captured:order", result.decision_reason)
    assert_true("pizza appended not replaced", qty(ud, "بيتزا مارجريتا") == 5, str(ud.order))
    assert_true("old items survived later order turn", qty(ud, "شاورما فراخ") == 1 and qty(ud, "شاورما لحمة") == 1, str(ud.order))
    return 6


TESTS = [
    messy_delivery_laf_w_dawaran,
    delivery_to_takeaway_mid_call_keeps_memory,
    reservation_customer_talks_over_slots,
    cancellation_after_order_does_not_submit,
    greeter_menu_then_order_before_mode_no_reask,
    greeter_unavailable_item_stays_fast,
    delivery_real_call_notes_zone_and_chunked_phone,
    name_question_not_captured_as_customer_name,
    livekit_log_regressions_do_not_forget_order_or_fake_name,
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
    print(f"EGYPTIAN_REAL_CALL_TESTS_PASSED: {len(TESTS) - len(failures)}/{len(TESTS)}")
    print(f"EGYPTIAN_REAL_CALL_CHECKS: {checks}")
    if failures:
        print("FAILED_EGYPTIAN_REAL_CALL_TESTS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
