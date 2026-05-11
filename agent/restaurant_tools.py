"""Function tools used by RestaurantAgent.

Each tool maps to one piece of customer state. The LLM calls them as it
gathers information, and confirm_and_submit hands the assembled state to
the existing backend submit_X helpers in agent.py.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Annotated, Literal

from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext
from pydantic import BaseModel, Field

from state.user_data import UserData
from nlp.arabic import normalize_ar
from utils.money import money2ar, num2ar

logger = logging.getLogger("restaurant.agent")

RunContext_T = RunContext[UserData]


# ─────────────────────────────────────────────────────────────────────────────
# Intent + identity
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def set_intent(
    intent: Annotated[
        Literal["takeaway", "delivery", "reservation", "complaint"],
        Field(
            description=(
                "نوع طلب العميل. takeaway = استلام، delivery = توصيل، "
                "reservation = حجز ترابيزة، complaint = شكوى."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل نوع طلب العميل بمجرد ما يتضح. ممكن تنده تاني لو غيّر رأيه."""
    ud = context.userdata
    cfg = ud.restaurant
    if intent == "delivery" and not cfg.delivery_enabled and not cfg.degraded_mode:
        # Plain fact only — let the persona/LLM phrase the customer-facing
        # reply naturally. Instruction-style returns ("اعرض على العميل ...")
        # used to leak into the audio reply verbatim.
        return "delivery_not_enabled"
    previous = ud.active_flow
    ud.active_flow = intent
    ud.intents_seen.add(intent)
    if previous and previous != intent:
        logger.info(
            "call=%s | intent_changed | from=%s | to=%s",
            ud.call_id, previous, intent,
        )
    # NOTE: success message must not contain words on the failure-detector
    # blocklist in main.py (e.g. "ناقص"/"فاضي") — they cause healthy tool
    # outcomes to be flagged as failures in telemetry.
    return f"النية اتسجلت: {intent}. كمّل عادي."


_NAME_BLOCKLIST = frozenset({
    # Generic / honorific words the agent must NEVER store as a name.
    "بنت", "ولد", "راجل", "ست", "حد", "حدا", "زبون", "زبونة", "عميل", "عميلة",
    "فندم", "حضرتك", "أستاذ", "أستاذة", "حاجة", "مدام", "أنسة",
    "girl", "boy", "lady", "sir", "madam", "miss", "mister", "mr", "mrs",
    "customer", "client", "anonymous", "unknown",
})


