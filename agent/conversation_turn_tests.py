import asyncio
import sys
from types import SimpleNamespace

from livekit.agents import StopResponse

import agent
from call_scenario_tests import SUBMISSIONS, make_cfg, make_ud, patched_backend
from complex_order_tests import payload_items, qty
from repeated_question_tests import asks_address, asks_name, asks_order, asks_phone


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class FakeMessage:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.text_content = text
        self.content = text


class FakeChatContext:
    def __init__(self) -> None:
        self.items: list[FakeMessage] = []

    def add_message(self, *, role: str, content: str) -> None:
        self.items.append(FakeMessage(role, content))


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


def reasks_address(text: str) -> bool:
    text = text or ""
    return any(phrase in text for phrase in ("عنوانك", "العنوان إيه", "ممكن العنوان", "فين هنوصل"))


def bind_all(ud: agent.UserData, start_flow: str) -> tuple[SimpleNamespace, list[str]]:
    said: list[str] = []
    session = SimpleNamespace(
        userdata=ud,
        current_agent=ud.agents[start_flow],
        options=SimpleNamespace(preemptive_generation=False),
    )

    async def say(text: str, **_: object) -> None:
        said.append(text)

    def update_agent(target: object) -> None:
        session.current_agent = target

    session.say = say
    session.update_agent = update_agent
    for inst in ud.agents.values():
        inst._get_activity_or_raise = lambda session=session: SimpleNamespace(session=session)
    return session, said


async def user_turn(session: SimpleNamespace, chat: FakeChatContext, said: list[str], text: str) -> str:
    before = len(said)
    try:
        await session.current_agent.on_user_turn_completed(chat, FakeMessage("user", text))
    except StopResponse:
        pass
    return said[-1] if len(said) > before else ""


async def transcript_delivery_happy_path_no_repeats() -> int:
    cfg = make_cfg()
    ud = make_ud("turn-delivery-happy", cfg)
    session, said = bind_all(ud, "delivery")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "عايز ٢ برجر كبير و٣ بطاطس وكولا دليفري")
    assert_true("turn order saved", qty(ud, "برجر كبير") == 2 and qty(ud, "بطاطس") == 3 and qty(ud, "كولا") == 1, str(payload_items(ud)))
    assert_true("last user message saved after order", ud.last_user_message.endswith("دليفري"), ud.last_user_message)
    assert_true("after order asks address", asks_address(msg), msg)
    assert_true("after order no repeat order", not asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "العنوان مدينة نصر شارع عباس العقاد عمارة ١٢ الدور ٣")
    assert_true("address saved", ud.delivery_address is not None and "عباس" in ud.delivery_address, str(ud.delivery_address))
    assert_true("after address asks name", asks_name(msg), msg)
    assert_true("after address no order repeat", not asks_order(msg), msg)
    assert_true("after address no address repeat", not reasks_address(msg), msg)

    msg = await user_turn(session, chat, said, "اسمي أحمد علي")
    assert_true("name saved", ud.customer_name == "أحمد علي", str(ud.customer_name))
    assert_true("after name asks phone", asks_phone(msg), msg)
    assert_true("after name no name repeat", not asks_name(msg), msg)
    assert_true("after name no order repeat", not asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "01012345678")
    assert_true("phone saved", ud.customer_phone == "01012345678", str(ud.customer_phone))
    assert_true("after phone asks confirm", "صح" in msg or "أكد" in msg or "تأكيد" in msg, msg)
    assert_true("after phone no phone repeat", not asks_phone(msg), msg)
    assert_true("after phone no order repeat", not asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "ايوه تمام")
    assert_true("delivery submitted", ud.order_confirmed and ("delivery", ud.call_id) in SUBMISSIONS, msg)
    assert_true("confirm reply no slot repeat", not asks_phone(msg) and not asks_order(msg) and not asks_address(msg), msg)
    return 15


