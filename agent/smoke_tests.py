import asyncio
import contextlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

import httpx
from livekit.agents import StopResponse, llm

import agent
from health import start_health_server
from utils.voice import _voice_safe_text


AR_TOMORROW_8_PM = "\u0628\u0643\u0631\u0629 \u0627\u0644\u0633\u0627\u0639\u0629 8 \u0628\u0627\u0644\u0644\u064a\u0644"
AR_BAD_TIME = "\u0628\u0627\u0644\u0644\u064a\u0644"
AR_BRANCH_GOOD = "\u0645\u062f\u064a\u0646\u0647 \u0646\u0635\u0631"
AR_BRANCH_BAD = "\u0627\u0644\u0632\u0642\u0627\u0632\u064a\u0642"
AR_BRANCH_CANON = "\u0645\u062f\u064a\u0646\u0629 \u0646\u0635\u0631"


def make_cfg() -> agent.RestaurantConfig:
    return agent.RestaurantConfig(
        name="test",
        phone="01011111111",
        delivery_enabled=True,
        delivery_fee=15.0,
        branches=[{"name": AR_BRANCH_CANON}, {"name": "\u0627\u0644\u0645\u0639\u0627\u062f\u064a"}],
        delivery_zones=["nasr city", "maadi"],
        menu_items=[
            {"name": "burger large", "price": 45, "available": True},
            {"name": "cola", "price": 15, "available": True},
            {"name": "water", "price": 10, "available": False},
        ],
        min_order=60,
    )


def make_big_menu_cfg() -> agent.RestaurantConfig:
    return agent.RestaurantConfig(
        name="big-menu",
        phone="01011111111",
        delivery_enabled=True,
        menu_items=[
            {"name": "كوشري صغير", "price": 35, "available": True},
            {"name": "كوشري وسط", "price": 45, "available": True},
            {"name": "كوشري كبير", "price": 55, "available": True},
            {"name": "حمصية", "price": 10, "available": True},
            {"name": "مياه صغيرة", "price": 8, "available": True},
            {"name": "بيبسي", "price": 15, "available": True},
        ],
    )


def make_upsell_cfg() -> agent.RestaurantConfig:
    cfg = make_cfg()
    cfg.upsell_rules = [{"item": "cola", "price": 15}]
    return cfg


def make_ctx(current_agent, cfg, ud=None, agents=None):
    userdata = ud or agent.UserData(call_id="call-test", restaurant=cfg)
    userdata.restaurant = cfg
    if agents is not None:
        userdata.agents = agents
    session = SimpleNamespace(current_agent=current_agent)
    return SimpleNamespace(userdata=userdata, session=session)


def bind_agent_session(inst, ud):
    said: list[dict] = []

    async def say(text, **kwargs):
        said.append({"text": text, "kwargs": dict(kwargs)})

    session = SimpleNamespace(
        userdata=ud,
        options=SimpleNamespace(preemptive_generation=False),
        say=say,
    )
    inst._get_activity_or_raise = lambda: SimpleNamespace(session=session)
    return session, said


