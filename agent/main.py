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
    # Reset realtime kickoff counter so this call's first session triggers
    # the synthetic greeting, but subsequent sessions (flow handoffs) don't
    # re-fire it on top of carried-over context.
    _reset_kickoff = getattr(getattr(_agent, "SESSION_REALTIME", None), "reset_kickoff_counter", None)
    if callable(_reset_kickoff):
        _reset_kickoff()
    session_stt = _agent._build_session_stt(cfg, client_reference_id=call_id)
    # Fire-and-forget Soniox WebSocket pre-warm. Runs in parallel with
    # config fetch / agent setup so the customer's first turn doesn't
    # wait through the cold-start handshake (saw 2.7-3.3 s EOU on first
    # turn before this; ~870 ms after).
    asyncio.create_task(_agent.prewarm_stt_connection(session_stt), name=f"stt_prewarm_{call_id}")
    # Phase 1.3 — pick TTS for this call (Azure A/B, otherwise primary).
    session_tts, tts_provider = _agent.pick_session_tts()
    # Gemini Live TTS keeps a websocket open across turns; prewarm it
    # in parallel so the first synth doesn't pay the ~300 ms TLS+handshake.
    _gemini_live_prewarm = getattr(
        getattr(session_tts, "_wrapped_tts", session_tts),
        "prewarm",
        None,
    )
    if callable(_gemini_live_prewarm):
        asyncio.create_task(_gemini_live_prewarm(), name=f"tts_prewarm_{call_id}")
    stt_context_terms = _agent._stt_context_terms_for_config(cfg)
    # Derive config_available from the actual cfg this call is using, not from
    # shared worker-level runtime_health. The latter is process-wide and can be
    # briefly flipped to False by a concurrent call that fell back to degraded,
    # producing the confusing "config_available=False | config_source=backend"
    # readout even when this call fetched a valid backend config.
    config_available_for_call = cfg.config_source != "degraded_fallback"
    logger.info(
        "call=%s | startup readiness | deps_ready=%s | config_available=%s | write_available=%s | config_source=%s | degraded=%s | stt_provider=%s | tts_provider=%s | stt_context_terms=%d | preemptive=%s",
        call_id,
        _agent.session_dependencies_ready(),
        config_available_for_call,
        _agent.backend_write_available(userdata.write_health),
        cfg.config_source,
        cfg.degraded_mode,
        _agent.SESSION_STT_PROVIDER,
        tts_provider,
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

    # Preemptive generation kicks the LLM off as soon as the user starts
    # speaking. With the deterministic engine + LLM understanding now
    # owning ~70-80 % of turns, the preemptive call is wasted compute on
    # most turns and adds 600-800 ms of LLM time we then throw away. The
    # net is slower turns, not faster. Force it off whenever the
    # understanding layer is active (the new default) — operators can
    # still flip ``SESSION_PREEMPTIVE_GENERATION=1`` and explicitly set
    # ``LLM_UNDERSTANDING_ENABLED=0`` if they want the old shape back.
    _understanding_enabled = os.environ.get("LLM_UNDERSTANDING_ENABLED", "1") != "0"
    _preemptive = _agent.SESSION_PREEMPTIVE_GENERATION and not _understanding_enabled
    if _agent.SESSION_PREEMPTIVE_GENERATION and _understanding_enabled:
        logger.info(
            "preemptive_generation forced OFF — LLM understanding is the primary path; "
            "set LLM_UNDERSTANDING_ENABLED=0 to restore preemptive behaviour"
        )

    # When realtime mode is enabled, the Gemini Live model owns audio in/out and
    # the LLM. STT/TTS are not used; VAD stays for client-side endpointing
    # (preferred for Arabic per the comment below). Preemptive generation is
    # unused — the realtime model has its own server-side turn handling.
    _realtime = getattr(_agent, "SESSION_REALTIME", None)
    # Migrated from the deprecated min_endpointing_delay / max_endpointing_delay /
    # allow_interruptions / min_interruption_duration / false_interruption_timeout
    # kwargs to ``turn_handling=TurnHandlingOptions(...)`` (the v2.0 API). The old
    # kwargs printed a per-call deprecation warning AND don't compose cleanly with
    # the new adaptive interruption detector — using the typed dict makes both
    # the warning and the "interruption detector" log noise go away.
    _turn_handling: dict[str, Any] = {
        "endpointing": {
            "min_delay": _agent.MIN_ENDPOINTING_DELAY_SECONDS,
            "max_delay": _agent.MAX_ENDPOINTING_DELAY_SECONDS,
        },
        "interruption": {
            "enabled": True,
            # ``"vad"`` uses Silero locally instead of LiveKit's cloud
            # adaptive detector. The cloud one returned ``error 2008
            # service might be unavailable in this region`` from EG/IL,
            # which spammed the log with retry traces. VAD-based detection
            # is good enough for restaurant calls (1-2 word interruptions
            # are easy to detect by audio energy alone).
            "mode": "vad",
            "min_duration": _agent.MIN_INTERRUPTION_DURATION_SECONDS,
            "false_interruption_timeout": _agent.FALSE_INTERRUPTION_TIMEOUT_SECONDS,
        },
        # turn_detection omitted intentionally: MultilingualModel doesn't support
        # Arabic and emits "Turn detector does not support language ar" warnings.
        # VAD + endpointing min_delay handle end-of-turn detection for ar.
    }
    _session_kwargs: dict[str, Any] = dict(
        userdata=userdata,
        vad=_agent.SESSION_VAD,
        turn_handling=_turn_handling,
        user_away_timeout=_agent.USER_AWAY_TIMEOUT_SECONDS,
        max_tool_steps=_agent.MAX_TOOL_STEPS,
    )
    if _realtime is not None:
        _session_kwargs["llm"] = _realtime
        _session_kwargs["preemptive_generation"] = False
        logger.info(
            "call=%s | realtime mode | model=%s | voice=%s",
            call_id, _agent.SESSION_REALTIME_MODEL, _agent.SESSION_REALTIME_VOICE,
        )
    else:
        _session_kwargs["stt"] = session_stt
        _session_kwargs["llm"] = _agent.SESSION_LLM
        _session_kwargs["tts"] = session_tts
        _session_kwargs["preemptive_generation"] = _preemptive

    session = AgentSession[UserData](**_session_kwargs)

    # Phase 2.1 — attach the mid-speech backchannel emitter. No-op when
    # the BACKCHANNEL_EMITTER_ENABLED env flag is off, and the emitter
    # itself enforces the gap / agent-speaking suppression.
    if _realtime is None:
        try:
            from backchannel_emitter import attach_backchannel_emitter
            attach_backchannel_emitter(session)
        except Exception as _bc_exc:
            logger.warning("backchannel emitter attach failed | %s", _bc_exc)

    # In realtime mode there is no TTS, so every ``session.say(text)`` call in
    # the codebase (greeting, tool fast-path, deterministic say_and_stop,
    # inactivity reprompt, farewell) raises ``RuntimeError: trying to generate
    # speech from text without a TTS model``. The realtime model is the only
    # speaker. Two paths:
    #   - 2.5 native-audio supports ``generate_reply(instructions=...)``, so we
    #     route the requested text through it and the model speaks it.
    #   - 3.1 Flash Live rejects ``generate_reply`` outright, so we noop and
    #     log — the model handles its own turn-taking from ``instructions`` +
    #     kickoff seed instead.
    if _realtime is not None:
        _is_31 = _agent.SESSION_REALTIME_MODEL == "gemini-3.1-flash-live-preview"
        _original_say = session.say
        _say_noop_count = {"n": 0}

        class _NoopSayHandle:
            def __await__(self_inner):  # type: ignore[no-untyped-def]
                async def _coro() -> None:
                    return None
                return _coro().__await__()

            def __getattr__(self_inner, _name):  # type: ignore[no-untyped-def]
                return lambda *a, **k: None

        def _realtime_say(text: str, *, allow_interruptions: bool = True, add_to_chat_ctx: bool = True, **_kwargs: Any) -> Any:
            try:
                return _original_say(
                    text,
                    allow_interruptions=allow_interruptions,
                    add_to_chat_ctx=add_to_chat_ctx,
                )
            except RuntimeError as say_err:
                if "without a TTS model" not in str(say_err):
                    raise
                if _is_31:
                    _say_noop_count["n"] += 1
                    if _say_noop_count["n"] <= 3:
                        logger.debug(
                            "call=%s | realtime(3.1): session.say() suppressed | text=%r",
                            call_id, text[:80],
                        )
                    return _NoopSayHandle()
                # 2.5 native-audio: re-route through generate_reply so the
                # realtime model produces audio for the canned text.
                try:
                    return session.generate_reply(
                        instructions=(
                            "Say the following exactly in natural Egyptian "
                            f"Arabic, no extra words: {text}"
                        ),
                    )
                except Exception as gen_err:
                    logger.warning(
                        "call=%s | realtime say→generate_reply failed | %s",
                        call_id, gen_err,
                    )
                    return _NoopSayHandle()

        session.say = _realtime_say  # type: ignore[method-assign]

    # Per-turn latency breakdown. Each turn collects timestamps as the
    # pipeline progresses; when the agent starts speaking we emit a
    # single ``METRICS TURN`` line with the full e2e breakdown so the
    # operator doesn't have to manually correlate four separate METRICS
    # lines. The wall-clock e2e is what the customer perceives.
    turn_perf: dict[str, float | int] = {}

    def _reset_turn_perf() -> None:
        turn_perf.clear()

    # Phase 4.2 — per-call cost accumulation. We sum LLM prompt+completion
    # tokens through the call and at ``call.end`` multiply by the per-1M
    # token rates configured in env. Default rates correspond to GPT-4o-mini
    # ($0.15 in / $0.60 out per 1M tokens, OpenAI public list price).
    call_cost = {
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "tts_chars": 0,
        "stt_audio_seconds": 0.0,
        "llm_calls": 0,
    }

    @session.on("metrics_collected")
    def _on_metrics(event):
        m = event.metrics
        if isinstance(m, STTMetrics):
            turn_perf["stt_duration_ms"] = m.duration * 1000
            try:
                call_cost["stt_audio_seconds"] += float(m.audio_duration or 0.0)
            except Exception:
                pass
            logger.info(
                "call=%s | METRICS STT | duration=%.0fms | audio=%.1fs",
                call_id, m.duration * 1000, m.audio_duration,
            )
        elif isinstance(m, LLMMetrics):
            # First LLM call of the turn = tool decision; second (if any)
            # = paraphrase. The fast-path skips the second so on tool
            # turns we typically only see one LLMMetrics event.
            turn_perf["llm_calls"] = int(turn_perf.get("llm_calls", 0)) + 1
            turn_perf["llm_total_ms"] = (
                float(turn_perf.get("llm_total_ms", 0.0)) + m.duration * 1000
            )
            turn_perf.setdefault("llm_first_ttft_ms", m.ttft * 1000)
            turn_perf["llm_last_ttft_ms"] = m.ttft * 1000
            try:
                call_cost["llm_prompt_tokens"] += int(m.prompt_tokens or 0)
                call_cost["llm_completion_tokens"] += int(m.completion_tokens or 0)
                call_cost["llm_calls"] += 1
            except Exception:
                pass
            logger.info(
                "call=%s | METRICS LLM | ttft=%.0fms | total=%.0fms | prompt=%d | completion=%d | tok/s=%.0f",
                call_id, m.ttft * 1000, m.duration * 1000,
                m.prompt_tokens, m.completion_tokens, m.tokens_per_second,
            )
        elif isinstance(m, TTSMetrics):
            turn_perf.setdefault("tts_ttfb_ms", m.ttfb * 1000)
            try:
                call_cost["tts_chars"] += int(m.characters_count or 0)
            except Exception:
                pass
            logger.info(
                "call=%s | METRICS TTS | ttfb=%.0fms | total=%.0fms | audio=%.1fs | chars=%d",
                call_id, m.ttfb * 1000, m.duration * 1000,
                m.audio_duration, m.characters_count,
            )
        elif isinstance(m, EOUMetrics):
            turn_perf["eou_delay_ms"] = m.end_of_utterance_delay * 1000
            turn_perf["transcription_delay_ms"] = m.transcription_delay * 1000
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
        # Capture pipeline phase timestamps so we can emit a single
        # end-to-end latency summary per turn (see METRICS TURN below).
        if event.new_state == "thinking" and "t_thinking_started" not in turn_perf:
            turn_perf["t_thinking_started"] = time.monotonic()
        if event.new_state == "speaking":
            now = time.monotonic()
            turn_perf["t_speaking_started"] = now
            user_stopped_at = turn_perf.get("t_user_stopped")
            if user_stopped_at:
                e2e_ms = (now - user_stopped_at) * 1000
                eou_ms = float(turn_perf.get("eou_delay_ms", 0.0))
                llm_ms = float(turn_perf.get("llm_total_ms", 0.0))
                llm_first_ttft = float(turn_perf.get("llm_first_ttft_ms", 0.0))
                tts_ttfb = float(turn_perf.get("tts_ttfb_ms", 0.0))
                llm_calls = int(turn_perf.get("llm_calls", 0))
                # The "other" bucket catches anything not covered by
                # the named stages (handler overhead, scheduling,
                # network gaps). If it's large, look there.
                accounted = eou_ms + llm_ms + tts_ttfb
                other_ms = max(e2e_ms - accounted, 0.0)
                target_marker = "OK" if e2e_ms <= 2500 else "SLOW"
                logger.info(
                    "call=%s | METRICS TURN [%s] | e2e=%.0fms | eou=%.0fms | "
                    "llm_calls=%d | llm_total=%.0fms | llm_first_ttft=%.0fms | "
                    "tts_ttfb=%.0fms | other=%.0fms",
                    call_id, target_marker, e2e_ms, eou_ms, llm_calls,
                    llm_ms, llm_first_ttft, tts_ttfb, other_ms,
                )
            _reset_turn_perf()
        logger.info(
            "call=%s | agent_state=%s→%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal last_user_activity_at
        if event.new_state == "speaking":
            last_user_activity_at = time.monotonic()
        if event.old_state == "speaking" and event.new_state == "listening":
            # User just stopped speaking — start the per-turn latency
            # clock here. The matching ``agent_state→speaking`` event
            # will close the loop and emit METRICS TURN.
            turn_perf["t_user_stopped"] = time.monotonic()
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
            # In realtime mode the model owns turn-taking server-side; the
            # local "no speech for N seconds" reprompt would just call
            # session.say (a noop here) and spam telemetry. Skip the reprompt
            # branch entirely and let the close-timeout below handle truly
            # dead calls.
            if _realtime is not None:
                if idle_for >= _agent.NO_SPEECH_CLOSE_SECONDS:
                    pass  # fall through to close branch below
                else:
                    continue
            if (
                _realtime is None
                and inactivity_prompt_count < _agent.NO_SPEECH_REPROMPT_LIMIT
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
        # Phase 4.2 — compute per-call cost estimate. Token rates default
        # to GPT-4o-mini ($0.15/$0.60 per 1M); override via env to match
        # whichever LLM the deployment actually uses.
        try:
            llm_in_per_1m = float(os.getenv("COST_LLM_INPUT_PER_1M_USD", "0.15"))
            llm_out_per_1m = float(os.getenv("COST_LLM_OUTPUT_PER_1M_USD", "0.60"))
            tts_per_1m_chars = float(os.getenv("COST_TTS_PER_1M_CHARS_USD", "16.0"))
            stt_per_min = float(os.getenv("COST_STT_PER_MINUTE_USD", "0.0036"))
            cost_estimate_usd = (
                call_cost["llm_prompt_tokens"] * llm_in_per_1m / 1_000_000
                + call_cost["llm_completion_tokens"] * llm_out_per_1m / 1_000_000
                + call_cost["tts_chars"] * tts_per_1m_chars / 1_000_000
                + (call_cost["stt_audio_seconds"] / 60.0) * stt_per_min
            )
        except Exception:
            cost_estimate_usd = 0.0
        # Close the Gemini Live TTS session so the next call gets a
        # fresh conversation context (otherwise the model accumulates
        # past TTS turns and may drift). Best-effort.
        _gemini_live_reset = getattr(
            getattr(session_tts, "_wrapped_tts", session_tts),
            "reset_session",
            None,
        )
        if callable(_gemini_live_reset):
            try:
                await _gemini_live_reset()
            except Exception as _reset_exc:
                logger.debug("gemini live tts reset on call end failed | %s", _reset_exc)

        _agent._emit_event(
            "call.end",
            call_id=call_id,
            duration_s=duration,
            close_reason=close_reason,
            order_confirmed=userdata.order_confirmed,
            reservation_confirmed=userdata.reservation_confirmed,
            complaint_logged=userdata.complaint_logged,
            config_source=userdata.restaurant.config_source,
            llm_prompt_tokens=call_cost["llm_prompt_tokens"],
            llm_completion_tokens=call_cost["llm_completion_tokens"],
            llm_calls=call_cost["llm_calls"],
            tts_chars=call_cost["tts_chars"],
            stt_audio_seconds=round(call_cost["stt_audio_seconds"], 2),
            cost_estimate_usd=round(cost_estimate_usd, 6),
        )
        logger.info(
            "call=%s | ended | duration=%ds | close_reason=%s | order=%s | reservation=%s | complaint=%s | config_source=%s",
            call_id, duration, close_reason, userdata.order_confirmed, userdata.reservation_confirmed,
            userdata.complaint_logged, userdata.restaurant.config_source,
        )

def _start_dev_dashboard() -> None:
    """Optionally start the developer dashboard alongside the agent.

    Disabled by setting ``DASHBOARD_ENABLED=0``. Failures (missing deps,
    port in use) are logged but never block agent startup.
    """
    if os.getenv("DASHBOARD_ENABLED", "1").lower() in {"0", "false", "no"}:
        return
    try:
        from dashboard import start_dashboard_server
        start_dashboard_server()
    except Exception as exc:
        logger.warning("dev dashboard not started | %s", exc)


if __name__ == "__main__":
    _start_parent_health_server()
    _start_dev_dashboard()
    cli.run_app(server)
