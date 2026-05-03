import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from livekit.agents import StopResponse

import agent


SUBMISSIONS: list[tuple[str, str | None]] = []


def make_cfg(*, delivery_enabled: bool = True, menu: bool = True) -> agent.RestaurantConfig:
    return agent.RestaurantConfig(
        name="مطعم الاختبار",
        phone="01011111111",
        delivery_enabled=delivery_enabled,
        delivery_fee=15.0,
        delivery_minutes=35,
        wait_minutes=20,
        min_order=0,
        min_guests=1,
        max_guests=12,
        branches=[{"name": "مدينة نصر"}, {"name": "المعادي"}],
        delivery_zones=["مدينة نصر", "المعادي"],
        menu_items=(
            [
                {"name": "برجر كبير", "price": 45, "available": True},
                {"name": "كولا", "price": 15, "available": True},
                {"name": "بطاطس", "price": 20, "available": True},
                {"name": "مياه", "price": 10, "available": False},
            ]
            if menu
            else []
        ),
        upsell_rules=[{"item": "كولا", "price": 15}],
    )


def make_agents(cfg: agent.RestaurantConfig) -> dict[str, object]:
    agents = {
        "greeter": agent.Greeter(cfg),
        "takeaway": agent.Takeaway(cfg),
        "reservation": agent.Reservation(cfg),
        "complaint": agent.Complaint(cfg),
    }
    if cfg.delivery_enabled:
        agents["delivery"] = agent.Delivery(cfg)
    return agents


def make_ud(name: str, cfg: agent.RestaurantConfig | None = None) -> agent.UserData:
    cfg = cfg or make_cfg()
    ud = agent.UserData(call_id=f"scenario-{name}", restaurant=cfg)
    ud.restaurant = cfg
    ud.agents = make_agents(cfg)
    return ud


def make_ctx(current_agent: object, ud: agent.UserData, session: object | None = None) -> SimpleNamespace:
    session = session or SimpleNamespace(current_agent=current_agent)
    return SimpleNamespace(userdata=ud, session=session)


def bind_session(inst: object, ud: agent.UserData) -> tuple[SimpleNamespace, list[str]]:
    said: list[str] = []
    session = SimpleNamespace(
        userdata=ud,
        current_agent=inst,
        options=SimpleNamespace(preemptive_generation=False),
    )

    async def say(text: str, **_: object) -> None:
        said.append(text)

    def update_agent(target: object) -> None:
        session.current_agent = target

    session.say = say
    session.update_agent = update_agent
    inst._get_activity_or_raise = lambda: SimpleNamespace(session=session)
    return session, said


def assert_that(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


async def maybe_stop(coro) -> None:
    try:
        await coro
    except StopResponse:
        return


async def fake_submit_takeaway(ud: agent.UserData) -> dict:
    SUBMISSIONS.append(("takeaway", ud.call_id))
    return {"order_id": f"takeaway-{ud.call_id}", "estimated_time": 20}


async def fake_submit_delivery(ud: agent.UserData) -> dict:
    SUBMISSIONS.append(("delivery", ud.call_id))
    return {"order_id": f"delivery-{ud.call_id}", "estimated_time": 35}


async def fake_submit_reservation(ud: agent.UserData) -> dict:
    SUBMISSIONS.append(("reservation", ud.call_id))
    return {"reservation_id": f"reservation-{ud.call_id}"}


async def fake_submit_complaint(ud: agent.UserData, text: str, ctype: str) -> dict:
    SUBMISSIONS.append(("complaint", ud.call_id))
    return {"complaint_id": f"complaint-{ud.call_id}", "text": text, "type": ctype}


@contextmanager
def patched_backend():
    original = (
        agent.BACKEND_BASE,
        agent.BACKEND_APIKEY,
        agent.submit_takeaway,
        agent.submit_delivery,
        agent.submit_reservation,
        agent.submit_complaint,
    )
    agent.BACKEND_BASE = "http://scenario-backend.local"
    agent.BACKEND_APIKEY = "scenario-key"
    agent.submit_takeaway = fake_submit_takeaway
    agent.submit_delivery = fake_submit_delivery
    agent.submit_reservation = fake_submit_reservation
    agent.submit_complaint = fake_submit_complaint
    try:
        yield
    finally:
        (
            agent.BACKEND_BASE,
            agent.BACKEND_APIKEY,
            agent.submit_takeaway,
            agent.submit_delivery,
            agent.submit_reservation,
            agent.submit_complaint,
        ) = original


async def scenario_delivery_happy_path() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-happy", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير 2"], ctx)
    await flow.update_delivery_address("مدينة نصر شارع عباس العقاد عمارة 12 الدور 3", "مدينة نصر", ctx)
    await agent.update_name("اسمي احمد علي", ctx)
    await agent.update_phone("01012345678", ctx)
    msg = await flow.confirm_delivery(ctx)
    assert_that("delivery confirmed", ud.order_confirmed and ud.order_id.startswith("delivery-"), msg)


