"""Entrypoint — LiveKit AgentServer + session setup."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing as mp
import os
import time
import uuid
import json as _json
from datetime import datetime, timezone
from typing import Any

from livekit.agents import AgentServer, JobContext, cli
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import AgentSession
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.plugins import noise_cancellation

from backend.config import RestaurantConfig
from health import HealthServerHandle, start_health_server
from state.user_data import UserData
from state.worker_context import WorkerContext

import agent as _agent

logger = logging.getLogger("restaurant.agent")


server = AgentServer(
    num_idle_processes=_agent.AGENT_IDLE_PROCESSES,
    setup_fnc=_agent._setup_worker_process,
)

MAX_CALL_DURATION = _agent._get_env_int("MAX_CALL_DURATION", 600, min_value=30)
AGENT_HEALTH_HOST = os.getenv("AGENT_HEALTH_HOST", "0.0.0.0")
AGENT_HEALTH_PORT = _agent._get_env_int("AGENT_HEALTH_PORT", 8082, min_value=0)
_HEALTH_SERVER_HANDLE: HealthServerHandle | None = None


def _build_health_report() -> tuple[int, dict[str, Any]]:
    return _agent.build_agent_health_report(
        server_connection_failed=bool(getattr(server, "_connection_failed", False)),
        active_jobs=len(getattr(server, "active_jobs", [])),
    )


def _start_parent_health_server() -> None:
    global _HEALTH_SERVER_HANDLE
    if _HEALTH_SERVER_HANDLE is not None:
        return
    if mp.parent_process() is not None:
        return
    _HEALTH_SERVER_HANDLE = start_health_server(
        host=AGENT_HEALTH_HOST,
        port=AGENT_HEALTH_PORT,
        report_builder=_build_health_report,
    )


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    call_id = str(uuid.uuid4())[:16]
    call_started_at_utc = datetime.now(timezone.utc)
    logger.info("call=%s | started | room=%s", call_id, ctx.room.name)
    _agent._emit_event("call.start", call_id=call_id, room=ctx.room.name)
    proc_context = ctx.proc.userdata.get("worker_context")
    if isinstance(proc_context, WorkerContext) and _agent._WORKER_CONTEXT is None:
        _agent._WORKER_CONTEXT = proc_context

    restaurant_id = ""
    try:
        meta = getattr(ctx.room, "metadata", None) or ""
        if meta:
            meta_dict = _json.loads(meta) if isinstance(meta, str) else meta
            restaurant_id = str(meta_dict.get("restaurant_id", ""))
    except Exception as _e:
        logger.warning("call=%s | could not parse room metadata: %s", call_id, _e)

    if not await _agent._acquire_session_slot(call_id):
        logger.warning("call=%s | rejected at capacity", call_id)
        return

    cfg = await _agent.fetch_config(call_id, restaurant_id=restaurant_id)
    userdata = UserData(call_id=call_id, restaurant=cfg)
    userdata.worker_context = _agent.worker_context()
    await _agent._ensure_backend_queue_worker_started()
    await _agent._ensure_config_refresh_started()
    session_stt = _agent._build_session_stt(cfg, client_reference_id=call_id)
    stt_context_terms = _agent._stt_context_terms_for_config(cfg)
    # Derive config_available from the actual cfg this call is using, not from
    # shared worker-level runtime_health. The latter is process-wide and can be
    # briefly flipped to False by a concurrent call that fell back to degraded,
    # producing the confusing "config_available=False | config_source=backend"
    # readout even when this call fetched a valid backend config.
    config_available_for_call = cfg.config_source != "degraded_fallback"
    logger.info(
        "call=%s | startup readiness | deps_ready=%s | config_available=%s | write_available=%s | config_source=%s | degraded=%s | stt_provider=%s | stt_context_terms=%d | preemptive=%s",
        call_id,
        _agent.session_dependencies_ready(),
        config_available_for_call,
        _agent.backend_write_available(userdata.write_health),
        cfg.config_source,
        cfg.degraded_mode,
        _agent.SESSION_STT_PROVIDER,
        len(stt_context_terms),
        _agent.SESSION_PREEMPTIVE_GENERATION,
    )

    from flows.greeter import Greeter
    from flows.takeaway import Takeaway
    from flows.delivery import Delivery
    from flows.reservation import Reservation
    from flows.complaint import Complaint

    agents = {
        "greeter":     Greeter(cfg),
        "takeaway":    Takeaway(cfg),
        "reservation": Reservation(cfg),
        "complaint":   Complaint(cfg),
    }
    if cfg.delivery_enabled or cfg.degraded_mode:
        agents["delivery"] = Delivery(cfg)

    userdata.agents = agents

    session = AgentSession[UserData](
        userdata       = userdata,
        stt            = session_stt,
        llm            = _agent.SESSION_LLM,
        tts            = _agent.SESSION_TTS,
        vad            = _agent.SESSION_VAD,
        # turn_detection omitted intentionally: MultilingualModel does not support Arabic
        # and emits "Turn detector does not support language ar" warnings. VAD + endpointing
        # delays provide stable end-of-turn detection for ar without the multilingual model.
        allow_interruptions=True,
        min_interruption_duration=_agent.MIN_INTERRUPTION_DURATION_SECONDS,
        min_endpointing_delay=_agent.MIN_ENDPOINTING_DELAY_SECONDS,
        max_endpointing_delay=_agent.MAX_ENDPOINTING_DELAY_SECONDS,
        false_interruption_timeout=_agent.FALSE_INTERRUPTION_TIMEOUT_SECONDS,
        user_away_timeout=_agent.USER_AWAY_TIMEOUT_SECONDS,
        preemptive_generation=_agent.SESSION_PREEMPTIVE_GENERATION,
        max_tool_steps = _agent.MAX_TOOL_STEPS,
    )

    @session.on("metrics_collected")
    def _on_metrics(event):
        m = event.metrics
        if isinstance(m, STTMetrics):
            logger.info(
                "call=%s | METRICS STT | duration=%.0fms | audio=%.1fs",
                call_id, m.duration * 1000, m.audio_duration,
            )
        elif isinstance(m, LLMMetrics):
            logger.info(
                "call=%s | METRICS LLM | ttft=%.0fms | total=%.0fms | prompt=%d | completion=%d | tok/s=%.0f",
                call_id, m.ttft * 1000, m.duration * 1000,
                m.prompt_tokens, m.completion_tokens, m.tokens_per_second,
            )
        elif isinstance(m, TTSMetrics):
            logger.info(
                "call=%s | METRICS TTS | ttfb=%.0fms | total=%.0fms | audio=%.1fs | chars=%d",
                call_id, m.ttfb * 1000, m.duration * 1000,
                m.audio_duration, m.characters_count,
            )
        elif isinstance(m, EOUMetrics):
            logger.info(
                "call=%s | METRICS EOU | eou_delay=%.0fms | transcription=%.0fms",
                call_id, m.end_of_utterance_delay * 1000,
                m.transcription_delay * 1000,
            )

    t_start = time.monotonic()
    close_event = asyncio.Event()
    close_state = {"closed": False}
    close_reason = "normal_close"
    last_user_activity_at = t_start
    agent_state = "initializing"
    inactivity_prompt_count = 0
    last_reprompt_at = 0.0
    watchdog_task: asyncio.Task | None = None

    @session.on("close")
    def _on_close(event):
        nonlocal close_reason
        close_reason = f"session_{event.reason}"
        close_event.set()

    @session.on("agent_state_changed")
    def _on_agent_state(event):
        nonlocal agent_state, last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        agent_state = event.new_state
        if event.old_state == "speaking" and event.new_state == "listening":
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
        logger.info(
            "call=%s | agent_state=%s→%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal last_user_activity_at
        if event.new_state == "speaking":
            last_user_activity_at = time.monotonic()
        logger.info(
            "call=%s | user_state=%s→%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_input_transcribed")
    def _on_transcribed(event):
        nonlocal last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        transcript = (event.transcript or "").strip()
        if transcript:
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
            if event.is_final:
                userdata.last_user_message = transcript[:280]
        logger.info(
            "call=%s | transcript final=%s | text=%s",
            call_id, event.is_final, transcript,
        )

    @session.on("agent_false_interruption")
    def _on_false_interruption(event):
        logger.info(
            "call=%s | false_interruption | resumed=%s",
            call_id, event.resumed,
        )

    @session.on("function_tools_executed")
    def _on_tools(event):
        tool_names = [getattr(call, "name", "") for call in event.function_calls]
        logger.info("call=%s | tools=%s", call_id, tool_names)
        for name in tool_names:
            _agent._emit_event("tool.called", call_id=call_id, tool=name)

    @session.on("error")
    def _on_error(event):
        from backend.client import exc_log_fields as _exc_log_fields
        logger.error("call=%s | session error | %s", call_id, _exc_log_fields(event.error))

    async def _watch_inactivity() -> None:
        nonlocal inactivity_prompt_count, last_reprompt_at, close_reason
        while not close_event.is_set():
            await asyncio.sleep(1.0)
            if close_event.is_set():
                return
            if agent_state in {"speaking", "thinking", "initializing"}:
                continue
            if userdata.session_transitional_state:
                continue
            if (
                userdata.order_submit_in_flight
                or userdata.reservation_submit_in_flight
                or userdata.complaint_submit_in_flight
            ):
                continue

            idle_for = time.monotonic() - last_user_activity_at
            if (
                inactivity_prompt_count < _agent.NO_SPEECH_REPROMPT_LIMIT
                and idle_for >= _agent.NO_SPEECH_PROMPT_SECONDS
                and (not last_reprompt_at or (time.monotonic() - last_reprompt_at) >= _agent.NO_SPEECH_REPROMPT_GAP_SECONDS)
            ):
                inactivity_prompt_count += 1
                last_reprompt_at = time.monotonic()
                logger.warning(
                    "call=%s | inactivity reprompt | idle_for=%.1fs | count=%d",
                    call_id, idle_for, inactivity_prompt_count,
                )
                flow_name = session.current_agent.__class__.__name__.lower() if session.current_agent else ""
                _agent._emit_event(
                    "call.inactivity",
                    call_id=call_id,
                    flow=flow_name,
                    action="reprompt",
                    idle_for_s=round(idle_for, 3),
                    count=inactivity_prompt_count,
                )
                reprompt_text = _agent._inactivity_reprompt(
                    userdata, flow_name, prompt_count=inactivity_prompt_count,
                )
                with contextlib.suppress(Exception):
                    userdata.last_agent_message = reprompt_text
                    await session.say(
                        reprompt_text,
                        allow_interruptions=True,
                        add_to_chat_ctx=False,
                    )
                continue

            if idle_for >= _agent.NO_SPEECH_CLOSE_SECONDS:
                close_reason = "inactivity_timeout"
                userdata.session_transitional_state = True
                logger.warning(
                    "call=%s | inactivity close | idle_for=%.1fs",
                    call_id, idle_for,
                )
                flow_name = session.current_agent.__class__.__name__.lower() if session.current_agent else ""
                _agent._emit_event(
                    "call.inactivity",
                    call_id=call_id,
                    flow=flow_name,
                    action="timeout",
                    idle_for_s=round(idle_for, 3),
                )
                await _agent._safe_close_session_once(
                    session,
                    close_state,
                    farewell="هقفل المكالمة دلوقتي، كلمنا تاني في أي وقت يا فندم.",
                )
                return

    try:
        await session.start(
            agent=userdata.agents["greeter"],
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
        watchdog_task = asyncio.create_task(_watch_inactivity(), name=f"inactivity_watchdog_{call_id}")
        await asyncio.wait_for(close_event.wait(), timeout=MAX_CALL_DURATION)
    except asyncio.TimeoutError:
        close_reason = "call_timeout"
        userdata.session_transitional_state = True
        logger.warning("call=%s | timeout after %ds — ending session", call_id, MAX_CALL_DURATION)
        await _agent._safe_close_session_once(
            session,
            close_state,
            farewell="معلش يا فندم، وقت المكالمة خلص. كلمنا تاني في أي وقت.",
        )
    except Exception as exc:
        close_reason = "session_error"
        logger.exception("call=%s | error: %s", call_id, exc)
        userdata.session_transitional_state = True
        await _agent._safe_aclose_session_once(session, close_state)
        raise
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task
        await _agent._safe_aclose_session_once(session, close_state)
        await _agent._release_session_slot(call_id)
        _agent._cleanup_turn_count(call_id)
        duration = int(time.monotonic() - t_start)
        call_ended_at_utc = datetime.now(timezone.utc)
        try:
            call_log_result = await _agent.submit_call_log(
                userdata,
                close_reason=close_reason,
                duration_seconds=duration,
                started_at_iso=call_started_at_utc.isoformat(),
                ended_at_iso=call_ended_at_utc.isoformat(),
            )
            if call_log_result is not None and call_log_result.get("queued"):
                logger.warning("call=%s | call log deferred to queue", call_id)
        except Exception:
            logger.exception("call=%s | call log submit failed", call_id)
        _agent._emit_event(
            "call.end",
            call_id=call_id,
            duration_s=duration,
            close_reason=close_reason,
            order_confirmed=userdata.order_confirmed,
            reservation_confirmed=userdata.reservation_confirmed,
            complaint_logged=userdata.complaint_logged,
            config_source=userdata.restaurant.config_source,
        )
        logger.info(
            "call=%s | ended | duration=%ds | close_reason=%s | order=%s | reservation=%s | complaint=%s | config_source=%s",
            call_id, duration, close_reason, userdata.order_confirmed, userdata.reservation_confirmed,
            userdata.complaint_logged, userdata.restaurant.config_source,
        )

if __name__ == "__main__":
    _start_parent_health_server()
    cli.run_app(server)
