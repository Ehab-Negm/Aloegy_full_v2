import asyncio
import sys
from types import SimpleNamespace

import agent
from call_scenario_tests import make_cfg, make_ud, patched_backend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def ctx(flow: object, ud: agent.UserData) -> SimpleNamespace:
    return SimpleNamespace(userdata=ud, session=SimpleNamespace(current_agent=flow))


def has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in (text or "") for word in words)


def asks_phone(text: str) -> bool:
    return has_any(text, ("موبايل", "رقمك", "رقم الموبايل", "التليفون"))


def asks_order(text: str) -> bool:
    return has_any(text, ("تحب تطلب", "تطلب إيه", "طلبك إيه", "عايز تطلب", "محتاج أعرف الطلب", "محتاج الطلب"))


def asks_name(text: str) -> bool:
    text = text or ""
    return has_any(text, ("اسمك", "الاسم", "مين حضرتك", "قولّي اسم")) or " اسم " in f" {text} "


def asks_address(text: str) -> bool:
    return has_any(text, ("العنوان", "عنوانك", "نوصل"))


def asks_guests(text: str) -> bool:
    return has_any(text, ("كام شخص", "كام فرد", "هتكونوا كام"))


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


async def call_takeaway_phone_order_name() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-phone-order-name", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    msg1 = await agent.update_phone("01012345678", c)
    await flow.update_order(["برجر كبير"], c)
    msg2 = await agent.update_name("احمد", c)
    assert_true("phone first asks order", asks_order(msg1), msg1)
    assert_true("name after existing phone does not ask phone", not asks_phone(msg2), msg2)
    assert_true("name after phone/order asks confirmation", "صح" in msg2, msg2)
    return 3


async def call_takeaway_order_phone_then_name() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-order-phone-name", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    msg1 = await agent.update_phone("01012345678", c)
    msg2 = await agent.update_name("احمد", c)
    assert_true("phone after order asks name", asks_name(msg1), msg1)
    assert_true("phone after order does not ask order", not asks_order(msg1), msg1)
    assert_true("name after phone does not ask phone", not asks_phone(msg2), msg2)
    return 3


async def call_takeaway_special_after_name_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-special-ready", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    ud.customer_name = "منى"
    ud.customer_phone = "01012345678"
    msg = await flow.update_special_requests("من غير بصل", c)
    assert_true("special ready does not ask name", not asks_name(msg), msg)
    assert_true("special ready does not ask phone", not asks_phone(msg), msg)
    assert_true("special ready asks confirmation", "صح" in msg, msg)
    return 3


async def call_takeaway_no_special_after_name_only() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-special-name-only", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    ud.customer_name = "كريم"
    msg = await flow.update_special_requests("لا", c)
    assert_true("no special with name asks phone", asks_phone(msg), msg)
    assert_true("no special with name does not ask name", not asks_name(msg), msg)
    return 2


async def call_takeaway_confirm_does_not_resubmit() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-confirm", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "هند"
    ud.customer_phone = "01012345678"
    msg1 = await flow.confirm_order(c)
    msg2 = await flow.confirm_order(c)
    assert_true("first confirm records", ud.order_confirmed, msg1)
    assert_true("second confirm does not ask phone", not asks_phone(msg2), msg2)
    assert_true("second confirm does not ask order", not asks_order(msg2), msg2)
    return 3


async def call_delivery_order_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-order-phone", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("delivery phone after order asks address", asks_address(msg), msg)
    assert_true("delivery phone after order does not ask order", not asks_order(msg), msg)
    return 2


async def call_delivery_order_address_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-order-address-phone", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    await flow.update_delivery_address("مدينة نصر شارع 1 عمارة 2", "مدينة نصر", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("delivery phone after address asks name", asks_name(msg), msg)
    assert_true("delivery phone after address does not ask phone", not asks_phone(msg), msg)
    assert_true("delivery phone after address does not ask order", not asks_order(msg), msg)
    return 3


