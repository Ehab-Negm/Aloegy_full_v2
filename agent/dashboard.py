"""Developer dashboard for the AloEgy voice agent.

A small FastAPI app that mounts on a separate port (default 8083) and
exposes:

  GET  /                 — the SPA shell (single HTML file)
  GET  /static/{file}    — js / css assets
  GET  /api/health       — same payload as the parent health server
  GET  /api/config       — current LLM / STT / TTS / pipeline settings
  GET  /api/events       — paginated history of telemetry events
  GET  /api/events/stream — Server-Sent Events stream of new events
  GET  /api/calls        — recent calls (aggregated from event buffer)
  GET  /api/calls/{id}   — full timeline for one call
  GET  /api/metrics      — turn-latency aggregates (p50 / p95 / count)

The dashboard runs in a daemon thread so the agent's main loop is never
blocked. All data comes from the in-memory event buffer in
``core.telemetry`` — no log files are tailed and no extra disk I/O.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.telemetry import (
    get_recent_events,
    subscribe_events,
    unsubscribe_events,
)
import dashboard_storage as _storage


logger = logging.getLogger("restaurant.agent")

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8083"))

_DASHBOARD_DIR = Path(__file__).parent / "dashboard_ui"
_INDEX_HTML = _DASHBOARD_DIR / "index.html"


def create_app() -> FastAPI:
    app = FastAPI(title="AloEgy Dev Dashboard", docs_url=None, redoc_url=None)

    # ── Static / index ────────────────────────────────────────────────
    if _DASHBOARD_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(_DASHBOARD_DIR)),
            name="static",
        )

    @app.get("/")
    async def index():  # type: ignore[no-redef]
        if _INDEX_HTML.is_file():
            return FileResponse(str(_INDEX_HTML))
        return JSONResponse({"error": "dashboard UI not installed"}, status_code=500)

    # ── Health passthrough ────────────────────────────────────────────
    @app.get("/api/health")
    async def health():  # type: ignore[no-redef]
        try:
            import agent as _agent
            ud_active = sum(1 for _ in _agent.worker_context().active_calls or {})
        except Exception:
            ud_active = 0
        return {
            "ok": True,
            "ts": _now_iso(),
            "active_calls": ud_active,
            "pid": os.getpid(),
        }

    # ── Config snapshot ───────────────────────────────────────────────
    @app.get("/api/config")
    async def config():  # type: ignore[no-redef]
        try:
            import agent as _agent
        except Exception as exc:
            return JSONResponse({"error": f"agent module not loaded: {exc}"}, status_code=503)

        tts = getattr(_agent, "SESSION_TTS", None)
        tts_class = type(tts).__name__ if tts is not None else None
        tts_streaming = bool(getattr(getattr(tts, "capabilities", None), "streaming", False)) if tts is not None else False

        return {
            "stt": {
                "provider": getattr(_agent, "SESSION_STT_PROVIDER", "?"),
                "model": getattr(_agent, "SESSION_STT_MODEL", "?"),
                "language": getattr(_agent, "SESSION_STT_LANGUAGE", "?"),
            },
            "llm": {
                "model": getattr(_agent, "SESSION_LLM_MODEL", "?"),
                "no_think": bool(getattr(_agent, "SESSION_LLM_NO_THINK", False)),
                "temperature": getattr(_agent, "SESSION_LLM_TEMPERATURE", None),
                "max_completion_tokens": getattr(_agent, "SESSION_LLM_MAX_COMPLETION_TOKENS", None),
                "preemptive_generation": bool(getattr(_agent, "SESSION_PREEMPTIVE_GENERATION", False)),
            },
            "tts": {
                "model": getattr(_agent, "SESSION_TTS_MODEL", "?"),
                "voice": getattr(_agent, "SESSION_TTS_VOICE", "?"),
                "language": getattr(_agent, "SESSION_TTS_LANGUAGE", "?"),
                "class": tts_class,
                "native_streaming": tts_streaming,
            },
            "realtime": {
                "enabled": bool(getattr(_agent, "SESSION_REALTIME_ENABLED", False)),
                "model": getattr(_agent, "SESSION_REALTIME_MODEL", "?"),
            },
            "pipeline": {
                "max_chat_ctx_items": getattr(_agent, "TURN_CHAT_CTX_MAX_ITEMS", None),
                "prompt_history_items": getattr(_agent, "PROMPT_HISTORY_ITEMS", None),
            },
        }

    # ── Events: history + SSE stream ──────────────────────────────────
    @app.get("/api/events")
    async def events_history(  # type: ignore[no-redef]
        limit: int = Query(200, ge=1, le=5000),
        since_seq: int | None = None,
        call_id: str | None = None,
        event: str | None = None,
        regex: str | None = Query(None, description="case-insensitive regex over the JSON payload"),
    ):
        # When a regex (or wider time window) is requested we hit SQLite
        # so the buffer's 5000-item cap doesn't blind the search. For
        # ordinary live queries we stay on the in-memory buffer.
        if regex:
            items = _storage.query_events(
                limit=limit,
                call_id=call_id,
                event=event,
                regex=regex,
            )
            return {"events": items, "count": len(items), "source": "sqlite"}

        items = get_recent_events(limit=limit, since_seq=since_seq)
        if call_id:
            items = [e for e in items if e.get("call_id") == call_id]
        if event:
            items = [e for e in items if e.get("event") == event]
        return {"events": items, "count": len(items), "source": "buffer"}

    @app.get("/api/events/stream")
    async def events_stream():  # type: ignore[no-redef]
        async def event_source():
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
            subscribe_events(queue)
            try:
                # Send the last 50 events on connect so the page has context.
                for ev in get_recent_events(limit=50):
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        # Heartbeat keeps proxies / browsers from closing the
                        # connection during quiet periods.
                        yield ": ping\n\n"
            finally:
                unsubscribe_events(queue)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Calls (aggregated) ────────────────────────────────────────────
    @app.get("/api/calls")
    async def calls_list(limit: int = Query(50, ge=1, le=500)):  # type: ignore[no-redef]
        events = get_recent_events(limit=2000)
        calls: dict[str, dict[str, Any]] = {}
        for ev in events:
            cid = ev.get("call_id")
            if not cid:
                continue
            entry = calls.setdefault(cid, {
                "call_id": cid,
                "started_at": None,
                "ended_at": None,
                "duration_s": None,
                "flow": None,
                "turns": 0,
                "errors": 0,
                "fast_path_count": 0,
                "llm_fallback_count": 0,
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
        ordered = sorted(
            calls.values(),
            key=lambda c: c.get("started_at") or "",
            reverse=True,
        )[:limit]
        return {"calls": ordered, "count": len(ordered)}

    @app.get("/api/calls/{call_id}")
    async def call_detail(call_id: str):  # type: ignore[no-redef]
        # Prefer SQLite (full history); fall back to in-memory buffer.
        events = _storage.query_events(call_id=call_id, limit=5000)
        if not events:
            events = [e for e in get_recent_events(limit=2000) if e.get("call_id") == call_id]
        return {"call_id": call_id, "events": events, "count": len(events)}

    @app.get("/api/calls/{call_id}/export")
    async def call_export(call_id: str):  # type: ignore[no-redef]
        """Download a single call's full event timeline as a JSON file."""
        events = _storage.query_events(call_id=call_id, limit=10000)
        if not events:
            events = [e for e in get_recent_events(limit=2000) if e.get("call_id") == call_id]
        if not events:
            raise HTTPException(status_code=404, detail="call not found")
        bundle = {
            "call_id": call_id,
            "exported_at": _now_iso(),
            "event_count": len(events),
            "events": events,
        }
        body = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="call-{call_id}.json"',
                "Cache-Control": "no-cache",
            },
        )

    # ── Metrics ───────────────────────────────────────────────────────
    @app.get("/api/metrics")
    async def metrics_summary():  # type: ignore[no-redef]
        events = get_recent_events(limit=2000)
        bucket_keys = ("eou_delay_ms", "engine_decision", "tts_enqueue", "turn_handler_total", "llm_fallback")
        buckets: dict[str, list[float]] = defaultdict(list)
        fast_path = 0
        llm_fallback = 0
        errors = 0
        pipeline_actions: dict[str, int] = defaultdict(int)
        for ev in events:
            kind = ev.get("event", "")
            if kind == "turn.trace":
                latency = ev.get("latency_ms") or {}
                for k in bucket_keys:
                    v = latency.get(k)
                    if isinstance(v, (int, float)):
                        buckets[k].append(float(v))
                if ev.get("fast_path"):
                    fast_path += 1
                if ev.get("llm_fallback"):
                    llm_fallback += 1
            elif kind == "pipeline.decision":
                action = str(ev.get("action") or "?")
                pipeline_actions[action] += 1
            elif kind in ("session.error", "fallback.triggered"):
                errors += 1
        summary = {
            "totals": {
                "events": len(events),
                "fast_path_turns": fast_path,
                "llm_fallback_turns": llm_fallback,
                "errors": errors,
            },
            "pipeline_actions": dict(pipeline_actions),
            "latency": {
                k: {
                    "count": len(v),
                    "p50": _quantile(v, 0.5),
                    "p95": _quantile(v, 0.95),
                    "max": max(v) if v else None,
                }
                for k, v in buckets.items()
            },
        }
        return summary

    @app.get("/api/metrics/timeseries")
    async def metrics_timeseries(  # type: ignore[no-redef]
        bucket: str = Query("turn_handler_total"),
        minutes: int = Query(60, ge=1, le=1440),
    ):
        """Per-event latency points over the last ``minutes`` for charting."""
        points = _storage.latency_timeseries(bucket=bucket, minutes=minutes)
        return {"bucket": bucket, "minutes": minutes, "points": points, "count": len(points)}

    # ── Alerts (Phase 3.3) ────────────────────────────────────────────
    @app.get("/api/alerts")
    async def alerts():  # type: ignore[no-redef]
        """Rule-based alerts derived from the recent event window.

        Each rule fires when the underlying metric crosses a threshold
        in the last N minutes. ``severity`` is "ok" | "warn" | "critical".
        Ops can poll this every 30s or rely on the SSE stream to refresh
        the dashboard banner.
        """
        events = get_recent_events(limit=3000)
        now = time.time()
        window_seconds = float(os.getenv("DASHBOARD_ALERT_WINDOW_S", "300"))
        window_start = now - window_seconds

        latency_p95_threshold_ms = float(os.getenv("DASHBOARD_ALERT_TURN_P95_MS", "3000"))
        error_rate_threshold = float(os.getenv("DASHBOARD_ALERT_ERROR_RATE", "0.05"))
        queue_depth_threshold = int(os.getenv("DASHBOARD_ALERT_QUEUE_DEPTH", "50"))
        config_age_multiplier = float(os.getenv("DASHBOARD_ALERT_CONFIG_AGE_MULT", "2.0"))

        turn_latencies: list[float] = []
        turns_total = 0
        errors = 0
        circuit_open = 0
        queue_depth_max = 0
        latest_config_age: float | None = None
        latest_config_ttl: float | None = None
        provider_fallback_count = 0
        for ev in events:
            ts_iso = ev.get("ts") or ""
            try:
                # ts is ISO with timezone; parse to epoch
                from datetime import datetime as _dt
                ev_epoch = _dt.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
            except Exception:
                ev_epoch = now
            if ev_epoch < window_start:
                continue
            kind = ev.get("event", "")
            if kind == "turn.trace":
                turns_total += 1
                lat = (ev.get("latency_ms") or {}).get("turn_handler_total")
                if isinstance(lat, (int, float)):
                    turn_latencies.append(float(lat))
            elif kind in ("session.error", "fallback.triggered"):
                errors += 1
            elif kind == "backend.circuit" and ev.get("state") == "open":
                circuit_open += 1
            elif kind == "backend.write_queue":
                depth = ev.get("queue_depth") or ev.get("depth") or 0
                if isinstance(depth, (int, float)):
                    queue_depth_max = max(queue_depth_max, int(depth))
            elif kind == "config.snapshot":
                age = ev.get("cache_age_s")
                ttl = ev.get("cache_ttl_s")
                if isinstance(age, (int, float)):
                    latest_config_age = float(age)
                if isinstance(ttl, (int, float)):
                    latest_config_ttl = float(ttl)
            elif kind == "provider.fallback":
                provider_fallback_count += 1

        rules: list[dict[str, Any]] = []

        p95 = _quantile(turn_latencies, 0.95) if turn_latencies else None
        if p95 is not None:
            severity = "critical" if p95 > latency_p95_threshold_ms else (
                "warn" if p95 > latency_p95_threshold_ms * 0.8 else "ok"
            )
            rules.append({
                "id": "turn_latency_p95",
                "name": "Turn latency p95",
                "value_ms": round(p95, 1),
                "threshold_ms": latency_p95_threshold_ms,
                "severity": severity,
                "samples": len(turn_latencies),
            })

        if turns_total > 0:
            error_rate = errors / max(turns_total, 1)
            severity = "critical" if error_rate > error_rate_threshold else (
                "warn" if error_rate > error_rate_threshold * 0.5 else "ok"
            )
            rules.append({
                "id": "error_rate",
                "name": "Session/Fallback error rate",
                "value": round(error_rate, 4),
                "threshold": error_rate_threshold,
                "severity": severity,
                "errors": errors,
                "turns": turns_total,
            })

        if queue_depth_max > 0:
            severity = "critical" if queue_depth_max > queue_depth_threshold else (
                "warn" if queue_depth_max > queue_depth_threshold * 0.5 else "ok"
            )
            rules.append({
                "id": "backend_queue_depth",
                "name": "Backend write-queue depth",
                "value": queue_depth_max,
                "threshold": queue_depth_threshold,
                "severity": severity,
            })

        if latest_config_age is not None and latest_config_ttl is not None and latest_config_ttl > 0:
            stale_threshold = latest_config_ttl * config_age_multiplier
            severity = "warn" if latest_config_age > stale_threshold else "ok"
            rules.append({
                "id": "config_stale",
                "name": "Restaurant config age",
                "value_s": round(latest_config_age, 1),
                "threshold_s": round(stale_threshold, 1),
                "severity": severity,
            })

        if circuit_open > 0:
            rules.append({
                "id": "backend_circuit_open",
                "name": "Backend circuit breaker opens",
                "value": circuit_open,
                "severity": "warn",
            })

        if provider_fallback_count > 0:
            rules.append({
                "id": "provider_fallback",
                "name": "Provider fallback fires (Phase 3.2)",
                "value": provider_fallback_count,
                "severity": "warn",
            })

        worst = "ok"
        for r in rules:
            if r["severity"] == "critical":
                worst = "critical"
                break
            if r["severity"] == "warn":
                worst = "warn"
        return {
            "window_seconds": window_seconds,
            "overall": worst,
            "rules": rules,
            "ts": _now_iso(),
        }

    # ── Degraded mode toggle (Phase 3.4) ──────────────────────────────
    @app.get("/api/degraded")
    async def degraded_state():  # type: ignore[no-redef]
        try:
            from core.ops_metrics import METRICS
            return {
                "enabled": METRICS.is_degraded(),
                "reason": METRICS.degraded_reason(),
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/degraded")
    async def degraded_set(  # type: ignore[no-redef]
        enabled: bool = Query(...),
        reason: str = Query("manual"),
    ):
        try:
            from core.ops_metrics import METRICS
            METRICS.set_degraded(enabled=enabled, reason=reason)
            return {
                "enabled": METRICS.is_degraded(),
                "reason": METRICS.degraded_reason(),
            }
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    # ── Test-call dispatch ────────────────────────────────────────────
    @app.post("/api/test_call")
    @app.get("/api/test_call")
    async def test_call(
        room: str | None = Query(None, description="optional room name; auto-generated if omitted"),
        identity: str = Query("dashboard-tester"),
    ):
        """Mint a LiveKit access token + return a join URL for browser testing.

        Click the URL → browser opens LiveKit Meet → joins the room → the
        agent worker auto-dispatches itself into that room. Lets ops run a
        full end-to-end test from the dashboard without going through the
        production frontend.
        """
        from livekit import api as lk_api

        api_key = os.getenv("LIVEKIT_API_KEY", "").strip()
        api_secret = os.getenv("LIVEKIT_API_SECRET", "").strip()
        ws_url = os.getenv("LIVEKIT_URL", "").strip()
        if not (api_key and api_secret and ws_url):
            raise HTTPException(
                status_code=500,
                detail="LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be set",
            )

        room_name = room or f"dashboard-test-{int(time.time())}"

        try:
            token = (
                lk_api.AccessToken(api_key, api_secret)
                .with_identity(identity)
                .with_name(identity)
                .with_grants(
                    lk_api.VideoGrants(
                        room_join=True,
                        room=room_name,
                        room_create=True,
                        can_publish=True,
                        can_subscribe=True,
                    )
                )
                .to_jwt()
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"token mint failed: {exc}")

        # LiveKit Meet open URL — opens in a new tab and joins the room.
        from urllib.parse import quote
        meet_url = (
            "https://meet.livekit.io/custom"
            f"?liveKitUrl={quote(ws_url, safe='')}"
            f"&token={quote(token, safe='')}"
        )
        return {
            "room": room_name,
            "ws_url": ws_url,
            "token": token,
            "meet_url": meet_url,
        }

    return app


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    try:
        return float(statistics.quantiles(values, n=100)[int(q * 100) - 1])
    except (statistics.StatisticsError, IndexError, ValueError):
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────
# Server lifecycle
# ──────────────────────────────────────────────────────────────────────

_SERVER_THREAD: threading.Thread | None = None
_SERVER: uvicorn.Server | None = None


def start_dashboard_server(*, host: str | None = None, port: int | None = None) -> None:
    """Start the dashboard server in a daemon thread.

    Idempotent — calling twice is a no-op. Failures (port already in use,
    missing dependencies, …) are logged but never raised so the agent's
    main loop is unaffected. Also kicks off the SQLite event-storage
    writer + hydrates the in-memory ring buffer with recent disk history
    so dashboards survive agent restarts.
    """
    global _SERVER_THREAD, _SERVER
    if _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
        return

    # Kick off persistence FIRST so any events emitted during dashboard
    # startup land in SQLite, then hydrate the in-memory ring buffer
    # from prior runs.
    try:
        _storage.start_storage()
        from core.telemetry import hydrate_event_buffer_from_storage
        hydrate_event_buffer_from_storage()
    except Exception as exc:
        logger.warning("dashboard storage init failed | %s", exc)

    bind_host = host or DASHBOARD_HOST
    bind_port = port or DASHBOARD_PORT

    config = uvicorn.Config(
        app=create_app(),
        host=bind_host,
        port=bind_port,
        log_level=os.getenv("DASHBOARD_LOG_LEVEL", "warning"),
        access_log=False,
    )
    _SERVER = uvicorn.Server(config)

    def _run() -> None:
        try:
            asyncio.run(_SERVER.serve())  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("dashboard server stopped | %s", exc)

    _SERVER_THREAD = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    _SERVER_THREAD.start()
    logger.info("dev dashboard | listening on http://%s:%d", bind_host, bind_port)


def stop_dashboard_server() -> None:
    global _SERVER, _SERVER_THREAD
    if _SERVER is not None:
        _SERVER.should_exit = True
    _SERVER = None
    _SERVER_THREAD = None


__all__ = ["start_dashboard_server", "stop_dashboard_server", "create_app"]
