"""SQLite-backed persistence for the dev dashboard.

Telemetry events are appended to a local SQLite database in a background
thread so the agent's hot path is never blocked by disk I/O. On startup
the most recent events are hydrated back into the in-memory ring buffer
so the dashboard has continuity across restarts.

Schema:

    events(
        seq        INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT    NOT NULL,            -- ISO 8601 UTC
        ts_epoch   REAL    NOT NULL,            -- ts as seconds since epoch
        event      TEXT    NOT NULL,            -- e.g. "turn.trace"
        call_id    TEXT,                        -- nullable for non-call events
        flow       TEXT,
        payload    TEXT    NOT NULL             -- full JSON of the event
    )

Indexes on ``ts_epoch``, ``call_id``, and ``event`` keep the dashboard
queries fast even with hundreds of thousands of rows.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


logger = logging.getLogger("restaurant.agent")


_DB_PATH = Path(
    os.getenv(
        "DASHBOARD_DB_PATH",
        str(Path(__file__).parent / ".runtime" / "dashboard.sqlite3"),
    )
)
_HYDRATE_LIMIT = int(os.getenv("DASHBOARD_HYDRATE_LIMIT", "500"))
_RETENTION_DAYS = int(os.getenv("DASHBOARD_RETENTION_DAYS", "7"))
_FLUSH_INTERVAL_SECONDS = float(os.getenv("DASHBOARD_FLUSH_INTERVAL_S", "1.0"))

_WRITE_QUEUE: queue.Queue[dict[str, Any]] | None = None
_WRITER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


# ──────────────────────────────────────────────────────────────────────
# Connection / schema
# ──────────────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            seq        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            ts_epoch   REAL NOT NULL,
            event      TEXT NOT NULL,
            call_id    TEXT,
            flow       TEXT,
            payload    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_ts_epoch ON events(ts_epoch);
        CREATE INDEX IF NOT EXISTS idx_events_call_id  ON events(call_id);
        CREATE INDEX IF NOT EXISTS idx_events_event    ON events(event);
        """
    )


# ──────────────────────────────────────────────────────────────────────
# Writer thread
# ──────────────────────────────────────────────────────────────────────


def _writer_loop() -> None:
    assert _WRITE_QUEUE is not None
    conn = _connect()
    _ensure_schema(conn)
    last_prune = time.monotonic()

    while not _STOP_EVENT.is_set():
        batch: list[dict[str, Any]] = []
        try:
            first = _WRITE_QUEUE.get(timeout=_FLUSH_INTERVAL_SECONDS)
            batch.append(first)
        except queue.Empty:
            pass
        # Drain anything else queued without blocking.
        while True:
            try:
                batch.append(_WRITE_QUEUE.get_nowait())
            except queue.Empty:
                break

        if batch:
            try:
                conn.executemany(
                    "INSERT INTO events (ts, ts_epoch, event, call_id, flow, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    list(_to_row(ev) for ev in batch),
                )
            except Exception as exc:
                logger.warning("dashboard storage write failed | n=%d | %s", len(batch), exc)

        # Periodic prune (cheap; once every ~5 min)
        if time.monotonic() - last_prune > 300:
            _prune_old(conn)
            last_prune = time.monotonic()

    try:
        conn.close()
    except Exception:
        pass