async def call_delivery_name_after_phone_ready() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-name-after-phone-ready", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.delivery_address = "مدينة نصر شارع 1 عمارة 2"
    ud.delivery_zone = "مدينة نصر"
    ud.customer_phone = "01012345678"
    msg = await agent.update_name("سارة", c)
    assert_true("delivery name after phone does not ask phone", not asks_phone(msg), msg)
    assert_true("delivery name after phone asks confirmation", "صح" in msg, msg)
    return 2


async def call_delivery_special_ready() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-special-ready", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.delivery_address = "مدينة نصر شارع 1 عمارة 2"
    ud.delivery_zone = "مدينة نصر"
    ud.customer_name = "علي"
    ud.customer_phone = "01012345678"
    msg = await flow.update_special_requests("لا", c)
    assert_true("delivery special ready does not ask address", not asks_address(msg), msg)
    assert_true("delivery special ready does not ask phone", not asks_phone(msg), msg)
    assert_true("delivery special ready confirms", "صح" in msg, msg)
    return 3


async def call_delivery_confirm_duplicate() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-confirm", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.delivery_address = "مدينة نصر شارع 1 عمارة 2"
    ud.delivery_zone = "مدينة نصر"
    ud.customer_name = "ليلى"
    ud.customer_phone = "01012345678"
    await flow.confirm_delivery(c)
    msg = await flow.confirm_delivery(c)
    assert_true("delivery second confirm no phone", not asks_phone(msg), msg)
    assert_true("delivery second confirm no order", not asks_order(msg), msg)
    return 2


async def call_reservation_phone_time() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-reservation-phone-time", cfg)
    flow = ud.agents["reservation"]
    c = ctx(flow, ud)
    msg1 = await agent.update_phone("01012345678", c)
    msg2 = await flow.update_reservation_time("بكرة الساعة 8 بالليل", c)
    assert_true("reservation phone first asks time", "تحجز" in msg1, msg1)
    assert_true("reservation time asks guests", asks_guests(msg2), msg2)
    return 2


