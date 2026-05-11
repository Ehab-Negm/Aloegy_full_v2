from __future__ import annotations

import json as _json
from dataclasses import InitVar, dataclass, field
from typing import Any

from backend.config import RestaurantConfig
from state.worker_context import WorkerContext


@dataclass
class CallWriteHealth:
    write_available: bool = True
    last_write_error: str = ""
    last_write_failure_kind: str = ""
    last_write_status_code: int | None = None
    write_blocked_until_monotonic: float = 0.0


@dataclass
class CustomerInfo:
    name: str | None = None
    phone: str | None = None
    pending_phone_digits: str = ""
    phone_capture_mode: bool = False
    phone_capture_turns: int = 0
    phone_capture_failures: int = 0


@dataclass
class ConfirmedFacts:
    """Tracks what was already explicitly confirmed by the customer.
    Used to prevent the LLM from re-asking confirmed information."""
    name_confirmed: bool = False
    phone_confirmed: bool = False
    order_confirmed_at_turn: int = 0
    address_confirmed: bool = False
    landmark_confirmed: bool = False
    reservation_time_confirmed: bool = False
    guests_count_confirmed: bool = False


@dataclass
class OrderState:
    items: list[str] | None = None
    validated: bool = False
    total: float = 0.0
    special_request: str | None = None
    upsell_offered: bool = False
    upsell_accepted: bool = False
    pending_upsell_item: str | None = None
    pending_upsell_price: float | None = None
    confirmed: bool = False
    order_id: str | None = None
    submit_in_flight: bool = False


@dataclass
class DeliveryState:
    address: str | None = None
    zone: str | None = None
    landmark: str | None = None
    landmark_asked: bool = False


@dataclass
class ReservationState:
    time: str | None = None
    time_iso: str | None = None
    guests: int | None = None
    branch: str | None = None
    notes: str | None = None
    confirmed: bool = False
    reservation_id: str | None = None
    submit_in_flight: bool = False


@dataclass
class ComplaintState:
    text: str | None = None
    category: str | None = None
    logged: bool = False
    submit_in_flight: bool = False