def _to_row(ev: dict[str, Any]) -> tuple:
    ts = str(ev.get("ts") or _now_iso())
    try:
        ts_epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        ts_epoch = time.time()
    return (
        ts,
        ts_epoch,
        str(ev.get("event") or ""),
        ev.get("call_id") or None,
        ev.get("flow") or None,
        json.dumps(ev, ensure_ascii=False),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prune_old(conn: sqlite3.Connection) -> None:
    if _RETENTION_DAYS <= 0:
        return
    cutoff = time.time() - _RETENTION_DAYS * 86400
    try:
        cur = conn.execute("DELETE FROM events WHERE ts_epoch < ?", (cutoff,))
        deleted = cur.rowcount or 0
        if deleted > 0:
            logger.info("dashboard storage pruned %d old events (>%dd)", deleted, _RETENTION_DAYS)
    except Exception as exc:
        logger.warning("dashboard storage prune failed | %s", exc)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def start_storage() -> None:
    """Start the SQLite writer thread (idempotent)."""
    global _WRITE_QUEUE, _WRITER_THREAD
    if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
        return
    _WRITE_QUEUE = queue.Queue(maxsize=10000)
    _STOP_EVENT.clear()
    _WRITER_THREAD = threading.Thread(target=_writer_loop, name="dashboard-storage", daemon=True)
    _WRITER_THREAD.start()
    logger.info("dashboard storage | path=%s | retention=%dd", _DB_PATH, _RETENTION_DAYS)


def stop_storage() -> None:
    _STOP_EVENT.set()


def append_event(event: dict[str, Any]) -> None:
    """Enqueue an event for async write. Drops on overflow rather than block."""
    if _WRITE_QUEUE is None:
        return
    try:
        _WRITE_QUEUE.put_nowait(event)
    except queue.Full:
        # Worst case: lose a few events if the writer can't keep up.
        # Far better than blocking the agent's hot path.
        pass


def hydrate_recent(limit: int | None = None) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` events from disk for buffer hydration."""
    n = limit or _HYDRATE_LIMIT
    if not _DB_PATH.is_file():
        return []
    try:
        conn = _connect()
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT payload FROM events ORDER BY seq DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("dashboard storage hydrate failed | %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for (payload,) in reversed(rows):  # oldest first so seq order makes sense
        try:
            out.append(json.loads(payload))
        except Exception:
            continue
    return out


# ──────────────────────────────────────────────────────────────────────
# Read queries (used by dashboard endpoints)
# ──────────────────────────────────────────────────────────────────────


def query_events(
    *,
    limit: int = 200,
    call_id: str | None = None,
    event: str | None = None,
    regex: str | None = None,
    since_ts_epoch: float | None = None,
    until_ts_epoch: float | None = None,
) -> list[dict[str, Any]]:
    if not _DB_PATH.is_file():
        return []
    sql = ["SELECT payload FROM events WHERE 1=1"]
    params: list[Any] = []
    if call_id:
        sql.append("AND call_id = ?")
        params.append(call_id)
    if event:
        sql.append("AND event = ?")
        params.append(event)
    if since_ts_epoch is not None:
        sql.append("AND ts_epoch >= ?")
        params.append(since_ts_epoch)
    if until_ts_epoch is not None:
        sql.append("AND ts_epoch <= ?")
        params.append(until_ts_epoch)
    sql.append("ORDER BY seq DESC LIMIT ?")
    # When regex is present we over-fetch and filter in Python (SQLite's LIKE
    # is too crude and we don't ship a regex extension).
    if regex:
        params.append(min(limit * 8, 5000))
    else:
        params.append(limit)
    try:
        conn = _connect()
        _ensure_schema(conn)
        rows = conn.execute(" ".join(sql), params).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("dashboard storage query failed | %s", exc)
        return []

    out: list[dict[str, Any]] = []
    pat = None
    if regex:
        try:
            pat = re.compile(regex, re.IGNORECASE)
        except re.error:
            pat = None  # invalid regex → silently fall back to no-filter
    for (payload,) in rows:
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if pat is not None and not pat.search(payload):
            continue
        out.append(obj)
        if len(out) >= limit:
            break
    return out


def list_calls(limit: int = 50) -> list[dict[str, Any]]:
    """Aggregate recent calls from disk (cheaper than scanning whole table)."""
    if not _DB_PATH.is_file():
        return []
    try:
        conn = _connect()
        _ensure_schema(conn)
        # Get distinct call_ids ordered by their most-recent event.
        call_ids = [
            row[0] for row in conn.execute(
                "SELECT call_id FROM events WHERE call_id IS NOT NULL "
                "GROUP BY call_id ORDER BY MAX(seq) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        if not call_ids:
            conn.close()
            return []
        placeholders = ",".join("?" for _ in call_ids)
        rows = conn.execute(
            f"SELECT payload FROM events WHERE call_id IN ({placeholders}) "
            f"ORDER BY seq ASC",
            call_ids,
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("dashboard storage list_calls failed | %s", exc)
        return []

    aggregated: dict[str, dict[str, Any]] = {}
    for (payload,) in rows:
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        cid = ev.get("call_id")
        if not cid:
            continue
        entry = aggregated.setdefault(cid, {
            "call_id": cid, "started_at": None, "ended_at": None,
            "duration_s": None, "flow": None, "turns": 0,
            "errors": 0, "fast_path_count": 0, "llm_fallback_count": 0,
            "close_reason": None,
        })
        kind = ev.get("event", "")
        if kind == "call.start":
            entry["started_at"] = ev.get("ts")
        elif kind == "call.end":
            entry["ended_at"] = ev.get("ts")
            entry["duration_s"] = ev.get("duration_s")
            entry["close_reason"] = ev.get("close_reason")
        elif kind == "turn.received":
            entry["turns"] = max(entry["turns"], int(ev.get("turn", 0) or 0))
            if ev.get("flow"):
                entry["flow"] = ev["flow"]
        elif kind == "turn.trace":
            if ev.get("fast_path"):
                entry["fast_path_count"] += 1
            if ev.get("llm_fallback"):
                entry["llm_fallback_count"] += 1
        elif kind in ("session.error", "fallback.triggered"):
            entry["errors"] += 1

    return sorted(
        aggregated.values(),
        key=lambda c: c.get("started_at") or "",
        reverse=True,
    )[:limit]


def latency_timeseries(
    *,
    bucket: str = "turn_handler_total",
    minutes: int = 60,
) -> list[dict[str, Any]]:
    """Per-event latency points for a given bucket within the last ``minutes``."""
    if not _DB_PATH.is_file():
        return []
    cutoff = time.time() - max(1, minutes) * 60
    try:
        conn = _connect()
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT ts_epoch, payload FROM events "
            "WHERE event='turn.trace' AND ts_epoch >= ? ORDER BY seq ASC",
            (cutoff,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("dashboard storage timeseries failed | %s", exc)
        return []
    points: list[dict[str, Any]] = []
    for ts_epoch, payload in rows:
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        v = (ev.get("latency_ms") or {}).get(bucket)
        if isinstance(v, (int, float)):
            points.append({
                "ts": float(ts_epoch),
                "value": float(v),
                "call_id": ev.get("call_id"),
                "fast_path": bool(ev.get("fast_path")),
            })
    return points


__all__ = [
    "start_storage",
    "stop_storage",
    "append_event",
    "hydrate_recent",
    "query_events",
    "list_calls",
    "latency_timeseries",
]