async def call_reservation_time_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-reservation-time-phone", cfg)
    flow = ud.agents["reservation"]
    c = ctx(flow, ud)
    await flow.update_reservation_time("بكرة الساعة 8 بالليل", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("reservation phone after time asks guests", asks_guests(msg), msg)
    assert_true("reservation phone after time does not ask phone", not asks_phone(msg), msg)
    return 2


async def call_reservation_name_after_phone_ready() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-reservation-ready-name", cfg)
    flow = ud.agents["reservation"]
    c = ctx(flow, ud)
    ud.reservation_time = "بكرة الساعة 8 بالليل"
    ud.reservation_time_iso = "2026-04-28T20:00:00+02:00"
    ud.guests_count = 4
    ud.selected_branch = "مدينة نصر"
    ud.customer_phone = "01012345678"
    msg = await agent.update_name("محمد", c)
    assert_true("reservation name after phone does not ask phone", not asks_phone(msg), msg)
    assert_true("reservation name after phone confirms", "صح" in msg, msg)
    return 2


async def call_complaint_name_after_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-complaint-name-after-phone", cfg)
    flow = ud.agents["complaint"]
    c = ctx(flow, ud)
    await flow.log_complaint("الطلب وصل ناقص", "order_issue", c)
    await agent.update_phone("01012345678", c)
    msg = await agent.update_name("محمود", c)
    assert_true("complaint name after phone no phone ask", not asks_phone(msg), msg)
    assert_true("complaint logged", ud.complaint_logged, msg)
    return 2


async def call_complaint_phone_after_name() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-complaint-phone-after-name", cfg)
    flow = ud.agents["complaint"]
    c = ctx(flow, ud)
    await flow.log_complaint("الدليفري اتأخر جدا", "delivery", c)
    await agent.update_name("سارة", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("complaint phone after name no phone ask", not asks_phone(msg), msg)
    assert_true("complaint logged after phone", ud.complaint_logged, msg)
    return 2


async def call_takeaway_chunked_phone_after_order() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-chunked", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    await agent.update_phone("010123", c)
    msg = await agent.update_phone("45678", c)
    assert_true("chunked phone after order asks name", asks_name(msg), msg)
    assert_true("chunked phone after order no order ask", not asks_order(msg), msg)
    return 2


async def call_delivery_chunked_phone_after_address() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-chunked", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    await flow.update_delivery_address("مدينة نصر شارع 1 عمارة 2", "مدينة نصر", c)
    await agent.update_phone("010123", c)
    msg = await agent.update_phone("45678", c)
    assert_true("delivery chunked phone asks name", asks_name(msg), msg)
    assert_true("delivery chunked phone no order ask", not asks_order(msg), msg)
    return 2


async def call_takeaway_prefilled_contact_order() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-prefilled", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = await flow.update_order(["برجر كبير"], c)
    assert_true("prefilled order response no phone ask", not asks_phone(msg), msg)
    assert_true("prefilled order response no name ask", not asks_name(msg), msg)
    return 2


async def call_delivery_prefilled_contact_order() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-prefilled", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = await flow.update_order(["برجر كبير"], c)
    assert_true("delivery prefilled order no phone ask", not asks_phone(msg), msg)
    assert_true("delivery prefilled order no name ask", not asks_name(msg), msg)
    return 2


async def call_takeaway_name_first_then_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-name-phone", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    msg1 = await agent.update_name("أحمد", c)
    msg2 = await agent.update_phone("01012345678", c)
    assert_true("name first can ask order", asks_order(msg1), msg1)
    assert_true("phone after name asks order", asks_order(msg2), msg2)
    return 2


async def call_delivery_name_first_then_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-name-phone", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    msg1 = await agent.update_name("أحمد", c)
    msg2 = await agent.update_phone("01012345678", c)
    assert_true("delivery name first can ask order", asks_order(msg1), msg1)
    assert_true("delivery phone after name asks order", asks_order(msg2), msg2)
    return 2


async def call_takeaway_order_name_phone_no_special() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-takeaway-order-name-phone", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    await agent.update_name("أحمد", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("phone after order/name confirms", "صح" in msg, msg)
    assert_true("phone after order/name no order ask", not asks_order(msg), msg)
    return 2


async def call_delivery_order_address_name_phone() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-delivery-ready-flow", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    await flow.update_order(["برجر كبير"], c)
    await flow.update_delivery_address("مدينة نصر شارع 1 عمارة 2", "مدينة نصر", c)
    await agent.update_name("أحمد", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("delivery ready phone confirms", "صح" in msg, msg)
    assert_true("delivery ready phone no phone ask", not asks_phone(msg), msg)
    return 2


async def call_reservation_full_phone_last() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-reservation-phone-last", cfg)
    flow = ud.agents["reservation"]
    c = ctx(flow, ud)
    await flow.update_reservation_time("بكرة الساعة 8 بالليل", c)
    await flow.update_guests_count(3, c)
    await flow.update_branch("مدينة نصر", c)
    await agent.update_name("أحمد", c)
    msg = await agent.update_phone("01012345678", c)
    assert_true("reservation phone last confirms", "صح" in msg, msg)
    assert_true("reservation phone last no phone ask", not asks_phone(msg), msg)
    return 2


async def call_reservation_notes_after_contact_ready() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-reservation-notes", cfg)
    flow = ud.agents["reservation"]
    c = ctx(flow, ud)
    ud.reservation_time = "بكرة الساعة 8 بالليل"
    ud.reservation_time_iso = "2026-04-28T20:00:00+02:00"
    ud.guests_count = 3
    ud.selected_branch = "مدينة نصر"
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = await flow.update_reservation_notes("لا", c)
    assert_true("reservation notes ready no phone ask", not asks_phone(msg), msg)
    assert_true("reservation notes ready no name ask", not asks_name(msg), msg)
    return 2


async def call_greeter_prefill_then_delivery_no_phone_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-greeter-prefill-delivery", cfg)
    flow = ud.agents["delivery"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    await flow.update_order(["برجر كبير"], c)
    msg = await flow.update_delivery_address("مدينة نصر شارع 1 عمارة 2", "مدينة نصر", c)
    assert_true("prefilled delivery address no phone ask", not asks_phone(msg), msg)
    assert_true("prefilled delivery address no name ask", not asks_name(msg), msg)
    return 2


async def call_greeter_prefill_then_takeaway_no_phone_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-greeter-prefill-takeaway", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = await flow.update_order(["برجر كبير"], c)
    assert_true("prefilled takeaway order no phone ask", not asks_phone(msg), msg)
    assert_true("prefilled takeaway order no name ask", not asks_name(msg), msg)
    return 2


async def call_backend_failure_no_restart_questions() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-backend-failure", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    ud.write_health.write_available = False
    msg = await flow.confirm_order(c)
    assert_true("backend failure no phone ask", not asks_phone(msg), msg)
    assert_true("backend failure no order ask", not asks_order(msg), msg)
    return 2


async def call_order_add_keeps_contact_no_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-order-add-contact", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "ضيف كولا"
    msg = await flow.update_order(["كولا"], c)
    assert_true("add item no phone ask", not asks_phone(msg), msg)
    assert_true("add item no name ask", not asks_name(msg), msg)
    return 2


async def call_order_replace_keeps_contact_no_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("repeat-order-replace-contact", cfg)
    flow = ud.agents["takeaway"]
    c = ctx(flow, ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    await flow.update_order(["برجر كبير"], c)
    ud.last_user_message = "لا خليه كولا بس"
    msg = await flow.update_order(["كولا"], c)
    assert_true("replace item no phone ask", not asks_phone(msg), msg)
    assert_true("replace item no name ask", not asks_name(msg), msg)
    return 2


CALLS = [
    call_takeaway_phone_order_name,
    call_takeaway_order_phone_then_name,
    call_takeaway_special_after_name_phone,
    call_takeaway_no_special_after_name_only,
    call_takeaway_confirm_does_not_resubmit,
    call_delivery_order_phone,
    call_delivery_order_address_phone,
    call_delivery_name_after_phone_ready,
    call_delivery_special_ready,
    call_delivery_confirm_duplicate,
    call_reservation_phone_time,
    call_reservation_time_phone,
    call_reservation_name_after_phone_ready,
    call_complaint_name_after_phone,
    call_complaint_phone_after_name,
    call_takeaway_chunked_phone_after_order,
    call_delivery_chunked_phone_after_address,
    call_takeaway_prefilled_contact_order,
    call_delivery_prefilled_contact_order,
    call_takeaway_name_first_then_phone,
    call_delivery_name_first_then_phone,
    call_takeaway_order_name_phone_no_special,
    call_delivery_order_address_name_phone,
    call_reservation_full_phone_last,
    call_reservation_notes_after_contact_ready,
    call_greeter_prefill_then_delivery_no_phone_repeat,
    call_greeter_prefill_then_takeaway_no_phone_repeat,
    call_backend_failure_no_restart_questions,
    call_order_add_keeps_contact_no_repeat,
    call_order_replace_keeps_contact_no_repeat,
]


async def main() -> int:
    failures: list[str] = []
    checks = 0
    with patched_backend():
        for index, call in enumerate(CALLS, start=1):
            try:
                count = await call()
                checks += count
                print(f"{index:02d}. {call.__name__}: PASS ({count} checks)")
            except Exception as exc:
                failures.append(f"{call.__name__}: {exc}")
                print(f"{index:02d}. {call.__name__}: FAIL - {exc}")
    print(f"TEXT_CALLS_PASSED: {len(CALLS) - len(failures)}/{len(CALLS)}")
    print(f"ANTI_REPEAT_CHECKS: {checks}")
    if failures:
        print("FAILED_CALLS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