async def transcript_takeaway_upsell_special_contact_confirm() -> int:
    cfg = make_cfg()
    ud = make_ud("turn-takeaway-upsell", cfg)
    session, said = bind_all(ud, "takeaway")
    chat = FakeChatContext()

    msg = await user_turn(session, chat, said, "برجر كبير اتنين وبطاطس")
    assert_true("spoken qty order saved", qty(ud, "برجر كبير") == 2 and qty(ud, "بطاطس") == 1, str(payload_items(ud)))
    assert_true("upsell pending", ud.pending_upsell_item == "كولا", str(ud.pending_upsell_item))
    assert_true("upsell question not order repeat", not asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "لا من غير بصل")
    assert_true("special saved after upsell reject", ud.special_requests and "بصل" in ud.special_requests, str(ud.special_requests))
    assert_true("after special asks name", asks_name(msg), msg)
    assert_true("after special no order repeat", not asks_order(msg), msg)

    msg = await user_turn(session, chat, said, "اسمي منى")
    assert_true("takeaway name saved", ud.customer_name == "منى", str(ud.customer_name))
    assert_true("takeaway asks phone", asks_phone(msg), msg)

    msg = await user_turn(session, chat, said, "01012345678")
    assert_true("takeaway phone saved", ud.customer_phone == "01012345678", str(ud.customer_phone))
    assert_true("takeaway ready confirmation", "صح" in msg or "أكد" in msg or "تأكيد" in msg, msg)
    assert_true("takeaway no phone repeat", not asks_phone(msg), msg)

    await user_turn(session, chat, said, "تمام")
    assert_true("takeaway submitted", ud.order_confirmed and ("takeaway", ud.call_id) in SUBMISSIONS)
    return 12


async def transcript_greeter_prefill_then_delivery_no_contact_repeat() -> int:
    cfg = make_cfg()
    ud = make_ud("turn-greeter-prefill-delivery", cfg)
    session, said = bind_all(ud, "greeter")
    chat = FakeChatContext()

    await user_turn(session, chat, said, "اسمي كريم ورقمي 01012345678 وعايز دليفري")
    assert_true("greeter routed delivery", session.current_agent.__class__.__name__.lower() == "delivery")
    assert_true("greeter captured name", ud.customer_name == "كريم", str(ud.customer_name))
    assert_true("greeter captured phone", ud.customer_phone == "01012345678", str(ud.customer_phone))

    msg = await user_turn(session, chat, said, "عايز برجر كبير وكولا")
    assert_true("prefilled order saved", qty(ud, "برجر كبير") == 1 and qty(ud, "كولا") == 1, str(payload_items(ud)))
    assert_true("prefilled asks address", asks_address(msg), msg)
    assert_true("prefilled no phone repeat", not asks_phone(msg), msg)
    assert_true("prefilled no name repeat", not asks_name(msg), msg)

    msg = await user_turn(session, chat, said, "مدينة نصر شارع الطيران عمارة ٥")
    assert_true("prefilled address saved", ud.delivery_address is not None, str(ud.delivery_address))
    assert_true("prefilled ready confirm", "صح" in msg or "أكد" in msg or "تأكيد" in msg, msg)
    assert_true("prefilled no contact repeat after address", not asks_phone(msg) and not asks_name(msg), msg)
    return 10


async def transcript_incremental_add_uses_real_last_user_message() -> int:
    cfg = make_cfg()
    ud = make_ud("turn-incremental-last-message", cfg)
    session, said = bind_all(ud, "takeaway")
    chat = FakeChatContext()
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "سارة"
    ud.customer_phone = "01012345678"
    ud.upsell_offered = True

    msg = await user_turn(session, chat, said, "ضيف كولا")
    assert_true("real turn updates last message", ud.last_user_message == "ضيف كولا", ud.last_user_message)
    assert_true("cola added once", qty(ud, "كولا") == 1, str(payload_items(ud)))
    assert_true("ready add no contact repeat", not asks_phone(msg) and not asks_name(msg), msg)

    await user_turn(session, chat, said, "ضيف كولا")
    assert_true("duplicate add not doubled", qty(ud, "كولا") == 1, str(payload_items(ud)))
    return 5


LEGACY_INTERCEPT_TESTS = [
    transcript_delivery_happy_path_no_repeats,
    transcript_takeaway_upsell_special_contact_confirm,
    transcript_greeter_prefill_then_delivery_no_contact_repeat,
    transcript_incremental_add_uses_real_last_user_message,
]
TESTS: list = LEGACY_INTERCEPT_TESTS


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
    print(f"CONVERSATION_TURN_TESTS_PASSED: {len(TESTS) - len(failures)}/{len(TESTS)}")
    print(f"CONVERSATION_TURN_CHECKS: {checks}")
    if failures:
        print("FAILED_CONVERSATION_TURN_TESTS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
