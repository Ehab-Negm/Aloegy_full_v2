"""Glue between the confirmation state machine and the live flow code.

Each ``confirm_*`` flow tool used to repeat the same boilerplate:

- check ``ud.<flow>_confirmed`` and return an "already submitted" line,
- check ``ud.<flow>_submit_in_flight`` and return a "thinking" line,
- check missing slots,
- check menu validation,
- check the backend circuit breaker,
- flip ``submit_in_flight`` around the awaited submit,
- emit ``order.submitted`` / ``reservation.submitted`` events on success.

This module hosts a small set of helpers that record the same decisions
to telemetry **and** route them through ``core.submission_policy`` so a
duplicate confirm or in-flight retry is caught at the engine layer (not
only at the backend's ``Idempotency-Key`` header).

The helpers do not replace the existing flow logic — they observe and
augment it — so behaviour for users does not change while we get the
production observability we need for Phase 7 review.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.confirmation import ConfirmationView, FlowKind, confirmation_view
from core.ops_metrics import METRICS
from core.submission_policy import (
    SubmissionRecord,
    SubmitDecision,
    compute_idempotency_key,
    evaluate_submission,
    get_or_create_tracker_for_call,
)
from core.telemetry import emit_event
from state.user_data import UserData


@dataclass(frozen=True)
class GateOutcome:
    """Result of pre-submit gating.

    ``allow`` is True when both the state machine and the per-call
    tracker agree the submit may proceed. ``reason`` is always set so
    QA traces can explain why a submit was blocked.
    """

    allow: bool
    reason: str
    view: ConfirmationView
    tracker_decision: SubmitDecision | None = None
    idempotency_key: str = ""


def gate_submit(flow: FlowKind, ud: UserData, payload: dict) -> GateOutcome:
    """Combine ``confirmation_view`` + ``SubmissionTracker`` into one decision.

    The flow code can either consult the result directly or fall back
    to its existing per-flow checks for the user-facing message. Either
    way the gate emits a ``submission.gate`` event for every attempt so
    the trace explains every decision.
    """
    view = confirmation_view(flow, ud)
    if view.is_terminal:
        emit_event(
            "submission.gate",
            call_id=ud.call_id or "",
            flow=flow,
            state=view.state,
            allow=False,
            reason="already_submitted",
        )
        return GateOutcome(
            allow=False,
            reason="already_submitted",
            view=view,
        )

    if not view.can_submit:
        emit_event(
            "submission.gate",
            call_id=ud.call_id or "",
            flow=flow,
            state=view.state,
            allow=False,
            reason=view.detail,
        )
        return GateOutcome(allow=False, reason=view.detail, view=view)

    tracker = get_or_create_tracker_for_call(ud.worker_context, ud.call_id)
    decision = evaluate_submission(
        tracker,
        flow=flow,
        call_id=ud.call_id or "no_call",
        payload=payload,
    )
    key = compute_idempotency_key(ud.call_id or "no_call", flow, payload)

    if not decision.allow:
        METRICS.record_submit_blocked(decision.reason)
        emit_event(
            "submission.gate",
            call_id=ud.call_id or "",
            flow=flow,
            state=view.state,
            allow=False,
            reason=decision.reason,
            idempotency_key=key,
        )
        return GateOutcome(
            allow=False,
            reason=decision.reason,
            view=view,
            tracker_decision=decision,
            idempotency_key=key,
        )

    emit_event(
        "submission.gate",
        call_id=ud.call_id or "",
        flow=flow,
        state=view.state,
        allow=True,
        reason="ok",
        idempotency_key=key,
    )
    return GateOutcome(
        allow=True,
        reason="ok",
        view=view,
        tracker_decision=decision,
        idempotency_key=key,
    )


def begin_submit(flow: FlowKind, ud: UserData, key: str) -> SubmissionRecord:
    """Mark a submit attempt as in-flight in the per-call tracker."""
    tracker = get_or_create_tracker_for_call(ud.worker_context, ud.call_id)
    METRICS.record_submit_attempt()
    return tracker.begin(flow, key)


def finish_submit(
    flow: FlowKind,
    ud: UserData,
    key: str,
    *,
    succeeded: bool,
    backend_id: str = "",
    error: str = "",
) -> SubmissionRecord | None:
    """Record the outcome of a submit attempt and emit telemetry."""
    tracker = get_or_create_tracker_for_call(ud.worker_context, ud.call_id)
    if succeeded:
        record = tracker.succeed(key, backend_id=backend_id)
        METRICS.record_submit_accepted()
        emit_event(
            "submission.outcome",
            call_id=ud.call_id or "",
            flow=flow,
            outcome="accepted",
            backend_id=backend_id,
            idempotency_key=key,
        )
        return record

    record = tracker.fail(key, error or "unknown")
    METRICS.record_submit_failed()
    emit_event(
        "submission.outcome",
        call_id=ud.call_id or "",
        flow=flow,
        outcome="failed",
        error=error,
        idempotency_key=key,
    )
    return record


__all__ = [
    "GateOutcome",
    "begin_submit",
    "finish_submit",
    "gate_submit",
]
