from __future__ import annotations

import re

from backend.config import RestaurantConfig
from core.actions import DialogueAction, QuestionCategory
from core.prebuilt_replies import question_for, repeat_for
from core.telemetry import emit_event
from state.user_data import UserData
from utils.money import money2ar, num2ar
from utils.voice import _voice_safe_text


# The dialogue engine sources its slot-question and repeat-guard replies
# from ``core.prebuilt_replies`` so the strings live in one place. Phase
# 2.2 turned these into variant pools — the helper functions pick a
# fresh phrasing per call so the agent stops sounding like a tape loop.


def spoken_order_items(order: list[str] | None) -> str:
    items = [item.strip() for item in (order or []) if item and item.strip()]
    return "، ".join(items)


def takeaway_missing_slot(ud: UserData) -> str | None:
    if not ud.order:
        return "الطلب"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def delivery_missing_slot(ud: UserData) -> str | None:
    if not ud.order:
        return "الطلب"
    if not ud.delivery_address:
        return "العنوان والمنطقة"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def reservation_missing_slot(ud: UserData, cfg: RestaurantConfig) -> str | None:
    if not ud.reservation_time:
        return "وقت الحجز"
    if ud.guests_count is None:
        return "عدد الضيوف"
    if len(cfg.branches) > 1 and not ud.selected_branch:
        return "الفرع"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def complaint_missing_slot(ud: UserData) -> str | None:
    if not ud.complaint_text:
        return "الشكوى"
    if not ud.complaint_type:
        return "نوع الشكوى"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def takeaway_confirmation_prompt(ud: UserData) -> str:
    total = f"، الإجمالي {money2ar(ud.order_total)} جنيه" if ud.order_validated and ud.order_total > 0 else ""
    return f"{spoken_order_items(ud.order)}{total} باسم {ud.customer_name}، صح؟"


def delivery_confirmation_prompt(ud: UserData) -> str:
    total = ud.order_total
    fee = float(getattr(ud.restaurant, "delivery_fee", 0) or 0)
    total += fee
    total_part = f"، الإجمالي {money2ar(total)} جنيه" if ud.order_validated and total > 0 else ""
    if fee > 0 and ud.order_validated:
        total_part += " شامل التوصيل"
    return f"{spoken_order_items(ud.order)}{total_part} لعنوان {ud.delivery_address} باسم {ud.customer_name}، صح؟"


def reservation_confirmation_prompt(ud: UserData) -> str:
    return f"حجز {num2ar(ud.guests_count or 0)} ضيوف يوم {ud.reservation_time} باسم {ud.customer_name}، صح؟"


def slot_to_category(slot: str | None) -> QuestionCategory:
    if slot == "الطلب":
        return "order"
    if slot == "الاسم":
        return "name"
    if slot == "رقم الموبايل":
        return "phone"
    if slot == "العنوان والمنطقة":
        return "address"
    if slot == "وقت الحجز":
        return "reservation_time"
    if slot == "عدد الضيوف":
        return "guests"
    if slot == "الفرع":
        return "branch"
    if slot == "الشكوى":
        return "complaint"
    if slot == "نوع الشكوى":
        return "complaint_type"
    return "unknown"


def _branch_question(cfg: RestaurantConfig) -> str:
    try:
        branches = cfg.branch_names()
    except Exception:
        branches = ""
    return f"أي فرع تفضل؟ {branches}".strip()


