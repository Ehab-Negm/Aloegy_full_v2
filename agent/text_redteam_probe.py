from __future__ import annotations

import asyncio
import random
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import agent
from call_scenario_tests import SUBMISSIONS, make_cfg, make_ud, patched_backend
from complex_order_tests import payload_items, qty
from conversation_turn_tests import FakeChatContext, bind_all, user_turn
from repeated_question_tests import asks_address, asks_name, asks_order, asks_phone


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def log_menu_cfg() -> agent.RestaurantConfig:
    cfg = make_cfg()
    cfg.menu_items = [
        {"name": "شاورما فراخ", "price": 95, "available": True},
        {"name": "شاورما لحمة", "price": 95, "available": True},
        {"name": "بيتزا مارجريتا", "price": 120, "available": True},
        {"name": "كولا", "price": 20, "available": True},
        {"name": "بطاطس", "price": 25, "available": True},
    ]
    cfg.upsell_rules = [{"item": "كولا", "price": 20}]
    cfg.delivery_zones = ["مدينة نصر", "المعادي"]
    cfg.branches = [{"name": "مدينة نصر"}, {"name": "المعادي"}]
    return cfg


def short(text: object, limit: int = 120) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in (text or "") for phrase in phrases)


def is_confirmation_prompt(text: str) -> bool:
    cleaned = (text or "").strip(" .،؟?")
    return (
        cleaned == "صح"
        or "، صح" in (text or "")
        or has_any(text, ("صح؟", "صح ?", "أكد", "تأكيد", "أأكد", "نأكد"))
    )


def classify_question(text: str) -> str:
    if is_confirmation_prompt(text):
        return "confirmation"
    if asks_order(text):
        return "order"
    if has_any(text, ("مش بنوصل", "التوصيل متاح", "مدينة نصر", "المعادي")):
        return "delivery_zones"
    if asks_address(text):
        return "address"
    if asks_name(text):
        return "name"
    if asks_phone(text):
        return "phone"
    return ""


def reasks_address(text: str) -> bool:
    return has_any(
        text,
        ("عنوانك إيه", "العنوان إيه", "ممكن العنوان", "فين هنوصل", "هنوصلك فين"),
    )


def state_snapshot(ud: agent.UserData, session: SimpleNamespace) -> dict[str, object]:
    return {
        "flow": session.current_agent.__class__.__name__.lower(),
        "order": list(ud.order or []),
        "total": float(ud.order_total or 0.0),
        "name": ud.customer_name,
        "phone": ud.customer_phone,
        "pending_phone": ud.pending_phone_digits,
        "address": ud.delivery_address,
        "zone": ud.delivery_zone,
        "pending_upsell": ud.pending_upsell_item,
        "confirmation_pending": bool(ud.confirmation_pending),
        "confirmed": bool(ud.order_confirmed),
        "decision": ud.turn_trace_decision_reason,
    }


def changed_slots(before: dict[str, object], after: dict[str, object]) -> list[str]:
    ignored = {"decision"}
    return [key for key in after if key not in ignored and before.get(key) != after.get(key)]


