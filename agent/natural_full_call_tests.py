from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import agent
from call_scenario_tests import SUBMISSIONS, make_cfg, make_ud, patched_backend
from repeated_question_tests import asks_address, asks_name, asks_order, asks_phone


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


BANNED_ROBOTIC = (
    "بكل سرور",
    "يسعدني",
    "أتشرف",
    "في خدمتك",
    "هل تود",
    "عزيزي العميل",
    "عميلنا العزيز",
    "سيدي",
    "سيدتي",
    "برجاء",
    "تم تنفيذ طلبكم بنجاح",
)


@dataclass
class NaturalCall:
    name: str
    start_flow: str
    cfg: agent.RestaurantConfig = field(default_factory=make_cfg)
    ud: agent.UserData = field(init=False)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ud = make_ud(f"natural-{self.name}", self.cfg)
        self.say_agent(self.current_agent()._opening)

    def current_agent(self):
        return self.ud.agents[self.start_flow]

    def ctx(self, flow=None) -> SimpleNamespace:
        flow = flow or self.current_agent()
        return SimpleNamespace(userdata=self.ud, session=SimpleNamespace(current_agent=flow))

    def customer(self, text: str) -> None:
        self.transcript.append(("customer", text))

    def say_agent(self, text: str, *, allow_long: bool = False, internal_tool_result: bool = False) -> str:
        visible = customer_visible_reply(text)
        self.transcript.append(("agent", visible))
        self._check_style(visible, allow_long=allow_long, internal_tool_result=internal_tool_result)
        return visible

    def transfer_to(self, flow_name: str) -> None:
        self.start_flow = flow_name
        self.say_agent(self.current_agent()._opening)

    def _check_style(self, text: str, *, allow_long: bool, internal_tool_result: bool) -> None:
        compact = " ".join((text or "").split())
        if not compact:
            self.failures.append("empty agent reply")
            return
        for phrase in BANNED_ROBOTIC:
            if phrase in compact:
                self.failures.append(f"robotic phrase `{phrase}` in: {compact}")
        if compact.count("؟") > 1:
            self.failures.append(f"multi-question reply: {compact}")
        word_count = len(compact.split())
        limit = 55 if allow_long else 32
        if word_count > limit:
            self.failures.append(f"too long ({word_count}>{limit}): {compact}")
        if not internal_tool_result and compact.startswith("["):
            self.failures.append(f"internal state leaked to customer: {compact}")
        previous_agent_replies = [msg for role, msg in self.transcript[:-1] if role == "agent"]
        if previous_agent_replies and previous_agent_replies[-1] == compact:
            self.failures.append(f"identical consecutive agent reply: {compact}")

    def assert_no_failures(self) -> int:
        if self.failures:
            raise AssertionError("; ".join(self.failures))
        return len([1 for role, _ in self.transcript if role == "agent"])

    def assert_no_reask_known_slots(self, reply: str) -> None:
        normalized = " ".join((reply or "").split())
        order_ack = "الطلب الحالي" in normalized or "طلبك" in normalized
        name_ack = "الاسم" in normalized and ("سجلت" in normalized or "تمام" in normalized)
        phone_ack = "رقم الموبايل" in normalized and ("صح" in normalized or "تمام" in normalized)
        address_ack = "سجلت العنوان" in normalized or "العنوان:" in normalized
        if self.ud.order and asks_order(reply) and not order_ack:
            self.failures.append(f"re-asked order after capture: {reply}")
        if self.ud.customer_name and asks_name(reply) and not name_ack:
            self.failures.append(f"re-asked name after capture: {reply}")
        if self.ud.customer_phone and asks_phone(reply) and not phone_ack:
            self.failures.append(f"re-asked phone after capture: {reply}")
        if self.ud.delivery_address and asks_address(reply) and not address_ack:
            self.failures.append(f"re-asked address after capture: {reply}")


