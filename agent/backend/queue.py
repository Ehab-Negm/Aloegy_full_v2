"""Background queue processing and circuit breaker logic for backend writes."""

import asyncio
import contextlib
import json as _json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config_env import (
    BACKEND_WRITE_QUEUE_PATH,
    BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES,
    BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD,
    BACKEND_WRITE_CIRCUIT_OPEN_SECONDS,
    BACKEND_WRITE_QUEUE_ENABLED,
    BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS,
)
from core.telemetry import logger, emit_event as _emit_event
from backend.client import exc_log_fields as _exc_log_fields, should_retry_backend_error as _should_retry_backend_error

# Use inline imports to avoid circular dependencies with agent.py
# which defines worker_context, _post, _idempotency_key, _runtime_file_path, etc.


def _backend_queue_path() -> Path:
    from agent import _runtime_file_path
    return _runtime_file_path(BACKEND_WRITE_QUEUE_PATH)


def _normalize_backend_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    from agent import _idempotency_key
    normalized = dict(item)
    idempotency_key = str(normalized.get("idempotency_key", "")).strip()
    if not idempotency_key:
        call_id = str(normalized.get("call_id", "")).strip()
        action = str(normalized.get("idempotency_action", "")).strip()
        payload = normalized.get("payload", {})
        if call_id and action and isinstance(payload, dict):
            idempotency_key = _idempotency_key(call_id, action, payload)
    if idempotency_key:
        normalized["idempotency_key"] = idempotency_key
    return normalized


def _dedupe_backend_queue_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    skipped = 0
    for item in items:
        normalized = _normalize_backend_queue_item(item)
        key = str(normalized.get("idempotency_key", "")).strip()
        if key:
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)
        deduped.append(normalized)
    return deduped, skipped


def _parse_backend_queue_recovery_lines(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    for line in lines:
        try:
            parsed = _json.loads(line)
        except Exception:
            invalid += 1
            continue
        if isinstance(parsed, dict):
            items.append(_normalize_backend_queue_item(parsed))
        else:
            invalid += 1
    return items, invalid


def _cap_backend_queue_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES)
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _backend_endpoint_class(endpoint: str) -> str:
    return endpoint.strip().lower() or "unknown"


def _get_backend_circuit(endpoint: str) -> Any:
    from agent import worker_context, BackendCircuitState
    ctx = worker_context()
    key = _backend_endpoint_class(endpoint)
    state = ctx.backend_circuits.get(key)
    if state is None:
        state = BackendCircuitState()
        ctx.backend_circuits[key] = state
    return state


async def backend_circuit_is_open(endpoint: str) -> bool:
    from agent import worker_context
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        return state.open_until_monotonic > time.monotonic()


async def record_backend_circuit_success(endpoint: str) -> None:
    from agent import worker_context, _schedule_worker_health_snapshot
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        was_open = state.open_until_monotonic > time.monotonic()
        had_failures = state.consecutive_failures > 0
        state.consecutive_failures = 0
        state.open_until_monotonic = 0.0
        state.last_error = ""
    if was_open or had_failures:
        _emit_event("backend.circuit", endpoint=endpoint, state="closed")
        _schedule_worker_health_snapshot("circuit_closed")


async def record_backend_circuit_failure(endpoint: str, exc: Exception) -> None:
    from agent import worker_context, _schedule_worker_health_snapshot
    if not _should_retry_backend_error(exc):
        return
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        state.consecutive_failures += 1
        state.last_error = _exc_log_fields(exc)
        if state.consecutive_failures >= BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD:
            state.open_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS
            logger.warning(
                "backend circuit opened | endpoint=%s | failures=%d | open_for=%.2fs | %s",
                endpoint, state.consecutive_failures, BACKEND_WRITE_CIRCUIT_OPEN_SECONDS, state.last_error,
            )
            _emit_event(
                "backend.circuit",
                endpoint=endpoint,
                state="open",
                failures=state.consecutive_failures,
                open_for_s=BACKEND_WRITE_CIRCUIT_OPEN_SECONDS,
                error=state.last_error,
            )
            _schedule_worker_health_snapshot("circuit_open")


def mark_backend_circuit_open(health: Any | None) -> None:
    from agent import _schedule_worker_health_snapshot
    if health is None:
        return
    health.write_available = False
    health.last_write_error = "type=CircuitOpen"
    health.last_write_failure_kind = "CircuitOpen"
    health.last_write_status_code = None
    health.write_blocked_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS
    _schedule_worker_health_snapshot("circuit_blocked")