@dataclass
class ProbeCall:
    name: str
    start_flow: str = "greeter"
    cfg: agent.RestaurantConfig = field(default_factory=log_menu_cfg)

    def __post_init__(self) -> None:
        self.ud = make_ud(f"text-redteam-{self.name}", self.cfg)
        self.session, self.said = bind_all(self.ud, self.start_flow)
        self.chat = FakeChatContext()
        self.issues: list[str] = []
        self.question_streak: dict[str, int] = {}
        self.last_question = ""
        self.last_question_text = ""
        self.turn_no = 0

    async def say(self, text: str) -> str:
        self.turn_no += 1
        before = state_snapshot(self.ud, self.session)
        reply = await user_turn(self.session, self.chat, self.said, text)
        after = state_snapshot(self.ud, self.session)
        if not reply and before["flow"] != after["flow"]:
            # In LiveKit a handoff immediately runs the target agent's on_enter
            # opening. This text harness calls the turn hook directly, so mirror
            # that customer-visible handoff response.
            reply = (getattr(self.session.current_agent, "_opening", "") or "").strip()
            if reply:
                self.ud.last_agent_message = reply
        changed = changed_slots(before, after)
        self._inspect(text, reply, before, after, changed)
        print(f"\n[{self.name} #{self.turn_no}] USER  : {text}")
        print(f"[{self.name} #{self.turn_no}] AGENT : {reply or '<NO TEXT RESPONSE>'}")
        print(
            f"[{self.name} #{self.turn_no}] STATE : "
            f"flow={after['flow']} order={after['order']} total={after['total']:g} "
            f"name={after['name'] or '-'} phone={after['phone'] or after['pending_phone'] or '-'} "
            f"address={short(after['address'], 55) or '-'} zone={after['zone'] or '-'} "
            f"pending_upsell={after['pending_upsell'] or '-'} confirm={after['confirmation_pending']} "
            f"decision={after['decision'] or '-'} changed={changed or '-'}"
        )
        return reply

    def issue(self, message: str) -> None:
        tagged = f"{self.name} turn {self.turn_no}: {message}"
        if tagged not in self.issues:
            self.issues.append(tagged)

    def _inspect(
        self,
        user_text: str,
        reply: str,
        before: dict[str, object],
        after: dict[str, object],
        changed: list[str],
    ) -> None:
        if not reply and after["decision"] == "fell_through_to_llm":
            self.issue("fell through to LLM in text harness and produced no deterministic reply")
        elif not reply:
            self.issue("empty agent reply")

        question = classify_question(reply)
        if question:
            question_text = " ".join((reply or "").strip().split())
            if question == self.last_question and question_text == self.last_question_text and not changed:
                self.question_streak[question] = self.question_streak.get(question, 1) + 1
                if self.question_streak[question] >= 3:
                    self.issue(f"repeated same question without slot progress: {question}")
            else:
                self.question_streak[question] = 1
            self.last_question = question
            self.last_question_text = question_text

        if after["order"] and asks_order(reply) and question == "order":
            self.issue(f"asks for order although order exists: {after['order']}")
        if after["address"] and reasks_address(reply):
            self.issue("asks for address although delivery address already exists")
        if after["phone"] and asks_phone(reply):
            self.issue("asks for phone although phone already exists")
        if after["name"] and asks_name(reply):
            self.issue("asks for name although name already exists")

        missing_delivery = []
        if after["flow"] == "delivery":
            if not after["order"]:
                missing_delivery.append("order")
            if not after["address"]:
                missing_delivery.append("address")
            if not after["phone"]:
                missing_delivery.append("phone")
            if not after["name"]:
                missing_delivery.append("name")
        if is_confirmation_prompt(reply) and missing_delivery:
            self.issue(f"confirmation prompt while delivery slots missing: {','.join(missing_delivery)}")

        stripped = user_text.strip(" .؟،")
        if stripped in {"أنا", "انا"} and after["name"] in {"أنا", "انا"}:
            self.issue("captured pronoun 'أنا' as customer name")
        if "ما قلتلكش الاسم" in user_text and after["name"] and after["name"] not in {"إيهاب", "ايهاب", "منى", "كريم"}:
            self.issue(f"captured protest/question as name: {after['name']}")

        if before["address"] and after["address"] != before["address"] and not has_any(
            user_text, ("غير", "بدل", "غلط", "صحح")
        ):
            self.issue("address changed without an explicit correction cue")

    def expect_qty(self, item: str, expected: int) -> None:
        actual = qty(self.ud, item)
        if actual != expected:
            self.issue(f"wrong quantity for {item}: expected {expected}, got {actual}; order={payload_items(self.ud)}")

    def expect_contains_reply(self, reply: str, phrase: str, label: str) -> None:
        if phrase not in (reply or ""):
            self.issue(f"{label}: expected reply to contain {phrase!r}, got {reply!r}")