def customer_visible_reply(text: str) -> str:
    """Strip tool-only state blocks before judging customer-facing copy."""
    text = (text or "").strip()
    if text.startswith("["):
        return re.sub(r"^\[[^\]]+\]\s*", "", text, count=1).strip()
    return text


async def scenario_delivery_full_complex() -> int:
    call = NaturalCall("delivery-full-complex", "greeter")
    delivery = call.ud.agents["delivery"]
    call.customer("أهلا، عايز دليفري")
    call.transfer_to("delivery")

    call.customer("عايز اتنين برجر كبير وبطاطس")
    reply = call.say_agent(await delivery.update_order(["برجر كبير × 2", "بطاطس"], call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)

    call.customer("لا، من غير بصل")
    reply = call.say_agent(await delivery.update_special_requests("من غير بصل", call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)

    call.customer("العنوان مدينة نصر شارع عباس العقاد عمارة 12 الدور التالت")
    reply = call.say_agent(await delivery.update_delivery_address(
        "مدينة نصر شارع عباس العقاد عمارة 12 الدور التالت",
        "مدينة نصر",
        call.ctx(delivery),
    ))
    call.assert_no_reask_known_slots(reply)

    call.customer("اسمي أحمد ورقمي 01012345678")
    reply = call.say_agent(await agent.update_name("أحمد", call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(delivery)), allow_long=True)
    call.assert_no_reask_known_slots(reply)

    call.customer("أكد")
    reply = call.say_agent(await delivery.confirm_delivery(call.ctx(delivery)))
    if not call.ud.order_confirmed:
        call.failures.append("delivery order not confirmed")
    if ("delivery", call.ud.call_id) not in SUBMISSIONS:
        call.failures.append("delivery submission missing")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_takeaway_change_then_confirm() -> int:
    call = NaturalCall("takeaway-change", "takeaway")
    flow = call.ud.agents["takeaway"]

    call.customer("برجر كبير وكولا")
    reply = call.say_agent(await flow.update_order(["برجر كبير", "كولا"], call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("لا خليهم اتنين برجر وكولا واحدة")
    call.ud.last_user_message = "لا خليهم اتنين برجر وكولا واحدة"
    reply = call.say_agent(await flow.update_order(["برجر كبير × 2", "كولا"], call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("مفيش طلب خاص")
    reply = call.say_agent(await flow.update_special_requests("لا", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("الاسم منى ورقمي 01012345678")
    reply = call.say_agent(await agent.update_name("منى", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(flow)), allow_long=True)
    call.assert_no_reask_known_slots(reply)

    call.customer("تمام أكد")
    reply = call.say_agent(await flow.confirm_order(call.ctx(flow)))
    if not call.ud.order_confirmed:
        call.failures.append("takeaway order not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_prefilled_contact_delivery() -> int:
    call = NaturalCall("prefilled-contact-delivery", "greeter")
    delivery = call.ud.agents["delivery"]

    call.customer("أنا كريم ورقمي 01012345678 وعايز توصيل")
    call.ud.customer_name = "كريم"
    call.ud.customer_phone = "01012345678"
    call.transfer_to("delivery")

    call.customer("هات برجر كبير وبطاطس")
    reply = call.say_agent(await delivery.update_order(["برجر كبير", "بطاطس"], call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)

    call.customer("مدينة نصر شارع الطيران عمارة 5")
    reply = call.say_agent(await delivery.update_delivery_address(
        "مدينة نصر شارع الطيران عمارة 5",
        "مدينة نصر",
        call.ctx(delivery),
    ), allow_long=True)
    if asks_name(reply) or asks_phone(reply):
        call.failures.append(f"prefilled contact re-asked: {reply}")

    call.customer("صح أكد")
    reply = call.say_agent(await delivery.confirm_delivery(call.ctx(delivery)))
    if not call.ud.order_confirmed:
        call.failures.append("prefilled delivery not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_delivery_min_order_recovery() -> int:
    cfg = make_cfg()
    cfg.min_order = 80
    call = NaturalCall("delivery-min-recovery", "delivery", cfg=cfg)
    delivery = call.ud.agents["delivery"]

    call.customer("عايز كولا بس دليفري")
    reply = call.say_agent(await delivery.update_order(["كولا"], call.ctx(delivery)), allow_long=True)
    if "أقل طلب" not in reply:
        call.failures.append(f"min order not explained: {reply}")

    call.customer("طيب ضيف اتنين برجر كبير")
    call.ud.last_user_message = "ضيف اتنين برجر كبير"
    reply = call.say_agent(await delivery.update_order(["برجر كبير × 2"], call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)

    call.customer("العنوان المعادي شارع 9 برج 12")
    reply = call.say_agent(await delivery.update_delivery_address("المعادي شارع 9 برج 12", "المعادي", call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)

    call.customer("أحمد 01012345678")
    reply = call.say_agent(await agent.update_name("أحمد", call.ctx(delivery)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(delivery)), allow_long=True)
    call.assert_no_reask_known_slots(reply)

    call.customer("أكد")
    reply = call.say_agent(await delivery.confirm_delivery(call.ctx(delivery)))
    if not call.ud.order_confirmed:
        call.failures.append("min recovery order not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_unavailable_item_recovery() -> int:
    call = NaturalCall("unavailable-recovery", "takeaway")
    flow = call.ud.agents["takeaway"]

    call.customer("عايز مياه")
    reply = call.say_agent(await flow.update_order(["مياه"], call.ctx(flow)), allow_long=True)
    if "مش موجود" not in reply and "مش متاح" not in reply:
        call.failures.append(f"unavailable item not explained: {reply}")

    call.customer("خلاص برجر كبير")
    reply = call.say_agent(await flow.update_order(["برجر كبير"], call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("الاسم سارة والرقم 01012345678")
    reply = call.say_agent(await agent.update_name("سارة", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(flow)), allow_long=True)
    call.assert_no_reask_known_slots(reply)

    call.customer("أكد")
    reply = call.say_agent(await flow.confirm_order(call.ctx(flow)))
    if not call.ud.order_confirmed:
        call.failures.append("unavailable recovery not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_reservation_full() -> int:
    call = NaturalCall("reservation-full", "reservation")
    flow = call.ud.agents["reservation"]

    call.customer("عايز أحجز بكرة الساعة 8")
    reply = call.say_agent(await flow.update_reservation_time("بكرة الساعة 8 بالليل", call.ctx(flow)))
    if "كام" not in reply and "عدد" not in reply:
        call.failures.append(f"reservation did not ask guests: {reply}")

    call.customer("4 أشخاص")
    reply = call.say_agent(await flow.update_guests_count(4, call.ctx(flow)))
    if "فرع" not in reply:
        call.failures.append(f"reservation did not ask branch: {reply}")

    call.customer("مدينة نصر")
    reply = call.say_agent(await flow.update_branch("مدينة نصر", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("باسم نور ورقمي 01012345678")
    reply = call.say_agent(await agent.update_name("نور", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(flow)), allow_long=True)
    call.assert_no_reask_known_slots(reply)

    call.customer("أكد الحجز")
    reply = call.say_agent(await flow.confirm_reservation(call.ctx(flow)))
    if not call.ud.reservation_confirmed:
        call.failures.append("reservation not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


async def scenario_complaint_full() -> int:
    call = NaturalCall("complaint-full", "complaint")
    flow = call.ud.agents["complaint"]

    call.customer("عندي شكوى، الطلب اتأخر ساعة")
    reply = call.say_agent(await flow.log_complaint("الطلب اتأخر ساعة", "delivery", call.ctx(flow)))
    if "اسم" not in reply:
        call.failures.append(f"complaint did not ask name: {reply}")

    call.customer("اسمي محمود ورقمي 01012345678")
    reply = call.say_agent(await agent.update_name("محمود", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(flow)), allow_long=True)
    call.assert_no_reask_known_slots(reply)
    if not call.ud.complaint_logged:
        call.failures.append("complaint not logged")
    return call.assert_no_failures()


async def scenario_duplicate_confirm_safe() -> int:
    call = NaturalCall("duplicate-confirm", "takeaway")
    flow = call.ud.agents["takeaway"]
    call.ud.order = ["برجر كبير"]
    call.ud.order_validated = True
    call.ud.order_total = 45
    call.ud.customer_name = "هند"
    call.ud.customer_phone = "01012345678"

    call.customer("أكد")
    first = call.say_agent(await flow.confirm_order(call.ctx(flow)))
    call.customer("أكد تاني")
    second = call.say_agent(await flow.confirm_order(call.ctx(flow)))
    if not call.ud.order_confirmed:
        call.failures.append("first confirm failed")
    if asks_order(second) or asks_name(second) or asks_phone(second):
        call.failures.append(f"duplicate confirm re-asked slot: {second}")
    if first == second:
        call.failures.append("duplicate confirm repeated exact same response")
    return call.assert_no_failures()


async def scenario_side_questions_then_order() -> int:
    call = NaturalCall("side-questions", "delivery")
    flow = call.ud.agents["delivery"]

    call.customer("بتوصلوا فين؟")
    reply = call.say_agent(agent._delivery_zone_user_message(call.cfg), allow_long=True)
    if "مدينة نصر" not in reply and "المعادي" not in reply:
        call.failures.append(f"zone answer missing zones: {reply}")

    call.customer("طيب المنيو فيه إيه؟")
    reply = call.say_agent(agent._menu_response_for_flow("delivery", call.cfg), allow_long=True)
    if "برجر" not in reply:
        call.failures.append(f"menu answer missing menu: {reply}")

    call.customer("عايز برجر كبير وكولا")
    reply = call.say_agent(await flow.update_order(["برجر كبير", "كولا"], call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("العنوان مدينة نصر شارع 10")
    reply = call.say_agent(await flow.update_delivery_address("مدينة نصر شارع 10", "مدينة نصر", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)

    call.customer("أحمد 01012345678")
    reply = call.say_agent(await agent.update_name("أحمد", call.ctx(flow)))
    call.assert_no_reask_known_slots(reply)
    reply = call.say_agent(await agent.update_phone("01012345678", call.ctx(flow)), allow_long=True)
    call.assert_no_reask_known_slots(reply)
    call.customer("أكد")
    reply = call.say_agent(await flow.confirm_delivery(call.ctx(flow)))
    if not call.ud.order_confirmed:
        call.failures.append("side question order not confirmed")
    call.assert_no_reask_known_slots(reply)
    return call.assert_no_failures()


SCENARIOS = [
    scenario_delivery_full_complex,
    scenario_takeaway_change_then_confirm,
    scenario_prefilled_contact_delivery,
    scenario_delivery_min_order_recovery,
    scenario_unavailable_item_recovery,
    scenario_reservation_full,
    scenario_complaint_full,
    scenario_duplicate_confirm_safe,
    scenario_side_questions_then_order,
]


async def main() -> int:
    failures: list[str] = []
    checks = 0
    SUBMISSIONS.clear()
    with patched_backend():
        for index, scenario in enumerate(SCENARIOS, start=1):
            try:
                count = await scenario()
                checks += count
                print(f"{index:02d}. {scenario.__name__}: PASS ({count} agent replies checked)")
            except Exception as exc:
                failures.append(f"{scenario.__name__}: {exc}")
                print(f"{index:02d}. {scenario.__name__}: FAIL - {exc}")
    print(f"NATURAL_FULL_CALL_SCENARIOS_PASSED: {len(SCENARIOS) - len(failures)}/{len(SCENARIOS)}")
    print(f"NATURAL_FULL_CALL_REPLY_CHECKS: {checks}")
    if failures:
        print("FAILED_NATURAL_FULL_CALL_SCENARIOS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