class FakeHttpClient:
    def __init__(self, *, get_results=None, post_results=None):
        self.get_results = list(get_results or [])
        self.post_results = list(post_results or [])
        self.get_calls = 0
        self.post_calls = 0
        self.get_kwargs: list[dict] = []
        self.post_kwargs: list[dict] = []
        self.is_closed = False

    async def get(self, *args, **kwargs):
        self.get_calls += 1
        self.get_kwargs.append(dict(kwargs))
        result = self.get_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def post(self, *args, **kwargs):
        self.post_calls += 1
        self.post_kwargs.append(dict(kwargs))
        result = self.post_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def main() -> int:
    results: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))
        print(f"{name}: {'PASS' if ok else 'FAIL'}")

    cfg = make_cfg()
    big_menu_cfg = make_big_menu_cfg()
    takeaway = agent.Takeaway(cfg)
    delivery = agent.Delivery(cfg)
    reservation = agent.Reservation(cfg)
    complaint = agent.Complaint(cfg)
    greeter = agent.Greeter(cfg)
    cfg_no_menu = agent.RestaurantConfig(name="no-menu", phone="01011111111", delivery_enabled=False, menu_items=[])
    takeaway_no_menu = agent.Takeaway(cfg_no_menu)
    greeter_no_delivery = agent.Greeter(cfg_no_menu)

    for inst in [greeter, takeaway, delivery, reservation, complaint]:
        names = [tool.info.name for tool in llm.ToolContext(inst.tools).flatten()]
        check(f"tools_unique_{inst.__class__.__name__.lower()}", len(names) == len(set(names)))

    greeter_tool_names = {tool.info.name for tool in llm.ToolContext(greeter.tools).flatten()}
    check(
        "prd032_greeter_tools_include_contact_tools",
        {"get_menu", "update_name", "update_phone"}.issubset(greeter_tool_names),
    )

    prd031_takeaway_a = agent.Takeaway(cfg)
    prd031_takeaway_b = agent.Takeaway(cfg)
    prd031_takeaway_a._turn_responded = True
    check(
        "prd031_instance_defaults_are_instance_scoped",
        "_opening" in prd031_takeaway_a.__dict__
        and "_opening" in prd031_takeaway_b.__dict__
        and "_turn_responded" in prd031_takeaway_a.__dict__
        and "_turn_responded" in prd031_takeaway_b.__dict__
        and prd031_takeaway_a._turn_responded is True
        and prd031_takeaway_b._turn_responded is False
        and prd031_takeaway_a._opening == "اتفضل يا فندم، تحب تطلب إيه؟"
        and prd031_takeaway_b._opening == "اتفضل يا فندم، تحب تطلب إيه؟",
    )

    ctx = make_ctx(takeaway, cfg)
    await agent.update_phone(phone="123", context=ctx)
    check("invalid_phone", ctx.userdata.customer_phone is None)
    degraded_cfg = agent._degraded_config()
    check(
        "degraded_config_shape",
        degraded_cfg.degraded_mode and degraded_cfg.config_source == "degraded_fallback" and degraded_cfg.is_open,
    )
    check("money_precision", agent.money2ar(25.5) == "خمسة وعشرين ونص")

    ctx = make_ctx(takeaway, cfg)
    await takeaway.update_order(items=["pizza"], context=ctx)
    check("unavailable_menu_item", ctx.userdata.order is None and ctx.userdata.order_total == 0)

    ctx = make_ctx(takeaway, cfg)
    await takeaway.update_order(items=["burger larg 2"], context=ctx)
    check(
        "partial_speech_fuzzy_match",
        bool(ctx.userdata.order)
        and ctx.userdata.order[0].startswith("burger large")
        and ctx.userdata.order_total == 90,
    )

    prd027_cfg = make_cfg()
    prd027_cfg.min_order = 0
    prd027_takeaway = agent.Takeaway(prd027_cfg)
    prd027_delivery = agent.Delivery(prd027_cfg)
    prd027_takeaway_ctx = make_ctx(
        prd027_takeaway,
        prd027_cfg,
        agent.UserData(call_id="call-prd027-takeaway", restaurant=prd027_cfg),
    )
    prd027_delivery_ctx = make_ctx(
        prd027_delivery,
        prd027_cfg,
        agent.UserData(call_id="call-prd027-delivery", restaurant=prd027_cfg),
    )
    saved_random_choice = agent._random.choice
    agent._random.choice = lambda choices: choices[0]
    try:
        prd027_takeaway_msg = await prd027_takeaway.update_order(items=["burger large"], context=prd027_takeaway_ctx)
        prd027_delivery_msg = await prd027_delivery.update_order(items=["burger large"], context=prd027_delivery_ctx)
    finally:
        agent._random.choice = saved_random_choice
    check(
        "prd027_shared_order_logic_consistent",
        prd027_takeaway_ctx.userdata.order == ["burger large"]
        and prd027_delivery_ctx.userdata.order == ["burger large"]
        and prd027_takeaway_ctx.userdata.order_validated
        and prd027_delivery_ctx.userdata.order_validated
        and prd027_takeaway_ctx.userdata.order_total == 45
        and prd027_delivery_ctx.userdata.order_total == 45
        and prd027_takeaway_msg == prd027_delivery_msg,
    )

    prd027_takeaway_min_ctx = make_ctx(
        takeaway,
        cfg,
        agent.UserData(call_id="call-prd027-takeaway-min", restaurant=cfg),
    )
    prd027_delivery_min_ctx = make_ctx(
        delivery,
        cfg,
        agent.UserData(call_id="call-prd027-delivery-min", restaurant=cfg),
    )
    prd027_takeaway_min_msg = await takeaway.update_order(items=["burger large"], context=prd027_takeaway_min_ctx)
    prd027_delivery_min_msg = await delivery.update_order(items=["burger large"], context=prd027_delivery_min_ctx)
    check(
        "prd027_flow_specific_delivery_minimum_preserved",
        prd027_takeaway_min_ctx.userdata.order == ["burger large"]
        and prd027_takeaway_min_ctx.userdata.order_total == 45
        and prd027_delivery_min_ctx.userdata.order is None
        and "أقل طلب للتوصيل" in prd027_delivery_min_msg
        and "أقل طلب للتوصيل" not in prd027_takeaway_min_msg,
    )
    no_menu_ctx = make_ctx(takeaway_no_menu, cfg_no_menu)
    no_menu_msg = await agent.get_menu(no_menu_ctx)
    await takeaway_no_menu.update_order(items=["كوشري كبير"], context=no_menu_ctx)
    check("menu_unavailable_message_is_helpful", "لو في صنف في بالك" in no_menu_msg)
    check(
        "order_can_be_captured_without_menu_config",
        no_menu_ctx.userdata.order == ["كوشري كبير"]
        and no_menu_ctx.userdata.order_total == 0.0
        and not no_menu_ctx.userdata.order_validated,
    )
    no_menu_ctx.userdata.customer_name = "Ahmed"
    no_menu_ctx.userdata.customer_phone = "01012345678"
    preliminary_confirm = await takeaway_no_menu.confirm_order(no_menu_ctx)
    check("preliminary_order_not_submitted", "محتاج المنيو ترجع" in preliminary_confirm and not no_menu_ctx.userdata.order_confirmed)

    takeaway_route = await greeter_no_delivery.resolve_request("هطلب تيكاواي واجي استلمه", make_ctx(greeter_no_delivery, cfg_no_menu, agents={"greeter": greeter_no_delivery, "takeaway": takeaway_no_menu, "reservation": reservation, "complaint": complaint}))
    delivery_unavailable_msg = await greeter_no_delivery.resolve_request("عايز أوردر توصيل", make_ctx(greeter_no_delivery, cfg_no_menu, agents={"greeter": greeter_no_delivery, "takeaway": takeaway_no_menu, "reservation": reservation, "complaint": complaint}))
    check("greeter_takeaway_routing_hint", isinstance(takeaway_route, tuple) and takeaway_route[0] is takeaway_no_menu)
    check("greeter_delivery_unavailable_message", isinstance(delivery_unavailable_msg, str) and "التوصيل مش متاح" in delivery_unavailable_msg)
    prd032_greeter = agent.Greeter(cfg)
    prd032_delivery = agent.Delivery(cfg)
    prd032_ud = agent.UserData(call_id="call-prd032-greeter-prefill", restaurant=cfg)
    prd032_ud.agents = {"greeter": prd032_greeter, "delivery": prd032_delivery}
    prd032_route_target = {"agent": None}

    def prd032_update_agent(target):
        prd032_route_target["agent"] = target

    prd032_session = SimpleNamespace(
        userdata=prd032_ud,
        current_agent=prd032_greeter,
        update_agent=prd032_update_agent,
    )
    prd032_greeter._get_activity_or_raise = lambda: SimpleNamespace(session=prd032_session)
    prd032_handled = await prd032_greeter._maybe_handle_turn_deterministically("انا احمد ورقمي 01012345678 وعايز توصيل")
    check(
        "prd032_greeter_prefills_contact_before_routing",
        prd032_handled
        and prd032_route_target["agent"] is prd032_delivery
        and prd032_ud.customer_name == "احمد"
        and prd032_ud.customer_phone == "01012345678",
    )
    greeting_decision = agent._greeter_turn_decision("أهلاً بيك", cfg, has_delivery_agent=True)
    degraded_cfg = agent._degraded_config()
    degraded_delivery_decision = agent._greeter_turn_decision("عايز أوردر توصيل", degraded_cfg, has_delivery_agent=True)
    unavailable_delivery_decision = agent._greeter_turn_decision("عايز أوردر توصيل", cfg_no_menu, has_delivery_agent=False)
    delivery_guard_msg = agent._flow_turn_guard_message(
        "delivery",
        agent.UserData(call_id="call-guard", restaurant=cfg),
        "مدينة السادات",
    )
    check("greeter_greeting_only_stays_on_script", greeting_decision.reason == "unknown_passthrough" and greeting_decision.message == "")
    check(
        "degraded_delivery_routes_without_false_denial",
        degraded_delivery_decision.action == "route"
        and degraded_delivery_decision.target_agent == "delivery"
        and degraded_delivery_decision.reason == "delivery_degraded",
    )
    check(
        "unavailable_delivery_does_not_route",
        unavailable_delivery_decision.action == "say" and unavailable_delivery_decision.reason == "delivery_unavailable",
    )
    check("delivery_guard_requires_order_first", "الطلب" in delivery_guard_msg and "وجّه" in delivery_guard_msg)

    phone_ud = agent.UserData(call_id="call-phone-buffer", restaurant=cfg)
    phone_msg_1 = await agent._apply_phone_update(phone_ud, "012", flow_name="takeaway")
    phone_msg_2 = await agent._apply_phone_update(phone_ud, "0788", flow_name="takeaway")
    phone_msg_3 = await agent._apply_phone_update(phone_ud, "2899", flow_name="takeaway")
    check(
        "phone_partial_buffering",
        "معايا" in phone_msg_1
        and "آخر أربع" in phone_msg_2
        and phone_ud.customer_phone == "01207882899"
        and "تحب تطلب" in phone_msg_3
        and not phone_ud.phone_capture_mode,
    )
    check("phone_prefix_spoken_natural", agent.phone2ar("01207882899").startswith("زيرو اتناشر"))

    # ── Spoken phone number patterns (Egyptian dialect) ──────────────────
    # Each pattern tests a common way Egyptians speak phone numbers to STT
    from nlp.phone_extract import merge_phone_digits, phone_digits_only, is_phone_like_text
    from nlp.arabic import spoken_words_to_digits

    # Pattern 1: All spoken words — 11 digits spoken individually = valid Egyptian mobile
    spoken_full = spoken_words_to_digits("صفر واحد صفر سبعه تمانيه تمانيه اتنين تمانيه تسعه تسعه تسعه", phone_mode=True)
    check("phone_spoken_full_digits", spoken_full == "01078828999" and len(spoken_full) == 11)

    # Pattern 2: "عشرة" as part of prefix — "زيرو عشرة سبعه..." should give "010..."
    spoken_with_ten = spoken_words_to_digits("صفر عشره سبعه تمانيه تمانيه اتنين تمانيه تسعه تسعه", phone_mode=True)
    check("phone_spoken_ten_expands", spoken_with_ten.startswith("010"))

    # Pattern 3: Mixed digits and words — "012 صفر سبعه تمانيه تمانيه اتنين تمانيه تسعه تسعه"
    mixed_phone = phone_digits_only("012 صفر سبعه تمانيه")
    check("phone_mixed_digits_words", mixed_phone.startswith("0120"))

    # Pattern 4: "حداشر" (11) in phone mode → "1","1"
    spoken_eleven = spoken_words_to_digits("صفر حداشر", phone_mode=True)
    check("phone_spoken_eleven_expands", spoken_eleven == "011")

    # Pattern 5: "اتناشر" (12) in phone mode → "1","2"
    spoken_twelve = spoken_words_to_digits("صفر اتناشر", phone_mode=True)
    check("phone_spoken_twelve_expands", spoken_twelve == "012")

    # Pattern 6: "خمستاشر" (15) in phone mode → "1","5"
    spoken_fifteen = spoken_words_to_digits("صفر خمستاشر", phone_mode=True)
    check("phone_spoken_fifteen_expands", spoken_fifteen == "015")

    # Pattern 7: is_phone_like_text detects spoken number words
    check("phone_spoken_detected", is_phone_like_text("\u0635\u0641\u0631 \u0648\u0627\u062d\u062f \u0635\u0641\u0631 \u0648\u0627\u062d\u062f \u0627\u062a\u0646\u064a\u0646"))
    check("prd007_order_numbers_not_phone_like", not is_phone_like_text("\u0627\u062a\u0646\u064a\u0646 \u0643\u0641\u062a\u0629 \u0648 \u062a\u0644\u0627\u062a\u0629 \u0643\u0628\u0627\u0628"))
    check("prd007_digit_quantities_not_phone_like", not is_phone_like_text("2 \u0643\u0641\u062a\u0629 3 \u0643\u0628\u0627\u0628"))
    check("prd019_short_prefix_chunk_appends", merge_phone_digits("010", "0123") == "0100123")
    check("prd019_full_restart_replaces", merge_phone_digits("010", "01012345678") == "01012345678")
    check("prd019_empty_buffer_sets", merge_phone_digits("", "01") == "01")

    # Pattern 8: Full spoken phone resolves to valid number via _apply_phone_update
    spoken_phone_ud = agent.UserData(call_id="call-spoken-phone", restaurant=cfg)
    spoken_phone_ud.order = ["burger large"]
    spoken_phone_ud.order_validated = True
    spoken_phone_ud.customer_name = "أحمد"
    spoken_phone_msg = await agent._apply_phone_update(
        spoken_phone_ud,
        "صفر واحد صفر سبعه تمانيه تمانيه اتنين تمانيه تسعه تسعه تسعه",
        flow_name="takeaway",
    )
    check("phone_spoken_full_valid", spoken_phone_ud.customer_phone == "01078828999")

    # Pattern 9: Chunked spoken — first chunk partial, second completes
    chunked_spoken_ud = agent.UserData(call_id="call-chunked-spoken", restaurant=cfg)
    chunked_spoken_ud.order = ["burger large"]
    chunked_spoken_ud.order_validated = True
    chunked_spoken_ud.customer_name = "أحمد"
    await agent._apply_phone_update(chunked_spoken_ud, "صفر واحد صفر", flow_name="takeaway")
    await agent._apply_phone_update(chunked_spoken_ud, "سبعه تمانيه تمانيه اتنين تمانيه تسعه تسعه تسعه", flow_name="takeaway")
    check("phone_spoken_chunked_valid", chunked_spoken_ud.customer_phone == "01078828999")

    # Pattern 10: Normal digit string still works
    digit_phone_ud = agent.UserData(call_id="call-digit-phone", restaurant=cfg)
    digit_phone_ud.order = ["burger large"]
    digit_phone_ud.order_validated = True
    digit_phone_ud.customer_name = "أحمد"
    await agent._apply_phone_update(digit_phone_ud, "01207882899", flow_name="takeaway")
    check("phone_plain_digits_still_work", digit_phone_ud.customer_phone == "01207882899")

    name_ud = agent.UserData(call_id="call-name-buffer", restaurant=cfg)
    name_ud.order = ["burger large"]
    name_ud.order_validated = True
    name_ud.delivery_address = "street 1"
    name_ud.delivery_zone = "nasr city"
    name_msg = await agent._apply_name_update(name_ud, "اسم إيهاب", flow_name="delivery")
    stt_options = agent._session_stt_options(context_terms=["كوشري", "حمصية"], client_reference_id="call-stt")
    compact_menu_text = big_menu_cfg.menu_text()
    check("name_prefix_extracted", name_ud.customer_name == "إيهاب" and ("رقم" in name_msg or "موبايل" in name_msg))
    check("question_not_captured_as_name", agent._extract_name_candidate("حضرتك معايا؟") is None)
    check("protest_not_captured_as_name", agent._extract_name_candidate("بقول لك اسمي أنا إيه يا ابنك؟") is None)
    check("name_enables_phone_mode", name_ud.phone_capture_mode)
    stt_context_ok = False
    if isinstance(stt_options.context, str):
        stt_context_ok = "كوشري" in stt_options.context and "حمصية" in stt_options.context
    elif stt_options.context is not None:
        stt_context_ok = "كوشري" in getattr(stt_options.context, "terms", []) and "حمصية" in getattr(stt_options.context, "terms", [])
    check(
        "stt_uses_language_hints_and_context",
        stt_options.model == agent.SESSION_STT_MODEL
        and stt_options.language_hints == ["ar"]
        and stt_context_ok
        and stt_options.client_reference_id == "call-stt",
    )
    if agent.session_dependencies_ready():
        soniox_stt = agent._build_session_stt(cfg, client_reference_id="call-soniox")
        check("soniox_builds_session_stt", soniox_stt.provider == "Soniox" and soniox_stt.model == agent.SESSION_STT_MODEL)
    original_stt_language = agent.SESSION_STT_LANGUAGE
    try:
        agent.SESSION_STT_LANGUAGE = "ar-EG"
        check("stt_dialect_normalized_to_ar", agent._session_stt_language_hints() == ["ar"])
    finally:
        agent.SESSION_STT_LANGUAGE = original_stt_language
    check("empty_answer_handles_la_tamam", agent._looks_empty_answer("لا تمام يا فندم"))
    looks_empty_source = inspect.getsource(agent._looks_empty_answer)
    check(
        "prd015_negative_forms_cached",
        hasattr(agent, "_NEGATIVE_FORMS")
        and "_NEGATIVE_FORMS" in looks_empty_source
        and "negative_forms = {" not in looks_empty_source,
    )
    check("rich_turn_guard_always_on", agent._should_add_turn_guard("ممكن أعرف ايه المتاح في المنيو"))
    check("short_turn_guard_kept", agent._should_add_turn_guard("لا تمام"))
    repeated_guard = agent._flow_turn_guard_message(
        "delivery",
        agent.UserData(call_id="guard-repeat", restaurant=cfg),
        "تمام",
    )
    repeated_guard_signature = agent._turn_guard_signature("delivery", repeated_guard)
    check(
        "prd023_identical_turn_guard_skipped",
        not agent._should_add_turn_guard(
            "لا تمام",
            flow="delivery",
            current_guard=repeated_guard,
            previous_guard_signature=repeated_guard_signature,
        ),
    )
    prd016_ctx = llm.ChatContext()
    prd016_ctx.add_message(role="system", content="system-keep")
    for idx in range(agent.TURN_CHAT_CTX_MAX_ITEMS + 6):
        prd016_ctx.add_message(role="user", content=f"user-{idx}")
    agent._limit_chat_ctx_preserving_system(prd016_ctx, max_items=agent.TURN_CHAT_CTX_MAX_ITEMS)
    check(
        "prd016_system_prompt_preserved_during_truncation",
        any(item.role == "system" and item.text_content == "system-keep" for item in prd016_ctx.items),
    )
    check(
        "prd016_context_window_stays_bounded",
        len(prd016_ctx.items) <= agent.TURN_CHAT_CTX_MAX_ITEMS,
    )
    prd021_cfg = make_cfg()
    prd021_greeter = agent.Greeter(prd021_cfg)
    prd021_delivery = agent.Delivery(prd021_cfg)
    prd021_ud = agent.UserData(call_id="call-prd021", restaurant=prd021_cfg)
    prd021_ud.prev_agent = prd021_delivery
    prd021_ud.agents = {"greeter": prd021_greeter, "delivery": prd021_delivery}
    prd021_greeter._chat_ctx = prd021_greeter.chat_ctx.copy()
    prd021_delivery._chat_ctx = prd021_delivery.chat_ctx.copy()
    for idx in range(agent.PROMPT_HISTORY_ITEMS + 4):
        prd021_greeter._chat_ctx.add_message(role="user", content=f"greeter-old-{idx}")
        prd021_delivery._chat_ctx.add_message(role="assistant", content=f"delivery-old-{idx}")
    prd021_updated_ctx = None

    async def _prd021_update_chat_ctx(chat_ctx):
        nonlocal prd021_updated_ctx
        prd021_updated_ctx = chat_ctx
        prd021_greeter._chat_ctx = chat_ctx

    prd021_session, _ = bind_agent_session(prd021_greeter, prd021_ud)
    prd021_session.generate_reply = lambda **_kwargs: None
    prd021_greeter.update_chat_ctx = _prd021_update_chat_ctx
    await prd021_greeter.on_enter()
    prd021_non_system = [
        item for item in (prd021_updated_ctx.items if prd021_updated_ctx else [])
        if item.role != "system"
    ]
    check(
        "prd021_transfer_context_bounded",
        prd021_updated_ctx is not None
        and len(prd021_non_system) <= agent.PROMPT_HISTORY_ITEMS * 2,
    )
    check(
        "prd021_keeps_recent_transfer_history_only",
        prd021_updated_ctx is not None
        and any(item.text_content == f"delivery-old-{agent.PROMPT_HISTORY_ITEMS + 3}" for item in prd021_non_system)
        and not any(item.text_content == "delivery-old-0" for item in prd021_non_system),
    )
    check(
        "delivery_zone_question_not_menu",
        agent._is_delivery_zone_question("ممكن أعرف فين متاح؟")
        and not agent._is_menu_question("ممكن أعرف فين متاح؟")
        and "التوصيل" in agent._delivery_zone_user_message(cfg),
    )
    check(
        "menu_voice_compact",
        compact_menu_text.startswith("المتاح دلوقتي:")
        and "…" not in compact_menu_text
        and compact_menu_text.count("بـ") <= 3
        and "تحب تطلب" in agent._menu_response_for_flow("delivery", big_menu_cfg),
    )
    total_ud = agent.UserData(call_id="call-total", restaurant=cfg)
    total_ud.order = ["burger large", "cola"]
    total_ud.order_total = 60
    total_ud.order_validated = True
    check("takeaway_total_direct", "ستين" in agent._order_total_user_message("takeaway", total_ud, cfg))
    check("delivery_total_direct", "خمسة وسبعين" in agent._order_total_user_message("delivery", total_ud, cfg))
    check("total_hint_stt_variant", agent._is_total_question("يبقى كده التوتر كام"))
    check("thanks_detected", agent._is_thanks_message("تمام شكرا جدا"))
    check("positive_confirmation_detected", agent._is_positive_confirmation("صح"))
    check("specific_address_detected", agent._address_seems_specific("شارع 9 عمارة 8"))
    takeaway_optional_ctx = make_ctx(takeaway, cfg, agent.UserData(call_id="call-optional-takeaway", restaurant=cfg))
    delivery_optional_ctx = make_ctx(delivery, cfg, agent.UserData(call_id="call-optional-delivery", restaurant=cfg))
    reservation_optional_ctx = make_ctx(reservation, cfg, agent.UserData(call_id="call-optional-res", restaurant=cfg))
    detailed_address_ctx = make_ctx(delivery, cfg, agent.UserData(call_id="call-detailed-address", restaurant=cfg))
    takeaway_optional_ctx.userdata.order = ["burger large"]
    delivery_optional_ctx.userdata.order = ["burger large"]
    delivery_optional_ctx.userdata.delivery_address = "street 1"
    delivery_optional_ctx.userdata.delivery_zone = "nasr city"
    reservation_optional_ctx.userdata.reservation_time = AR_TOMORROW_8_PM
    reservation_optional_ctx.userdata.guests_count = 2
    detailed_address_msg = await delivery.update_delivery_address("شارع 9 عمارة 8", "nasr city", detailed_address_ctx)
    takeaway_no_special = await takeaway.update_special_requests("لا تمام", takeaway_optional_ctx)
    delivery_no_special = await delivery.update_special_requests("لا تمام", delivery_optional_ctx)
    delivery_no_landmark = await delivery.update_delivery_landmark("لا تمام", delivery_optional_ctx)
    reservation_no_notes = await reservation.update_reservation_notes("لا تمام", reservation_optional_ctx)
    check("takeaway_no_special_natural", "مفيش طلب خاص" in takeaway_no_special and takeaway_optional_ctx.userdata.special_requests is None)
    check("delivery_no_special_natural", "مفيش طلب خاص" in delivery_no_special and delivery_optional_ctx.userdata.special_requests is None)
    name_as_landmark_ctx = make_ctx(delivery, cfg, agent.UserData(call_id="call-name-as-landmark", restaurant=cfg))
    name_as_landmark_ctx.userdata.order = ["burger large"]
    name_as_landmark_ctx.userdata.delivery_address = "street 1"
    name_as_landmark_ctx.userdata.delivery_zone = "nasr city"
    landmark_name_msg = await delivery.update_delivery_landmark("اسمي أحمد", name_as_landmark_ctx)
    check("delivery_no_landmark_natural", "تمام يا فندم" in delivery_no_landmark and delivery_optional_ctx.userdata.delivery_landmark is None)
    check(
        "name_reply_not_saved_as_landmark",
        name_as_landmark_ctx.userdata.customer_name == "أحمد"
        and name_as_landmark_ctx.userdata.delivery_landmark is None
        and ("رقم" in landmark_name_msg or "موبايل" in landmark_name_msg),
    )
    check("detailed_address_skips_landmark", ("اسم" in detailed_address_msg) and detailed_address_ctx.userdata.delivery_landmark is None)
    check("reservation_no_notes_natural", "تمام يا فندم" in reservation_no_notes and reservation_optional_ctx.userdata.reservation_notes is None)

    upsell_cfg = make_upsell_cfg()
    takeaway_upsell = agent.Takeaway(upsell_cfg)
    upsell_ctx = make_ctx(takeaway_upsell, upsell_cfg, agent.UserData(call_id="call-upsell", restaurant=upsell_cfg))
    upsell_offer_msg = await takeaway_upsell.update_order(items=["burger large"], context=upsell_ctx)
    accepted_item = agent._accept_pending_upsell(upsell_ctx.userdata, upsell_cfg)
    await takeaway_upsell.update_special_requests("لا تمام", upsell_ctx)
    check(
        "upsell_acceptance_persists",
        accepted_item == "cola"
        and
        "cola" in (upsell_ctx.userdata.order or [])
        and upsell_ctx.userdata.order_total == 60
        and upsell_ctx.userdata.pending_upsell_item is None
        and upsell_ctx.userdata.upsell_accepted is True
        and "cola" in upsell_offer_msg
    )
    check(
        "upsell_acceptance_needs_explicit_intent",
        agent._is_explicit_upsell_acceptance("أيوه ضيفها", "cola")
        and not agent._is_explicit_upsell_acceptance("تمام", "cola")
    )
    prd028_takeaway = agent.Takeaway(upsell_cfg)
    prd028_delivery_cfg = make_upsell_cfg()
    prd028_delivery_cfg.min_order = 0
    prd028_delivery = agent.Delivery(prd028_delivery_cfg)
    prd028_takeaway_ud = agent.UserData(call_id="call-prd028-takeaway", restaurant=upsell_cfg)
    prd028_delivery_ud = agent.UserData(call_id="call-prd028-delivery", restaurant=prd028_delivery_cfg)
    prd028_takeaway_ctx = make_ctx(prd028_takeaway, upsell_cfg, prd028_takeaway_ud)
    prd028_delivery_ctx = make_ctx(prd028_delivery, prd028_delivery_cfg, prd028_delivery_ud)
    await prd028_takeaway.update_order(items=["burger large"], context=prd028_takeaway_ctx)
    await prd028_delivery.update_order(items=["burger large"], context=prd028_delivery_ctx)
    bind_agent_session(prd028_takeaway, prd028_takeaway_ud)
    bind_agent_session(prd028_delivery, prd028_delivery_ud)
    prd028_takeaway_msgs: list[str] = []
    prd028_delivery_msgs: list[str] = []

    async def fake_prd028_takeaway_stop(text: str, **_kwargs) -> None:
        prd028_takeaway_msgs.append(text)
        raise agent.StopResponse()

    async def fake_prd028_delivery_stop(text: str, **_kwargs) -> None:
        prd028_delivery_msgs.append(text)
        raise agent.StopResponse()

    prd028_takeaway._say_and_stop = fake_prd028_takeaway_stop
    prd028_delivery._say_and_stop = fake_prd028_delivery_stop
    with contextlib.suppress(agent.StopResponse):
        await prd028_takeaway._maybe_handle_turn_deterministically("ضيفها ومفيش طلبات خاصة")
    with contextlib.suppress(agent.StopResponse):
        await prd028_delivery._maybe_handle_turn_deterministically("ضيفها ومفيش طلبات خاصة")
    check(
        "prd028_shared_upsell_acceptance_state",
        "cola" in (prd028_takeaway_ud.order or [])
        and "cola" in (prd028_delivery_ud.order or [])
        and prd028_takeaway_ud.order_total == 60
        and prd028_delivery_ud.order_total == 60
        and prd028_takeaway_ud.pending_upsell_item is None
        and prd028_delivery_ud.pending_upsell_item is None
        and prd028_takeaway_ud.special_requests is None
        and prd028_delivery_ud.special_requests is None
        and prd028_takeaway_ud.upsell_accepted is True
        and prd028_delivery_ud.upsell_accepted is True,
    )
    check(
        "prd028_upsell_followup_remains_flow_specific",
        prd028_takeaway_msgs
        and prd028_delivery_msgs
        and "اسم" in prd028_takeaway_msgs[-1]
        and ("عنوان" in prd028_delivery_msgs[-1] or "هنوصلك" in prd028_delivery_msgs[-1]),
    )
    same_item_upsell_cfg = agent.RestaurantConfig(
        name="upsell-normalize",
        phone="01011111111",
        upsell_rules=[{"item": "\u0643\u0628\u062f\u0647", "price": 15}],
    )
    same_item_upsell_ud = agent.UserData(call_id="call-upsell-normalize", restaurant=same_item_upsell_cfg)
    same_item_upsell_ud.order = ["\u0643\u0628\u062f\u0629"]
    different_item_upsell_cfg = agent.RestaurantConfig(
        name="upsell-normalize-diff",
        phone="01011111111",
        upsell_rules=[{"item": "\u0643\u0628\u0627\u0628", "price": 15}],
    )
    different_item_upsell_ud = agent.UserData(call_id="call-upsell-normalize-diff", restaurant=different_item_upsell_cfg)
    different_item_upsell_ud.order = ["\u0643\u0641\u062a\u0629"]
    check(
        "prd010_upsell_normalizes_same_item",
        agent._get_upsell_suggestion(same_item_upsell_ud, same_item_upsell_cfg) is None,
    )
    check(
        "prd010_upsell_still_offers_different_item",
        bool(agent._get_upsell_suggestion(different_item_upsell_ud, different_item_upsell_cfg)),
    )

    takeaway_ambiguous_upsell = agent.Takeaway(upsell_cfg)
    takeaway_ambiguous_ud = agent.UserData(call_id="call-upsell-ambiguous", restaurant=upsell_cfg)
    takeaway_ambiguous_ctx = make_ctx(takeaway_ambiguous_upsell, upsell_cfg, takeaway_ambiguous_ud)
    await takeaway_ambiguous_upsell.update_order(items=["burger large"], context=takeaway_ambiguous_ctx)
    takeaway_ambiguous_upsell._activity = SimpleNamespace(
        session=SimpleNamespace(userdata=takeaway_ambiguous_ud, current_agent=takeaway_ambiguous_upsell)
    )
    takeaway_ambiguous_msgs: list[str] = []

    async def fake_takeaway_say_and_stop(text: str) -> None:
        takeaway_ambiguous_msgs.append(text)
        raise agent.StopResponse()

    takeaway_ambiguous_upsell._say_and_stop = fake_takeaway_say_and_stop
    with contextlib.suppress(agent.StopResponse):
        await takeaway_ambiguous_upsell._maybe_handle_turn_deterministically("تمام")
    check(
        "takeaway_ambiguous_upsell_not_added",
        "cola" not in (takeaway_ambiguous_ud.order or [])
        and takeaway_ambiguous_ud.pending_upsell_item is None
        and takeaway_ambiguous_ud.upsell_accepted is False
        and takeaway_ambiguous_msgs
        and ("طلب خاص" in takeaway_ambiguous_msgs[-1] or "ملاحظة" in takeaway_ambiguous_msgs[-1] or "تحضير" in takeaway_ambiguous_msgs[-1])
    )

    delivery_upsell_cfg = make_upsell_cfg()
    delivery_upsell_cfg.min_order = 0
    delivery_ambiguous_upsell = agent.Delivery(delivery_upsell_cfg)
    delivery_ambiguous_ud = agent.UserData(call_id="call-delivery-upsell-ambiguous", restaurant=delivery_upsell_cfg)
    delivery_ambiguous_ctx = make_ctx(delivery_ambiguous_upsell, delivery_upsell_cfg, delivery_ambiguous_ud)
    await delivery_ambiguous_upsell.update_order(items=["burger large"], context=delivery_ambiguous_ctx)
    delivery_ambiguous_upsell._activity = SimpleNamespace(
        session=SimpleNamespace(userdata=delivery_ambiguous_ud, current_agent=delivery_ambiguous_upsell)
    )
    delivery_ambiguous_msgs: list[str] = []

    async def fake_delivery_say_and_stop(text: str) -> None:
        delivery_ambiguous_msgs.append(text)
        raise agent.StopResponse()

    delivery_ambiguous_upsell._say_and_stop = fake_delivery_say_and_stop
    with contextlib.suppress(agent.StopResponse):
        await delivery_ambiguous_upsell._maybe_handle_turn_deterministically("تمام")
    check(
        "delivery_ambiguous_upsell_not_added",
        "cola" not in (delivery_ambiguous_ud.order or [])
        and delivery_ambiguous_ud.pending_upsell_item is None
        and delivery_ambiguous_ud.upsell_accepted is False
        and delivery_ambiguous_msgs
        and ("طلب خاص" in delivery_ambiguous_msgs[-1] or "ملاحظة" in delivery_ambiguous_msgs[-1])
    )

    takeaway_special_upsell = agent.Takeaway(upsell_cfg)
    takeaway_special_ud = agent.UserData(call_id="call-upsell-special", restaurant=upsell_cfg)
    takeaway_special_ctx = make_ctx(takeaway_special_upsell, upsell_cfg, takeaway_special_ud)
    await takeaway_special_upsell.update_order(items=["burger large"], context=takeaway_special_ctx)
    takeaway_special_upsell._activity = SimpleNamespace(
        session=SimpleNamespace(userdata=takeaway_special_ud, current_agent=takeaway_special_upsell)
    )
    takeaway_special_msgs: list[str] = []

    async def fake_takeaway_special_say_and_stop(text: str) -> None:
        takeaway_special_msgs.append(text)
        raise agent.StopResponse()

    takeaway_special_upsell._say_and_stop = fake_takeaway_special_say_and_stop
    with contextlib.suppress(agent.StopResponse):
        await takeaway_special_upsell._maybe_handle_turn_deterministically("هضيف cola وبس لو البرجر يكون حار")
    check(
        "takeaway_upsell_and_special_request_same_turn",
        "cola" in (takeaway_special_ud.order or [])
        and takeaway_special_ud.special_requests is not None
        and "حار" in takeaway_special_ud.special_requests
        and takeaway_special_msgs
        and ("اسم" in takeaway_special_msgs[-1]),
    )

    delivery_special_upsell = agent.Delivery(delivery_upsell_cfg)
    delivery_special_ud = agent.UserData(call_id="call-delivery-upsell-special", restaurant=delivery_upsell_cfg)
    delivery_special_ctx = make_ctx(delivery_special_upsell, delivery_upsell_cfg, delivery_special_ud)
    await delivery_special_upsell.update_order(items=["burger large"], context=delivery_special_ctx)
    delivery_special_upsell._activity = SimpleNamespace(
        session=SimpleNamespace(userdata=delivery_special_ud, current_agent=delivery_special_upsell)
    )
    delivery_special_msgs: list[str] = []

    async def fake_delivery_special_say_and_stop(text: str) -> None:
        delivery_special_msgs.append(text)
        raise agent.StopResponse()

    delivery_special_upsell._say_and_stop = fake_delivery_special_say_and_stop
    with contextlib.suppress(agent.StopResponse):
        await delivery_special_upsell._maybe_handle_turn_deterministically("لا وبس لو البرجر يكون حار")
    check(
        "delivery_reject_upsell_keep_special_request",
        "cola" not in (delivery_special_ud.order or [])
        and delivery_special_ud.special_requests is not None
        and "حار" in delivery_special_ud.special_requests
        and delivery_special_msgs
        and ("عنوان" in delivery_special_msgs[-1] or "هنوصلك" in delivery_special_msgs[-1]),
    )

    saved_client = agent._backend_client._http_client
    saved_cache = dict(agent.worker_context().config_cache)
    saved_shared_cache_path = agent.CONFIG_SHARED_CACHE_PATH
    isolated_shared_cache_path = ".runtime/test_isolated_config_cache.json"
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(isolated_shared_cache_path).unlink()
    agent.CONFIG_SHARED_CACHE_PATH = isolated_shared_cache_path
    stale_cfg = agent.RestaurantConfig(name="stale-name", config_source="backend")
    backend_request = httpx.Request("GET", f"{agent.BACKEND_BASE}/restaurant/config")
    backend_response = httpx.Response(
        200,
        request=backend_request,
        json={
            "name": "fresh-name",
            "phone": "01011111111",
            "address": "addr",
            "branches": [],
            "hours": {},
            "menu_items": [],
            "upsell_rules": [],
            "is_open": True,
            "delivery_enabled": False,
        },
    )
    agent.worker_context().config_cache = {
        "__default__": agent.CachedConfigEntry(
            fetched_at_monotonic=time.monotonic() - agent.CONFIG_CACHE_TTL - 5.0,
            config=stale_cfg,
        )
    }
    agent._backend_client._http_client = FakeHttpClient(get_results=[backend_response])
    refreshed_cfg = await agent.fetch_config("call-cache-refresh")
    check("stale_cache_refreshes_backend", refreshed_cfg.name == "fresh-name" and refreshed_cfg.config_source == "backend")
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(isolated_shared_cache_path).unlink()

    timeout_request = httpx.Request("GET", f"{agent.BACKEND_BASE}/restaurant/config")
    agent.worker_context().config_cache = {
        "__default__": agent.CachedConfigEntry(
            fetched_at_monotonic=time.monotonic() - agent.CONFIG_CACHE_TTL - 5.0,
            config=stale_cfg,
        )
    }
    agent._backend_client._http_client = FakeHttpClient(
        get_results=[
            httpx.ConnectTimeout("timeout", request=timeout_request),
            httpx.ConnectTimeout("timeout", request=timeout_request),
        ]
    )
    stale_used_cfg = await agent.fetch_config("call-cache-stale")
    check("stale_cache_fallback_after_fetch_failure", stale_used_cfg.name == "stale-name" and stale_used_cfg.config_source == "cache_stale")
    agent._backend_client._http_client = saved_client
    agent.worker_context().config_cache = saved_cache
    agent.CONFIG_SHARED_CACHE_PATH = saved_shared_cache_path
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(isolated_shared_cache_path).unlink()

    post_request = httpx.Request("POST", f"{agent.BACKEND_BASE}/orders")
    post_response_422 = httpx.Response(422, request=post_request, json={"detail": "bad request"})
    post_client = FakeHttpClient(
        post_results=[httpx.HTTPStatusError("bad request", request=post_request, response=post_response_422)]
    )
    saved_client = agent._backend_client._http_client
    agent._backend_client._http_client = post_client
    write_health = agent.CallWriteHealth()
    post_result = await agent._post(
        "/orders",
        {"call_id": "call-422"},
        "call-422",
        idempotency_action="takeaway",
        max_retries=3,
        write_health=write_health,
    )
    check("post_4xx_no_retry", post_result is None and post_client.post_calls == 1 and agent.backend_write_available(write_health))
    agent._backend_client._http_client = saved_client

    timeout_retry_client = FakeHttpClient(
        post_results=[
            httpx.ReadTimeout("timeout", request=post_request),
            httpx.Response(200, request=post_request, json={"order_id": "retry-ok"}),
        ]
    )
    saved_client = agent._backend_client._http_client
    agent._backend_client._http_client = timeout_retry_client
    timeout_retry_result = await agent._post(
        "/orders",
        {"call_id": "call-timeout"},
        "call-timeout",
        idempotency_action="takeaway",
        tool_timeout=2.25,
        max_retries=2,
        enqueue_on_retryable_failure=False,
        write_health=agent.CallWriteHealth(),
    )
    check(
        "prd012_post_uses_explicit_timeout",
        bool(timeout_retry_result and timeout_retry_result.get("order_id") == "retry-ok")
        and timeout_retry_client.post_calls == 2
        and all(call.get("timeout") == 2.25 for call in timeout_retry_client.post_kwargs),
    )
    agent._backend_client._http_client = saved_client

    health_a = agent.CallWriteHealth(write_available=False, write_blocked_until_monotonic=time.monotonic() + 5.0)
    health_b = agent.CallWriteHealth()
    check("write_health_is_per_call", not agent.backend_write_available(health_a) and agent.backend_write_available(health_b))
    context_terms = agent._stt_context_terms_for_config(cfg)
    check("stt_context_terms_include_business_terms", "test" in [term.lower() for term in context_terms] and any("burger" in term.lower() for term in context_terms))

    saved_queue_path = agent.BACKEND_WRITE_QUEUE_PATH
    saved_recovery_cap = agent.BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES
    saved_circuits = dict(agent.worker_context().backend_circuits)
    queue_path = ".runtime/test_backend_write_queue.jsonl"
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(queue_path).unlink()
    try:
        agent.BACKEND_WRITE_QUEUE_PATH = queue_path
        agent.BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES = 5
        agent.worker_context().backend_circuits.clear()
        while not agent.worker_context().backend_write_queue.empty():
            with contextlib.suppress(Exception):
                agent.worker_context().backend_write_queue.get_nowait()
                agent.worker_context().backend_write_queue.task_done()
        queue_client = FakeHttpClient(
            post_results=[httpx.ConnectTimeout("timeout", request=post_request)]
        )
        saved_client = agent._backend_client._http_client
        agent._backend_client._http_client = queue_client
        queued_result = await agent._post(
            "/orders",
            {"call_id": "call-queue"},
            "call-queue",
            idempotency_action="takeaway",
            max_retries=1,
            write_health=agent.CallWriteHealth(),
        )
        queue_file = agent._runtime_file_path(queue_path)
        check(
            "retryable_write_gets_queued",
            bool(queued_result and queued_result.get("queued"))
            and (not agent.worker_context().backend_write_queue.empty() or queue_file.exists()),
        )

        agent._backend_client._http_client = FakeHttpClient(
            post_results=[httpx.Response(200, request=post_request, json={"order_id": "queued-ok"})]
        )
        await agent._drain_backend_write_queue_once()
        check(
            "queued_write_drains_successfully",
            not queue_file.exists() and agent.worker_context().backend_write_queue.empty(),
        )

        item_a = {
            "endpoint": "/orders",
            "payload": {"call_id": "call-recovery-a"},
            "call_id": "call-recovery-a",
            "idempotency_action": "takeaway",
        }
        item_b = {
            "endpoint": "/orders",
            "payload": {"call_id": "call-recovery-b"},
            "call_id": "call-recovery-b",
            "idempotency_action": "takeaway",
        }
        item_c = {
            "endpoint": "/orders",
            "payload": {"call_id": "call-recovery-c"},
            "call_id": "call-recovery-c",
            "idempotency_action": "takeaway",
        }
        append_first = await agent._append_backend_queue_recovery_items([item_a], call_id="call-recovery-a", endpoint="/orders")
        append_duplicate = await agent._append_backend_queue_recovery_items([dict(item_a)], call_id="call-recovery-a", endpoint="/orders")
        recovery_items_after_duplicate, _ = agent._parse_backend_queue_recovery_lines(await agent._read_backend_queue_recovery_lines())
        check(
            "prd011_recovery_file_dedupes_same_item",
            append_first
            and append_duplicate
            and len(recovery_items_after_duplicate) == 1
            and bool(recovery_items_after_duplicate[0].get("idempotency_key")),
        )

        agent.BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES = 2
        append_over_cap = await agent._append_backend_queue_recovery_items(
            [item_b, item_c],
            call_id="call-recovery-cap",
            endpoint="/orders",
        )
        recovery_items_after_cap, _ = agent._parse_backend_queue_recovery_lines(await agent._read_backend_queue_recovery_lines())
        check(
            "prd011_recovery_cap_applies",
            not append_over_cap and len(recovery_items_after_cap) == 2,
        )

        await agent._rewrite_backend_queue_recovery_lines([])
        agent.BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES = 5
        await agent._append_backend_queue_recovery_items([item_a, item_b], call_id="call-recovery-seed", endpoint="/orders")
        agent.worker_context().backend_write_queue.put_nowait(dict(item_b))
        replay_client = FakeHttpClient(
            post_results=[
                httpx.Response(200, request=post_request, json={"order_id": "replay-a"}),
                httpx.Response(200, request=post_request, json={"order_id": "replay-b"}),
            ]
        )
        agent._backend_client._http_client = replay_client
        await agent._drain_backend_write_queue_once()
        check(
            "prd011_replay_dedupes_before_submit",
            replay_client.post_calls == 2
            and not queue_file.exists()
            and agent.worker_context().backend_write_queue.empty(),
        )
        agent._backend_client._http_client = saved_client
    finally:
        agent.BACKEND_WRITE_QUEUE_PATH = saved_queue_path
        agent.BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES = saved_recovery_cap
        agent.worker_context().backend_circuits.clear()
        agent.worker_context().backend_circuits.update(saved_circuits)
        while not agent.worker_context().backend_write_queue.empty():
            with contextlib.suppress(Exception):
                agent.worker_context().backend_write_queue.get_nowait()
                agent.worker_context().backend_write_queue.task_done()
        with contextlib.suppress(FileNotFoundError):
            agent._runtime_file_path(queue_path).unlink()

    async def fail_takeaway(_ud):
        return None

    orig_submit_takeaway = agent.submit_takeaway
    agent.submit_takeaway = fail_takeaway
    ud = agent.UserData(
        call_id="call-backend-down",
        restaurant=cfg,
        customer_name="Ahmed",
        customer_phone="01012345678",
        order=["burger large"],
        order_validated=True,
    )
    await takeaway.confirm_order(context=make_ctx(takeaway, cfg, ud))
    check("backend_down_takeaway", ud.order_confirmed is False and not ud.order_id)
    agent.submit_takeaway = orig_submit_takeaway

    submit_calls = {"count": 0}

    async def ok_takeaway(_ud):
        submit_calls["count"] += 1
        return {"order_id": "o1", "estimated_time": 15}

    agent.submit_takeaway = ok_takeaway
    ud = agent.UserData(
        call_id="call-dup",
        restaurant=cfg,
        customer_name="Ahmed",
        customer_phone="01012345678",
        order=["burger large"],
        order_validated=True,
    )
    ctx = make_ctx(takeaway, cfg, ud)
    await takeaway.confirm_order(context=ctx)
    await takeaway.confirm_order(context=ctx)
    check("duplicate_calls_takeaway", submit_calls["count"] == 1 and ud.order_confirmed and ud.order_id == "o1")
    agent.submit_takeaway = orig_submit_takeaway

    ctx = make_ctx(reservation, cfg)
    await reservation.update_reservation_time(time=AR_BAD_TIME, context=ctx)
    bad_time_state = ctx.userdata.reservation_time is None
    await reservation.update_reservation_time(time=AR_TOMORROW_8_PM, context=ctx)
    good_time_state = ctx.userdata.reservation_time == AR_TOMORROW_8_PM
    parsed_iso_state = bool(ctx.userdata.reservation_time_iso)
    await reservation.update_branch(branch=AR_BRANCH_BAD, context=ctx)
    bad_branch_state = ctx.userdata.selected_branch is None
    await reservation.update_branch(branch=AR_BRANCH_GOOD, context=ctx)
    good_branch_state = ctx.userdata.selected_branch == AR_BRANCH_CANON
    check("reservation_time_validation", bad_time_state and good_time_state and parsed_iso_state)
    check("branch_validation", bad_branch_state and good_branch_state)
    captured_reservation_payload = {}

    async def capture_post(endpoint, payload, call_id, **kwargs):
        captured_reservation_payload["endpoint"] = endpoint
        captured_reservation_payload["payload"] = payload
        return {"reservation_id": "r1"}

    orig_post = agent._post
    agent._post = capture_post
    reservation_ud = agent.UserData(
        call_id="call-reservation-payload",
        restaurant=cfg,
        customer_name="Sara",
        customer_phone="01012345678",
        reservation_time=AR_TOMORROW_8_PM,
        reservation_time_iso="2030-01-02 20:00",
        guests_count=4,
        selected_branch=AR_BRANCH_CANON,
    )
    await agent.submit_reservation(reservation_ud)
    check(
        "reservation_payload_includes_iso",
        captured_reservation_payload.get("payload", {}).get("reservation_time") == AR_TOMORROW_8_PM
        and captured_reservation_payload.get("payload", {}).get("reservation_time_iso") == "2030-01-02 20:00",
    )
    agent._post = orig_post

    captured_takeaway_payload = {}

    async def capture_takeaway_post(endpoint, payload, call_id, **kwargs):
        captured_takeaway_payload["endpoint"] = endpoint
        captured_takeaway_payload["payload"] = payload
        return {"order_id": "o-upsell", "estimated_time": 15}

    agent._post = capture_takeaway_post
    takeaway_payload_ud = agent.UserData(
        call_id="call-upsell-payload",
        restaurant=upsell_cfg,
        customer_name="Ahmed",
        customer_phone="01012345678",
        order=["burger large", "cola"],
        order_validated=True,
        order_total=60,
        upsell_offered=True,
        upsell_accepted=True,
    )
    await agent.submit_takeaway(takeaway_payload_ud)
    check(
        "takeaway_payload_tracks_upsell_acceptance",
        captured_takeaway_payload.get("payload", {}).get("upsell_accepted") is True,
    )
    agent._post = orig_post

    shared_cache_path = ".runtime/test_shared_config_cache.json"
    saved_shared_cache_path = agent.CONFIG_SHARED_CACHE_PATH
    saved_cache = dict(agent.worker_context().config_cache)
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(shared_cache_path).unlink()
    try:
        agent.CONFIG_SHARED_CACHE_PATH = shared_cache_path
        shared_cfg = make_cfg()
        shared_cfg.name = "shared-cache-name"
        await agent._write_shared_cache_entry("__default__", shared_cfg)
        agent.worker_context().config_cache = {}
        shared_loaded_cfg = await agent.fetch_config("call-shared-cache")
        check(
            "shared_cache_cross_worker_ready",
            shared_loaded_cfg.name == "shared-cache-name" and shared_loaded_cfg.config_source == "cache_fresh",
        )
    finally:
        agent.CONFIG_SHARED_CACHE_PATH = saved_shared_cache_path
        agent.worker_context().config_cache = saved_cache
        with contextlib.suppress(FileNotFoundError):
            agent._runtime_file_path(shared_cache_path).unlink()

    complaint_calls = {"count": 0}

    async def ok_complaint(_ud, text, ctype):
        complaint_calls["count"] += 1
        return {"id": "c1", "text": text, "type": ctype}

    orig_submit_complaint = agent.submit_complaint
    agent.submit_complaint = ok_complaint
    ud = agent.UserData(call_id="call-complaint", restaurant=cfg)
    ud.agents = {"complaint": complaint, "greeter": greeter}
    ctx = make_ctx(complaint, cfg, ud, ud.agents)
    await complaint.log_complaint(complaint_text="order wrong", complaint_type="nonsense", context=ctx)
    invalid_type_state = ud.complaint_type is None
    await complaint.log_complaint(complaint_text="order wrong", complaint_type="order", context=ctx)
    staged_state = (
        ud.complaint_text == "order wrong"
        and ud.complaint_type == "order_issue"
        and not ud.complaint_logged
        and complaint_calls["count"] == 0
    )
    await agent.update_name(name="Ahmed", context=ctx)
    await agent.update_phone(phone="01012345678", context=ctx)
    check("complaint_type_validation", invalid_type_state)
    check("complaint_before_name_phone", staged_state and ud.complaint_logged and complaint_calls["count"] == 1)
    agent.submit_complaint = orig_submit_complaint

    ud = agent.UserData(
        call_id="call-complaint-pending",
        restaurant=cfg,
        customer_name="Ahmed",
        complaint_text="order wrong",
        complaint_type="order_issue",
    )
    ud.write_health.write_available = False
    ud.write_health.write_blocked_until_monotonic = time.monotonic() + 5.0
    ud.agents = {"complaint": complaint, "greeter": greeter}
    pending_ctx = make_ctx(complaint, cfg, ud, ud.agents)
    pending_msg = await agent.update_phone(phone="01012345678", context=pending_ctx)
    check(
        "complaint_pending_note_when_write_unavailable",
        "محفوظة مبدئيًا" in pending_msg and not ud.complaint_logged,
    )

    ud = agent.UserData(call_id="call-transfer", restaurant=cfg)
    ud.agents = {"takeaway": takeaway}
    ctx = make_ctx(takeaway, cfg, ud, ud.agents)
    same_agent, same_msg = await takeaway._transfer("takeaway", ctx)
    missing_agent, missing_msg = await takeaway._transfer("delivery", ctx)
    check("transfer_loop_guard", same_agent is takeaway and same_msg == "")
    check("missing_transfer_guard", missing_agent is takeaway and bool(missing_msg))

    async def fail_delivery(_ud):
        return None

    orig_submit_delivery = agent.submit_delivery
    agent.submit_delivery = fail_delivery
    ud = agent.UserData(
        call_id="call-delivery",
        restaurant=cfg,
        customer_name="Ahmed",
        customer_phone="01012345678",
        order_validated=True,
    )
    ctx = make_ctx(delivery, cfg, ud)
    await delivery.update_order(items=["burger large", "cola"], context=ctx)
    ud.delivery_address = "street 10"
    await delivery.confirm_delivery(context=ctx)
    check("delivery_total_unified", ud.order == ["burger large", "cola"] and ud.order_total == 60)
    check("backend_down_delivery", ud.order_confirmed is False and not ud.order_id)
    agent.submit_delivery = orig_submit_delivery

    ctx = make_ctx(delivery, cfg)
    await delivery.update_special_requests(requests="none", context=ctx)
    check("no_speech_tool_level", ctx.userdata.special_requests is None)
    address_msg = await delivery.update_delivery_address(
        address="street 10 building 5 apartment 3 very long marker but should stay intact",
        zone="nasr city",
        context=make_ctx(delivery, cfg),
    )
    check("critical_text_not_truncated", "…" not in address_msg and "street 10" in address_msg)

    prd014_address_item, prd014_address_qty = agent._parse_order_item("street 15")
    prd014_prefix_item, prd014_prefix_qty = agent._parse_order_item("2 kofta")
    prd014_explicit_item, prd014_explicit_qty = agent._parse_order_item("kofta x 3")
    check(
        "prd014_address_like_quantity_not_split",
        prd014_address_item == "street 15" and prd014_address_qty == 1,
    )
    check(
        "prd014_prefix_quantity_still_parses",
        prd014_prefix_item == "kofta" and prd014_prefix_qty == 2,
    )
    check(
        "prd014_explicit_multiplier_still_parses",
        prd014_explicit_item == "kofta" and prd014_explicit_qty == 3,
    )

    prd026_menu = [
        {"name": "chicken grilled", "available": True},
        {"name": "chicken pane", "available": True},
        {"name": "cola", "available": True},
    ]
    prd026_ambiguous = agent._resolve_menu_item("chicken", prd026_menu)
    prd026_exact = agent._resolve_menu_item("cola", prd026_menu)
    prd026_multi = agent._resolve_menu_item("burger larg", cfg.menu_items)
    check("prd026_short_token_ambiguous_no_match", prd026_ambiguous is None)
    check(
        "prd026_single_token_exact_still_matches",
        isinstance(prd026_exact, dict) and prd026_exact.get("name") == "cola",
    )
    check(
        "prd026_multi_token_fuzzy_match_kept",
        isinstance(prd026_multi, dict) and prd026_multi.get("name") == "burger large",
    )

    prd030_long_text = " ".join(["shawarma"] * 20)
    prd030_truncated = _voice_safe_text(prd030_long_text, max_chars=40)
    prd030_exact = "shawarma burger"
    check(
        "prd030_truncates_at_word_boundary",
        prd030_truncated.endswith("…")
        and prd030_truncated[:-1].endswith("shawarma")
        and len(prd030_truncated) <= 40,
    )
    check(
        "prd030_exact_limit_unchanged",
        _voice_safe_text(prd030_exact, max_chars=len(prd030_exact)) == prd030_exact,
    )

    def _has_turn_cap_note(chat_ctx: llm.ChatContext) -> bool:
        return any(
            item.role == "system" and agent._TURN_CAP_PROMPT_MARKER in item.text_content
            for item in chat_ctx.items
        )

    saved_increment_turn_count = agent._increment_turn_count
    saved_should_add_turn_guard = agent._should_add_turn_guard
    agent._should_add_turn_guard = lambda _text, **_kwargs: False
    try:
        prd035_warning_agent = agent.Takeaway(cfg)
        prd035_warning_ud = agent.UserData(
            call_id="call-prd035-warning",
            restaurant=cfg,
            order=["burger large"],
            customer_name="Ahmed",
        )
        _, prd035_warning_said = bind_agent_session(prd035_warning_agent, prd035_warning_ud)
        prd035_warning_ctx = llm.ChatContext()
        prd035_warning_msg = prd035_warning_ctx.add_message(role="user", content="continue")
        agent._increment_turn_count = lambda _call_id: agent.MAX_TURNS_PER_SESSION - 1
        await prd035_warning_agent.on_user_turn_completed(prd035_warning_ctx, prd035_warning_msg)
        check(
            "prd035_turn_cap_warning_note",
            not prd035_warning_said and _has_turn_cap_note(prd035_warning_ctx),
        )

        prd035_grace_agent = agent.Takeaway(cfg)
        prd035_grace_ud = agent.UserData(
            call_id="call-prd035-grace",
            restaurant=cfg,
            order=["burger large"],
            customer_name="Ahmed",
        )
        _, prd035_grace_said = bind_agent_session(prd035_grace_agent, prd035_grace_ud)
        prd035_grace_ctx = llm.ChatContext()
        prd035_grace_msg = prd035_grace_ctx.add_message(role="user", content="continue")
        agent._increment_turn_count = lambda _call_id: agent.MAX_TURNS_PER_SESSION
        await prd035_grace_agent.on_user_turn_completed(prd035_grace_ctx, prd035_grace_msg)
        check(
            "prd035_turn_cap_grace_allows_near_complete",
            not prd035_grace_said and _has_turn_cap_note(prd035_grace_ctx),
        )

        prd035_hard_agent = agent.Takeaway(cfg)
        prd035_hard_ud = agent.UserData(call_id="call-prd035-hard", restaurant=cfg)
        _, prd035_hard_said = bind_agent_session(prd035_hard_agent, prd035_hard_ud)
        prd035_hard_ctx = llm.ChatContext()
        prd035_hard_msg = prd035_hard_ctx.add_message(role="user", content="continue")
        agent._increment_turn_count = lambda _call_id: agent.MAX_TURNS_PER_SESSION
        prd035_hard_stopped = False
        try:
            await prd035_hard_agent.on_user_turn_completed(prd035_hard_ctx, prd035_hard_msg)
        except agent.StopResponse:
            prd035_hard_stopped = True
        check(
            "prd035_turn_cap_hard_cuts_stalled_call",
            prd035_hard_stopped
            and bool(prd035_hard_said)
            and "المكالمة طولت" in prd035_hard_said[0]["text"],
        )

        prd035_expired_agent = agent.Takeaway(cfg)
        prd035_expired_ud = agent.UserData(
            call_id="call-prd035-expired",
            restaurant=cfg,
            order=["burger large"],
            customer_name="Ahmed",
        )
        _, prd035_expired_said = bind_agent_session(prd035_expired_agent, prd035_expired_ud)
        prd035_expired_ctx = llm.ChatContext()
        prd035_expired_msg = prd035_expired_ctx.add_message(role="user", content="continue")
        agent._increment_turn_count = lambda _call_id: agent.MAX_TURNS_PER_SESSION + agent.TURN_CAP_GRACE_TURNS
        prd035_expired_stopped = False
        try:
            await prd035_expired_agent.on_user_turn_completed(prd035_expired_ctx, prd035_expired_msg)
        except agent.StopResponse:
            prd035_expired_stopped = True
        check(
            "prd035_turn_cap_grace_expires",
            prd035_expired_stopped
            and bool(prd035_expired_said)
            and "المكالمة طولت" in prd035_expired_said[0]["text"],
        )
    finally:
        agent._increment_turn_count = saved_increment_turn_count
        agent._should_add_turn_guard = saved_should_add_turn_guard

    agent_text = open("agent.py", encoding="utf-8").read()
    main_text = open("main.py", encoding="utf-8").read()
    combined_text = agent_text + main_text
    check(
        "session_interruptions_configured",
        "allow_interruptions=True" in combined_text
        and "preemptive_generation=" in combined_text
        and "SESSION_VAD" in combined_text,
    )
    check("inactivity_watchdog_present", "_watch_inactivity" in combined_text and "_safe_close_session" in combined_text)

    # ── PRD-001: _handle_quick_intercepts uses elif (not if/if) ──
    intercepts_src = inspect.getsource(agent.BaseAgent._handle_quick_intercepts)
    check(
        "prd001_quick_intercepts_elif",
        "elif flow in" in intercepts_src,
    )

    # ── PRD-002: _handle_post_completion uses elif (not if/if) ──
    post_completion_src = inspect.getsource(agent.BaseAgent._handle_post_completion)
    check(
        "prd002_post_completion_elif",
        "elif _is_positive_confirmation" in post_completion_src,
    )

    # ── PRD-009: _handle_phone_intercept does not raise StopResponse on empty reply ──
    phone_intercept_src = inspect.getsource(agent.BaseAgent._handle_phone_intercept)
    # The old code had a bare "raise StopResponse()" after the if-block.
    # After the fix, the function returns True instead of raising when reply is empty.
    # Verify: no bare "raise StopResponse()" outside the _say_and_stop call.
    import re as _test_re
    # Count occurrences of StopResponse in the method — should be zero now
    # (the only StopResponse raise was the bare one at the end, which is removed)
    stop_response_count = len(_test_re.findall(r"raise StopResponse", phone_intercept_src))
    check(
        "prd009_phone_intercept_no_bare_stop_response",
        stop_response_count == 0,
    )

    # ── PRD-009: _phone_capture_short_reply can return empty string ──
    # Verify the edge case exists: 11+ digits buffered but invalid
    empty_reply = agent._phone_capture_short_reply(
        agent.UserData(call_id="test-empty-reply", restaurant=cfg),
        "01099999999",  # 11 digits — remaining <= 0
    )
    check(
        "prd009_short_reply_can_be_empty",
        empty_reply == "",
    )

    prd020_tool_functions = [
        agent.update_name,
        agent.update_phone,
        agent.get_menu,
        agent.to_greeter,
        agent.Takeaway.to_complaint,
        agent.Takeaway.update_order,
        agent.Takeaway.update_special_requests,
        agent.Takeaway.confirm_order,
        agent.Delivery.update_order,
        agent.Delivery.update_special_requests,
        agent.Delivery.update_delivery_address,
        agent.Delivery.update_delivery_landmark,
        agent.Delivery.to_complaint,
        agent.Delivery.confirm_delivery,
        agent.Reservation.update_reservation_time,
        agent.Reservation.update_guests_count,
        agent.Reservation.update_branch,
        agent.Reservation.update_reservation_notes,
        agent.Reservation.confirm_reservation,
        agent.Complaint.log_complaint,
        agent.Greeter.to_reservation,
        agent.Greeter.to_takeaway,
        agent.Greeter.to_delivery,
        agent.Greeter.to_complaint,
        agent.Greeter.resolve_request,
    ]
    check(
        "prd020_all_function_tools_wrapped",
        all("_run_tool_safely" in inspect.getsource(fn) for fn in prd020_tool_functions),
    )

    saved_apply_name_update = agent._apply_name_update

    async def boom_apply_name_update(*args, **kwargs):
        raise RuntimeError("boom-name")

    agent._apply_name_update = boom_apply_name_update
    try:
        prd020_name_msg = await agent.update_name(name="أحمد", context=make_ctx(takeaway, cfg))
    finally:
        agent._apply_name_update = saved_apply_name_update
    check(
        "prd020_shared_tool_returns_arabic_error",
        isinstance(prd020_name_msg, str) and "حصلت مشكلة عندنا" in prd020_name_msg,
    )

    saved_submit_takeaway = agent.submit_takeaway

    async def boom_submit_takeaway(_ud):
        raise RuntimeError("boom-takeaway")

    agent.submit_takeaway = boom_submit_takeaway
    prd020_confirm_ud = agent.UserData(
        call_id="call-prd020-confirm",
        restaurant=cfg,
        customer_name="Ahmed",
        customer_phone="01012345678",
        order=["burger large"],
        order_validated=True,
    )
    try:
        prd020_confirm_msg = await takeaway.confirm_order(context=make_ctx(takeaway, cfg, prd020_confirm_ud))
    finally:
        agent.submit_takeaway = saved_submit_takeaway
    check(
        "prd020_flow_tool_returns_arabic_error",
        isinstance(prd020_confirm_msg, str)
        and "حصلت مشكلة عندنا" in prd020_confirm_msg
        and not prd020_confirm_ud.order_confirmed,
    )

    # ── PRD-004: Circuit breaker functions are async with locking ──
    check(
        "prd004_circuit_breaker_is_async",
        asyncio.iscoroutinefunction(agent._backend_circuit_is_open)
        and asyncio.iscoroutinefunction(agent._record_backend_circuit_success)
        and asyncio.iscoroutinefunction(agent._record_backend_circuit_failure),
    )

    # ── PRD-004: circuit_lock exists on WorkerContext ──
    check(
        "prd004_circuit_lock_exists",
        hasattr(agent.worker_context(), "circuit_lock")
        and isinstance(agent.worker_context().circuit_lock, asyncio.Lock),
    )

    # ── PRD-004: Circuit breaker open/close round-trip works ──
    saved_circuits_prd004 = dict(agent.worker_context().backend_circuits)
    agent.worker_context().backend_circuits.clear()
    test_endpoint = "/test-circuit-prd004"
    circuit_open_before = await agent._backend_circuit_is_open(test_endpoint)
    test_request = httpx.Request("POST", f"{agent.BACKEND_BASE}/test")
    for _ in range(agent.BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD):
        await agent._record_backend_circuit_failure(
            test_endpoint,
            httpx.ConnectTimeout("timeout", request=test_request),
        )
    circuit_open_after = await agent._backend_circuit_is_open(test_endpoint)
    await agent._record_backend_circuit_success(test_endpoint)
    circuit_open_after_success = await agent._backend_circuit_is_open(test_endpoint)
    check(
        "prd004_circuit_breaker_roundtrip",
        not circuit_open_before
        and circuit_open_after
        and not circuit_open_after_success,
    )
    agent.worker_context().backend_circuits.clear()
    agent.worker_context().backend_circuits.update(saved_circuits_prd004)

    # ── PRD-005/006: _ensure_parent_dir is async ──
    check(
        "prd005_ensure_parent_dir_is_async",
        asyncio.iscoroutinefunction(agent._ensure_parent_dir),
    )

    # ── PRD-005: no sync path.exists() in agent.py ──
    prd005_async_src = "\n".join(
        [
            inspect.getsource(agent._read_shared_cache_map),
            inspect.getsource(agent._write_shared_cache_entry),
            inspect.getsource(agent._read_backend_queue_recovery_lines),
            inspect.getsource(agent._append_backend_queue_recovery_items),
        ]
    )
    # All .exists() should be wrapped in to_thread — no bare .exists() calls
    import re as _re_prd005
    bare_exists_calls = _re_prd005.findall(r"(?<!to_thread\()(?:path|queue_path)\.exists\(\)", prd005_async_src)
    check(
        "prd005_no_bare_path_exists",
        len(bare_exists_calls) == 0,
    )

    # ── PRD-013: turn count functions have safety comments ──
    increment_src = inspect.getsource(agent._increment_turn_count)
    cleanup_src = inspect.getsource(agent._cleanup_turn_count)
    check(
        "prd013_turn_count_safety_comments",
        "SAFETY:" in increment_src and "atomic" in increment_src
        and "SAFETY:" in cleanup_src,
    )

    # ── PRD-018: main.py entrypoint guards _WORKER_CONTEXT ──
    main_src = open("main.py", encoding="utf-8").read()
    check(
        "prd018_worker_context_guard",
        "_agent._WORKER_CONTEXT is None" in main_src
        and "global _WORKER_CONTEXT" not in main_src,
    )

    # ── PRD-025: fork safety handler registered ──
    from backend import client as _backend_client_mod
    check(
        "prd025_fork_reset_function_exists",
        hasattr(_backend_client_mod, "_reset_http_client_after_fork")
        and callable(_backend_client_mod._reset_http_client_after_fork),
    )

    phase8_sources = {
        "agent": open("agent.py", encoding="utf-8").read(),
        "base_agent": open("base_agent.py", encoding="utf-8").read(),
        "main": open("main.py", encoding="utf-8").read(),
        "backend_main": Path("..", "backend", "main.py").read_text(encoding="utf-8"),
    }
    check(
        "prd029_event_hooks_present",
        all(
            token in (phase8_sources["agent"] + phase8_sources["base_agent"] + phase8_sources["main"])
            for token in [
                '"config.cache"',
                '"backend.circuit"',
                '"backend.queue"',
                '"phone.capture"',
                '"name.capture"',
                '"upsell.offer"',
                '"upsell.accepted"',
                '"upsell.rejected"',
                '"flow.transfer"',
                '"turn.guard"',
                '"call.inactivity"',
            ]
        ),
    )

    prd029_events: list[tuple[str, dict]] = []
    saved_emit_event = agent._emit_event
    agent._emit_event = lambda event, **kwargs: prd029_events.append((event, dict(kwargs)))
    try:
        prd029_takeaway = agent.Takeaway(upsell_cfg)
        prd029_delivery = agent.Delivery(upsell_cfg)
        prd029_greeter = agent.Greeter(upsell_cfg)

        transfer_ud = agent.UserData(call_id="call-prd029-transfer", restaurant=upsell_cfg)
        transfer_ud.agents = {"takeaway": prd029_takeaway, "delivery": prd029_delivery}
        transfer_ctx = SimpleNamespace(
            userdata=transfer_ud,
            session=SimpleNamespace(current_agent=prd029_takeaway),
        )
        transfer_target, transfer_message = await prd029_takeaway._transfer("delivery", transfer_ctx)

        live_ud = agent.UserData(call_id="call-prd029-live", restaurant=upsell_cfg)
        live_ud.agents = {"greeter": prd029_greeter, "delivery": prd029_delivery}
        live_target = {"agent": None}

        def _update_agent(target):
            live_target["agent"] = target

        live_session = SimpleNamespace(
            userdata=live_ud,
            current_agent=prd029_greeter,
            update_agent=_update_agent,
            options=SimpleNamespace(preemptive_generation=False),
            say=None,
        )
        prd029_greeter._get_activity_or_raise = lambda: SimpleNamespace(session=live_session)
        live_transfer_ok = prd029_greeter._transfer_live("delivery")

        upsell_accept_ud = agent.UserData(call_id="call-prd029-upsell-accept", restaurant=upsell_cfg)
        upsell_accept_ud.order = ["burger large"]
        upsell_accept_ud.order_validated = True
        upsell_accept_ud.pending_upsell_item = "cola"
        upsell_accept_ud.pending_upsell_price = 15.0
        bind_agent_session(prd029_takeaway, upsell_accept_ud)
        with contextlib.suppress(StopResponse):
            await prd029_takeaway._handle_pending_upsell(
                "أيوه ضيفها",
                flow_name="takeaway",
                post_upsell_prompt=lambda: "تحب أي حاجة تانية؟",
            )

        upsell_reject_ud = agent.UserData(call_id="call-prd029-upsell-reject", restaurant=upsell_cfg)
        upsell_reject_ud.order = ["burger large"]
        upsell_reject_ud.order_validated = True
        upsell_reject_ud.pending_upsell_item = "cola"
        upsell_reject_ud.pending_upsell_price = 15.0
        bind_agent_session(prd029_delivery, upsell_reject_ud)
        with contextlib.suppress(StopResponse):
            await prd029_delivery._handle_pending_upsell(
                "لا تمام",
                flow_name="delivery",
                post_upsell_prompt=lambda: "عايز العنوان؟",
            )
    finally:
        agent._emit_event = saved_emit_event

    prd029_event_names = [event for event, _payload in prd029_events]
    check(
        "prd029_transfer_and_upsell_events_emitted",
        transfer_target is prd029_delivery
        and transfer_message == ""
        and live_transfer_ok
        and live_target["agent"] is prd029_delivery
        and "flow.transfer" in prd029_event_names
        and "upsell.accepted" in prd029_event_names
        and "upsell.rejected" in prd029_event_names,
    )

    check(
        "prd033_backend_checks_idempotency_header",
        phase8_sources["backend_main"].count('alias="Idempotency-Key"') >= 3
        and "Order.idempotency_key == idempotency_key" in phase8_sources["backend_main"]
        and "Reservation.idempotency_key == idempotency_key" in phase8_sources["backend_main"]
        and "Issue.idempotency_key == idempotency_key" in phase8_sources["backend_main"],
    )
    check(
        "prd033_backend_enforces_idempotency_uniqueness",
        phase8_sources["backend_main"].count('UniqueConstraint("idempotency_key")') >= 3,
    )

    class _RunningTask:
        def done(self) -> bool:
            return False

    ctx = agent.worker_context()
    saved_health_snapshot_dir = agent.AGENT_HEALTH_SNAPSHOT_DIR
    saved_backend_queue_path = agent.BACKEND_WRITE_QUEUE_PATH
    saved_active_sessions = ctx.active_sessions
    saved_circuits = dict(ctx.backend_circuits)
    saved_config_available = ctx.runtime_health.config_available
    saved_last_config_error = ctx.runtime_health.last_config_error
    saved_queue_worker = ctx.backend_queue_worker
    saved_config_refresh_worker = ctx.config_refresh_worker

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        agent.AGENT_HEALTH_SNAPSHOT_DIR = str(tmpdir_path / "health")
        agent.BACKEND_WRITE_QUEUE_PATH = str(tmpdir_path / "backend_queue.jsonl")
        try:
            ctx.active_sessions = 2
            ctx.backend_circuits.clear()
            ctx.runtime_health.config_available = True
            ctx.runtime_health.last_config_error = ""
            ctx.backend_queue_worker = _RunningTask()
            ctx.config_refresh_worker = _RunningTask()
            agent._write_worker_health_snapshot_sync(reason="test_ok")
            prd034_ok_status, prd034_ok_payload = agent.build_agent_health_report(active_jobs=2)

            Path(agent.BACKEND_WRITE_QUEUE_PATH).write_text('{"queued": true}\n', encoding="utf-8")
            ctx.runtime_health.config_available = False
            ctx.runtime_health.last_config_error = "backend-down"
            ctx.backend_circuits["/orders"] = agent.BackendCircuitState(
                consecutive_failures=agent.BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD,
                open_until_monotonic=time.monotonic() + 30.0,
                last_error="boom",
            )
            agent._write_worker_health_snapshot_sync(reason="test_degraded")
            prd034_bad_status, prd034_bad_payload = agent.build_agent_health_report(active_jobs=2)
        finally:
            with contextlib.suppress(Exception):
                agent._remove_worker_health_snapshot_sync()
            agent.AGENT_HEALTH_SNAPSHOT_DIR = saved_health_snapshot_dir
            agent.BACKEND_WRITE_QUEUE_PATH = saved_backend_queue_path
            ctx.active_sessions = saved_active_sessions
            ctx.backend_circuits.clear()
            ctx.backend_circuits.update(saved_circuits)
            ctx.runtime_health.config_available = saved_config_available
            ctx.runtime_health.last_config_error = saved_last_config_error
            ctx.backend_queue_worker = saved_queue_worker
            ctx.config_refresh_worker = saved_config_refresh_worker

    check(
        "prd034_health_report_states",
        prd034_ok_status == 200
        and prd034_ok_payload["status"] == "ok"
        and prd034_ok_payload["active_sessions"] == 2
        and prd034_bad_status == 503
        and prd034_bad_payload["status"] == "degraded"
        and "config_unavailable" in prd034_bad_payload["reasons"]
        and "circuits_open" in prd034_bad_payload["reasons"]
        and "write_queue_backlog" in prd034_bad_payload["reasons"],
    )

    health_handle = start_health_server(
        host="127.0.0.1",
        port=0,
        report_builder=lambda: (200, {"status": "ok", "source": "smoke"}),
    )
    try:
        if health_handle is None:
            prd034_health_response_ok = False
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://127.0.0.1:{health_handle.port}/healthz")
            prd034_health_response_ok = response.status_code == 200 and response.json() == {
                "status": "ok",
                "source": "smoke",
            }
    finally:
        if health_handle is not None:
            health_handle.close()

    check(
        "prd034_health_endpoint_serves_json",
        prd034_health_response_ok
        and "start_health_server" in phase8_sources["main"]
        and "parent_process()" in phase8_sources["main"]
        and "/healthz" in Path("health.py").read_text(encoding="utf-8"),
    )

    failed = [name for name, ok in results if not ok]
    print(f"FAILED_COUNT: {len(failed)}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