async def messy_delivery_call() -> ProbeCall:
    call = ProbeCall("messy_delivery", "greeter")
    await call.say("أنا.")
    await call.say("ازيك عامل ايه؟")
    await call.say("عايز اطلب أوردر توصيل")
    await call.say("التوصيل متاح فين؟")
    await call.say("محتاج اطلب ساندوتش شاورما فراخ وساندوتش شاورما لحمة وآآ")
    await call.say("بيتزا مارجريتا محتاج منها خمسة")
    call.expect_qty("شاورما فراخ", 1)
    call.expect_qty("شاورما لحمة", 1)
    call.expect_qty("بيتزا مارجريتا", 5)
    await call.say("لا مش عايز كولا")
    await call.say("العنوان شبين الكوم شارع سعد زغلول برج الراشد")
    await call.say("طب خلاص العنوان مدينة نصر شارع الطيران عمارة خمسة")
    await call.say("ماشي")
    await call.say("0155")
    await call.say("8950484")
    await call.say("انت ناسي الحاجات دي كلها وأنا ما قلتلكش الاسم؟")
    await call.say("اسمي إيهاب")
    await call.say("غلط طبعا أنا طالب شاورما معاك ازاي انت ناسي الطلب؟")
    call.expect_qty("شاورما فراخ", 1)
    call.expect_qty("شاورما لحمة", 1)
    call.expect_qty("بيتزا مارجريتا", 5)
    await call.say("أكد")
    return call


async def takeaway_corrections_call() -> ProbeCall:
    call = ProbeCall("takeaway_corrections", "takeaway")
    await call.say("ايه المنيو؟")
    await call.say("هات بيتزا مارجريتا وكولا")
    await call.say("لا شيل الكولا")
    call.expect_qty("كولا", 0)
    await call.say("طب الحساب كام؟")
    await call.say("لا غير الطلب اعمله شاورما فراخ بس")
    call.expect_qty("بيتزا مارجريتا", 0)
    call.expect_qty("شاورما فراخ", 1)
    await call.say("مفيش طلب خاص")
    await call.say("ماشي")
    await call.say("اسمي أنا؟")
    if call.ud.customer_name in {"أنا", "انا"}:
        call.issue("captured 'اسمي أنا؟' as real name")
    await call.say("اسمي منى")
    await call.say("رقمي 010123")
    await call.say("45678")
    await call.say("تمام أكد")
    return call


async def delivery_memory_argument_call() -> ProbeCall:
    call = ProbeCall("delivery_memory_argument", "delivery")
    await call.say("عايز دليفري")
    await call.say("بيتزا مارجريتا اتنين")
    await call.say("العنوان مدينة نصر شارع عباس العقاد عمارة ١٢")
    await call.say("01012345678")
    await call.say("انت ناسي الاسم والحاجات اللي قلتها؟")
    await call.say("الاسم كريم")
    call.expect_qty("بيتزا مارجريتا", 2)
    await call.say("أكد")
    return call


async def main() -> int:
    random.seed(7)
    SUBMISSIONS.clear()
    probes = []
    with patched_backend():
        for scenario in (
            messy_delivery_call,
            takeaway_corrections_call,
            delivery_memory_argument_call,
        ):
            probes.append(await scenario())

    all_issues = [issue for probe in probes for issue in probe.issues]
    print("\n=== TEXT REDTEAM SUMMARY ===")
    for probe in probes:
        status = "ISSUES" if probe.issues else "CLEAN"
        print(f"{probe.name}: {status} ({len(probe.issues)})")
        for issue in probe.issues:
            print(f"  - {issue}")
    print(f"TOTAL_ISSUES: {len(all_issues)}")
    return 1 if all_issues else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