class DialogueEngine:
    """Deterministic policy owner for required slots and next questions."""

    def handle_turn(self, flow: str, ud: UserData, text: str = "") -> DialogueAction:
        """Return the deterministic next action for a user turn.

        Phase 1 keeps extraction in the existing flow code. The turn text is
        accepted here so callers can move to the engine API without changing
        their call shape again when extractors are promoted in later phases.
        """
        _ = text
        return self.next_action(flow, ud)

    def next_action(self, flow: str, ud: UserData) -> DialogueAction:
        flow = (flow or "").strip().lower()
        if flow == "takeaway":
            return self._flow_action(flow, takeaway_missing_slot(ud), ud, takeaway_confirmation_prompt)
        if flow == "delivery":
            return self._flow_action(flow, delivery_missing_slot(ud), ud, delivery_confirmation_prompt)
        if flow == "reservation":
            return self._flow_action(
                flow,
                reservation_missing_slot(ud, ud.restaurant),
                ud,
                reservation_confirmation_prompt,
            )
        if flow == "complaint":
            missing = complaint_missing_slot(ud)
            if missing is None:
                return self._question_action("post_completion", question_for("post_completion"), slot=None, ud=ud)
            return self._slot_question_action(flow, missing, ud, cfg=ud.restaurant)
        return DialogueAction(type="no_action")

    def next_question(self, flow: str, ud: UserData) -> str:
        return self.next_action(flow, ud).message

    def _flow_action(
        self,
        flow: str,
        missing: str | None,
        ud: UserData,
        confirmation_builder,
    ) -> DialogueAction:
        if missing is None:
            message = _voice_safe_text(confirmation_builder(ud), max_chars=220)
            self._record_question(ud, "confirmation")
            emit_event("order.confirmation_prompted", call_id=ud.call_id or "", flow=flow)
            return DialogueAction(
                type="confirm",
                message=message,
                question_category="confirmation",
                critical=True,
            )
        return self._slot_question_action(flow, missing, ud, cfg=ud.restaurant)

    def _slot_question_action(self, flow: str, slot: str, ud: UserData, *, cfg: RestaurantConfig) -> DialogueAction:
        category = slot_to_category(slot)
        if category == "branch":
            message = _branch_question(cfg)
        else:
            message = question_for(category)

        # Cut a turn off the call when the customer needs to give both
        # the name and the phone — ask for them together. The LLM
        # understanding extracts both from a single response so the
        # engine handles the combined turn cleanly. The repeat-guard
        # below keeps catching genuine "asked the same thing twice"
        # bugs because we still record the higher-priority slot.
        combined = self._maybe_combine_name_phone(category, ud)
        if combined is not None:
            message = combined

        if self._would_repeat_question(ud, category):
            message = repeat_for(category) or message
            emit_event(
                "slot.repeated_question_blocked",
                call_id=ud.call_id or "",
                flow=flow,
                question_category=category,
                slot=slot,
            )
        self._record_question(ud, category)
        return DialogueAction(type="ask_slot", message=message, question_category=category, slot=slot)

    @staticmethod
    def _maybe_combine_name_phone(category: QuestionCategory, ud: UserData) -> str | None:
        """Return a combined "ممكن الاسم ورقم الموبايل؟" prompt when both
        are still missing, so we don't spend two separate turns on it.

        Returns ``None`` (caller falls back to the per-slot prompt) when
        only one of the two slots is missing.
        """
        if category not in {"name", "phone"}:
            return None
        if ud.customer_name or ud.customer_phone:
            return None
        return "ممكن الاسم ورقم الموبايل لو سمحت؟"

    def _question_action(
        self,
        category: QuestionCategory,
        message: str,
        *,
        slot: str | None,
        ud: UserData,
    ) -> DialogueAction:
        if self._would_repeat_question(ud, category):
            message = repeat_for(category) or message
        self._record_question(ud, category)
        return DialogueAction(type="ask_slot", message=message, question_category=category, slot=slot)

    @staticmethod
    def _would_repeat_question(ud: UserData, category: QuestionCategory) -> bool:
        return bool(category and ud.last_question_category == category)

    @staticmethod
    def _record_question(ud: UserData, category: QuestionCategory) -> None:
        ud.last_question_category = category
        history = list(ud.question_category_history or [])
        history.append(category)
        ud.question_category_history = history[-8:]


def clean_question_category(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()
