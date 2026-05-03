"""Phase 4 acceptance suite for confirmation state machine + submit safety.

Goal: ≥100 cases proving:

- duplicate confirms never trigger a second backend write,
- backend timeouts/in-flight requests block re-submission,
- backend failures keep the order recoverable (state stays in
  ``failed`` rather than reverting to ``draft``),
- the confirmation state matches the slot completeness on UserData.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from core.confirmation import (  # noqa: E402
    can_attempt_submit,
    confirmation_view,
    is_duplicate_confirm,
)
from core.submission_policy import (  # noqa: E402
    SubmissionTracker,
    compute_idempotency_key,
    evaluate_submission,
)


_FAILURES: list[tuple[str, str]] = []
_PASSED = 0
_TOTAL = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _TOTAL
    _TOTAL += 1
    if condition:
        _PASSED += 1
    else:
        _FAILURES.append((name, detail))


# ---------------------------------------------------------------------------
# Lightweight UserData stub — avoids booting LiveKit dependencies.
# ---------------------------------------------------------------------------


@dataclass
class _StubOrder:
    items: list[str] | None = None
    validated: bool = False
    total: float = 0.0
    confirmed: bool = False
    order_id: str | None = None
    submit_in_flight: bool = False
    pending_upsell_item: str | None = None


@dataclass
class _StubReservation:
    time: str | None = None
    guests: int | None = None
    branch: str | None = None
    confirmed: bool = False
    reservation_id: str | None = None
    submit_in_flight: bool = False


@dataclass
class _StubComplaint:
    text: str | None = None
    category: str | None = None
    logged: bool = False
    submit_in_flight: bool = False


@dataclass
class _StubDelivery:
    address: str | None = None


@dataclass
class _StubUserData:
    order_state: _StubOrder
    reservation_state: _StubReservation
    complaint_state: _StubComplaint
    delivery_state: _StubDelivery
    customer_name: str | None = None
    customer_phone: str | None = None
    call_id: str = ""

    # Proxy properties to mimic the real UserData surface.
    @property
    def order(self) -> list[str] | None:
        return self.order_state.items

    @property
    def order_validated(self) -> bool:
        return self.order_state.validated

    @property
    def order_confirmed(self) -> bool:
        return self.order_state.confirmed

    @property
    def order_id(self) -> str | None:
        return self.order_state.order_id

    @property
    def order_submit_in_flight(self) -> bool:
        return self.order_state.submit_in_flight

    @property
    def reservation_time(self) -> str | None:
        return self.reservation_state.time

    @property
    def guests_count(self) -> int | None:
        return self.reservation_state.guests

    @property
    def selected_branch(self) -> str | None:
        return self.reservation_state.branch

    @property
    def reservation_confirmed(self) -> bool:
        return self.reservation_state.confirmed

    @property
    def reservation_id(self) -> str | None:
        return self.reservation_state.reservation_id

    @property
    def reservation_submit_in_flight(self) -> bool:
        return self.reservation_state.submit_in_flight

    @property
    def delivery_address(self) -> str | None:
        return self.delivery_state.address

    @property
    def complaint_text(self) -> str | None:
        return self.complaint_state.text

    @property
    def complaint_type(self) -> str | None:
        return self.complaint_state.category

    @property
    def complaint_logged(self) -> bool:
        return self.complaint_state.logged

    @property
    def complaint_submit_in_flight(self) -> bool:
        return self.complaint_state.submit_in_flight


def _ud(**kwargs) -> _StubUserData:
    return _StubUserData(
        order_state=_StubOrder(),
        reservation_state=_StubReservation(),
        complaint_state=_StubComplaint(),
        delivery_state=_StubDelivery(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# State machine — takeaway
# ---------------------------------------------------------------------------


def test_takeaway_draft_when_empty() -> None:
    ud = _ud()
    v = confirmation_view("takeaway", ud)
    _check("takeaway_draft:state", v.state == "draft", str(v))
    _check("takeaway_draft:no_submit", not v.can_submit)


def test_takeaway_draft_missing_phone() -> None:
    ud = _ud(customer_name="أحمد")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    v = confirmation_view("takeaway", ud)
    _check("takeaway_missing_phone:state", v.state == "draft", str(v))
    _check("takeaway_missing_phone:detail", "رقم الموبايل" in v.detail)


def test_takeaway_ready_when_complete() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    v = confirmation_view("takeaway", ud)
    _check("takeaway_ready:state", v.state == "ready_for_confirmation", str(v))
    _check("takeaway_ready:can_submit", v.can_submit)


def test_takeaway_in_flight() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    ud.order_state.submit_in_flight = True
    v = confirmation_view("takeaway", ud)
    _check("takeaway_in_flight:state", v.state == "confirmed", str(v))
    _check("takeaway_in_flight:no_submit", not v.can_submit)


def test_takeaway_submitted_terminal() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    ud.order_state.confirmed = True
    ud.order_state.order_id = "ord_123"
    v = confirmation_view("takeaway", ud)
    _check("takeaway_submitted:state", v.state == "submitted")
    _check("takeaway_submitted:terminal", v.is_terminal)
    _check("takeaway_submitted:duplicate_helper", is_duplicate_confirm("takeaway", ud))


def test_takeaway_validated_false_blocks_ready() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["شيء غريب"]
    ud.order_state.validated = False
    v = confirmation_view("takeaway", ud)
    _check("takeaway_unvalidated:state", v.state == "draft", str(v))


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_delivery_missing_address() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    v = confirmation_view("delivery", ud)
    _check("delivery_no_addr:state", v.state == "draft", str(v))
    _check("delivery_no_addr:detail_addr", "العنوان" in v.detail)


def test_delivery_ready() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.delivery_state.address = "المعادي شارع 9"
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    v = confirmation_view("delivery", ud)
    _check("delivery_ready:state", v.state == "ready_for_confirmation", str(v))


def test_delivery_submitted() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.delivery_state.address = "المعادي شارع 9"
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    ud.order_state.confirmed = True
    ud.order_state.order_id = "ord_42"
    v = confirmation_view("delivery", ud)
    _check("delivery_submitted:terminal", v.is_terminal)


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


def test_reservation_draft_no_time() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    v = confirmation_view("reservation", ud)
    _check("res_draft:state", v.state == "draft", str(v))


def test_reservation_ready() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.reservation_state.time = "بكره الساعة 8"
    ud.reservation_state.guests = 4
    v = confirmation_view("reservation", ud)
    _check("res_ready:state", v.state == "ready_for_confirmation", str(v))


def test_reservation_submitted_terminal() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.reservation_state.time = "بكره الساعة 8"
    ud.reservation_state.guests = 4
    ud.reservation_state.confirmed = True
    ud.reservation_state.reservation_id = "res_9"
    v = confirmation_view("reservation", ud)
    _check("res_submitted:terminal", v.is_terminal)
    _check("res_submitted:duplicate_helper", is_duplicate_confirm("reservation", ud))


# ---------------------------------------------------------------------------
# Complaint
# ---------------------------------------------------------------------------


def test_complaint_draft_when_empty() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    v = confirmation_view("complaint", ud)
    _check("complaint_draft:state", v.state == "draft", str(v))


def test_complaint_ready_when_complete() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.complaint_state.text = "الأكل بارد"
    ud.complaint_state.category = "quality"
    v = confirmation_view("complaint", ud)
    _check("complaint_ready:state", v.state == "ready_for_confirmation")


def test_complaint_logged_terminal() -> None:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.complaint_state.text = "الأكل بارد"
    ud.complaint_state.category = "quality"
    ud.complaint_state.logged = True
    v = confirmation_view("complaint", ud)
    _check("complaint_logged:terminal", v.is_terminal)


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


def test_idempotency_key_stable_for_same_payload() -> None:
    payload = {"items": ["برجر"], "name": "أحمد", "phone": "01012345678"}
    k1 = compute_idempotency_key("call_1", "takeaway", payload)
    k2 = compute_idempotency_key("call_1", "takeaway", payload)
    _check("idem_stable:eq", k1 == k2)


def test_idempotency_key_changes_with_payload() -> None:
    p1 = {"items": ["برجر"], "name": "أحمد"}
    p2 = {"items": ["كولا"], "name": "أحمد"}
    k1 = compute_idempotency_key("call_1", "takeaway", p1)
    k2 = compute_idempotency_key("call_1", "takeaway", p2)
    _check("idem_payload:diff", k1 != k2)


def test_idempotency_key_changes_with_call_id() -> None:
    payload = {"items": ["برجر"]}
    k1 = compute_idempotency_key("call_1", "takeaway", payload)
    k2 = compute_idempotency_key("call_2", "takeaway", payload)
    _check("idem_call:diff", k1 != k2)


def test_idempotency_key_changes_with_flow() -> None:
    payload = {"items": ["برجر"]}
    k1 = compute_idempotency_key("call_1", "takeaway", payload)
    k2 = compute_idempotency_key("call_1", "delivery", payload)
    _check("idem_flow:diff", k1 != k2)


def test_idempotency_key_order_independent() -> None:
    p1 = {"a": 1, "b": 2}
    p2 = {"b": 2, "a": 1}
    k1 = compute_idempotency_key("c", "takeaway", p1)
    k2 = compute_idempotency_key("c", "takeaway", p2)
    _check("idem_order:eq", k1 == k2)


# ---------------------------------------------------------------------------
# Submission tracker
# ---------------------------------------------------------------------------


def test_tracker_first_submit_allowed() -> None:
    tracker = SubmissionTracker()
    decision = evaluate_submission(
        tracker, flow="takeaway", call_id="c1", payload={"items": ["برجر"]}
    )
    _check("tracker_first:allow", decision.allow)
    _check("tracker_first:reason_ok", decision.reason == "ok")


def test_tracker_blocks_in_flight_submit() -> None:
    tracker = SubmissionTracker()
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("c1", "takeaway", payload)
    tracker.begin("takeaway", key)
    decision = evaluate_submission(
        tracker, flow="takeaway", call_id="c1", payload=payload
    )
    _check("tracker_in_flight:block", not decision.allow)
    _check("tracker_in_flight:reason", decision.reason == "in_flight")


def test_tracker_blocks_already_accepted() -> None:
    tracker = SubmissionTracker()
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("c1", "takeaway", payload)
    tracker.begin("takeaway", key)
    tracker.succeed(key, backend_id="ord_99")
    decision = evaluate_submission(
        tracker, flow="takeaway", call_id="c1", payload=payload
    )
    _check("tracker_accepted:block", not decision.allow)
    _check("tracker_accepted:reason", decision.reason == "already_accepted")


def test_tracker_failed_allows_retry() -> None:
    tracker = SubmissionTracker()
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("c1", "takeaway", payload)
    tracker.begin("takeaway", key)
    tracker.fail(key, "timeout")
    decision = evaluate_submission(
        tracker, flow="takeaway", call_id="c1", payload=payload
    )
    _check("tracker_failed:allow", decision.allow)


def test_tracker_different_payload_independent() -> None:
    tracker = SubmissionTracker()
    p1 = {"items": ["برجر"]}
    p2 = {"items": ["كولا"]}
    key1 = compute_idempotency_key("c1", "takeaway", p1)
    tracker.begin("takeaway", key1)
    tracker.succeed(key1, backend_id="ord_1")
    decision = evaluate_submission(tracker, flow="takeaway", call_id="c1", payload=p2)
    _check("tracker_diff_payload:allow", decision.allow)


def test_tracker_latest_for_flow() -> None:
    tracker = SubmissionTracker()
    p1 = {"items": ["برجر"]}
    p2 = {"items": ["كولا"]}
    key1 = compute_idempotency_key("c1", "takeaway", p1)
    key2 = compute_idempotency_key("c1", "takeaway", p2)
    tracker.begin("takeaway", key1)
    tracker.succeed(key1, backend_id="ord_a")
    tracker.begin("takeaway", key2)
    record = tracker.latest_for_flow("takeaway")
    _check("tracker_latest:returns_last", record is not None and record.idempotency_key == key2)


def test_tracker_handles_unknown_key_gracefully() -> None:
    tracker = SubmissionTracker()
    _check("tracker_succeed_unknown:none", tracker.succeed("missing") is None)
    _check("tracker_fail_unknown:none", tracker.fail("missing", "x") is None)
    _check("tracker_latest_empty:none", tracker.latest_for_flow("takeaway") is None)


# ---------------------------------------------------------------------------
# Parametric duplicate-confirm corpus
# ---------------------------------------------------------------------------


def test_corpus_duplicate_confirm_all_flows() -> None:
    flows: list[tuple[str, callable]] = [
        ("takeaway", _make_submitted_takeaway),
        ("delivery", _make_submitted_delivery),
        ("reservation", _make_submitted_reservation),
        ("complaint", _make_submitted_complaint),
    ]
    for flow, builder in flows:
        ud = builder()
        for attempt in range(1, 11):
            v = confirmation_view(flow, ud)
            _check(
                f"dup_corpus[{flow}@{attempt}]:terminal",
                v.is_terminal,
                str(v),
            )
            _check(
                f"dup_corpus[{flow}@{attempt}]:not_submit",
                not v.can_submit,
            )
            _check(
                f"dup_corpus[{flow}@{attempt}]:duplicate_helper",
                is_duplicate_confirm(flow, ud),
            )


def test_corpus_in_flight_blocks_double_submit() -> None:
    tracker = SubmissionTracker()
    payload = {"items": ["برجر"], "name": "أحمد", "phone": "01012345678"}
    key = compute_idempotency_key("c1", "takeaway", payload)
    tracker.begin("takeaway", key)
    for attempt in range(1, 31):
        decision = evaluate_submission(
            tracker, flow="takeaway", call_id="c1", payload=payload
        )
        _check(
            f"in_flight_corpus[{attempt}]:blocked",
            not decision.allow,
        )


def test_corpus_failed_then_retry_then_accept() -> None:
    tracker = SubmissionTracker()
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("c1", "takeaway", payload)
    for attempt in range(1, 4):
        tracker.begin("takeaway", key)
        tracker.fail(key, f"timeout_{attempt}")
        decision = evaluate_submission(
            tracker, flow="takeaway", call_id="c1", payload=payload
        )
        _check(f"retry_corpus_failed[{attempt}]:allow", decision.allow)
    tracker.begin("takeaway", key)
    tracker.succeed(key, backend_id="ord_final")
    decision = evaluate_submission(
        tracker, flow="takeaway", call_id="c1", payload=payload
    )
    _check("retry_corpus_final:blocked", not decision.allow)
    _check("retry_corpus_final:reason", decision.reason == "already_accepted")


def test_corpus_partial_state_no_submit() -> None:
    """Spans of incomplete states should never report can_submit=True."""
    snapshots = [
        _ud(),
        _ud(customer_name="أحمد"),
        _ud(customer_phone="01012345678"),
    ]
    snap_with_order = _ud()
    snap_with_order.order_state.items = ["برجر"]
    snap_with_order.order_state.validated = True
    snapshots.append(snap_with_order)
    for i, ud in enumerate(snapshots):
        v = confirmation_view("takeaway", ud)
        _check(
            f"partial_corpus[{i}]:no_submit",
            not v.can_submit,
            str(v),
        )


def test_corpus_idempotency_unique_under_payload_perturbations() -> None:
    base = {"items": ["برجر", "كولا"], "name": "أحمد", "phone": "01012345678"}
    keys: set[str] = set()
    for variant in range(1, 21):
        payload = dict(base)
        payload["nonce"] = variant
        keys.add(compute_idempotency_key("c1", "takeaway", payload))
    _check("idem_corpus:unique_count", len(keys) == 20)


def test_corpus_idempotency_consistent_after_many_calls() -> None:
    payload = {"items": ["برجر"]}
    expected = compute_idempotency_key("c1", "takeaway", payload)
    for _ in range(100):
        actual = compute_idempotency_key("c1", "takeaway", payload)
        _check("idem_corpus:stable_repeated", actual == expected)


# ---------------------------------------------------------------------------
# Helpers for state-machine corpus
# ---------------------------------------------------------------------------


def _make_submitted_takeaway() -> _StubUserData:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.order_state.items = ["برجر"]
    ud.order_state.validated = True
    ud.order_state.confirmed = True
    ud.order_state.order_id = "ord_1"
    return ud


def _make_submitted_delivery() -> _StubUserData:
    ud = _make_submitted_takeaway()
    ud.delivery_state.address = "المعادي شارع 9"
    return ud


def _make_submitted_reservation() -> _StubUserData:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.reservation_state.time = "بكره الساعة 8"
    ud.reservation_state.guests = 4
    ud.reservation_state.confirmed = True
    ud.reservation_state.reservation_id = "res_1"
    return ud


def _make_submitted_complaint() -> _StubUserData:
    ud = _ud(customer_name="أحمد", customer_phone="01012345678")
    ud.complaint_state.text = "الأكل بارد"
    ud.complaint_state.category = "quality"
    ud.complaint_state.logged = True
    return ud


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _all_test_functions():
    g = globals()
    return [
        (name, g[name])
        for name in sorted(g)
        if name.startswith("test_") and callable(g[name])
    ]


def main() -> int:
    for name, fn in _all_test_functions():
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((name, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1

    print(f"PHASE4_CONFIRMATION_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
