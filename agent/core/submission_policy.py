"""Submission policy + idempotency tracking for the dialogue engine.

The agent must not submit the same order twice. ``backend.client`` already
sends an ``Idempotency-Key`` header per request, but a second-line
defence in the engine catches the case where the customer says "أكد"
twice in a row before the backend response has come back.

This module tracks, per call:

- which submissions have been attempted, with their idempotency key,
- which submissions have been accepted by the backend (terminal),
- the last failure reason for each flow (so we can offer a recoverable
  retry without restarting slot capture).

The tracker lives on ``UserData`` via a side dict keyed by call id; we
do not add new fields to the dataclass to keep the wire format stable.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Literal


SubmissionStatus = Literal[
    "not_attempted",
    "in_flight",
    "accepted",
    "failed",
    "blocked_duplicate",
]


@dataclass
class SubmissionRecord:
    flow: str
    idempotency_key: str
    status: SubmissionStatus = "not_attempted"
    started_at_ms: float = 0.0
    ended_at_ms: float = 0.0
    backend_id: str = ""
    error: str = ""


@dataclass
class SubmissionTracker:
    by_key: dict[str, SubmissionRecord] = field(default_factory=dict)
    by_flow: dict[str, str] = field(default_factory=dict)  # flow → latest key

    def latest_for_flow(self, flow: str) -> SubmissionRecord | None:
        key = self.by_flow.get(flow)
        if not key:
            return None
        return self.by_key.get(key)

    def is_duplicate(self, flow: str, key: str) -> bool:
        record = self.by_key.get(key)
        if record is None:
            return False
        return record.status in {"in_flight", "accepted"}

    def begin(self, flow: str, key: str) -> SubmissionRecord:
        record = self.by_key.get(key) or SubmissionRecord(flow=flow, idempotency_key=key)
        record.status = "in_flight"
        record.started_at_ms = time.time() * 1000.0
        record.error = ""
        self.by_key[key] = record
        self.by_flow[flow] = key
        return record

    def succeed(self, key: str, backend_id: str = "") -> SubmissionRecord | None:
        record = self.by_key.get(key)
        if record is None:
            return None
        record.status = "accepted"
        record.ended_at_ms = time.time() * 1000.0
        record.backend_id = backend_id
        return record

    def fail(self, key: str, error: str) -> SubmissionRecord | None:
        record = self.by_key.get(key)
        if record is None:
            return None
        record.status = "failed"
        record.ended_at_ms = time.time() * 1000.0
        record.error = error
        return record


def compute_idempotency_key(call_id: str, flow: str, payload: dict) -> str:
    """Stable per-call, per-flow idempotency key.

    The hash is over the *contents* of the payload — same items + same
    name + same phone yields the same key, so a duplicate confirm that
    happens to compose the same payload collapses to one record.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{call_id}-{flow}-{digest}"


@dataclass(frozen=True)
class SubmitDecision:
    allow: bool
    reason: str
    record: SubmissionRecord | None = None


def evaluate_submission(
    tracker: SubmissionTracker,
    *,
    flow: str,
    call_id: str,
    payload: dict,
) -> SubmitDecision:
    """Decide whether the engine may issue a backend write right now."""
    key = compute_idempotency_key(call_id or "no_call", flow, payload)
    existing = tracker.by_key.get(key)
    if existing is not None and existing.status == "accepted":
        return SubmitDecision(allow=False, reason="already_accepted", record=existing)
    if existing is not None and existing.status == "in_flight":
        return SubmitDecision(allow=False, reason="in_flight", record=existing)
    return SubmitDecision(allow=True, reason="ok", record=existing)


def get_or_create_tracker_for_call(worker_context: object | None, call_id: str | None) -> SubmissionTracker:
    """Return the per-call ``SubmissionTracker`` from a ``WorkerContext``.

    Trackers are keyed by ``call_id`` so concurrent calls on the same
    worker stay isolated. If no worker context is available (text test
    paths) we return a fresh, in-memory tracker — the caller still gets
    duplicate-detection within a single submit attempt.
    """
    key = call_id or "no_call"
    if worker_context is None:
        return SubmissionTracker()
    bucket = getattr(worker_context, "submission_trackers", None)
    if bucket is None:
        return SubmissionTracker()
    tracker = bucket.get(key)
    if tracker is None:
        tracker = SubmissionTracker()
        bucket[key] = tracker
    return tracker


def release_tracker_for_call(worker_context: object | None, call_id: str | None) -> None:
    """Drop a finished call's tracker so memory does not grow unbounded."""
    if worker_context is None or not call_id:
        return
    bucket = getattr(worker_context, "submission_trackers", None)
    if bucket is None:
        return
    bucket.pop(call_id, None)


__all__ = [
    "SubmissionRecord",
    "SubmissionStatus",
    "SubmissionTracker",
    "SubmitDecision",
    "compute_idempotency_key",
    "evaluate_submission",
    "get_or_create_tracker_for_call",
    "release_tracker_for_call",
]