async def enqueue_backend_write(
    endpoint: str,
    payload: dict,
    call_id: str,
    *,
    idempotency_action: str,
    idempotency_key: str = "",
) -> bool:
    from agent import _backend_queue_instance, _schedule_worker_health_snapshot
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return False

    item = {
        "endpoint": endpoint,
        "payload": payload,
        "call_id": call_id,
        "idempotency_action": idempotency_action,
        "idempotency_key": idempotency_key,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    queue = _backend_queue_instance()
    try:
        queue.put_nowait(item)
        logger.warning("call=%s | backend write queued | endpoint=%s", call_id, endpoint)
        _emit_event(
            "backend.queue",
            call_id=call_id,
            endpoint=endpoint,
            target="memory",
            size=queue.qsize(),
        )
        _schedule_worker_health_snapshot("queue_memory")
        return True
    except asyncio.QueueFull:
        logger.error("backend in-memory queue full | endpoint=%s | size=%d", endpoint, queue.qsize())
        return await _append_backend_queue_recovery_items([item], call_id=call_id, endpoint=endpoint)


async def _read_backend_queue_recovery_lines() -> list[str]:
    from agent import _backend_queue_lock_instance
    queue_path = _backend_queue_path()
    if not await asyncio.to_thread(queue_path.exists):
        return []
    async with _backend_queue_lock_instance():
        if not await asyncio.to_thread(queue_path.exists):
            return []
        raw = await asyncio.to_thread(queue_path.read_text, encoding="utf-8")
    return [line for line in raw.splitlines() if line.strip()]


async def _rewrite_backend_queue_recovery_lines(lines: list[str]) -> None:
    from agent import _backend_queue_lock_instance, _ensure_parent_dir
    queue_path = _backend_queue_path()
    async with _backend_queue_lock_instance():
        if not lines:
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(queue_path.unlink)
            return
        await _ensure_parent_dir(queue_path)
        tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
        payload = "\n".join(lines) + "\n"
        await asyncio.to_thread(tmp_path.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(os.replace, tmp_path, queue_path)


async def _append_backend_queue_recovery_items(
    items: list[dict[str, Any]],
    *,
    call_id: str,
    endpoint: str,
) -> bool:
    from agent import _backend_queue_lock_instance, _ensure_parent_dir, _schedule_worker_health_snapshot
    if not items:
        return True
    queue_path = _backend_queue_path()
    async with _backend_queue_lock_instance():
        existing_items: list[dict[str, Any]] = []
        invalid_existing = 0
        if await asyncio.to_thread(queue_path.exists):
            raw = await asyncio.to_thread(queue_path.read_text, encoding="utf-8")
            existing_items, invalid_existing = _parse_backend_queue_recovery_lines(
                [line for line in raw.splitlines() if line.strip()]
            )
        if invalid_existing:
            logger.warning(
                "backend recovery queue dropped invalid lines during append | path=%s | count=%d",
                queue_path,
                invalid_existing,
            )
        await _ensure_parent_dir(queue_path)
        combined_items, skipped_duplicates = _dedupe_backend_queue_items(existing_items + items)
        capped_items, dropped_due_cap = _cap_backend_queue_items(combined_items)
        if skipped_duplicates:
            logger.warning(
                "backend recovery queue skipped duplicates | path=%s | count=%d",
                queue_path,
                skipped_duplicates,
            )
        if dropped_due_cap:
            logger.error(
                "backend recovery queue cap reached | path=%s | cap=%d | dropped=%d",
                queue_path,
                BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES,
                dropped_due_cap,
            )
        new_lines = [_json.dumps(item, ensure_ascii=False) for item in capped_items]
        tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
        payload = "\n".join(new_lines) + "\n"
        await asyncio.to_thread(tmp_path.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(os.replace, tmp_path, queue_path)
    logger.warning("call=%s | backend write queued to recovery file | endpoint=%s", call_id, endpoint)
    _emit_event(
        "backend.queue",
        call_id=call_id,
        endpoint=endpoint,
        target="recovery_file",
        queued_items=len(capped_items),
        skipped_duplicates=skipped_duplicates,
        dropped=dropped_due_cap,
    )
    _schedule_worker_health_snapshot("queue_recovery")
    return dropped_due_cap == 0


async def _submit_queued_backend_write(item: dict[str, Any]) -> bool:
    from agent import _post
    endpoint = str(item.get("endpoint", "")).strip()
    if not endpoint or await backend_circuit_is_open(endpoint):
        return False
    result = await _post(
        endpoint,
        item.get("payload", {}),
        str(item.get("call_id", "queued-write")),
        idempotency_action=str(item.get("idempotency_action", "")),
        max_retries=1,
        write_health=None,
        enqueue_on_retryable_failure=False,
    )
    return result is not None


async def drain_backend_write_queue_once() -> None:
    from agent import _backend_queue_instance, _schedule_worker_health_snapshot
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    queue = _backend_queue_instance()
    in_memory_items: list[dict[str, Any]] = []
    while True:
        try:
            in_memory_items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    recovery_lines = await _read_backend_queue_recovery_lines()
    recovery_items, invalid_recovery_lines = _parse_backend_queue_recovery_lines(recovery_lines)
    if invalid_recovery_lines:
        logger.warning(
            "backend queue dropped invalid recovery lines | path=%s | count=%d",
            _backend_queue_path(),
            invalid_recovery_lines,
        )

    remaining_items: list[dict[str, Any]] = []
    pending_items, skipped_duplicates = _dedupe_backend_queue_items(recovery_items + in_memory_items)
    if skipped_duplicates:
        logger.warning(
            "backend queue replay skipped duplicates | count=%d",
            skipped_duplicates,
        )
    for item in pending_items:
        try:
            success = await _submit_queued_backend_write(item)
        except Exception:
            logger.exception("backend queue worker submit error")
            success = False
        if not success:
            remaining_items.append(item)

    for _ in in_memory_items:
        with contextlib.suppress(ValueError):
            queue.task_done()

    deduped_remaining_items, skipped_remaining_duplicates = _dedupe_backend_queue_items(remaining_items)
    if skipped_remaining_duplicates:
        logger.warning(
            "backend queue remaining-items dedupe skipped duplicates | count=%d",
            skipped_remaining_duplicates,
        )
    capped_remaining_items, dropped_remaining_due_cap = _cap_backend_queue_items(deduped_remaining_items)
    if dropped_remaining_due_cap:
        logger.error(
            "backend recovery queue cap reached during rewrite | path=%s | cap=%d | dropped=%d",
            _backend_queue_path(),
            BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES,
            dropped_remaining_due_cap,
        )
    recovery_remaining_lines = [_json.dumps(item, ensure_ascii=False) for item in capped_remaining_items]
    await _rewrite_backend_queue_recovery_lines(recovery_remaining_lines)
    _schedule_worker_health_snapshot("queue_drain")


async def backend_queue_worker_loop() -> None:
    from agent import _backend_queue_instance
    _BATCH_SIZE = 10
    _MAX_BACKOFF = 60.0
    backoff = BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS
    while True:
        try:
            queue = _backend_queue_instance()
            # Wait for at least one item
            first_item = await queue.get()
            batch: list[dict[str, Any]] = [first_item]
            # Drain up to _BATCH_SIZE more items that are already queued
            for _ in range(_BATCH_SIZE - 1):
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            failed_items: list[dict[str, Any]] = []
            for item in batch:
                try:
                    success = await _submit_queued_backend_write(item)
                except Exception:
                    logger.exception("backend queue worker submit error")
                    success = False
                if not success:
                    failed_items.append(item)
                with contextlib.suppress(ValueError):
                    queue.task_done()

            if failed_items:
                await _append_backend_queue_recovery_items(
                    failed_items,
                    call_id="batch-retry",
                    endpoint="batch",
                )

            # Also drain recovery file items
            await drain_backend_write_queue_once()
            # Reset backoff on successful cycle
            backoff = BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS
        except asyncio.CancelledError:
            # Graceful shutdown — flush remaining queue to recovery file
            leftovers: list[dict[str, Any]] = []
            queue = _backend_queue_instance()
            while True:
                try:
                    leftovers.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if leftovers:
                with contextlib.suppress(Exception):
                    await _append_backend_queue_recovery_items(
                        leftovers,
                        call_id="worker-shutdown",
                        endpoint="batch",
                    )
            logger.info("backend queue worker shutting down | flushed=%d items", len(leftovers))
            raise
        except Exception:
            logger.exception("backend queue worker error | backoff=%.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)


async def ensure_backend_queue_worker_started() -> None:
    from agent import worker_context, _schedule_worker_health_snapshot
    ctx = worker_context()
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    if ctx.backend_queue_worker is not None and not ctx.backend_queue_worker.done():
        return
    ctx.backend_queue_worker = asyncio.create_task(
        backend_queue_worker_loop(),
        name="backend_write_queue_worker",
    )
    logger.info("backend queue worker started | path=%s", _backend_queue_path())
    _schedule_worker_health_snapshot("queue_worker_started")
    await drain_backend_write_queue_once()


__all__ = [
    "backend_circuit_is_open",
    "record_backend_circuit_success",
    "record_backend_circuit_failure",
    "mark_backend_circuit_open",
    "enqueue_backend_write",
    "drain_backend_write_queue_once",
    "backend_queue_worker_loop",
    "ensure_backend_queue_worker_started",
]
