"""Explicit confirmation state machine for orders and reservations.

The dialogue engine consults this module to know whether a turn should:

- ask the customer to confirm an order summary,
- ignore a re-confirm because the backend already accepted the order,
- queue a retry because the previous submit failed,
- block submission because required slots are still missing.

States:

    DRAFT                    – customer is still building the order or
                               filling required slots.
    READY_FOR_CONFIRMATION   – every required slot is captured and the
                               order is validated against the menu.
    CONFIRMATION_PROMPTED    – we have read the summary back to the
                               customer and are waiting for a yes/no.
    CONFIRMED                – customer said "yes". Submission has not
                               started yet.
    SUBMITTED                – backend accepted the order and returned
                               an id. This is a terminal success.
    FAILED                   – backend rejected the order. The order is
                               still recoverable; we don't reset slots.

Transitions are deterministic. The engine drives them from inspecting
``UserData`` (which is the source of truth) — this module is pure logic
with no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from state.user_data import UserData


ConfirmationState = Literal[
    "draft",
    "ready_for_confirmation",
    "confirmation_prompted",
    "confirmed",
    "submitted",
    "failed",
]


FlowKind = Literal["takeaway", "delivery", "reservation", "complaint"]


@dataclass(frozen=True)
class ConfirmationView:
    state: ConfirmationState
    flow: FlowKind
    summary_required: bool
    can_submit: bool
    is_terminal: bool
    detail: str = ""


def confirmation_view(flow: FlowKind, ud: UserData) -> ConfirmationView:
    """Compute the current confirmation state for a flow.

    The function is read-only; it never mutates ``UserData``.
    """
    if flow == "takeaway":
        return _order_view(ud, flow="takeaway", missing=_takeaway_missing(ud))
    if flow == "delivery":
        return _order_view(ud, flow="delivery", missing=_delivery_missing(ud))
    if flow == "reservation":
        return _reservation_view(ud)
    if flow == "complaint":
        return _complaint_view(ud)
    return ConfirmationView(
        state="draft",
        flow=flow,
        summary_required=True,
        can_submit=False,
        is_terminal=False,
        detail="unknown_flow",
    )


def _takeaway_missing(ud: UserData) -> list[str]:
    missing: list[str] = []
    if not ud.order:
        missing.append("الطلب")
    if not ud.customer_name:
        missing.append("الاسم")
    if not ud.customer_phone:
        missing.append("رقم الموبايل")
    return missing


def _delivery_missing(ud: UserData) -> list[str]:
    missing: list[str] = []
    if not ud.order:
        missing.append("الطلب")
    if not ud.delivery_address:
        missing.append("العنوان والمنطقة")
    if not ud.customer_name:
        missing.append("الاسم")
    if not ud.customer_phone:
        missing.append("رقم الموبايل")
    return missing


def _reservation_missing(ud: UserData) -> list[str]:
    missing: list[str] = []
    if not ud.reservation_time:
        missing.append("وقت الحجز")
    if ud.guests_count is None:
        missing.append("عدد الضيوف")
    if not ud.customer_name:
        missing.append("الاسم")
    if not ud.customer_phone:
        missing.append("رقم الموبايل")
    return missing


def _complaint_missing(ud: UserData) -> list[str]:
    missing: list[str] = []
    if not ud.complaint_text:
        missing.append("الشكوى")
    if not ud.complaint_type:
        missing.append("نوع الشكوى")
    if not ud.customer_name:
        missing.append("الاسم")
    if not ud.customer_phone:
        missing.append("رقم الموبايل")
    return missing


def _order_view(ud: UserData, *, flow: FlowKind, missing: list[str]) -> ConfirmationView:
    if ud.order_confirmed and ud.order_id:
        return ConfirmationView(
            state="submitted",
            flow=flow,
            summary_required=False,
            can_submit=False,
            is_terminal=True,
            detail="order_already_submitted",
        )
    if ud.order_submit_in_flight:
        return ConfirmationView(
            state="confirmed",
            flow=flow,
            summary_required=False,
            can_submit=False,
            is_terminal=False,
            detail="submit_in_flight",
        )
    if missing:
        return ConfirmationView(
            state="draft",
            flow=flow,
            summary_required=True,
            can_submit=False,
            is_terminal=False,
            detail="missing:" + ",".join(missing),
        )
    if not ud.order_validated:
        return ConfirmationView(
            state="draft",
            flow=flow,
            summary_required=True,
            can_submit=False,
            is_terminal=False,
            detail="order_not_validated",
        )
    return ConfirmationView(
        state="ready_for_confirmation",
        flow=flow,
        summary_required=True,
        can_submit=True,
        is_terminal=False,
        detail="all_slots_complete",
    )


def _reservation_view(ud: UserData) -> ConfirmationView:
    if ud.reservation_confirmed and ud.reservation_id:
        return ConfirmationView(
            state="submitted",
            flow="reservation",
            summary_required=False,
            can_submit=False,
            is_terminal=True,
            detail="reservation_already_submitted",
        )
    if ud.reservation_submit_in_flight:
        return ConfirmationView(
            state="confirmed",
            flow="reservation",
            summary_required=False,
            can_submit=False,
            is_terminal=False,
            detail="submit_in_flight",
        )
    missing = _reservation_missing(ud)
    if missing:
        return ConfirmationView(
            state="draft",
            flow="reservation",
            summary_required=True,
            can_submit=False,
            is_terminal=False,
            detail="missing:" + ",".join(missing),
        )
    return ConfirmationView(
        state="ready_for_confirmation",
        flow="reservation",
        summary_required=True,
        can_submit=True,
        is_terminal=False,
        detail="all_slots_complete",
    )


def _complaint_view(ud: UserData) -> ConfirmationView:
    if ud.complaint_logged:
        return ConfirmationView(
            state="submitted",
            flow="complaint",
            summary_required=False,
            can_submit=False,
            is_terminal=True,
            detail="complaint_already_logged",
        )
    if ud.complaint_submit_in_flight:
        return ConfirmationView(
            state="confirmed",
            flow="complaint",
            summary_required=False,
            can_submit=False,
            is_terminal=False,
            detail="submit_in_flight",
        )
    missing = _complaint_missing(ud)
    if missing:
        return ConfirmationView(
            state="draft",
            flow="complaint",
            summary_required=True,
            can_submit=False,
            is_terminal=False,
            detail="missing:" + ",".join(missing),
        )
    return ConfirmationView(
        state="ready_for_confirmation",
        flow="complaint",
        summary_required=False,
        can_submit=True,
        is_terminal=False,
        detail="all_slots_complete",
    )


def is_duplicate_confirm(flow: FlowKind, ud: UserData) -> bool:
    """Return True if the engine should reply "already submitted"."""
    return confirmation_view(flow, ud).state == "submitted"


def can_attempt_submit(flow: FlowKind, ud: UserData) -> bool:
    return confirmation_view(flow, ud).can_submit


__all__ = [
    "ConfirmationState",
    "ConfirmationView",
    "FlowKind",
    "can_attempt_submit",
    "confirmation_view",
    "is_duplicate_confirm",
]