async def scenario_delivery_incremental_add() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-add", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير"], ctx)
    ud.last_user_message = "ضيف كولا كمان"
    await flow.update_order(["كولا"], ctx)
    await flow.update_order(["كولا"], ctx)
    assert_that("incremental add once", ud.order == ["برجر كبير", "كولا"] and ud.order_total == 60)


async def scenario_delivery_replace_order() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-replace", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير"], ctx)
    ud.last_user_message = "لا خليه كولا بس"
    await flow.update_order(["كولا"], ctx)
    assert_that("replace order", ud.order == ["كولا"] and ud.order_total == 15)


async def scenario_delivery_ambiguous_upsell_not_added() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-upsell-ambiguous", cfg)
    flow = ud.agents["delivery"]
    session, _ = bind_session(flow, ud)
    ctx = make_ctx(flow, ud, session)
    await flow.update_order(["برجر كبير"], ctx)
    assert_that("upsell pending", ud.pending_upsell_item == "كولا")
    await maybe_stop(flow._handle_pending_upsell("تمام", flow_name="delivery", post_upsell_prompt=agent._ask_address))
    assert_that("ambiguous upsell skipped", ud.order == ["برجر كبير"] and not ud.upsell_accepted)


async def scenario_delivery_upsell_accept() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-upsell-accept", cfg)
    flow = ud.agents["delivery"]
    session, _ = bind_session(flow, ud)
    ctx = make_ctx(flow, ud, session)
    await flow.update_order(["برجر كبير"], ctx)
    await maybe_stop(flow._handle_pending_upsell("ايوه ضيف الكولا", flow_name="delivery", post_upsell_prompt=agent._ask_address))
    assert_that("upsell accepted", ud.order == ["برجر كبير", "كولا"] and ud.upsell_accepted)


async def scenario_delivery_upsell_reject_with_special() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-upsell-reject-special", cfg)
    flow = ud.agents["delivery"]
    session, _ = bind_session(flow, ud)
    ctx = make_ctx(flow, ud, session)
    await flow.update_order(["برجر كبير"], ctx)
    await maybe_stop(flow._handle_pending_upsell("لا بس البرجر من غير بصل", flow_name="delivery", post_upsell_prompt=agent._ask_address))
    assert_that("reject keeps special", ud.order == ["برجر كبير"] and ud.special_requests and "بصل" in ud.special_requests)


async def scenario_delivery_specific_address_skips_landmark() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-specific-address", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_delivery_address("مدينة نصر شارع الطيران رقم 10 الدور الرابع شقة 8", "مدينة نصر", ctx)
    assert_that("specific address skips landmark", ud.landmark_asked is True and ud.delivery_address)


async def scenario_delivery_unsupported_zone_rejected() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-zone-reject", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_delivery_address("الزقازيق شارع المحافظة", "الزقازيق", ctx)
    assert_that("unsupported zone not saved", ud.delivery_address is None and "مش بنوصل" in msg)


async def scenario_delivery_landmark_name_capture() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-landmark-name", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    await flow.update_delivery_landmark("اسمي كريم محمود", ctx)
    assert_that("name captured during landmark", ud.customer_name == "كريم محمود" and ud.delivery_landmark is None)