@dataclass
class UserData:
    customer: CustomerInfo = field(default_factory=CustomerInfo)
    order_state: OrderState = field(default_factory=OrderState)
    delivery_state: DeliveryState = field(default_factory=DeliveryState)
    reservation_state: ReservationState = field(default_factory=ReservationState)
    complaint_state: ComplaintState = field(default_factory=ComplaintState)
    confirmed_facts: ConfirmedFacts = field(default_factory=ConfirmedFacts)

    agents: dict[str, Any] = field(default_factory=dict)
    prev_agent: Any | None = None
    call_id: str | None = None
    restaurant: RestaurantConfig = field(default_factory=RestaurantConfig)
    write_health: CallWriteHealth = field(default_factory=CallWriteHealth)
    worker_context: WorkerContext | None = None
    session_transitional_state: bool = False
    last_user_message: str = ""
    last_agent_message: str = ""
    turn_count: int = 0
    asked_slot_questions: dict[str, int] = field(default_factory=dict)
    active_flow: str = ""
    intents_seen: set[str] = field(default_factory=set)
    handoff_target: str = ""
    # Set by the conversation_item_added listener when the LLM re-asks a slot
    # that's already captured in this UserData (the "soft" alert in the
    # per-turn prompt was ignored). On the next user turn, the agent skips the
    # LLM entirely and says this exact line — a deterministic recovery instead
    # of a third re-ask. One-shot; cleared by on_user_turn_completed.
    pending_corrective_response: str = ""
    pending_corrective_turn: int = 0
    pending_corrective_source: str = ""
    # Set by the end_call function tool just before scheduling the session
    # close. Recorded in call telemetry so we can distinguish agent-initiated
    # hangups from inactivity / participant disconnect / errors.
    end_call_reason: str = ""
    backend_unavailable: bool = False
    background_tasks: list[Any] = field(default_factory=list)
    # Channel the call arrived on: "web" for browser widgets, "sip" for
    # phone calls bridged through LiveKit-SIP (e.g. customer's Issabel PBX).
    # Logging, prompts, and the realtime greeting bootstrap branch on this.
    call_source: str = "web"
    # E.164 caller phone for SIP calls (e.g. "+201001234567"). Empty for
    # web calls or anonymous SIP From headers.
    caller_phone: str = ""
    # Asterisk / SIP trunk that delivered the call. Lets us correlate
    # agent-side incidents with the customer's PBX logs and bill calls
    # back to the right tenant when one trunk == one restaurant.
    sip_trunk_id: str = ""
    sip_call_id: str = ""
    # Unix timestamp when the call entered the agent. Used to gate
    # `end_call(customer_done)` so a polite "شكراً" within the first few
    # seconds doesn't trigger a premature hangup before the customer has
    # placed anything.
    call_started_at: float = 0.0
    # Public restaurant identifier from room metadata (e.g. "demo-restaurant",
    # "pizza-king"). The agent forwards this as ``X-Restaurant-ID`` on every
    # /orders, /reservations, /complaints, /calls/upsert POST so the backend
    # stores the row under the correct tenant instead of always defaulting to
    # ``demo-restaurant``. Empty string = agent should NOT send the header and
    # let the backend pick its default.
    restaurant_public_id: str = ""
    # Tracks the last missing_* code returned by confirm_and_submit and how
    # many times it's been returned in a row. Lets the tool escalate its
    # response when the model loops on confirm_and_submit instead of routing
    # through the relevant slot tool (set_name, set_delivery_info, …).
    confirm_last_missing: str = ""
    confirm_missing_count: int = 0

    customer_name: InitVar[str | None] = None
    customer_phone: InitVar[str | None] = None
    pending_phone_digits: InitVar[str] = ""
    phone_capture_mode: InitVar[bool] = False
    phone_capture_turns: InitVar[int] = 0
    phone_capture_failures: InitVar[int] = 0

    order: InitVar[list[str] | None] = None
    order_validated: InitVar[bool] = False
    order_total: InitVar[float] = 0.0
    special_requests: InitVar[str | None] = None
    upsell_offered: InitVar[bool] = False
    upsell_accepted: InitVar[bool] = False
    pending_upsell_item: InitVar[str | None] = None
    pending_upsell_price: InitVar[float | None] = None
    order_confirmed: InitVar[bool] = False
    order_id: InitVar[str | None] = None
    order_submit_in_flight: InitVar[bool] = False

    delivery_address: InitVar[str | None] = None
    delivery_zone: InitVar[str | None] = None
    delivery_landmark: InitVar[str | None] = None
    landmark_asked: InitVar[bool] = False

    reservation_time: InitVar[str | None] = None
    reservation_time_iso: InitVar[str | None] = None
    guests_count: InitVar[int | None] = None
    selected_branch: InitVar[str | None] = None
    reservation_notes: InitVar[str | None] = None
    reservation_confirmed: InitVar[bool] = False
    reservation_id: InitVar[str | None] = None
    reservation_submit_in_flight: InitVar[bool] = False

    complaint_text: InitVar[str | None] = None
    complaint_type: InitVar[str | None] = None
    complaint_logged: InitVar[bool] = False
    complaint_submit_in_flight: InitVar[bool] = False

    def __post_init__(
        self,
        customer_name: str | None,
        customer_phone: str | None,
        pending_phone_digits: str,
        phone_capture_mode: bool,
        phone_capture_turns: int,
        phone_capture_failures: int,
        order: list[str] | None,
        order_validated: bool,
        order_total: float,
        special_requests: str | None,
        upsell_offered: bool,
        upsell_accepted: bool,
        pending_upsell_item: str | None,
        pending_upsell_price: float | None,
        order_confirmed: bool,
        order_id: str | None,
        order_submit_in_flight: bool,
        delivery_address: str | None,
        delivery_zone: str | None,
        delivery_landmark: str | None,
        landmark_asked: bool,
        reservation_time: str | None,
        reservation_time_iso: str | None,
        guests_count: int | None,
        selected_branch: str | None,
        reservation_notes: str | None,
        reservation_confirmed: bool,
        reservation_id: str | None,
        reservation_submit_in_flight: bool,
        complaint_text: str | None,
        complaint_type: str | None,
        complaint_logged: bool,
        complaint_submit_in_flight: bool,
    ) -> None:
        self.customer = CustomerInfo(
            name=customer_name,
            phone=customer_phone,
            pending_phone_digits=pending_phone_digits,
            phone_capture_mode=phone_capture_mode,
            phone_capture_turns=phone_capture_turns,
            phone_capture_failures=phone_capture_failures,
        )
        self.order_state = OrderState(
            items=order,
            validated=order_validated,
            total=order_total,
            special_request=special_requests,
            upsell_offered=upsell_offered,
            upsell_accepted=upsell_accepted,
            pending_upsell_item=pending_upsell_item,
            pending_upsell_price=pending_upsell_price,
            confirmed=order_confirmed,
            order_id=order_id,
            submit_in_flight=order_submit_in_flight,
        )
        self.delivery_state = DeliveryState(
            address=delivery_address,
            zone=delivery_zone,
            landmark=delivery_landmark,
            landmark_asked=landmark_asked,
        )
        self.reservation_state = ReservationState(
            time=reservation_time,
            time_iso=reservation_time_iso,
            guests=guests_count,
            branch=selected_branch,
            notes=reservation_notes,
            confirmed=reservation_confirmed,
            reservation_id=reservation_id,
            submit_in_flight=reservation_submit_in_flight,
        )
        self.complaint_state = ComplaintState(
            text=complaint_text,
            category=complaint_type,
            logged=complaint_logged,
            submit_in_flight=complaint_submit_in_flight,
        )

    def summarize(self) -> str:
        return _json.dumps(
            {
                "name": self.customer_name or "—",
                "phone": self.customer_phone or "—",
                "order": self.order or "—",
                "special_requests": self.special_requests or "—",
                "pending_upsell": self.pending_upsell_item or "—",
                "upsell_accepted": self.upsell_accepted,
                "delivery_address": self.delivery_address or "—",
                "delivery_zone": self.delivery_zone or "—",
                "reservation_time": self.reservation_time or "—",
                "guests_count": self.guests_count or "—",
                "branch": self.selected_branch or "—",
                "confirmed_facts": {
                    "name": self.confirmed_facts.name_confirmed,
                    "phone": self.confirmed_facts.phone_confirmed,
                    "address": self.confirmed_facts.address_confirmed,
                    "landmark": self.confirmed_facts.landmark_confirmed,
                    "reservation_time": self.confirmed_facts.reservation_time_confirmed,
                    "guests_count": self.confirmed_facts.guests_count_confirmed,
                },
            },
            ensure_ascii=False,
        )