@function_tool()
async def set_name(
    name: Annotated[
        str,
        Field(
            description=(
                "الاسم اللي قاله العميل صراحة (مثلاً 'اسمي أحمد'). "
                "ممنوع تخمين أو استنتاج اسم من جنس/لهجة/تخمين. لو العميل "
                "ما قالش اسمه بوضوح، متندهيش الـ tool ده."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل اسم العميل **بس لو قاله صراحة**.

    Guard: the model sometimes hallucinates a name when the customer said
    something like "بنت" or "راجل" / addressed themselves by gender. The
    blocklist catches those, and any string that isn't a plausible first
    name (length < 2 or all digits) is rejected so the agent falls back
    to "يا فندم" instead of "تمام يا بنت".
    """
    ud = context.userdata
    cleaned = (name or "").strip(" .,،؟!\"'")
    if not cleaned:
        return "name_empty"

    # Strip honorific prefixes ("الأستاذ أحمد" → "أحمد").
    for prefix in ("الأستاذ ", "الأستاذة ", "أستاذ ", "أستاذة ", "مدام ", "أنسة "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Reject obvious junk before saving.
    lowered = cleaned.lower()
    if lowered in _NAME_BLOCKLIST:
        logger.warning(
            "call=%s | set_name rejected blocked token | name=%r",
            ud.call_id, cleaned,
        )
        return "name_rejected_generic"
    if len(cleaned) < 2 or cleaned.isdigit():
        return "name_rejected_invalid"

    ud.customer_name = cleaned
    logger.info("call=%s | name_set | name=%s", ud.call_id, cleaned)
    return f"name_saved | name={cleaned}"


# ─────────────────────────────────────────────────────────────────────────────
# Order
# ─────────────────────────────────────────────────────────────────────────────

class OrderItemSpec(BaseModel):
    """Structured order line — name + quantity. Keeps qty separate from
    the item name so quantities like 'تلاتة شاورما' or '12 صندوق' get
    recorded correctly instead of being baked into the name string."""
    name: Annotated[str, Field(description="اسم الصنف زي ما هو في المنيو، بدون كميات")]
    qty: Annotated[int, Field(description="الكمية المطلوبة من الصنف ده", ge=1, le=99)] = 1


@function_tool()
async def update_order(
    items: Annotated[
        list[OrderItemSpec],
        Field(
            description=(
                "أصناف للإضافة (name من غير كميات، qty منفصلة). "
                "بيضيف للطلب الموجود؛ لو بدّل الطلب كله نده clear_order الأول."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """ضيف أصناف لطلب العميل. الـ tool بيتأكد من المنيو ويحسب الإجمالي."""
    from agent import _normalize_order_items, _parse_order_item, _resolve_menu_item

    ud = context.userdata
    cfg = ud.restaurant

    # Convert structured (name, qty) into the "name x qty" string format
    # the existing parser understands, so qty is preserved through the
    # pipeline. qty=1 stays as a bare name (cleaner downstream display).
    new_items: list[str] = []
    requested_pairs: list[tuple[str, int]] = []
    for spec in items:
        name = (spec.name or "").strip()
        if not name:
            continue
        qty = max(1, min(99, int(spec.qty or 1)))
        new_items.append(name if qty == 1 else f"{name} x {qty}")
        requested_pairs.append((name, qty))

    if not new_items:
        return "items_empty"

    # Idempotency guard against realtime-model re-emission. The model often
    # re-calls update_order with the SAME items after an ambiguous user reply
    # ("نعم" / mistranscribed audio), which silently multiplies the cart.
    # If every requested item is already in the cart at >= requested qty,
    # treat as a no-op and tell the model to use set_order_item_qty if it
    # genuinely wants to change quantities.
    if requested_pairs and cfg.menu_items and ud.order:
        cart_qty: dict[str, int] = {}
        for raw in ud.order:
            ex_name, ex_qty = _parse_order_item(raw)
            ex_canon = _resolve_menu_item(ex_name, cfg.menu_items)
            key = normalize_ar(ex_canon["name"]) if ex_canon else normalize_ar(ex_name)
            cart_qty[key] = cart_qty.get(key, 0) + ex_qty
        already_satisfied = True
        for name, qty in requested_pairs:
            canon = _resolve_menu_item(name, cfg.menu_items)
            key = normalize_ar(canon["name"]) if canon else normalize_ar(name)
            if cart_qty.get(key, 0) < qty:
                already_satisfied = False
                break
        if already_satisfied:
            logger.info(
                "update_order: idempotent no-op — items already in cart at requested qty | items=%s",
                requested_pairs,
            )
            return f"item_already_in_cart | total={money2ar(ud.order_total or 0.0)}"

    # Degraded mode: no menu to validate against — accept items raw
    if not cfg.menu_items:
        ud.order = (ud.order or []) + new_items
        ud.order_validated = False
        ud.order_total = 0.0
        if ud.order_confirmed:
            ud.order_confirmed = False
        return "order_added_degraded"

    # Append to existing order (use clear_order first if customer wants reset)
    combined = (ud.order or []) + new_items
    normalized, unknown, total = _normalize_order_items(combined, cfg.menu_items)

    if not normalized:
        # None of the requested items exist on the menu — give the model the
        # available list to pick alternatives from, but no instruction phrase.
        return f"none_on_menu | unknown={', '.join(unknown)} | menu={cfg.menu_text()}"

    ud.order = normalized
    ud.order_total = total
    ud.order_validated = not unknown

    # If the order changed after confirmation, the confirmation is no longer valid
    if ud.order_confirmed:
        ud.order_confirmed = False

    # Return a compact status: ok + total. The full order list is already in
    # [CALL_STATE], so echoing it here causes the model to read it twice.
    msg = f"item_added | total={money2ar(total)}"
    if unknown:
        msg += f" | unknown={', '.join(unknown)}"
    return msg


@function_tool()
async def remove_order_item(
    name: Annotated[
        str,
        Field(
            description=(
                "اسم الصنف اللي العميل عاوز يشيله من الطلب، زي ما هو في "
                "المنيو (مثلاً 'بيتزا مارجريتا'). الـ tool بيدور fuzzy."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """شيل صنف واحد من الطلب لما العميل يقول 'شيل/الغي/اطلع' الصنف ده."""
    from agent import _resolve_menu_item, _normalize_order_items, _parse_order_item

    ud = context.userdata
    cfg = ud.restaurant
    if not ud.order:
        return "order_already_empty"
    target = _resolve_menu_item(name, cfg.menu_items) if cfg.menu_items else None
    target_name = (target["name"] if target else name).strip()
    target_norm = normalize_ar(target_name)

    kept: list[str] = []
    removed = False
    for raw in ud.order:
        item_name, qty = _parse_order_item(raw)
        if not removed and normalize_ar(item_name) == target_norm:
            removed = True
            continue
        kept.append(raw)

    if not removed:
        return f"item_not_in_order | name={target_name}"

    if not kept:
        ud.order = []
        ud.order_total = 0.0
        ud.order_validated = False
    else:
        normalized, _unknown, total = _normalize_order_items(kept, cfg.menu_items or [])
        ud.order = normalized or kept
        ud.order_total = total
    if ud.order_confirmed:
        ud.order_confirmed = False
    logger.info("call=%s | order_item_removed | name=%s", ud.call_id, target_name)
    return f"item_removed | name={target_name}"


@function_tool()
async def set_order_item_qty(
    name: Annotated[
        str,
        Field(description="اسم الصنف من المنيو اللي العميل عاوز يغيّر كميته."),
    ],
    qty: Annotated[
        int,
        Field(description="الكمية الجديدة (1..99). لو 0 شيل الصنف.", ge=0, le=99),
    ],
    context: RunContext_T,
) -> str:
    """عدّل كمية صنف موجود في الطلب (مثلاً '٣ بيتزا' → '٢ بيتزا')."""
    from agent import (
        _resolve_menu_item,
        _normalize_order_items,
        _parse_order_item,
        _format_order_item,
    )

    ud = context.userdata
    cfg = ud.restaurant
    if not ud.order:
        return "order_already_empty"
    target = _resolve_menu_item(name, cfg.menu_items) if cfg.menu_items else None
    target_name = (target["name"] if target else name).strip()
    target_norm = normalize_ar(target_name)

    updated: list[str] = []
    found = False
    for raw in ud.order:
        item_name, _q = _parse_order_item(raw)
        if not found and normalize_ar(item_name) == target_norm:
            found = True
            if qty <= 0:
                continue  # remove
            updated.append(_format_order_item(item_name, qty))
        else:
            updated.append(raw)

    if not found:
        return f"item_not_in_order | name={target_name}"

    if not updated:
        ud.order = []
        ud.order_total = 0.0
        ud.order_validated = False
    else:
        normalized, _unknown, total = _normalize_order_items(updated, cfg.menu_items or [])
        ud.order = normalized or updated
        ud.order_total = total
        ud.order_validated = True
    if ud.order_confirmed:
        ud.order_confirmed = False
    logger.info(
        "call=%s | order_item_qty_set | name=%s | qty=%d",
        ud.call_id, target_name, qty,
    )
    return f"qty_updated | name={target_name} | qty={qty} | total={money2ar(ud.order_total)}"


@function_tool()
async def clear_order(context: RunContext_T) -> str:
    """امسح الطلب الحالي بالكامل. استخدم لما العميل يقول 'الغي الطلب' أو 'هبدأ من الأول'."""
    ud = context.userdata
    if not ud.order:
        return "order_already_empty"
    last_user = normalize_ar(getattr(ud, "last_user_message", "") or "")
    switching_to_delivery = (
        any(word in last_user for word in ("دليفري", "توصيل", "وصل", "وصلي", "وصلها"))
        and not any(word in last_user for word in ("الغي", "الغى", "إلغي", "امسح", "ابدأ من الاول", "من الأول"))
    )
    if switching_to_delivery:
        # Customer is swapping fulfillment, not cancelling. Keep order intact.
        return "intent_swap_not_clear"
    ud.order = []
    ud.order_total = 0.0
    ud.order_validated = False
    if ud.order_confirmed:
        ud.order_confirmed = False
    logger.info("call=%s | order_cleared", ud.call_id)
    return "order_cleared"


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

_ADDRESS_NONSENSE_TOKENS = frozenset({
    # Words the model sometimes mistakes for an address. If the entire
    # captured string is one of these, refuse — better to ask again than
    # save garbage like "عندي شراكة" → "شارع 9".
    "شراكة", "شراكه", "حاجة", "حاجه", "كده", "كدا", "هنا", "هناك", "اوكي",
    "تمام", "ماشي", "حاضر", "ايوة", "ايوه", "اه", "لا", "لأ",
})


def _looks_like_address(text: str) -> bool:
    """Heuristic: does this string actually contain an address-like signal?

    Real Egyptian addresses always have at least one of: a street/area
    keyword, a number, or a recognisable landmark. Short generic phrases
    fail this check, which prevents the model from saving "عندي شراكة" as
    a delivery address.
    """
    if not text:
        return False
    norm = text.strip().lower()
    if len(norm) < 4:
        return False
    if norm in _ADDRESS_NONSENSE_TOKENS:
        return False
    # Look for area/street vocabulary OR digits.
    address_words = (
        "شارع", "ميدان", "كورنيش", "حي", "منطقة", "عمارة", "برج", "تقاطع",
        "بجوار", "جنب", "خلف", "أمام", "امام", "قدام", "محل", "مدخل",
        "المعادي", "حلوان", "المقطم", "البساتين", "دار السلام", "زهراء",
        "الجديدة", "القديمة", "الهضبة", "كورنيش", "كوبري",
    )
    if any(w in norm for w in address_words):
        return True
    if any(ch.isdigit() for ch in norm):
        return True
    return False


@function_tool()
async def set_delivery_info(
    address: Annotated[
        str,
        Field(
            description=(
                "عنوان التوصيل كامل بالشارع والمنطقة، زي ما قاله العميل "
                "حرفياً. ممنوع تخمين العنوان من جملة عامة زي 'عندي شراكة' "
                "أو 'هنا قريب'. لو الكلام مش عنوان واضح، ابعت سترينج فاضي."
            )
        ),
    ],
    landmark: Annotated[
        str,
        Field(
            description=(
                "معلم قريب يساعد المندوب يلاقي العنوان. "
                "ابعت سترينج فاضي '' لو ما قالش معلم."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل عنوان التوصيل ومعلم قريب."""
    ud = context.userdata
    cfg = ud.restaurant
    cleaned_address = (address or "").strip(" .,،؟!\"'")
    if not cleaned_address:
        return "address_empty"
    if not _looks_like_address(cleaned_address):
        # Don't poison delivery_address with a non-address phrase. The model
        # will ask the customer to repeat — which is the correct behaviour.
        logger.warning(
            "call=%s | set_delivery_info rejected non-address | text=%r",
            ud.call_id, cleaned_address,
        )
        return "address_ambiguous"
    if cfg.delivery_zones:
        from agent import _match_delivery_zone

        matched_zone = _match_delivery_zone(cleaned_address, cfg.delivery_zones)
        if not matched_zone:
            ud.delivery_address = None
            ud.delivery_zone = None
            return (
                f"address_outside_zone | zones={cfg.delivery_zones_text()}"
            )
        ud.delivery_zone = matched_zone
    ud.delivery_address = cleaned_address
    if not ud.active_flow:
        ud.active_flow = "delivery"
    landmark_clean = (landmark or "").strip()
    if landmark_clean:
        ud.delivery_landmark = landmark_clean
    logger.info(
        "call=%s | delivery_address_set | landmark=%s",
        ud.call_id, bool(landmark_clean),
    )
    return "address_saved"


# ─────────────────────────────────────────────────────────────────────────────
# Reservation
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def set_reservation_info(
    time_text: Annotated[
        str,
        Field(
            description=(
                "ميعاد الحجز كنص بكلام العميل. "
                "مثال: 'بكرة الساعة 8 المسا' أو 'يوم الجمعة الظهر'."
            )
        ),
    ],
    guests: Annotated[int, Field(description="عدد الضيوف", ge=1, le=200)],
    branch: Annotated[
        str,
        Field(
            description=(
                "اسم الفرع لو المطعم له فروع متعددة. "
                "ابعت سترينج فاضي '' لو فرع واحد أو ما قالش."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل بيانات الحجز: الميعاد، العدد، والفرع لو متعدد."""
    ud = context.userdata
    cfg = ud.restaurant

    if guests < cfg.min_guests:
        return f"guests_below_min | min={cfg.min_guests}"
    if guests > cfg.max_guests:
        return f"guests_above_max | max={cfg.max_guests}"

    cleaned_time = (time_text or "").strip()
    if not cleaned_time:
        return "time_empty"

    ud.reservation_time = cleaned_time
    # Parse the spoken time to an ISO timestamp for the backend. Falls back
    # to None on parse failure so the model still has the human-readable
    # form to confirm with the customer.
    try:
        from agent import _parse_reservation_time

        parsed = _parse_reservation_time(cleaned_time)
        if parsed and getattr(parsed, "scheduled_at", None):
            ud.reservation_time_iso = parsed.scheduled_at.isoformat()
    except Exception as exc:  # noqa: BLE001
        logger.debug("call=%s | reservation_time parse failed: %s", ud.call_id, exc)
    ud.guests_count = guests
    if not ud.active_flow:
        ud.active_flow = "reservation"

    branch_clean = (branch or "").strip()
    if branch_clean:
        ud.selected_branch = branch_clean
    elif cfg.branches:
        ud.selected_branch = cfg.branches[0].get("name", "") or ""

    logger.info("call=%s | reservation_set | guests=%d", ud.call_id, guests)
    return f"reservation_saved | guests={guests}"


# ─────────────────────────────────────────────────────────────────────────────
# Complaint
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def set_complaint(
    text: Annotated[str, Field(description="نص الشكوى كامل بكلام العميل")],
    category: Annotated[
        str,
        Field(
            description=(
                "نوع الشكوى. اختار من: 'جودة' لمشكلة في الأكل، "
                "'تأخير' لتأخير الطلب، 'خدمة' لمشكلة معاملة، "
                "'توصيل' لمشكلة مع المندوب، 'تاني' لأي حاجة تانية."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل شكوى العميل بنصها كاملة."""
    ud = context.userdata
    cleaned = (text or "").strip()
    if not cleaned:
        return "complaint_text_empty"
    ud.complaint_text = cleaned
    if not ud.active_flow:
        ud.active_flow = "complaint"
    ud.complaint_type = (category or "").strip() or "general"
    logger.info("call=%s | complaint_set | type=%s", ud.call_id, ud.complaint_type)
    return f"complaint_saved | type={ud.complaint_type}"


# ─────────────────────────────────────────────────────────────────────────────
# Menu lookup
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def get_menu(context: RunContext_T) -> str:
    """رجّع المنيو المتاح للعميل بصيغة مختصرة.

    Cache guard: realtime models sometimes re-issue ``get_menu`` after every
    tool cancellation or "ألو" filler. Returning a short cached marker for
    repeat calls within the same session prevents the model from re-reading
    the whole menu out loud each time.
    """
    import time as _time

    ud = context.userdata
    cfg = ud.restaurant
    now = _time.monotonic()
    last_called = getattr(ud, "_get_menu_last_called_at", 0.0)
    if last_called and (now - last_called) < 90.0:
        # Same call, < 90s window: return a stub so the model knows the menu
        # is already in context. The persona tells it not to re-read.
        logger.info("call=%s | get_menu cached (skip re-read)", ud.call_id)
        return "menu_already_loaded"
    ud._get_menu_last_called_at = now
    return cfg.menu_text()


# ─────────────────────────────────────────────────────────────────────────────
# End call — graceful hangup
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def end_call(
    reason: Annotated[
        Literal[
            "order_completed",
            "reservation_completed",
            "complaint_logged",
            "customer_done",
            "other",
        ],
        Field(
            description=(
                "سبب إنهاء المكالمة. order_completed/reservation_completed/"
                "complaint_logged بعد ما يخلص الـ flow، customer_done لو "
                "العميل قال خلاص شكراً، other لأي حاجة تانية."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """قفل المكالمة بشكل لائق مع جملة وداع مسموعة.

    استخدمها بعد ما العميل يأكد على الطلب. الـ tool نفسه هيقول جملة
    الوداع، يستنى الكلام يخلص، وبعدها يقفل المكالمة بأمان.
    """
    from agent import _voice_safe_text

    ud = context.userdata

    # Guard against premature hangup. The realtime model sometimes maps a
    # polite "شكراً"/"اوكي" early in the call to customer_done and hangs up
    # before the customer has placed anything. Refuse unless either:
    #   - a flow has produced concrete state (order/reservation/complaint), or
    #   - the call is at least 25s old (long enough that "thanks" really is a sign-off)
    # The model gets a clear code back so it can keep the conversation going.
    if reason == "customer_done":
        has_state = bool(
            (ud.order or [])
            or (ud.reservation_confirmed)
            or (ud.complaint_logged)
            or ((ud.delivery_address or "").strip())
        )
        import time as _time
        call_age_s = max(0.0, _time.time() - float(ud.call_started_at or _time.time()))
        if not has_state and call_age_s < 25.0:
            logger.info(
                "call=%s | end_call(customer_done) refused | empty_state | age=%.1fs",
                ud.call_id, call_age_s,
            )
            return "end_call_refused_too_early | اسأل الزبون عن طلبه الأول قبل ما تقفل"

    # Guard against the model lying to the customer. The realtime model
    # sometimes calls end_call('order_completed') BEFORE confirm_and_submit
    # actually succeeded — usually right after set_name when it conflates
    # "all slots collected" with "order submitted". That paints a fake
    # success farewell ("الطلب اتسجل") for an order that was never sent.
    # Refuse and force the model to call confirm_and_submit first.
    if reason == "order_completed" and not ud.order_confirmed:
        logger.warning(
            "call=%s | end_call(order_completed) refused | order_confirmed=False — "
            "model tried to hang up before confirm_and_submit succeeded",
            ud.call_id,
        )
        return "end_call_refused_order_not_submitted | نده confirm_and_submit الأول وانتظر takeaway_confirmed أو delivery_confirmed"
    if reason == "reservation_completed" and not ud.reservation_confirmed:
        logger.warning(
            "call=%s | end_call(reservation_completed) refused | reservation_confirmed=False",
            ud.call_id,
        )
        return "end_call_refused_reservation_not_submitted | نده confirm_and_submit الأول"
    if reason == "complaint_logged" and not ud.complaint_logged:
        logger.warning(
            "call=%s | end_call(complaint_logged) refused | complaint_logged=False",
            ud.call_id,
        )
        return "end_call_refused_complaint_not_logged | نده confirm_and_submit الأول"

    logger.info("call=%s | end_call requested | reason=%s", ud.call_id, reason)
    # Mark transitional state so the inactivity watchdog stops reprompting
    # and the call-end bookkeeping treats this as a clean close. Also flag
    # that no further LLM generation should produce audio — the realtime
    # model has a habit of finishing one more half-sentence after a tool
    # call, which the customer hears AFTER the farewell.
    ud.session_transitional_state = True
    ud.end_call_reason = reason
    farewell = _end_call_farewell(ud, reason)
    ud.last_agent_message = farewell

    try:
        from utils.voice import say_safe as _say_safe
        await _say_safe(
            context.session,
            _voice_safe_text(farewell),
            allow_interruptions=False,
            add_to_chat_ctx=True,
        )
    except Exception as exc:
        logger.warning("call=%s | farewell say failed | %s", ud.call_id, exc)

    # Wait for the goodbye line to actually finish playing before tearing
    # down the session. In classic mode this is straightforward via
    # wait_for_playout. In realtime mode (Gemini Live), the synthetic
    # turn we just pushed needs time to (a) reach the model, (b) be
    # processed, and (c) be rendered as audio. wait_for_playout returns
    # immediately because there's no LiveKit-side speech handle to wait
    # on — so we poll the session's agent_state instead: wait for the
    # transition into "speaking", then for it to leave "speaking".
    session = context.session
    is_realtime = getattr(session, "tts", None) is None
    try:
        await context.wait_for_playout()
    except Exception as exc:
        logger.warning("call=%s | wait_for_playout failed | %s", ud.call_id, exc)

    if is_realtime:
        import asyncio as _asyncio
        # Phase 1: wait up to 2s for the model to START speaking.
        for _ in range(40):
            if getattr(session, "agent_state", "") == "speaking":
                break
            await _asyncio.sleep(0.05)
        # Phase 2: wait up to 12s for the model to FINISH speaking (the
        # farewell is short — ~20 chars Arabic — and Gemini Live renders
        # at roughly natural pace, so 12s is a generous upper bound).
        for _ in range(240):
            if getattr(session, "agent_state", "") != "speaking":
                break
            await _asyncio.sleep(0.05)
        # Small tail so the last frames flush into the RTP path before
        # we tear the session down.
        await _asyncio.sleep(0.3)

    # Hard-close immediately. The previous 200ms grace let Gemini 3.1 Live
    # squeeze a trailing half-sentence into the call ("…كينج.") which the
    # customer heard after the farewell. Kill the realtime session first so
    # no more model output reaches the room, then shut down the LiveKit
    # AgentSession. (session and _asyncio are bound above when is_realtime
    # is checked; rebind unconditionally so the classic path also has them.)
    import asyncio as _asyncio

    async def _delayed_close() -> None:
        # Try to interrupt any in-flight model generation before closing.
        try:
            activity = getattr(session, "_activity", None)
            rt = getattr(activity, "realtime_llm_session", None) if activity else None
            if rt is not None:
                for attr in ("interrupt", "close", "aclose"):
                    method = getattr(rt, attr, None)
                    if callable(method):
                        with contextlib.suppress(Exception):
                            result = method()
                            if hasattr(result, "__await__"):
                                await result
                        break
        except Exception as exc:  # noqa: BLE001
            logger.debug("call=%s | realtime interrupt before close: %s", ud.call_id, exc)
        try:
            shutdown = getattr(session, "shutdown", None)
            if callable(shutdown):
                shutdown(drain=False)
            else:
                await session.aclose()
        except Exception as exc:
            logger.warning("call=%s | session close failed | %s", ud.call_id, exc)

    close_task = _asyncio.create_task(_delayed_close(), name=f"end_call_{ud.call_id}")
    ud.background_tasks.append(close_task)
    return "تم قول جملة الوداع للعميل، والمكالمة هتقفل بعد ما الصوت يخلص."


def _end_call_farewell(ud: UserData, reason: str) -> str:
    name = (ud.customer_name or "").strip()
    name_part = f" يا {name}" if name else " يا فندم"
    if reason == "order_completed":
        if (ud.active_flow or "") == "delivery":
            return f"تمام{name_part}، الطلب اتسجل وهيجيلك في أسرع وقت. نورتنا!"
        return f"تمام{name_part}، الطلب اتسجل وهيبقى جاهز في أسرع وقت. نورتنا!"
    if reason == "reservation_completed":
        return f"تمام{name_part}، الحجز اتأكد. نورتنا!"
    if reason == "complaint_logged":
        return f"تمام{name_part}، سجلت الشكوى وهنراجعها مع الإدارة. نورتنا!"
    if reason == "customer_done":
        return f"تمام{name_part}، شكراً لاتصالك. نورتنا!"
    return f"تمام{name_part}، شكراً لاتصالك. نورتنا!"


# ─────────────────────────────────────────────────────────────────────────────
# Confirm and submit
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def confirm_and_submit(context: RunContext_T) -> str:
    """أكد بيانات العميل وقدّم الطلب/الحجز/الشكوى للنظام.

    لازم قبل ما تنده الـ tool ده:
    1. تكون ندهت set_name/set_delivery_info/set_intent على المعلومات اللي الزبون قالها.
    2. تأكد ان كل المطلوب موجود في الـ [CALL_STATE].
    3. الزبون قال "أكد/تمام/صح" بعد ملخصك.

    لو رجّع `missing_*` يعني سلوت ناقصة — ندّه الـ slot tool المطلوب (set_name إلخ)
    **متعيدش** confirm_and_submit بدون ما تنده الـ slot tool الأول.

    لو رجّع `*_confirmed` فيه order_id/reservation_id يبقى تم — ندّه end_call('order_completed')/('reservation_completed').
    """
    from agent import (
        _backend_failure_user_message,
        _backend_queued_user_message,
        _can_attempt_backend_write,
        _emit_event,
        submit_complaint,
        submit_delivery,
        submit_reservation,
        submit_takeaway,
    )

    ud = context.userdata
    cfg = ud.restaurant
    intent = (ud.active_flow or "").strip()

    if not intent:
        return "missing_intent"

    # When the same missing-slot code keeps coming back, the model is stuck
    # in a confirm_and_submit retry loop without calling the slot-setter
    # (e.g. it heard the customer's name but jumps straight to confirm).
    # Echo the customer's last utterance so the model sees what it should
    # have extracted and routes through the right slot tool first.
    def _stuck_hint(code: str) -> str:
        last_user = (ud.last_user_message or "").strip()
        if ud.confirm_last_missing == code:
            ud.confirm_missing_count += 1
        else:
            ud.confirm_last_missing = code
            ud.confirm_missing_count = 1
        if ud.confirm_missing_count >= 2 and last_user:
            return (
                f' | LOOP_DETECTED — رجعتلك {code} {ud.confirm_missing_count} مرات. '
                f'الزبون قال "{last_user}" — استخرج المعلومة وندّه الـ slot tool '
                f'(set_name/set_delivery_info) الأول. متعيدش confirm_and_submit '
                f'قبل ما تنده الـ slot tool!'
            )
        if last_user:
            return f' | اسمع آخر كلام الزبون "{last_user}" واستخرج المعلومة وندّه الـ tool المناسب الأول'
        return ""

    def _clear_stuck() -> None:
        ud.confirm_last_missing = ""
        ud.confirm_missing_count = 0

    # Validate slots per intent — terse codes only. The persona uses these
    # codes to know which slot to collect; no narrative goes into the tool
    # return so the model never reads "tell the customer..." out loud.
    if intent in {"takeaway", "delivery"}:
        if not ud.order:
            return "missing_order" + _stuck_hint("missing_order")
        if intent == "delivery" and not ud.delivery_address:
            return "missing_address | ندّه set_delivery_info الأول" + _stuck_hint("missing_address")
        if intent == "delivery" and cfg.delivery_zones and not ud.delivery_zone:
            return f"address_outside_zone | zones={cfg.delivery_zones_text()}"
        if not ud.customer_name:
            return "missing_name | ندّه set_name(name) الأول بالاسم اللي قاله الزبون" + _stuck_hint("missing_name")
    elif intent == "reservation":
        if not ud.reservation_time:
            return "missing_reservation_time | ندّه set_reservation_info الأول" + _stuck_hint("missing_reservation_time")
        if not ud.guests_count:
            return "missing_guests | ندّه set_reservation_info الأول" + _stuck_hint("missing_guests")
        if len(cfg.branches) > 1 and not ud.selected_branch:
            return f"missing_branch | branches={cfg.branch_names()}"
        if not ud.customer_name:
            return "missing_name | ندّه set_name(name) الأول" + _stuck_hint("missing_name")
    elif intent == "complaint":
        if not ud.complaint_text:
            return "missing_complaint_text"

    # All validation passed — clear stuck-loop counter before submission.
    _clear_stuck()

    # Idempotency — already submitted
    if intent in {"takeaway", "delivery"} and ud.order_confirmed:
        return "already_confirmed"
    if intent == "reservation" and ud.reservation_confirmed:
        return "already_confirmed"
    if intent == "complaint" and ud.complaint_logged:
        return "already_confirmed"

    # In-flight protection
    if (
        (intent in {"takeaway", "delivery"} and ud.order_submit_in_flight)
        or (intent == "reservation" and ud.reservation_submit_in_flight)
        or (intent == "complaint" and ud.complaint_submit_in_flight)
    ):
        return "submit_in_flight"

    if not _can_attempt_backend_write(ud):
        ud.backend_unavailable = True
        _emit_event(
            "backend.write_unavailable",
            call_id=ud.call_id,
            flow=intent,
            action="confirm_and_submit_blocked",
        )
        return "backend_unavailable"

    # ─── takeaway ───────────────────────────────────────────────
    if intent == "takeaway":
        if not ud.order_validated:
            return "order_unvalidated"
        ud.order_submit_in_flight = True
        try:
            result = await submit_takeaway(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            ud.backend_unavailable = True
            return "backend_failed"
        if result.get("queued"):
            return "submit_queued"
        ud.backend_unavailable = False
        ud.order_id = result.get("order_id", "")
        ud.order_confirmed = True
        _emit_event(
            "order.confirmed", call_id=ud.call_id, flow="takeaway",
            order_id=ud.order_id,
        )
        if ud.complaint_text and not ud.complaint_logged:
            complaint_result = await submit_complaint(
                ud, ud.complaint_text, ud.complaint_type or "general",
            )
            if complaint_result and not complaint_result.get("queued"):
                ud.complaint_logged = True
                _emit_event("complaint.logged", call_id=ud.call_id, flow="takeaway")
        wait = result.get("estimated_time", cfg.wait_minutes)
        return f"takeaway_confirmed | wait_minutes={wait} | order_id={ud.order_id}"

    # ─── delivery ───────────────────────────────────────────────
    if intent == "delivery":
        if not ud.order_validated:
            return "order_unvalidated"
        if cfg.min_order > 0 and ud.order_total < cfg.min_order:
            return (
                f"below_min_order | total={money2ar(ud.order_total)} | min={money2ar(cfg.min_order)}"
            )
        ud.order_submit_in_flight = True
        try:
            result = await submit_delivery(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            ud.backend_unavailable = True
            return "backend_failed"
        if result.get("queued"):
            return "submit_queued"
        ud.backend_unavailable = False
        ud.order_id = result.get("order_id", "")
        ud.order_confirmed = True
        _emit_event(
            "order.confirmed", call_id=ud.call_id, flow="delivery",
            order_id=ud.order_id,
        )
        if ud.complaint_text and not ud.complaint_logged:
            complaint_result = await submit_complaint(
                ud, ud.complaint_text, ud.complaint_type or "general",
            )
            if complaint_result and not complaint_result.get("queued"):
                ud.complaint_logged = True
                _emit_event("complaint.logged", call_id=ud.call_id, flow="delivery")
        wait = result.get("estimated_time", cfg.delivery_minutes)
        return f"delivery_confirmed | wait_minutes={wait} | order_id={ud.order_id}"

    # ─── reservation ────────────────────────────────────────────
    if intent == "reservation":
        ud.reservation_submit_in_flight = True
        try:
            result = await submit_reservation(ud)
        finally:
            ud.reservation_submit_in_flight = False
        if not result:
            ud.backend_unavailable = True
            return "backend_failed"
        if result.get("queued"):
            return "submit_queued"
        ud.backend_unavailable = False
        ud.reservation_id = result.get("reservation_id", "")
        ud.reservation_confirmed = True
        _emit_event(
            "reservation.confirmed", call_id=ud.call_id, flow="reservation",
            reservation_id=ud.reservation_id,
        )
        return f"reservation_confirmed | reservation_id={ud.reservation_id}"

    # ─── complaint ──────────────────────────────────────────────
    if intent == "complaint":
        ud.complaint_submit_in_flight = True
        try:
            result = await submit_complaint(
                ud, ud.complaint_text, ud.complaint_type or "general",
            )
        finally:
            ud.complaint_submit_in_flight = False
        if not result:
            ud.backend_unavailable = True
            return "backend_failed"
        if result.get("queued"):
            return "submit_queued"
        ud.backend_unavailable = False
        ud.complaint_logged = True
        _emit_event("complaint.logged", call_id=ud.call_id, flow="complaint")
        return "complaint_confirmed"

    return "unknown_intent"


__all__ = [
    "set_intent",
    "set_name",
    "update_order",
    "remove_order_item",
    "set_order_item_qty",
    "clear_order",
    "set_delivery_info",
    "set_reservation_info",
    "set_complaint",
    "get_menu",
    "confirm_and_submit",
    "end_call",
]
