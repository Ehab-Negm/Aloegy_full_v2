"""Structured telemetry, turn tracing, and logging."""
from __future__ import annotations

import json as _json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("restaurant.agent")

_telemetry_logger = logging.getLogger("restaurant.telemetry")
_TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() in {"1", "true", "yes"}
_TRACE_LOCK = threading.Lock()

# In-memory ring buffer for the dev dashboard (agent/dashboard.py).
# Holds the last N telemetry events so the UI can replay recent history
# on page load and stream live updates via SSE. Bounded so a long-running
# worker doesn't grow unbounded — at ~1KB per event, 5000 events ≈ 5MB.
_EVENT_BUFFER_LOCK = threading.Lock()
_EVENT_BUFFER_MAX = int(os.getenv("TELEMETRY_BUFFER_MAX", "5000"))
_EVENT_BUFFER: list[dict[str, Any]] = []
_EVENT_SUBSCRIBERS: list[Any] = []  # asyncio.Queue instances; hot-attached
_EVENT_SEQ = 0


def _publish_event_to_buffer(payload: dict[str, Any]) -> None:
    """Append an event to the in-memory buffer + fan out to live subscribers.

    Called on every ``emit_event`` so the dashboard can replay history and
    stream new events without touching log files. ``asyncio.Queue`` is
    used for subscribers so the SSE handler can ``await`` cleanly. Also
    enqueues to the optional SQLite-backed dashboard storage for
    cross-restart persistence.
    """
    global _EVENT_SEQ
    with _EVENT_BUFFER_LOCK:
        _EVENT_SEQ += 1
        payload = {**payload, "_seq": _EVENT_SEQ}
        _EVENT_BUFFER.append(payload)
        if len(_EVENT_BUFFER) > _EVENT_BUFFER_MAX:
            del _EVENT_BUFFER[: len(_EVENT_BUFFER) - _EVENT_BUFFER_MAX]
        subs = list(_EVENT_SUBSCRIBERS)
    # Drop subscribers that can't keep up rather than block the emitter.
    for queue in subs:
        try:
            queue.put_nowait(payload)
        except Exception:
            pass
    # Best-effort SQLite write (fire-and-forget through a background queue).
    try:
        import dashboard_storage as _storage  # local import to avoid cycles
        _storage.append_event(payload)
    except Exception:
        pass


def hydrate_event_buffer_from_storage() -> None:
    """Re-populate the in-memory ring buffer from the SQLite event log.

    Called once at dashboard startup so the dashboard has continuity
    across agent restarts. Safe to call multiple times — the seq counter
    resumes from the highest restored value so new events keep monotonic
    ordering.
    """
    global _EVENT_SEQ
    try:
        import dashboard_storage as _storage  # local import to avoid cycles
    except Exception:
        return
    rows = _storage.hydrate_recent()
    if not rows:
        return
    with _EVENT_BUFFER_LOCK:
        # Strip any pre-existing _seq from disk and assign fresh ones in
        # in-memory order (so SSE clients get strictly increasing values).
        for ev in rows:
            _EVENT_SEQ += 1
            entry = {k: v for k, v in ev.items() if k != "_seq"}
            entry["_seq"] = _EVENT_SEQ
            _EVENT_BUFFER.append(entry)
        if len(_EVENT_BUFFER) > _EVENT_BUFFER_MAX:
            del _EVENT_BUFFER[: len(_EVENT_BUFFER) - _EVENT_BUFFER_MAX]


def get_recent_events(*, limit: int = 200, since_seq: int | None = None) -> list[dict[str, Any]]:
    """Return the tail of the in-memory event buffer.

    ``since_seq`` filters to events newer than the given sequence
    number — useful for SSE clients that disconnect briefly and want
    to fast-forward without replaying the whole history.
    """
    with _EVENT_BUFFER_LOCK:
        if since_seq is None:
            return list(_EVENT_BUFFER[-limit:])
        return [e for e in _EVENT_BUFFER if e.get("_seq", 0) > since_seq][:limit]


def subscribe_events(queue: Any) -> None:
    """Register an ``asyncio.Queue`` to receive live telemetry events.

    Caller is responsible for ``unsubscribe_events`` when done.
    """
    with _EVENT_BUFFER_LOCK:
        _EVENT_SUBSCRIBERS.append(queue)