async def scenario_delivery_duplicate_confirm() -> None:
    cfg = make_cfg()
    ud = make_ud("delivery-duplicate-confirm", cfg)
    flow = ud.agents["delivery"]
    ctx = make_ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.delivery_address = "مدينة نصر شارع 1 عمارة 2"
    ud.delivery_zone = "مدينة نصر"
    ud.customer_name = "منى"
    ud.customer_phone = "01012345678"
    await flow.confirm_delivery(ctx)
    first_count = len([item for item in SUBMISSIONS if item == ("delivery", ud.call_id)])
    await flow.confirm_delivery(ctx)
    second_count = len([item for item in SUBMISSIONS if item == ("delivery", ud.call_id)])
    assert_that("duplicate delivery skipped", first_count == 1 and second_count == 1)


async def scenario_takeaway_happy_path() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-happy", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير", "بطاطس"], ctx)
    await flow.update_special_requests("من غير مخلل", ctx)
    await agent.update_name("احمد", ctx)
    await agent.update_phone("01012345678", ctx)
    await flow.confirm_order(ctx)
    assert_that("takeaway confirmed", ud.order_confirmed and ud.order_id.startswith("takeaway-"))


async def scenario_takeaway_incremental_add() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-add", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير"], ctx)
    ud.last_user_message = "زود بطاطس"
    await flow.update_order(["بطاطس"], ctx)
    assert_that("takeaway add", ud.order == ["برجر كبير", "بطاطس"])


async def scenario_takeaway_replace() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-replace", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير"], ctx)
    ud.last_user_message = "بدلها ببطاطس"
    await flow.update_order(["بطاطس"], ctx)
    assert_that("takeaway replace", ud.order == ["بطاطس"])


async def scenario_takeaway_ambiguous_upsell_not_added() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-upsell-ambiguous", cfg)
    flow = ud.agents["takeaway"]
    session, _ = bind_session(flow, ud)
    ctx = make_ctx(flow, ud, session)
    await flow.update_order(["برجر كبير"], ctx)
    await maybe_stop(flow._handle_pending_upsell("ماشي", flow_name="takeaway", post_upsell_prompt=agent._ask_name))
    assert_that("ambiguous takeaway upsell skipped", ud.order == ["برجر كبير"] and not ud.upsell_accepted)


async def scenario_takeaway_upsell_accept() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-upsell-accept", cfg)
    flow = ud.agents["takeaway"]
    session, _ = bind_session(flow, ud)
    ctx = make_ctx(flow, ud, session)
    await flow.update_order(["برجر كبير"], ctx)
    await maybe_stop(flow._handle_pending_upsell("اه حط كولا", flow_name="takeaway", post_upsell_prompt=agent._ask_name))
    assert_that("takeaway upsell accepted", "كولا" in (ud.order or []) and ud.upsell_accepted)


async def scenario_takeaway_no_special_goes_next_missing() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-no-special", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_special_requests("لا", ctx)
    assert_that("no special clears", ud.special_requests is None and "تطلب" in msg)


async def scenario_takeaway_no_menu_captured_unvalidated() -> None:
    cfg = make_cfg(menu=False)
    ud = make_ud("takeaway-no-menu", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["طبق مخصوص"], ctx)
    assert_that("no menu capture unvalidated", ud.order == ["طبق مخصوص"] and not ud.order_validated)


async def scenario_takeaway_duplicate_confirm() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-duplicate-confirm", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "هند"
    ud.customer_phone = "01012345678"
    await flow.confirm_order(ctx)
    first_count = len([item for item in SUBMISSIONS if item == ("takeaway", ud.call_id)])
    await flow.confirm_order(ctx)
    second_count = len([item for item in SUBMISSIONS if item == ("takeaway", ud.call_id)])
    assert_that("duplicate takeaway skipped", first_count == 1 and second_count == 1)