_FLAT_FIELD_MAP: dict[str, tuple[str, str]] = {
    "customer_name": ("customer", "name"),
    "customer_phone": ("customer", "phone"),
    "pending_phone_digits": ("customer", "pending_phone_digits"),
    "phone_capture_mode": ("customer", "phone_capture_mode"),
    "phone_capture_turns": ("customer", "phone_capture_turns"),
    "phone_capture_failures": ("customer", "phone_capture_failures"),
    "order": ("order_state", "items"),
    "order_validated": ("order_state", "validated"),
    "order_total": ("order_state", "total"),
    "special_requests": ("order_state", "special_request"),
    "upsell_offered": ("order_state", "upsell_offered"),
    "upsell_accepted": ("order_state", "upsell_accepted"),
    "pending_upsell_item": ("order_state", "pending_upsell_item"),
    "pending_upsell_price": ("order_state", "pending_upsell_price"),
    "order_confirmed": ("order_state", "confirmed"),
    "order_id": ("order_state", "order_id"),
    "order_submit_in_flight": ("order_state", "submit_in_flight"),
    "delivery_address": ("delivery_state", "address"),
    "delivery_zone": ("delivery_state", "zone"),
    "delivery_landmark": ("delivery_state", "landmark"),
    "landmark_asked": ("delivery_state", "landmark_asked"),
    "reservation_time": ("reservation_state", "time"),
    "reservation_time_iso": ("reservation_state", "time_iso"),
    "guests_count": ("reservation_state", "guests"),
    "selected_branch": ("reservation_state", "branch"),
    "reservation_notes": ("reservation_state", "notes"),
    "reservation_confirmed": ("reservation_state", "confirmed"),
    "reservation_id": ("reservation_state", "reservation_id"),
    "reservation_submit_in_flight": ("reservation_state", "submit_in_flight"),
    "complaint_text": ("complaint_state", "text"),
    "complaint_type": ("complaint_state", "category"),
    "complaint_logged": ("complaint_state", "logged"),
    "complaint_submit_in_flight": ("complaint_state", "submit_in_flight"),
}


def _make_proxy_property(container_name: str, attr_name: str) -> property:
    def getter(self: UserData) -> Any:
        return getattr(getattr(self, container_name), attr_name)

    def setter(self: UserData, value: Any) -> None:
        setattr(getattr(self, container_name), attr_name, value)

    return property(getter, setter)


for _public_name, (_container_name, _attr_name) in _FLAT_FIELD_MAP.items():
    setattr(UserData, _public_name, _make_proxy_property(_container_name, _attr_name))


__all__ = [
    "CallWriteHealth",
    "ComplaintState",
    "ConfirmedFacts",
    "CustomerInfo",
    "DeliveryState",
    "OrderState",
    "ReservationState",
    "UserData",
]
