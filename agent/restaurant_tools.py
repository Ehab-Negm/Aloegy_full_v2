"""Function tools used by RestaurantAgent.

Each tool maps to one piece of customer state. The LLM calls them as it
gathers information, and confirm_and_submit hands the assembled state to
the existing backend submit_X helpers in agent.py.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext
from pydantic import BaseModel, Field

from nlp.phone_extract import (
    is_plausible_partial_phone_digits,
    local_phone_digits,
    merge_phone_digits,
    phone_digits_only,
    validate_phone,
)
from state.user_data import UserData
from utils.money import money2ar, num2ar, phone2ar

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
        return "التوصيل مش متاح في المطعم. اعرض على العميل الاستلام بدلاً منه."
    previous = ud.active_flow
    ud.active_flow = intent
    if previous and previous != intent:
        logger.info(
            "call=%s | intent_changed | from=%s | to=%s",
            ud.call_id, previous, intent,
        )
    return f"النية اتسجلت: {intent}. كمّل عادي وشوف ناقص إيه."


@function_tool()
async def set_name(
    name: Annotated[str, Field(description="اسم العميل بالعربي زي ما قاله")],
    context: RunContext_T,
) -> str:
    """سجّل اسم العميل لما يقوله."""
    ud = context.userdata
    cleaned = (name or "").strip()
    if not cleaned:
        return "الاسم فاضي. اطلب من العميل يعيده."
    ud.customer_name = cleaned
    logger.info("call=%s | name_set | name=%s", ud.call_id, cleaned)
    return f"الاسم اتسجل: {cleaned}"


@function_tool()
async def set_phone(
    phone: Annotated[
        str,
        Field(
            description=(
                "أرقام موبايل العميل زي ما قالها — كاملة (01012345678) أو مجزأة "
                "(0155 أو 8950484). الـ tool بيبافر الأجزاء لحد ما يكتمل الرقم."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل أو كمّل رقم موبايل العميل. بيقبل الرقم كامل أو على chunks ويبافر لحد ما يكتمل ١١ رقم."""
    ud = context.userdata

    incoming_digits = phone_digits_only(phone)
    if not incoming_digits:
        return (
            "مفيش أرقام في كلام العميل. "
            "اطلب منه يقول الرقم تاني بوضوح."
        )

    # Try the incoming chunk on its own first (in case the customer restated
    # the full number rather than continuing a partial).
    direct_valid = validate_phone(incoming_digits)
    combined_digits = merge_phone_digits(ud.pending_phone_digits or "", incoming_digits)
    combined_valid = validate_phone(combined_digits)
    cleaned = direct_valid or combined_valid

    if cleaned:
        was_chunked = bool(ud.pending_phone_digits) and not direct_valid
        ud.customer_phone = cleaned
        ud.pending_phone_digits = ""
        logger.info(
            "call=%s | phone_set | chunked=%s",
            ud.call_id, was_chunked,
        )
        return f"الرقم اتسجل كامل: {phone2ar(cleaned)}"

    # Not yet a full valid number — buffer if it looks plausible.
    partial = combined_digits if is_plausible_partial_phone_digits(combined_digits) else ""
    if partial:
        ud.pending_phone_digits = partial
        local = local_phone_digits(partial)
        remaining = max(0, 11 - len(local))
        logger.info(
            "call=%s | phone_partial_buffered | digits=%d | remaining=%d",
            ud.call_id, len(partial), remaining,
        )
        if remaining > 0:
            return (
                f"اتخزّن مبدئياً ({len(local)} رقم من ١١). "
                f"اطلب من العميل باقي الـ{remaining} رقم."
            )
        return (
            f"اتخزّن {len(local)} رقم بس مش valid. "
            "ممكن يبدأ بـ 010 أو 011 أو 012 أو 015 — اطلب من العميل يعيد الرقم."
        )

    # Not plausible at all — reset buffer and ask for re-entry
    ud.pending_phone_digits = ""
    return (
        "الرقم اللي قاله مش رقم مصري صحيح. "
        "لازم يبدأ بـ 010 أو 011 أو 012 أو 015 ويكون ١١ رقم. "
        "اطلب من العميل يعيده من الأول."
    )


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
                "قائمة الأصناف. كل عنصر فيه name (اسم الصنف فقط) و qty (الكمية). "
                "أمثلة:\n"
                "- 'بيتزا واحدة' → [{name:'بيتزا مارجريتا', qty:1}]\n"
                "- 'تلاتة شاورما فراخ' → [{name:'شاورما فراخ', qty:3}]\n"
                "- '12 صندوق شاورما لحمة' → [{name:'شاورما لحمة', qty:12}]\n"
                "- 'بيتزا وكولا' → [{name:'بيتزا', qty:1}, {name:'كولا', qty:1}]"
            )
        ),
    ],
    replace: Annotated[
        bool,
        Field(
            description=(
                "True لو العميل بدّل الطلب كله. "
                "False لو بيضيف على الموجود."
            )
        ),
    ],
    context: RunContext_T,
) -> str:
    """سجّل أو حدّث طلب العميل بالكميات. الـ tool بيتأكد من المنيو ويحسب الإجمالي."""
    from agent import _normalize_order_items

    ud = context.userdata
    cfg = ud.restaurant

    # Convert structured (name, qty) into the "name x qty" string format
    # the existing parser understands, so qty is preserved through the
    # pipeline. qty=1 stays as a bare name (cleaner downstream display).
    new_items: list[str] = []
    for spec in items:
        name = (spec.name or "").strip()
        if not name:
            continue
        qty = max(1, min(99, int(spec.qty or 1)))
        new_items.append(name if qty == 1 else f"{name} x {qty}")

    if not new_items:
        return "الطلب فاضي. اطلب من العميل يقول الأصناف."

    # Degraded mode: no menu to validate against — accept items raw
    if not cfg.menu_items:
        if replace or not ud.order:
            ud.order = new_items
        else:
            ud.order = (ud.order or []) + new_items
        ud.order_validated = False
        ud.order_total = 0.0
        if ud.order_confirmed:
            ud.order_confirmed = False
        return (
            f"الطلب اتسجل مبدئياً (المنيو مش متاح للتأكيد دلوقتي): "
            f"{', '.join(ud.order)}"
        )

    # Combine with existing order if appending
    combined = new_items if (replace or not ud.order) else (ud.order + new_items)
    normalized, unknown, total = _normalize_order_items(combined, cfg.menu_items)

    if not normalized:
        return (
            f"مفيش حاجة من اللي طلبه ('{', '.join(unknown)}') في المنيو. "
            f"اعرض على العميل المتاح: {cfg.menu_text()}"
        )

    ud.order = normalized
    ud.order_total = total
    ud.order_validated = not unknown

    # If the order changed after confirmation, the confirmation is no longer valid
    if ud.order_confirmed:
        ud.order_confirmed = False

    msg = f"الطلب اتسجل: {', '.join(normalized)} (إجمالي: {money2ar(total)} جنيه)."
    if unknown:
        msg += (
            f" بس '{', '.join(unknown)}' مش في المنيو — "
            "لو حابب اعرض بدائل للعميل."
        )
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def set_delivery_info(
    address: Annotated[str, Field(description="عنوان التوصيل كامل بالشارع والمنطقة")],
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
    cleaned_address = (address or "").strip()
    if not cleaned_address:
        return "العنوان فاضي. اطلب من العميل يقوله تاني."
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
    msg = f"العنوان اتسجل: {cleaned_address}"
    if landmark_clean:
        msg += f" (معلم: {landmark_clean})"
    return msg


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
        return (
            f"أقل عدد ضيوف للحجز عندنا {num2ar(cfg.min_guests)}. "
            "اعتذر للعميل."
        )
    if guests > cfg.max_guests:
        return (
            f"أكتر عدد ضيوف نقدر نحجزله {num2ar(cfg.max_guests)}. "
            "اعتذر للعميل."
        )

    cleaned_time = (time_text or "").strip()
    if not cleaned_time:
        return "ميعاد الحجز فاضي. اطلب من العميل يحدد الميعاد."

    ud.reservation_time = cleaned_time
    ud.guests_count = guests
    if not ud.active_flow:
        ud.active_flow = "reservation"

    branch_clean = (branch or "").strip()
    if branch_clean:
        ud.selected_branch = branch_clean
    elif len(cfg.branches) == 1:
        ud.selected_branch = cfg.branches[0].get("name", "") or ""

    logger.info("call=%s | reservation_set | guests=%d", ud.call_id, guests)
    msg = f"الحجز اتسجل: {cleaned_time} لـ{num2ar(guests)} نفر"
    if ud.selected_branch:
        msg += f" - فرع {ud.selected_branch}"
    elif len(cfg.branches) > 1:
        msg += f" — لسه محتاجين الفرع (متاح: {cfg.branch_names()})"
    return msg


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
        return "نص الشكوى فاضي. اطلب من العميل يحكي بالتفصيل ايه اللي حصل."
    ud.complaint_text = cleaned
    if not ud.active_flow:
        ud.active_flow = "complaint"
    ud.complaint_type = (category or "").strip() or "general"
    logger.info("call=%s | complaint_set | type=%s", ud.call_id, ud.complaint_type)
    return f"الشكوى اتسجلت. النوع: {ud.complaint_type}"


# ─────────────────────────────────────────────────────────────────────────────
# Menu lookup
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def get_menu(context: RunContext_T) -> str:
    """رجّع المنيو المتاح للعميل بصيغة مختصرة."""
    cfg = context.userdata.restaurant
    return cfg.menu_text()


# ─────────────────────────────────────────────────────────────────────────────
# Confirm and submit
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def confirm_and_submit(context: RunContext_T) -> str:
    """أكد بيانات العميل وقدّم الطلب/الحجز/الشكوى للنظام.

    استخدم لما تكون جمعت كل البيانات اللازمة والعميل قال 'تمام' أو 'أكد'.
    الـ tool هيراجع البيانات الناقصة ويرجّع رسالة لو في حاجة لازم تسأل عنها.
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
        return (
            "النية مش معروفة. اسأل العميل: عايز استلام، توصيل، حجز، ولا شكوى؟"
        )

    # Validate slots per intent
    if intent in {"takeaway", "delivery"}:
        if not ud.order:
            return "ناقص الطلب. اسأل العميل عن الأصناف اللي عايزها."
        if intent == "delivery" and not ud.delivery_address:
            return "ناقص العنوان. اطلبه من العميل قبل التأكيد."
        if not ud.customer_name:
            return "ناقص الاسم. اطلبه من العميل."
        if not ud.customer_phone:
            return "ناقص الموبايل. اطلبه من العميل."
    elif intent == "reservation":
        if not ud.reservation_time:
            return "ناقص ميعاد الحجز."
        if not ud.guests_count:
            return "ناقص عدد الضيوف."
        if len(cfg.branches) > 1 and not ud.selected_branch:
            return f"ناقص الفرع. الفروع المتاحة: {cfg.branch_names()}."
        if not ud.customer_name:
            return "ناقص الاسم. اطلبه من العميل."
        if not ud.customer_phone:
            return "ناقص الموبايل. اطلبه من العميل."
    elif intent == "complaint":
        if not ud.complaint_text:
            return "ناقص نص الشكوى. اطلب من العميل يحكي ايه اللي حصل."

    # Idempotency — already submitted
    if intent in {"takeaway", "delivery"} and ud.order_confirmed:
        return "الطلب متسجل خلاص. لو سأل العميل عن حاجة تانية رد عليه."
    if intent == "reservation" and ud.reservation_confirmed:
        return "الحجز متسجل خلاص."
    if intent == "complaint" and ud.complaint_logged:
        return "الشكوى متسجلة خلاص."

    # In-flight protection
    if (
        (intent in {"takeaway", "delivery"} and ud.order_submit_in_flight)
        or (intent == "reservation" and ud.reservation_submit_in_flight)
        or (intent == "complaint" and ud.complaint_submit_in_flight)
    ):
        return "ثانية واحدة، البيانات بتتسجل دلوقتي."

    if not _can_attempt_backend_write(ud):
        return _backend_failure_user_message(ud)

    # ─── takeaway ───────────────────────────────────────────────
    if intent == "takeaway":
        if not ud.order_validated:
            return (
                "الطلب لسه محتاج تأكيد من المنيو. "
                "نده update_order مرة تانية بنفس الأصناف."
            )
        ud.order_submit_in_flight = True
        try:
            result = await submit_takeaway(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("order")
        ud.order_id = result.get("order_id", "")
        ud.order_confirmed = True
        _emit_event(
            "order.confirmed", call_id=ud.call_id, flow="takeaway",
            order_id=ud.order_id,
        )
        wait = result.get("estimated_time", cfg.wait_minutes)
        return (
            f"الطلب اتأكد. أكد للعميل: 'تمام يا {ud.customer_name}، "
            f"الطلب اتسجل، هيبقى جاهز خلال {num2ar(wait)} دقيقة.'"
        )

    # ─── delivery ───────────────────────────────────────────────
    if intent == "delivery":
        if not ud.order_validated:
            return "الطلب لسه محتاج تأكيد من المنيو."
        if cfg.min_order > 0 and ud.order_total < cfg.min_order:
            return (
                f"إجمالي الطلب ({money2ar(ud.order_total)} جنيه) أقل من الحد الأدنى "
                f"للتوصيل ({money2ar(cfg.min_order)} جنيه). اعرض زيادة."
            )
        ud.order_submit_in_flight = True
        try:
            result = await submit_delivery(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("order")
        ud.order_id = result.get("order_id", "")
        ud.order_confirmed = True
        _emit_event(
            "order.confirmed", call_id=ud.call_id, flow="delivery",
            order_id=ud.order_id,
        )
        wait = result.get("estimated_time", cfg.delivery_minutes)
        return (
            f"التوصيل اتأكد. أكد للعميل: 'تمام يا {ud.customer_name}، "
            f"الطلب هيوصلك خلال {num2ar(wait)} دقيقة.'"
        )

    # ─── reservation ────────────────────────────────────────────
    if intent == "reservation":
        ud.reservation_submit_in_flight = True
        try:
            result = await submit_reservation(ud)
        finally:
            ud.reservation_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("reservation")
        ud.reservation_id = result.get("reservation_id", "")
        ud.reservation_confirmed = True
        _emit_event(
            "reservation.confirmed", call_id=ud.call_id, flow="reservation",
            reservation_id=ud.reservation_id,
        )
        return (
            f"الحجز اتأكد. أكد للعميل: 'تمام يا {ud.customer_name}، "
            f"حجزتلك ترابيزة لـ{num2ar(ud.guests_count)} نفر يوم {ud.reservation_time}.'"
        )

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
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("complaint")
        ud.complaint_logged = True
        _emit_event("complaint.logged", call_id=ud.call_id, flow="complaint")
        name_part = f"يا {ud.customer_name}" if ud.customer_name else "يا فندم"
        return (
            f"الشكوى اتسجلت. أكد للعميل: 'تمام {name_part}، "
            "سجلت شكواك وفريق الإدارة هيتابعها معاك.'"
        )

    return "النية غير معروفة."


__all__ = [
    "set_intent",
    "set_name",
    "set_phone",
    "update_order",
    "set_delivery_info",
    "set_reservation_info",
    "set_complaint",
    "get_menu",
    "confirm_and_submit",
]