async def scenario_takeaway_total_message() -> None:
    cfg = make_cfg()
    ud = make_ud("takeaway-total", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير", "كولا"], ctx)
    msg = agent._order_total_user_message("takeaway", ud, cfg)
    assert_that("total mentioned", "ستين" in msg or "60" in msg)


async def scenario_greeter_prefill_and_route_delivery() -> None:
    """The Greeter should capture volunteered contact details and route
    without waiting for an LLM round-trip."""
    cfg = make_cfg()
    ud = make_ud("greeter-prefill-delivery", cfg)
    flow = ud.agents["greeter"]
    session, _ = bind_session(flow, ud)
    handled = await flow._maybe_handle_turn_deterministically(
        "انا اسمي احمد ورقمي 01012345678 وعايز توصيل"
    )
    assert_that(
        "greeter prefilled and routed",
        handled is True
        and ud.customer_name == "احمد"
        and ud.customer_phone == "01012345678"
        and session.current_agent is ud.agents["delivery"],
    )


async def scenario_greeter_delivery_unavailable() -> None:
    cfg = make_cfg(delivery_enabled=False)
    ud = make_ud("greeter-no-delivery", cfg)
    flow = ud.agents["greeter"]
    ctx = make_ctx(flow, ud)
    msg = await flow.to_delivery(ctx)
    assert_that("delivery unavailable", isinstance(msg, str) and "مش متاح" in msg)


async def scenario_greeter_ambiguous_order_asks_mode() -> None:
    cfg = make_cfg()
    ud = make_ud("greeter-ambiguous-order", cfg)
    flow = ud.agents["greeter"]
    ctx = make_ctx(flow, ud)
    msg = await flow.resolve_request("عايز اطلب برجر", ctx)
    assert_that("ambiguous asks mode", isinstance(msg, str) and "تاخده" in msg and "نوص" in msg)


async def scenario_greeter_menu_question() -> None:
    cfg = make_cfg()
    ud = make_ud("greeter-menu", cfg)
    msg = agent._menu_response_for_flow("greeter", ud, cfg) if False else agent._menu_response_for_flow("greeter", cfg)
    assert_that("menu response", "برجر" in msg and "كولا" in msg)


async def scenario_greeter_routes_complaint() -> None:
    cfg = make_cfg()
    ud = make_ud("greeter-complaint", cfg)
    flow = ud.agents["greeter"]
    ctx = make_ctx(flow, ud)
    result = await flow.resolve_request("عندي شكوى من الطلب", ctx)
    assert_that("complaint route", result is ud.agents["complaint"])


async def scenario_greeter_routes_reservation() -> None:
    cfg = make_cfg()
    ud = make_ud("greeter-reservation", cfg)
    flow = ud.agents["greeter"]
    ctx = make_ctx(flow, ud)
    result = await flow.resolve_request("عايز احجز ترابيزة", ctx)
    assert_that("reservation route", result is ud.agents["reservation"])


async def scenario_reservation_valid_time() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-time", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    await flow.update_reservation_time("بكرة الساعة 8 بالليل", ctx)
    assert_that("reservation time", bool(ud.reservation_time and ud.reservation_time_iso))


async def scenario_reservation_bad_time_rejected() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-bad-time", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_reservation_time("بالليل", ctx)
    assert_that("bad time rejected", ud.reservation_time is None and "مش واضح" in msg)


async def scenario_reservation_too_many_guests() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-too-many", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_guests_count(20, ctx)
    assert_that("too many guests", ud.guests_count is None and "أكتر" in msg)


async def scenario_reservation_branch_valid() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-branch", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    await flow.update_branch("مدينة نصر", ctx)
    assert_that("branch valid", ud.selected_branch == "مدينة نصر")


async def scenario_reservation_branch_invalid() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-branch-invalid", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_branch("الزقازيق", ctx)
    assert_that("branch invalid", ud.selected_branch is None and "مش واضح" in msg)


async def scenario_reservation_empty_notes() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-empty-notes", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    msg = await flow.update_reservation_notes("لا", ctx)
    assert_that("empty notes", ud.reservation_notes is None and "فرع" in msg)


async def scenario_reservation_full_confirm() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-confirm", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    await flow.update_reservation_time("بكرة الساعة 8 بالليل", ctx)
    await flow.update_guests_count(4, ctx)
    await flow.update_reservation_notes("عيد ميلاد", ctx)
    await flow.update_branch("المعادي", ctx)
    await agent.update_name("نور", ctx)
    await agent.update_phone("01012345678", ctx)
    await flow.confirm_reservation(ctx)
    assert_that("reservation confirmed", ud.reservation_confirmed and ud.reservation_id.startswith("reservation-"))


async def scenario_reservation_duplicate_confirm() -> None:
    cfg = make_cfg()
    ud = make_ud("reservation-duplicate", cfg)
    flow = ud.agents["reservation"]
    ctx = make_ctx(flow, ud)
    ud.reservation_time = "بكرة الساعة 8 بالليل"
    ud.reservation_time_iso = "2026-04-28T20:00:00+02:00"
    ud.guests_count = 2
    ud.selected_branch = "مدينة نصر"
    ud.customer_name = "علي"
    ud.customer_phone = "01012345678"
    await flow.confirm_reservation(ctx)
    first_count = len([item for item in SUBMISSIONS if item == ("reservation", ud.call_id)])
    await flow.confirm_reservation(ctx)
    second_count = len([item for item in SUBMISSIONS if item == ("reservation", ud.call_id)])
    assert_that("duplicate reservation skipped", first_count == 1 and second_count == 1)


async def scenario_complaint_pending_missing_contact() -> None:
    cfg = make_cfg()
    ud = make_ud("complaint-pending", cfg)
    flow = ud.agents["complaint"]
    ctx = make_ctx(flow, ud)
    await flow.log_complaint("الأكل وصل بارد جدا", "quality", ctx)
    assert_that("complaint pending contact", ud.complaint_text and ud.complaint_type == "quality" and not ud.complaint_logged)


async def scenario_complaint_type_delivery() -> None:
    assert_that("complaint type delivery", agent._normalize_complaint_type("الدليفري") == "delivery")


async def scenario_complaint_too_short_rejected() -> None:
    cfg = make_cfg()
    ud = make_ud("complaint-short", cfg)
    flow = ud.agents["complaint"]
    ctx = make_ctx(flow, ud)
    msg = await flow.log_complaint("لا", "service", ctx)
    assert_that("short complaint rejected", not ud.complaint_text and "أوضح" in msg)


async def scenario_complaint_with_contact_submits() -> None:
    cfg = make_cfg()
    ud = make_ud("complaint-submit", cfg)
    flow = ud.agents["complaint"]
    ctx = make_ctx(flow, ud)
    await flow.log_complaint("المندوب اتأخر ساعة", "delivery", ctx)
    await agent.update_name("سارة", ctx)
    await agent.update_phone("01012345678", ctx)
    assert_that("complaint submitted", ud.complaint_logged)


async def scenario_complaint_duplicate_skip() -> None:
    cfg = make_cfg()
    ud = make_ud("complaint-duplicate", cfg)
    flow = ud.agents["complaint"]
    ctx = make_ctx(flow, ud)
    ud.customer_name = "سارة"
    ud.customer_phone = "01012345678"
    await flow.log_complaint("الخدمة كانت بطيئة جدا", "service", ctx)
    first_count = len([item for item in SUBMISSIONS if item == ("complaint", ud.call_id)])
    await flow.log_complaint("الخدمة كانت بطيئة جدا", "service", ctx)
    second_count = len([item for item in SUBMISSIONS if item == ("complaint", ud.call_id)])
    assert_that("duplicate complaint skipped", first_count == 1 and second_count == 1)


async def scenario_spoken_phone_valid() -> None:
    cfg = make_cfg()
    ud = make_ud("phone-spoken", cfg)
    ctx = make_ctx(ud.agents["takeaway"], ud)
    await agent.update_phone("صفر عشرة واحد اتنين تلاتة اربعة خمسة ستة سبعة تمانية", ctx)
    assert_that("spoken phone valid", ud.customer_phone == "01012345678")


async def scenario_chunked_phone_valid() -> None:
    cfg = make_cfg()
    ud = make_ud("phone-chunked", cfg)
    ctx = make_ctx(ud.agents["takeaway"], ud)
    await agent.update_phone("010123", ctx)
    await agent.update_phone("45678", ctx)
    assert_that("chunked phone valid", ud.customer_phone == "01012345678")


async def scenario_order_numbers_not_phone_like() -> None:
    cfg = make_cfg()
    ud = make_ud("order-numbers", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    await flow.update_order(["برجر كبير 2"], ctx)
    assert_that("order qty not phone", ud.customer_phone is None and ud.order_total == 90)


async def scenario_name_extraction_explicit() -> None:
    assert_that("name extraction", agent._extract_name_candidate("اسمي محمود عبد الله") == "محمود عبد الله")


async def scenario_name_protest_rejected() -> None:
    assert_that("protest rejected", agent._extract_name_candidate("انا قلتلك قبل كده") is None)


async def scenario_handoff_summary_includes_facts() -> None:
    cfg = make_cfg()
    ud = make_ud("handoff-summary", cfg)
    ud.customer_name = "احمد"
    ud.customer_phone = "01012345678"
    ud.order = ["برجر كبير"]
    ud.delivery_address = "مدينة نصر شارع 1"
    summary = ud.conversational_summary()
    assert_that("handoff facts", "احمد" in summary and "01012345678" in summary and "برجر كبير" in summary)


async def scenario_post_completion_generic_stops() -> None:
    cfg = make_cfg()
    ud = make_ud("post-completion", cfg)
    ud.order_confirmed = True
    flow = ud.agents["takeaway"]
    bind_session(flow, ud)
    await maybe_stop(flow._handle_post_completion("takeaway", ud, "تمام شكرا"))
    assert_that("post completion handled", bool(ud.last_agent_message))


async def scenario_menu_question_not_zone() -> None:
    cfg = make_cfg()
    msg = agent._menu_response_for_flow("delivery", cfg)
    assert_that("menu question response", "برجر" in msg and "منطقة" not in msg)


async def scenario_delivery_zone_question_not_menu() -> None:
    cfg = make_cfg()
    msg = agent._delivery_zone_user_message(cfg)
    assert_that("zone response", "مدينة نصر" in msg and "برجر" not in msg)


async def scenario_backend_failure_keeps_unconfirmed() -> None:
    cfg = make_cfg()
    ud = make_ud("backend-failure", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "ليلى"
    ud.customer_phone = "01012345678"

    async def failing_submit(_: agent.UserData) -> None:
        return None

    original = agent.submit_takeaway
    agent.submit_takeaway = failing_submit
    try:
        await flow.confirm_order(ctx)
    finally:
        agent.submit_takeaway = original
    assert_that("backend failure unconfirmed", not ud.order_confirmed and not ud.order_id)


async def scenario_backend_queued_message() -> None:
    cfg = make_cfg()
    ud = make_ud("backend-queued", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "ليلى"
    ud.customer_phone = "01012345678"

    async def queued_submit(_: agent.UserData) -> dict:
        return {"queued": True}

    original = agent.submit_takeaway
    agent.submit_takeaway = queued_submit
    try:
        msg = await flow.confirm_order(ctx)
    finally:
        agent.submit_takeaway = original
    assert_that("queued message", not ud.order_confirmed and "مؤقت" in msg)


async def scenario_write_unavailable_message() -> None:
    cfg = make_cfg()
    ud = make_ud("write-unavailable", cfg)
    flow = ud.agents["takeaway"]
    ctx = make_ctx(flow, ud)
    ud.order = ["برجر كبير"]
    ud.order_validated = True
    ud.order_total = 45
    ud.customer_name = "ليلى"
    ud.customer_phone = "01012345678"
    ud.write_health.write_available = False
    msg = await flow.confirm_order(ctx)
    assert_that("write unavailable", not ud.order_confirmed and "النظام" in msg)


SCENARIOS = [
    ("delivery happy path", scenario_delivery_happy_path),
    ("delivery incremental add", scenario_delivery_incremental_add),
    ("delivery replace order", scenario_delivery_replace_order),
    ("delivery ambiguous upsell not added", scenario_delivery_ambiguous_upsell_not_added),
    ("delivery upsell accept", scenario_delivery_upsell_accept),
    ("delivery upsell reject with special", scenario_delivery_upsell_reject_with_special),
    ("delivery specific address skips landmark", scenario_delivery_specific_address_skips_landmark),
    ("delivery unsupported zone rejected", scenario_delivery_unsupported_zone_rejected),
    ("delivery landmark name capture", scenario_delivery_landmark_name_capture),
    ("delivery duplicate confirm", scenario_delivery_duplicate_confirm),
    ("takeaway happy path", scenario_takeaway_happy_path),
    ("takeaway incremental add", scenario_takeaway_incremental_add),
    ("takeaway replace", scenario_takeaway_replace),
    ("takeaway ambiguous upsell not added", scenario_takeaway_ambiguous_upsell_not_added),
    ("takeaway upsell accept", scenario_takeaway_upsell_accept),
    ("takeaway no special goes next missing", scenario_takeaway_no_special_goes_next_missing),
    ("takeaway no menu captured unvalidated", scenario_takeaway_no_menu_captured_unvalidated),
    ("takeaway duplicate confirm", scenario_takeaway_duplicate_confirm),
    ("takeaway total message", scenario_takeaway_total_message),
    ("greeter prefill and route delivery", scenario_greeter_prefill_and_route_delivery),
    ("greeter delivery unavailable", scenario_greeter_delivery_unavailable),
    ("greeter ambiguous order asks mode", scenario_greeter_ambiguous_order_asks_mode),
    ("greeter menu question", scenario_greeter_menu_question),
    ("greeter routes complaint", scenario_greeter_routes_complaint),
    ("greeter routes reservation", scenario_greeter_routes_reservation),
    ("reservation valid time", scenario_reservation_valid_time),
    ("reservation bad time rejected", scenario_reservation_bad_time_rejected),
    ("reservation too many guests", scenario_reservation_too_many_guests),
    ("reservation branch valid", scenario_reservation_branch_valid),
    ("reservation branch invalid", scenario_reservation_branch_invalid),
    ("reservation empty notes", scenario_reservation_empty_notes),
    ("reservation full confirm", scenario_reservation_full_confirm),
    ("reservation duplicate confirm", scenario_reservation_duplicate_confirm),
    ("complaint pending missing contact", scenario_complaint_pending_missing_contact),
    ("complaint type delivery", scenario_complaint_type_delivery),
    ("complaint too short rejected", scenario_complaint_too_short_rejected),
    ("complaint with contact submits", scenario_complaint_with_contact_submits),
    ("complaint duplicate skip", scenario_complaint_duplicate_skip),
    ("spoken phone valid", scenario_spoken_phone_valid),
    ("chunked phone valid", scenario_chunked_phone_valid),
    ("order numbers not phone like", scenario_order_numbers_not_phone_like),
    ("name extraction explicit", scenario_name_extraction_explicit),
    ("name protest rejected", scenario_name_protest_rejected),
    ("handoff summary includes facts", scenario_handoff_summary_includes_facts),
    ("post completion generic stops", scenario_post_completion_generic_stops),
    ("menu question not zone", scenario_menu_question_not_zone),
    ("delivery zone question not menu", scenario_delivery_zone_question_not_menu),
    ("backend failure keeps unconfirmed", scenario_backend_failure_keeps_unconfirmed),
    ("backend queued message", scenario_backend_queued_message),
    ("write unavailable message", scenario_write_unavailable_message),
]


async def main() -> int:
    failed: list[tuple[str, str]] = []
    SUBMISSIONS.clear()
    with patched_backend():
        for idx, (name, fn) in enumerate(SCENARIOS, start=1):
            try:
                await fn()
                print(f"{idx:02d}. {name}: PASS")
            except Exception as exc:
                failed.append((name, str(exc)))
                print(f"{idx:02d}. {name}: FAIL - {exc}")
    print(f"SCENARIOS_PASSED: {len(SCENARIOS) - len(failed)}/{len(SCENARIOS)}")
    if failed:
        print("FAILED_SCENARIOS:")
        for name, detail in failed:
            print(f"- {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