def unsubscribe_events(queue: Any) -> None:
    with _EVENT_BUFFER_LOCK:
        try:
            _EVENT_SUBSCRIBERS.remove(queue)
        except ValueError:
            pass


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_event(event: str, *, call_id: str = "", flow: str = "", **kwargs: Any) -> None:
    """Emit a structured telemetry event as JSON.

    Goes to two places:
    1. The ``restaurant.telemetry`` logger (file/stdout — the existing path)
    2. The in-memory ring buffer that the dev dashboard reads via SSE.
    """
    if not _TELEMETRY_ENABLED:
        return
    payload = {
        "event": event,
        "ts": utc_iso(),
        "call_id": call_id,
    }
    if flow:
        payload["flow"] = flow
    payload.update(kwargs)
    _telemetry_logger.info(_json.dumps(payload, ensure_ascii=False))
    _publish_event_to_buffer(payload)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def elapsed_ms(start_monotonic: float | None) -> int | None:
    if not start_monotonic:
        return None
    return max(0, int((time.monotonic() - start_monotonic) * 1000))


def call_trace_path() -> Path:
    raw = os.getenv("CALL_TRACE_PATH", "").strip()
    if raw:
        return Path(raw)
    app_env = os.getenv("APP_ENV", "prod").strip() or "prod"
    agent_dir = Path(__file__).resolve().parents[1]
    return agent_dir / ".runtime" / app_env / "call_traces.jsonl"


def call_trace_enabled() -> bool:
    return os.getenv("CALL_TRACE_ENABLED", "true").lower() in {"1", "true", "yes"}


def write_call_trace(record: dict[str, Any]) -> None:
    """Append a single structured QA trace record to local JSONL."""
    if not call_trace_enabled():
        return
    payload = {"ts": utc_iso(), **record}
    path = call_trace_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with _TRACE_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        logger.exception("failed to write call trace | path=%s", path)


def _phone_tail(value: str | None) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


def snapshot_slots(ud: Any) -> dict[str, Any]:
    """Return a compact, privacy-light snapshot of business slots."""
    order_items = [str(item) for item in (getattr(ud, "order", None) or [])]
    return {
        "customer_name_present": bool(getattr(ud, "customer_name", None)),
        "customer_phone_present": bool(getattr(ud, "customer_phone", None)),
        "customer_phone_tail": _phone_tail(getattr(ud, "customer_phone", None)),
        "order_items": order_items,
        "order_validated": bool(getattr(ud, "order_validated", False)),
        "order_total": float(getattr(ud, "order_total", 0.0) or 0.0),
        "order_confirmed": bool(getattr(ud, "order_confirmed", False)),
        "delivery_address_present": bool(getattr(ud, "delivery_address", None)),
        "delivery_zone": getattr(ud, "delivery_zone", None) or "",
        "reservation_time_present": bool(getattr(ud, "reservation_time", None)),
        "guests_count": getattr(ud, "guests_count", None),
        "selected_branch": getattr(ud, "selected_branch", None) or "",
        "reservation_confirmed": bool(getattr(ud, "reservation_confirmed", False)),
        "complaint_text_present": bool(getattr(ud, "complaint_text", None)),
        "complaint_type": getattr(ud, "complaint_type", None) or "",
        "complaint_logged": bool(getattr(ud, "complaint_logged", False)),
        "pending_upsell_item": getattr(ud, "pending_upsell_item", None) or "",
    }


def changed_slots(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [key for key in keys if before.get(key) != after.get(key)]


def turn_trace_record(
    *,
    call_id: str,
    flow: str,
    turn: int,
    status: str,
    transcript: str,
    decision_mode: str,
    decision_reason: str,
    slot_before: dict[str, Any],
    slot_after: dict[str, Any],
    agent_message: str,
    question_category: str,
    started_monotonic: float,
    engine_decision_ms: int | None = None,
    tts_enqueue_ms: int | None = None,
) -> dict[str, Any]:
    slot_changes = changed_slots(slot_before, slot_after)
    llm_fallback = decision_mode == "llm_fallback"
    return {
        "event": "turn.trace",
        "call_id": call_id or "",
        "flow": flow,
        "turn": turn,
        "status": status,
        "intent": flow,
        "fast_path": not llm_fallback,
        "llm_fallback": llm_fallback,
        "decision_mode": decision_mode,
        "decision_reason": decision_reason,
        "slot_changed": slot_changes,
        "slot_before": slot_before,
        "slot_after": slot_after,
        "question_category": question_category or ("response" if agent_message else ""),
        "question_asked": bool(agent_message),
        "agent_message": agent_message,
        "transcript_preview": " ".join((transcript or "").split())[:240],
        "latency_ms": {
            "stt_final_to_handler": None,
            "engine_decision": engine_decision_ms,
            "tts_enqueue": tts_enqueue_ms,
            "turn_handler_total": elapsed_ms(started_monotonic),
            "llm_fallback": None if not llm_fallback else elapsed_ms(started_monotonic),
        },
    }


__all__ = [
    "logger",
    "emit_event",
    "monotonic_ms",
    "elapsed_ms",
    "call_trace_path",
    "call_trace_enabled",
    "write_call_trace",
    "snapshot_slots",
    "changed_slots",
    "turn_trace_record",
]
