import asyncio
import contextlib
import sys
import time
from types import SimpleNamespace

import httpx
from livekit.agents import llm

import agent


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


def make_ctx(current_agent, cfg, ud=None, agents=None):
    userdata = ud or agent.UserData(call_id="call-test", restaurant=cfg)
    userdata.restaurant = cfg
    if agents is not None:
        userdata.agents = agents
    session = SimpleNamespace(current_agent=current_agent)
    return SimpleNamespace(userdata=userdata, session=session)


class FakeHttpClient:
    def __init__(self, *, get_results=None, post_results=None):
        self.get_results = list(get_results or [])
        self.post_results = list(post_results or [])
        self.get_calls = 0
        self.post_calls = 0
        self.is_closed = False

    async def get(self, *args, **kwargs):
        self.get_calls += 1
        result = self.get_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def post(self, *args, **kwargs):
        self.post_calls += 1
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
    greeting_decision = agent._greeter_turn_decision("أهلاً بيك", cfg, has_delivery_agent=True)
    degraded_cfg = agent._degraded_config()
    degraded_delivery_decision = agent._greeter_turn_decision("عايز أوردر توصيل", degraded_cfg, has_delivery_agent=True)
    unavailable_delivery_decision = agent._greeter_turn_decision("عايز أوردر توصيل", cfg_no_menu, has_delivery_agent=False)
    delivery_guard_msg = agent._flow_turn_guard_message(
        "delivery",
        agent.UserData(call_id="call-guard", restaurant=cfg),
        "مدينة السادات",
    )
    check("greeter_greeting_only_stays_on_script", greeting_decision.action == "say" and "طلب أكل" in greeting_decision.message)
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
    check("delivery_guard_requires_order_first", "الطلب فقط" in delivery_guard_msg and "لا تطلب العنوان" in delivery_guard_msg)

    phone_ud = agent.UserData(call_id="call-phone-buffer", restaurant=cfg)
    phone_msg_1 = await agent._apply_phone_update(phone_ud, "012", flow_name="takeaway")
    phone_msg_2 = await agent._apply_phone_update(phone_ud, "0788", flow_name="takeaway")
    phone_msg_3 = await agent._apply_phone_update(phone_ud, "2899", flow_name="takeaway")
    check(
        "phone_partial_buffering",
        phone_msg_1 == ""
        and "آخر أربع" in phone_msg_2
        and phone_ud.customer_phone == "01207882899"
        and "تحب تطلب" in phone_msg_3
        and "زيرو" not in phone_msg_3
        and not phone_ud.phone_capture_mode,
    )
    check("phone_prefix_spoken_natural", agent.phone2ar("01207882899").startswith("زيرو اتناشر"))

    name_ud = agent.UserData(call_id="call-name-buffer", restaurant=cfg)
    name_ud.order = ["burger large"]
    name_ud.order_validated = True
    name_ud.delivery_address = "street 1"
    name_ud.delivery_zone = "nasr city"
    name_msg = await agent._apply_name_update(name_ud, "اسم إيهاب", flow_name="delivery")
    stt_options = agent._session_stt_options(context_terms=["كوشري", "حمصية"], client_reference_id="call-stt")
    compact_menu_text = big_menu_cfg.menu_text()
    check("name_prefix_extracted", name_ud.customer_name == "إيهاب" and "ورقم موبايلك" in name_msg)
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
    check("rich_turn_guard_skipped", not agent._should_add_turn_guard("ممكن أعرف ايه المتاح في المنيو"))
    check("short_turn_guard_kept", agent._should_add_turn_guard("لا تمام"))
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
    check("delivery_no_landmark_natural", "تمام يا فندم" in delivery_no_landmark and delivery_optional_ctx.userdata.delivery_landmark is None)
    check("detailed_address_skips_landmark", "اسمك إيه" in detailed_address_msg and detailed_address_ctx.userdata.delivery_landmark is None)
    check("reservation_no_notes_natural", "تمام يا فندم" in reservation_no_notes and reservation_optional_ctx.userdata.reservation_notes is None)

    saved_client = agent._http_client
    saved_cache = dict(agent._config_cache)
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
    agent._config_cache = {
        "__default__": agent.CachedConfigEntry(
            fetched_at_monotonic=time.monotonic() - agent.CONFIG_CACHE_TTL - 5.0,
            config=stale_cfg,
        )
    }
    agent._http_client = FakeHttpClient(get_results=[backend_response])
    refreshed_cfg = await agent.fetch_config("call-cache-refresh")
    check("stale_cache_refreshes_backend", refreshed_cfg.name == "fresh-name" and refreshed_cfg.config_source == "backend")
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(isolated_shared_cache_path).unlink()

    timeout_request = httpx.Request("GET", f"{agent.BACKEND_BASE}/restaurant/config")
    agent._config_cache = {
        "__default__": agent.CachedConfigEntry(
            fetched_at_monotonic=time.monotonic() - agent.CONFIG_CACHE_TTL - 5.0,
            config=stale_cfg,
        )
    }
    agent._http_client = FakeHttpClient(
        get_results=[
            httpx.ConnectTimeout("timeout", request=timeout_request),
            httpx.ConnectTimeout("timeout", request=timeout_request),
        ]
    )
    stale_used_cfg = await agent.fetch_config("call-cache-stale")
    check("stale_cache_fallback_after_fetch_failure", stale_used_cfg.name == "stale-name" and stale_used_cfg.config_source == "cache_stale")
    agent._http_client = saved_client
    agent._config_cache = saved_cache
    agent.CONFIG_SHARED_CACHE_PATH = saved_shared_cache_path
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(isolated_shared_cache_path).unlink()

    post_request = httpx.Request("POST", f"{agent.BACKEND_BASE}/orders")
    post_response_422 = httpx.Response(422, request=post_request, json={"detail": "bad request"})
    post_client = FakeHttpClient(
        post_results=[httpx.HTTPStatusError("bad request", request=post_request, response=post_response_422)]
    )
    saved_client = agent._http_client
    agent._http_client = post_client
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
    agent._http_client = saved_client

    health_a = agent.CallWriteHealth(write_available=False, write_blocked_until_monotonic=time.monotonic() + 5.0)
    health_b = agent.CallWriteHealth()
    check("write_health_is_per_call", not agent.backend_write_available(health_a) and agent.backend_write_available(health_b))
    context_terms = agent._stt_context_terms_for_config(cfg)
    check("stt_context_terms_include_business_terms", "test" in [term.lower() for term in context_terms] and any("burger" in term.lower() for term in context_terms))

    saved_queue_path = agent.BACKEND_WRITE_QUEUE_PATH
    saved_circuits = dict(agent._backend_circuits)
    queue_path = ".runtime/test_backend_write_queue.jsonl"
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(queue_path).unlink()
    try:
        agent.BACKEND_WRITE_QUEUE_PATH = queue_path
        agent._backend_circuits.clear()
        queue_client = FakeHttpClient(
            post_results=[httpx.ConnectTimeout("timeout", request=post_request)]
        )
        saved_client = agent._http_client
        agent._http_client = queue_client
        queued_result = await agent._post(
            "/orders",
            {"call_id": "call-queue"},
            "call-queue",
            idempotency_action="takeaway",
            max_retries=1,
            write_health=agent.CallWriteHealth(),
        )
        queue_file = agent._runtime_file_path(queue_path)
        check("retryable_write_gets_queued", bool(queued_result and queued_result.get("queued")) and queue_file.exists())

        agent._http_client = FakeHttpClient(
            post_results=[httpx.Response(200, request=post_request, json={"order_id": "queued-ok"})]
        )
        await agent._drain_backend_write_queue_once()
        check("queued_write_drains_successfully", not queue_file.exists())
        agent._http_client = saved_client
    finally:
        agent.BACKEND_WRITE_QUEUE_PATH = saved_queue_path
        agent._backend_circuits.clear()
        agent._backend_circuits.update(saved_circuits)
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

    shared_cache_path = ".runtime/test_shared_config_cache.json"
    saved_shared_cache_path = agent.CONFIG_SHARED_CACHE_PATH
    saved_cache = dict(agent._config_cache)
    with contextlib.suppress(FileNotFoundError):
        agent._runtime_file_path(shared_cache_path).unlink()
    try:
        agent.CONFIG_SHARED_CACHE_PATH = shared_cache_path
        shared_cfg = make_cfg()
        shared_cfg.name = "shared-cache-name"
        agent._write_shared_cache_entry("__default__", shared_cfg)
        agent._config_cache = {}
        shared_loaded_cfg = await agent.fetch_config("call-shared-cache")
        check(
            "shared_cache_cross_worker_ready",
            shared_loaded_cfg.name == "shared-cache-name" and shared_loaded_cfg.config_source == "cache_fresh",
        )
    finally:
        agent.CONFIG_SHARED_CACHE_PATH = saved_shared_cache_path
        agent._config_cache = saved_cache
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

    text = open("agent.py", encoding="utf-8").read()
    check(
        "session_interruptions_configured",
        "allow_interruptions=True" in text
        and "preemptive_generation=SESSION_PREEMPTIVE_GENERATION" in text
        and "vad            = SESSION_VAD" in text,
    )
    check("inactivity_watchdog_present", "_watch_inactivity" in text and "_safe_close_session" in text)

    failed = [name for name, ok in results if not ok]
    print(f"FAILED_COUNT: {len(failed)}")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
