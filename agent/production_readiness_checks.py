"""Offline production-readiness checks for the restaurant voice agent.

These checks intentionally avoid LiveKit rooms, provider APIs, and backend
network calls. They verify invariants that must hold before live QA:

- slot-question tracking catches repeated asks inside one call;
- captured slots are treated as repeated if asked again;
- the per-turn state prompt carries asked-once evidence back to the LLM;
- latency telemetry has a 1s first-audio SLO event;
- direct Groq/Cerebras warmup strips routing prefixes before API calls.
"""
from __future__ import annotations

import sys
import asyncio
import contextlib
import io
import os
import time
from pathlib import Path
from types import SimpleNamespace

# The repo-local .env may intentionally contain production settings for
# deployment. Offline checks monkeypatch backend submitters and should not
# import the runtime agent in prod mode.
os.environ.setdefault("APP_ENV", "qa")
os.environ.setdefault("TELEMETRY_ENABLED", "false")
os.environ.setdefault("TELEMETRY_LOG_PATH", ".runtime/qa/telemetry-readiness.jsonl")

from backend.config import RestaurantConfig
import restaurant_tools
from qa_alerts import evaluate_alerts
from qa_call_matrix import DEFAULT_REQUIRED_SCENARIOS, evaluate_matrix, load_matrix
from qa_live_preflight import run_preflight
from qa_market_gate import evaluate_market_readiness
from qa_progress import evaluate_progress
from qa_sync_matrix import sync_matrix_rows
from qa_telemetry_gate import evaluate_events, load_telemetry_events, main as qa_gate_main
from qa_transcript_review import evaluate_transcript_events
from restaurant_agent import RestaurantAgent
from state.user_data import UserData


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "agent" / "main.py"
AGENT_PATH = ROOT / "agent" / "agent.py"
HAMSA_TTS_PATH = ROOT / "agent" / "hamsa_tts.py"
ENV_EXAMPLE_PATH = ROOT / "agent" / ".env.example"
BACKEND_ENV_EXAMPLE_PATH = ROOT / "backend" / ".env.example"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _ar(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def check_slot_question_classifier() -> None:
    examples = {
        "order": r"\u062a\u062d\u0628 \u062a\u0637\u0644\u0628 \u0627\u064a\u0647\u061f",
        "address": r"\u0627\u0644\u0639\u0646\u0648\u0627\u0646 \u0641\u064a\u0646\u061f",
        "name": r"\u0627\u0633\u0645 \u062d\u0636\u0631\u062a\u0643 \u0627\u064a\u0647\u061f",
        "reservation_time": r"\u0645\u064a\u0639\u0627\u062f \u0627\u0644\u062d\u062c\u0632 \u0627\u0645\u062a\u0649\u061f",
        "guests": r"\u0643\u0627\u0645 \u0641\u0631\u062f\u061f",
        "branch": r"\u0627\u0646\u0647\u064a \u0641\u0631\u0639\u061f",
        "complaint": r"\u0627\u064a\u0647 \u0627\u0644\u0644\u064a \u062d\u0635\u0644\u061f",
    }
    for expected, text in examples.items():
        detected = RestaurantAgent.detect_slot_question_categories(_ar(text))
        _assert(
            expected in detected,
            f"expected {expected!r} question to be detected; got {detected!r}",
        )


def check_repeated_question_tracking() -> None:
    ud = UserData()
    ask_name = _ar(r"\u0627\u0633\u0645 \u062d\u0636\u0631\u062a\u0643 \u0627\u064a\u0647\u061f")
    _assert(
        RestaurantAgent.record_asked_slot_questions(ud, ask_name) == [],
        "first ask for an uncaptured slot should not count as repeated",
    )
    _assert(
        RestaurantAgent.record_asked_slot_questions(ud, ask_name) == [],
        "second ask for still-missing slot should not fail repeated-question gate",
    )
    _assert(ud.asked_slot_questions["name"] == 2, "asked count should increment")

    captured = UserData(customer_name=_ar(r"\u0627\u062d\u0645\u062f"))
    _assert(
        RestaurantAgent.record_asked_slot_questions(captured, ask_name) == ["name"],
        "asking for an already captured slot should count as repeated immediately",
    )
    corrective = RestaurantAgent.corrective_response_for_repeated_questions(
        captured, ["name"]
    )
    _assert(
        _ar(r"\u0627\u062d\u0645\u062f") in corrective,
        "corrective response should include already captured name",
    )


def check_explicit_cancel_or_done_handling() -> None:
    cancel_text = _ar(r"\u0627\u0644\u063a\u064a \u0627\u0644\u0637\u0644\u0628")
    _assert(
        RestaurantAgent.is_explicit_cancel_or_done(cancel_text),
        "explicit Arabic cancellation should be detected",
    )
    _assert(
        RestaurantAgent.is_explicit_cancel_or_done("never mind"),
        "explicit English cancellation should be detected",
    )

    order_ud = UserData(order=["pizza x 2"], order_total=240.0, order_validated=True)
    order_agent = SimpleNamespace(session=SimpleNamespace(userdata=order_ud))
    order_reply = RestaurantAgent._deterministic_done_reply(order_agent, cancel_text)
    _assert(order_reply, "cancelled order should produce a farewell")
    _assert(order_ud.order == [], "cancelled order should clear order state")
    _assert(order_ud.order_total == 0.0, "cancelled order should clear total")
    _assert(not order_ud.order_validated, "cancelled order should clear validation")

    reservation_ud = UserData(
        reservation_time="tomorrow 8 pm",
        guests_count=4,
        selected_branch="main",
    )
    reservation_agent = SimpleNamespace(session=SimpleNamespace(userdata=reservation_ud))
    reservation_reply = RestaurantAgent._deterministic_done_reply(
        reservation_agent, "never mind"
    )
    _assert(reservation_reply, "cancelled reservation should produce a farewell")
    _assert(not reservation_ud.reservation_time, "cancelled reservation should clear time")
    _assert(not reservation_ud.guests_count, "cancelled reservation should clear guests")
    _assert(not reservation_ud.selected_branch, "cancelled reservation should clear branch")


def check_state_snapshot_includes_asked_once() -> None:
    ud = UserData(
        customer_name=_ar(r"\u0633\u0627\u0631\u0629"),
        order=[_ar(r"\u0643\u0634\u0631\u064a \u0643\u0628\u064a\u0631 x 2")],
    )
    ud.active_flow = "takeaway"
    ud.asked_slot_questions["name"] = 1
    fake_agent = SimpleNamespace(
        session=SimpleNamespace(userdata=ud),
        cfg=RestaurantConfig(name="QA Restaurant"),
    )
    snapshot = RestaurantAgent._build_state_snapshot(fake_agent)
    _assert(snapshot.startswith("[CALL_STATE]"), "snapshot should be a state prompt")
    _assert("name=" in snapshot, "snapshot should include captured name")
    _assert("order=[" in snapshot, "snapshot should include captured order")
    _assert("asked_once=name" in snapshot, "snapshot should include asked_once")
    _assert(
        "rule=never_repeat_asked_slot_question" in snapshot,
        "snapshot should include no-repeat rule",
    )


def check_latency_slo_wiring() -> None:
    source = MAIN_PATH.read_text(encoding="utf-8")
    _assert(
        'TARGET_E2E_FIRST_AUDIO_MS", 600.0' in source,
        "main.py should default first-audio SLO to 600ms",
    )
    _assert('"latency.e2e"' in source, "main.py should emit latency.e2e")
    _assert("breach=e2e_breached" in source, "latency.e2e should carry breach flag")
    _assert(
        "METRICS E2E SLO BREACH" in source,
        "main.py should log first-audio SLO breaches",
    )
    _assert(
        "pending_corrective_response" in source,
        "main.py should set deterministic correction after repeated captured-slot questions",
    )
    agent_source = AGENT_PATH.read_text(encoding="utf-8")
    _assert(
        "TELEMETRY_LOG_PATH" in agent_source,
        "agent.py should expose an opt-in telemetry JSONL file sink",
    )
    _assert(
        'logging.Formatter("%(message)s")' in agent_source,
        "telemetry file sink should write raw JSON messages",
    )
    _assert(
        "_validate_production_env()" in agent_source,
        "agent.py should validate production environment before startup",
    )
    _assert(
        "mock_secret_key" in agent_source,
        "production validator should reject mock backend credentials",
    )
    _assert(
        "QA_TRANSCRIPT_EVENTS_ENABLED" in agent_source,
        "agent.py should expose opt-in transcript telemetry for QA review",
    )
    _assert(
        "_redact_qa_transcript_text" in agent_source,
        "agent.py should redact transcript telemetry before emitting it",
    )
    _assert(
        "_emit_qa_transcript" in source,
        "main.py should emit opt-in transcript events for QA review",
    )


def check_dotenv_loaded_before_telemetry_setup() -> None:
    source = AGENT_PATH.read_text(encoding="utf-8")
    dotenv_index = source.find('load_dotenv(AGENT_DIR / ".env")')
    telemetry_path_index = source.find('_TELEMETRY_LOG_PATH = os.getenv("TELEMETRY_LOG_PATH"')
    transcript_flag_index = source.find('QA_TRANSCRIPT_EVENTS_ENABLED = os.getenv("QA_TRANSCRIPT_EVENTS_ENABLED"')
    setup_index = source.find("_setup_telemetry_file_logging()")
    _assert(dotenv_index != -1, "agent.py should load agent/.env")
    _assert(telemetry_path_index != -1, "agent.py should read TELEMETRY_LOG_PATH")
    _assert(transcript_flag_index != -1, "agent.py should read QA_TRANSCRIPT_EVENTS_ENABLED")
    _assert(setup_index != -1, "agent.py should initialize telemetry file logging")
    _assert(
        "if not _TELEMETRY_ENABLED or not _TELEMETRY_LOG_PATH:" in source,
        "telemetry file logging should respect TELEMETRY_ENABLED=false",
    )
    _assert(
        dotenv_index < telemetry_path_index < setup_index,
        "agent.py must load .env before reading TELEMETRY_LOG_PATH and setting up telemetry logging",
    )
    _assert(
        dotenv_index < transcript_flag_index,
        "agent.py must load .env before reading QA_TRANSCRIPT_EVENTS_ENABLED",
    )


def check_hamsa_prewarm_matches_livekit_adapter() -> None:
    source = HAMSA_TTS_PATH.read_text(encoding="utf-8")
    _assert(
        "def prewarm(self) -> None:" in source,
        "Hamsa TTS prewarm must be synchronous because LiveKit StreamAdapter calls it without await",
    )
    _assert(
        "async def warmup(self) -> None:" in source,
        "Hamsa TTS should expose an awaitable warmup for explicit agent startup warmup",
    )
    _assert(
        "async def prewarm(self)" not in source,
        "Hamsa TTS must not expose async prewarm; it causes unawaited coroutine warnings",
    )


def check_env_example_has_qa_settings() -> None:
    source = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    for key in [
        "TARGET_E2E_FIRST_AUDIO_MS=600",
        "TELEMETRY_LOG_PATH=.runtime/prod/telemetry.jsonl",
        "QA_TRANSCRIPT_EVENTS_ENABLED=true",
        "CALL_METRICS_PATH=.runtime/prod/call_metrics.jsonl",
        "AGENT_HEALTH_HEARTBEAT_SECONDS=30",
        "SESSION_PREEMPTIVE_GENERATION=true",
        "SESSION_TTS_STREAMING_ENABLED=true",
    ]:
        _assert(key in source, f".env.example should document {key}")


def check_backend_env_example_has_prod_settings() -> None:
    source = BACKEND_ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    for key in [
        "JWT_SECRET=generate-a-strong-32-byte-secret",
        "BACKEND_API_KEY=your-backend-api-key",
        "BIRD_API_KEY=your-bird-access-key",
        "BIRD_WORKSPACE_ID=your-bird-workspace-id",
        "BIRD_CHANNEL_ID=your-bird-channel-id",
        "BIRD_OTP_TEMPLATE_PROJECT_ID=your-bird-otp-template-project-id",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]:
        _assert(key in source, f"backend .env.example should document {key}")


def check_service_preflight_checks_demo_session() -> None:
    source = (ROOT / "agent" / "qa_service_preflight.py").read_text(encoding="utf-8")
    for key in [
        "DEFAULT_DEMO_SESSION_URL",
        "/demo/livekit-session",
        "qaPreflight",
        "qa_service_preflight",
        "qa_preflight_room_name",
        "qa-preflight-",
        "token_present",
        "room_metadata_present",
        "qa_preflight_metadata",
        "--skip-demo-session",
    ]:
        _assert(key in source, f"service preflight should cover demo session field {key}")
    backend_source = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    for key in ["qaPreflight", "qa_service_preflight", "qa_preflight", "qa-preflight"]:
        _assert(key in backend_source, f"backend demo session should tag preflight rooms with {key}")
    main_source = MAIN_PATH.read_text(encoding="utf-8")
    for key in ["qa_service_preflight", "qa_preflight", "qa-preflight-", "preflight room ignored"]:
        _assert(key in main_source, f"agent should ignore demo preflight rooms before QA telemetry: {key}")


def check_idle_health_report_is_ready() -> None:
    import agent as agent_module

    now = time.time()
    idle_snapshot = {
        "pid": 12345,
        "updated_at_epoch": now,
        "active_sessions": 0,
        "config_available": False,
        "circuits_open": [],
        "write_queue_size": 0,
        "queue_worker_running": False,
        "config_refresh_worker_running": False,
    }
    originals = {
        "_read_worker_health_snapshots": agent_module._read_worker_health_snapshots,
        "_backend_recovery_queue_count": agent_module._backend_recovery_queue_count,
    }
    try:
        agent_module._read_worker_health_snapshots = lambda: [idle_snapshot]
        agent_module._backend_recovery_queue_count = lambda: 0
        status_code, payload = agent_module.build_agent_health_report()
        _assert(status_code == 200, f"idle fresh worker should be healthy: {payload!r}")
        _assert(payload["status"] == "ok", "idle fresh worker should report ok")
        _assert(payload["reasons"] == [], "idle fresh worker should not report degraded reasons")

        active_snapshot = dict(idle_snapshot)
        active_snapshot["active_sessions"] = 1
        agent_module._read_worker_health_snapshots = lambda: [active_snapshot]
        active_status, active_payload = agent_module.build_agent_health_report()
        _assert(active_status == 503, "active worker without config should be degraded")
        _assert(
            "config_unavailable" in active_payload["reasons"],
            "active config failure should stay visible in health",
        )
    finally:
        agent_module._read_worker_health_snapshots = originals["_read_worker_health_snapshots"]
        agent_module._backend_recovery_queue_count = originals["_backend_recovery_queue_count"]


def check_provider_warmup_prefix_stripping() -> None:
    source = AGENT_PATH.read_text(encoding="utf-8")
    _assert(
        'warmup_model = SESSION_LLM_MODEL.split("/", 1)[1]' in source,
        "direct Groq/Cerebras warmup should strip routing prefix",
    )
    _assert(
        "model=warmup_model" in source,
        "warmup completion should use the derived warmup model",
    )


def check_qa_telemetry_gate() -> None:
    clean_events = [
        {
            "event": "latency.e2e",
            "call_id": f"call-{idx}",
            "user_to_first_audio_ms": 900 + idx,
            "breach": False,
        }
        for idx in range(50)
    ]
    flows = ("takeaway", "delivery", "reservation", "complaint")
    for idx in range(50):
        flow = flows[idx % len(flows)]
        event = {"event": "call.end", "call_id": f"call-{idx}", "flow": flow}
        if flow in {"takeaway", "delivery"}:
            event["order_confirmed"] = True
        elif flow == "reservation":
            event["reservation_confirmed"] = True
        elif flow == "complaint":
            event["complaint_logged"] = True
        clean_events.append(event)
    clean = evaluate_events(clean_events, min_calls=50, target_ms=1000.0)
    _assert(clean.passed, f"clean telemetry should pass gate: {clean.reasons!r}")
    _assert(clean.calls_seen == 50, "gate should count unique calls")
    _assert(clean.completed_calls == 50, "gate should count completed call.end calls")
    _assert(clean.p95_latency_ms is not None, "gate should compute p95 latency")
    _assert(clean.missing_flows == [], "gate should pass complete flow coverage")
    _assert(
        clean.missing_successful_flows == [],
        "gate should pass successful flow coverage",
    )
    _assert(
        clean.missing_latency_flows == [],
        "gate should pass per-flow latency coverage",
    )
    _assert(
        set(clean.per_flow_p95_latency_ms) == set(flows),
        "gate should compute p95 latency per required flow",
    )

    per_flow_slow = [
        event for event in clean_events
        if event.get("event") != "latency.e2e"
    ]
    for idx in range(50):
        flow = "delivery" if idx == 0 else "takeaway"
        per_flow_slow.append(
            {
                "event": "latency.e2e",
                "call_id": f"call-{idx}",
                "flow": flow,
                "user_to_first_audio_ms": 2600 if idx == 0 else 900,
                "breach": False,
            }
        )
    per_flow_slow_result = evaluate_events(
        per_flow_slow,
        min_calls=50,
        target_ms=1000.0,
        required_flows=["takeaway", "delivery"],
    )
    _assert(
        not per_flow_slow_result.passed,
        "one slow required flow should fail even when overall p95 is fast",
    )
    _assert(
        per_flow_slow_result.slow_latency_flows == {"delivery": 2600.0},
        "gate should report the slow required flow",
    )

    unsuccessful_delivery = [
        {
            **event,
            "order_confirmed": False,
        }
        if event.get("event") == "call.end" and event.get("flow") == "delivery"
        else event
        for event in clean_events
    ]
    unsuccessful_delivery_result = evaluate_events(
        unsuccessful_delivery,
        min_calls=50,
        target_ms=1000.0,
    )
    _assert(
        not unsuccessful_delivery_result.passed,
        "flow coverage without successful delivery should fail gate",
    )
    _assert(
        unsuccessful_delivery_result.missing_successful_flows == ["delivery"],
        "gate should report missing successful delivery",
    )

    latency_only = evaluate_events(
        [event for event in clean_events if event.get("event") == "latency.e2e"],
        min_calls=50,
        target_ms=1000.0,
        required_flows=[],
    )
    _assert(not latency_only.passed, "latency-only telemetry should not satisfy min completed calls")
    _assert(
        latency_only.completed_calls == 0,
        "gate should use call.end events as completed-call source of truth",
    )

    missing_flow_events = [
        event for event in clean_events
        if not (event.get("event") == "call.end" and event.get("flow") == "complaint")
    ]
    missing_flow = evaluate_events(missing_flow_events, min_calls=50, target_ms=1000.0)
    _assert(not missing_flow.passed, "missing flow coverage should fail gate")
    _assert(
        missing_flow.missing_flows == ["complaint"],
        "gate should report exactly the missing flow",
    )

    repeated = clean_events + [
        {
            "event": "slot.repeated_question_detected",
            "call_id": "call-1",
            "categories": ["name"],
        }
    ]
    repeated_result = evaluate_events(repeated, min_calls=50, target_ms=1000.0)
    _assert(not repeated_result.passed, "repeated-question telemetry should fail gate")
    _assert(
        repeated_result.repeated_question_events == 1,
        "gate should count repeated-question events",
    )

    slow_events = [
        {
            "event": "latency.e2e",
            "call_id": f"slow-{idx}",
            "user_to_first_audio_ms": 2600,
            "breach": True,
        }
        for idx in range(50)
    ]
    slow = evaluate_events(slow_events, min_calls=50, target_ms=1000.0)
    _assert(not slow.passed, "slow telemetry should fail gate")
    _assert(slow.latency_breach_events == 50, "gate should count breach events")

    telemetry_path = ROOT / ".runtime" / "qa-telemetry-gate-check.log"
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        telemetry_path.write_text(
            "\n".join(
                [
                    '{"event":"latency.e2e","call_id":"json","user_to_first_audio_ms":1000,"breach":false}',
                    '2026-05-06T00:00:00 | INFO | restaurant.telemetry | {"event":"latency.e2e","call_id":"log","user_to_first_audio_ms":1100,"breach":false}',
                    "not json",
                ]
            ),
            encoding="utf-8",
        )
        loaded = load_telemetry_events(telemetry_path)
        _assert(len(loaded) == 2, "gate should parse raw JSONL and decorated log lines")
    finally:
        if telemetry_path.exists():
            telemetry_path.unlink()

    missing_path = ROOT / ".runtime" / "missing-telemetry.jsonl"
    if missing_path.exists():
        missing_path.unlink()
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = qa_gate_main(["--telemetry", str(missing_path)])
    _assert(exit_code == 1, "missing telemetry file should fail cleanly")
    missing_summary = captured.getvalue()
    _assert(
        "telemetry file not found" in missing_summary,
        "missing telemetry failure should explain the missing file",
    )


def check_live_qa_preflight() -> None:
    telemetry_path = ROOT / ".runtime" / "preflight-telemetry.jsonl"
    if telemetry_path.exists():
        telemetry_path.unlink()
    env_updates = {
        "APP_ENV": "prod",
        "LIVEKIT_URL": "wss://qa.livekit.cloud",
        "LIVEKIT_API_KEY": "lk_abc123validvalue",
        "LIVEKIT_API_SECRET": "lksecabc123validvalue",
        "BACKEND_BASE_URL": "http://localhost:8000",
        "BACKEND_API_KEY": "bk_abc123validvalue",
        "SONIOX_API_KEY": "sonabc123validvalue",
        "SESSION_LLM_MODEL": "gemini-3.1-flash-lite",
        "SESSION_LLM_USE_VERTEXAI": "true",
        "SESSION_LLM_LOCATION": "global",
        "GROQ_API_KEY": "groqabc123validvalue",
        "SESSION_TTS_MODEL": "cloud-tts/chirp3-hd",
        "SESSION_TTS_VOICE": "ar-XA-Chirp3-HD-Sulafat",
        "SESSION_TTS_LANGUAGE": "ar-XA",
        "GOOGLE_APPLICATION_CREDENTIALS": str(ROOT / "agent" / "nimble-radio-476115-f2-8a719d5ce347.json"),
        "SESSION_TTS_STREAMING_ENABLED": "true",
        "SESSION_PREEMPTIVE_GENERATION": "true",
        "TELEMETRY_LOG_PATH": str(telemetry_path),
        "QA_TRANSCRIPT_EVENTS_ENABLED": "true",
    }
    previous_env = {key: os.environ.get(key) for key in env_updates}
    try:
        os.environ.update(env_updates)
        report = run_preflight(
            telemetry_path=telemetry_path,
            min_calls=50,
            target_ms=600.0,
        )
        _assert(report.passed, f"valid live QA env should pass preflight: {report.errors!r}")
        _assert(
            report.checks.get("telemetry_parent_writable") is True,
            "preflight should verify telemetry directory writability",
        )
        _assert(
            "telemetry file does not exist yet" in " ".join(report.warnings),
            "preflight should warn when telemetry has not been collected",
        )
        _assert(
            report.checks.get("qa_transcript_events_enabled") == "true",
            "preflight should expose transcript review toggle state",
        )
        os.environ["BACKEND_API_KEY"] = "mock_secret_key"
        failed = run_preflight(
            telemetry_path=telemetry_path,
            min_calls=50,
            target_ms=600.0,
        )
        _assert(not failed.passed, "placeholder backend API key should fail preflight")
        _assert(
            any("BACKEND_API_KEY looks like a placeholder" in error for error in failed.errors),
            "preflight should identify placeholder backend credentials",
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if telemetry_path.exists():
            telemetry_path.unlink()


def check_live_qa_alerts() -> None:
    healthy_events = [
        {
            "event": "latency.e2e",
            "call_id": f"call-{idx}",
            "user_to_first_audio_ms": 800 + idx,
            "breach": False,
        }
        for idx in range(5)
    ]
    healthy_events.extend(
        {
            "event": "call.end",
            "call_id": f"call-{idx}",
            "flow": "takeaway",
            "order_confirmed": True,
            "close_reason": "completed_idle",
        }
        for idx in range(5)
    )
    healthy = evaluate_alerts(healthy_events, target_ms=1000.0)
    _assert(healthy.passed, f"healthy partial telemetry should not alert: {healthy.alerts!r}")
    _assert(healthy.completed_calls == 5, "alerts should count completed calls")

    bad_events = healthy_events + [
        {
            "event": "slot.repeated_question_detected",
            "call_id": "call-1",
            "categories": ["name"],
        },
        {
            "event": "latency.e2e",
            "call_id": "slow",
            "user_to_first_audio_ms": 3100,
            "breach": True,
        },
        {
            "event": "backend.circuit",
            "endpoint": "/orders",
            "state": "open",
            "failures": 3,
        },
        {
            "event": "backend.queue",
            "call_id": "call-2",
            "endpoint": "/orders",
            "target": "recovery_file",
            "dropped": 2,
        },
        {
            "event": "call.end",
            "call_id": "failed",
            "flow": "delivery",
            "order_confirmed": False,
            "close_reason": "session_error",
        },
    ]
    bad = evaluate_alerts(bad_events, target_ms=1000.0)
    _assert(not bad.passed, "blocking telemetry should fail live alerts")
    _assert(bad.repeated_question_events == 1, "alerts should count repeated questions")
    _assert(bad.latency_breach_events == 1, "alerts should count latency breaches")
    _assert(bad.backend_circuit_open_events == 1, "alerts should count circuit opens")
    _assert(bad.backend_queue_dropped_items == 2, "alerts should count queue drops")
    _assert(bad.failed_calls == 1, "alerts should count failed call outcomes")
    _assert(
        any("backend circuit opened" in alert for alert in bad.alerts),
        "alerts should report backend circuit opens",
    )


def check_qa_matrix_sync() -> None:
    events = [
        {
            "event": "call.end",
            "call_id": "call-1",
            "flow": "delivery",
            "close_reason": "end_call:order_completed",
            "order_confirmed": True,
        },
        {
            "event": "call.end",
            "call_id": "call-2",
            "flow": "complaint",
            "complaint_logged": True,
        },
    ]
    rows, summary = sync_matrix_rows(
        [
            {"call_id": "call-1", "scenarios": "noisy_stt", "audio_reviewed": "true"},
            {"call_id": "", "scenarios": "takeaway", "audio_reviewed": "true"},
        ],
        events,
    )
    _assert(summary["telemetry_completed_calls"] == 2, "sync should count completed calls")
    _assert(summary["added_calls"] == ["call-2"], "sync should append missing telemetry calls")
    by_call = {row["call_id"]: row for row in rows}
    _assert(
        by_call["call-1"]["scenarios"] == "delivery,noisy_stt",
        "sync should add inferred flow without dropping manual scenario labels",
    )
    _assert(
        by_call["call-1"]["audio_reviewed"] == "true",
        "sync should preserve human review status",
    )
    _assert(
        by_call["call-2"]["audio_reviewed"] == "false",
        "sync must not auto-approve newly discovered calls",
    )
    _assert(
        "review audio" in by_call["call-2"]["notes"],
        "sync should leave an explicit human-review note",
    )
    empty_rows, empty_summary = sync_matrix_rows(
        [{"call_id": "", "scenarios": "takeaway", "audio_reviewed": "true"}],
        [],
    )
    _assert(empty_summary["template_rows_preserved"] == 1, "sync should preserve template rows before calls")
    _assert(empty_rows[0]["scenarios"] == "takeaway", "sync should not erase the scenario checklist")

    backup_matrix = ROOT / ".runtime" / "qa" / "qa-call-matrix.csv.bak"
    backup_matrix.parent.mkdir(parents=True, exist_ok=True)
    backup_matrix.write_text(
        "call_id,scenarios,audio_reviewed,notes\ncall-1,delivery,true,backup\n",
        encoding="utf-8",
    )
    try:
        loaded = load_matrix(backup_matrix)
    finally:
        backup_matrix.unlink(missing_ok=True)
    _assert(
        loaded and loaded[0]["call_id"] == "call-1",
        "matrix loader should parse backed-up CSV files such as .csv.bak",
    )


def check_qa_progress_summary() -> None:
    empty = evaluate_progress([], min_calls=50, target_ms=1000.0)
    _assert(empty["completed_calls"] == 0, "progress should report zero completed calls")
    _assert(empty["remaining_calls"] == 50, "progress should report remaining calls")
    _assert(
        any("completed_calls 0 < min_calls 50" in item for item in empty["next_items"]),
        "progress should expose the min-call blocker",
    )

    flows = ("takeaway", "delivery", "reservation", "complaint")
    events: list[dict] = []
    matrix_rows: list[dict] = []
    for idx in range(50):
        flow = flows[idx % len(flows)]
        call_id = f"progress-{idx}"
        events.extend(
            [
                {
                    "event": "latency.e2e",
                    "call_id": call_id,
                    "flow": flow,
                    "user_to_first_audio_ms": 900 + idx,
                    "breach": False,
                },
                {
                    "event": "qa.transcript",
                    "call_id": call_id,
                    "role": "user",
                    "text": "order",
                },
                {
                    "event": "qa.transcript",
                    "call_id": call_id,
                    "role": "assistant",
                    "text": "تمام يا فندم.",
                },
            ]
        )
        end_event = {"event": "call.end", "call_id": call_id, "flow": flow}
        if flow in {"takeaway", "delivery"}:
            end_event["order_confirmed"] = True
        elif flow == "reservation":
            end_event["reservation_confirmed"] = True
        else:
            end_event["complaint_logged"] = True
        events.append(end_event)
        scenarios = [flow]
        if idx < len(DEFAULT_REQUIRED_SCENARIOS):
            scenarios.append(DEFAULT_REQUIRED_SCENARIOS[idx])
        matrix_rows.append(
            {
                "call_id": call_id,
                "scenarios": scenarios,
                "audio_reviewed": True,
            }
        )
    progress = evaluate_progress(
        events,
        min_calls=50,
        target_ms=1000.0,
        matrix_rows=matrix_rows,
    )
    _assert(progress["passed"], f"clean progress batch should pass: {progress!r}")
    _assert(progress["remaining_calls"] == 0, "passing progress should have no remaining calls")
    _assert(progress["missing_scenarios"] == [], "passing progress should have full scenario coverage")


def check_qa_transcript_review() -> None:
    import agent as agent_module

    redacted = agent_module._redact_qa_transcript_text("my phone is 01012345678")
    _assert("[redacted_phone" in redacted, "QA transcript redaction should remove phone numbers")
    spoken_redacted = agent_module._redact_qa_transcript_text("010 123 45678")
    _assert("[redacted_phone" in spoken_redacted, "QA transcript redaction should remove spaced phone numbers")

    clean_events = [
        {"event": "qa.transcript", "call_id": "call-1", "role": "user", "text": _ar(r"\u0639\u0627\u064a\u0632 \u0627\u0637\u0644\u0628 \u0628\u064a\u062a\u0632\u0627")},
        {"event": "qa.transcript", "call_id": "call-1", "role": "assistant", "text": _ar(r"\u062a\u0645\u0627\u0645 \u064a\u0627 \u0641\u0646\u062f\u0645\u060c \u0627\u0644\u0627\u0633\u0645 \u0627\u064a\u0647\u061f")},
        {"event": "qa.transcript", "call_id": "call-1", "role": "user", "text": _ar(r"\u0639\u0644\u064a")},
        {"event": "qa.transcript", "call_id": "call-1", "role": "assistant", "text": _ar(r"\u062a\u0645\u0627\u0645 \u064a\u0627 \u0639\u0644\u064a\u060c \u0627\u0644\u0637\u0644\u0628 \u0635\u062d\u061f")},
    ]
    clean = evaluate_transcript_events(clean_events)
    _assert(clean.passed, f"clean transcript should pass review: {clean.reasons!r}")
    _assert(clean.assistant_turns == 2, "transcript review should count assistant turns")

    bad_events = clean_events + [
        {"event": "qa.transcript", "call_id": "call-1", "role": "assistant", "text": _ar(r"\u062a\u0645\u0627\u0645 \u064a\u0627 \u0641\u0646\u062f\u0645\u060c \u0627\u0644\u0627\u0633\u0645 \u0627\u064a\u0647\u061f")},
        {"event": "qa.transcript", "call_id": "call-1", "role": "assistant", "text": _ar(r"\u062a\u062d\u0628 \u062a\u0637\u0644\u0628 \u0627\u064a\u0647\u061f \u0648\u0627\u0644\u0639\u0646\u0648\u0627\u0646 \u0641\u064a\u0646\u061f \u0648\u0627\u0644\u0627\u0633\u0645 \u0627\u064a\u0647\u061f")},
        {"event": "qa.transcript", "call_id": "call-1", "role": "assistant", "text": " ".join([_ar(r"\u062a\u0645\u0627\u0645")] * 40)},
    ]
    bad = evaluate_transcript_events(bad_events)
    _assert(not bad.passed, "bad transcript should fail review")
    _assert(bad.repeated_assistant_messages == 1, "review should count repeated assistant text")
    _assert(bad.multi_question_assistant_turns == 1, "review should count multi-question turns")
    _assert(bad.long_assistant_turns == 1, "review should count long assistant turns")


def check_market_readiness_gate() -> None:
    market_gate_source = (ROOT / "agent" / "qa_market_gate.py").read_text(encoding="utf-8")
    _assert(
        'parser.add_argument("--matrix", required=True' in market_gate_source,
        "market gate CLI should require the QA call matrix",
    )
    flows = ("takeaway", "delivery", "reservation", "complaint")
    events: list[dict] = []
    for idx in range(50):
        flow = flows[idx % len(flows)]
        call_id = f"market-{idx}"
        events.append(
            {
                "event": "latency.e2e",
                "call_id": call_id,
                "flow": flow,
                "user_to_first_audio_ms": 900 + idx,
                "breach": False,
            }
        )
        end_event = {"event": "call.end", "call_id": call_id, "flow": flow}
        if flow in {"takeaway", "delivery"}:
            end_event["order_confirmed"] = True
        elif flow == "reservation":
            end_event["reservation_confirmed"] = True
        elif flow == "complaint":
            end_event["complaint_logged"] = True
        events.append(end_event)
        events.extend(
            [
                {
                    "event": "qa.transcript",
                    "call_id": call_id,
                    "flow": flow,
                    "role": "user",
                    "text": _ar(r"\u0639\u0627\u064a\u0632 \u062e\u062f\u0645\u0629"),
                },
                {
                    "event": "qa.transcript",
                    "call_id": call_id,
                    "flow": flow,
                    "role": "assistant",
                    "text": _ar(r"\u062a\u0645\u0627\u0645 \u064a\u0627 \u0641\u0646\u062f\u0645\u060c \u0645\u0639\u0627\u0643."),
                },
            ]
        )
    result = evaluate_market_readiness(events, min_calls=50, target_ms=1000.0)
    _assert(result["passed"], f"clean market batch should pass: {result!r}")
    matrix_rows = [
        {
            "call_id": f"market-{idx}",
            "scenarios": [flows[idx % len(flows)]],
            "audio_reviewed": True,
        }
        for idx in range(50)
    ]
    for idx, scenario in enumerate(DEFAULT_REQUIRED_SCENARIOS):
        matrix_rows[idx]["scenarios"].append(scenario)
    matrix_result = evaluate_matrix(matrix_rows, events)
    _assert(matrix_result.passed, f"complete QA matrix should pass: {matrix_result.reasons!r}")
    with_matrix = evaluate_market_readiness(
        events,
        min_calls=50,
        target_ms=1000.0,
        matrix_rows=matrix_rows,
    )
    _assert(with_matrix["passed"], "market gate should pass with a complete matrix")
    _assert(
        with_matrix["call_matrix"]["manifest_calls"] == 50,
        "market gate should expose matrix call count",
    )

    unreviewed_rows = [dict(row) for row in matrix_rows]
    unreviewed_rows[0]["audio_reviewed"] = False
    unreviewed = evaluate_market_readiness(
        events,
        min_calls=50,
        target_ms=1000.0,
        matrix_rows=unreviewed_rows,
    )
    _assert(
        not unreviewed["passed"],
        "market gate should fail when audio review is missing",
    )
    _assert(
        "market-0" in unreviewed["call_matrix"]["audio_unreviewed_calls"],
        "market gate should expose unreviewed call IDs",
    )

    missing_matrix_rows = matrix_rows[1:]
    missing_matrix = evaluate_market_readiness(
        events,
        min_calls=50,
        target_ms=1000.0,
        matrix_rows=missing_matrix_rows,
    )
    _assert(
        not missing_matrix["passed"],
        "market gate should fail when completed telemetry calls are absent from matrix",
    )
    _assert(
        "market-0" in missing_matrix["call_matrix"]["telemetry_calls_missing_from_manifest"],
        "market gate should expose missing matrix call IDs",
    )

    no_transcripts = [
        event for event in events
        if event.get("event") != "qa.transcript"
    ]
    missing_transcript_result = evaluate_market_readiness(
        no_transcripts,
        min_calls=50,
        target_ms=1000.0,
    )
    _assert(
        not missing_transcript_result["passed"],
        "market gate should fail when transcript evidence is missing",
    )
    _assert(
        missing_transcript_result["transcript_review"]["transcript_events"] == 0,
        "market gate should expose missing transcript evidence",
    )

    repeated = events + [
        {
            "event": "slot.repeated_question_detected",
            "call_id": "market-1",
            "categories": ["name"],
        }
    ]
    repeated_result = evaluate_market_readiness(repeated, min_calls=50, target_ms=1000.0)
    _assert(
        not repeated_result["passed"],
        "market gate should fail when repeated questions are present",
    )
    _assert(
        repeated_result["telemetry_gate"]["repeated_question_events"] == 1,
        "market gate should expose repeated question count",
    )


def _scenario_config() -> RestaurantConfig:
    return RestaurantConfig(
        name="QA Restaurant",
        delivery_enabled=True,
        delivery_minutes=35,
        wait_minutes=15,
        min_guests=1,
        max_guests=12,
        branches=[{"name": "main"}],
        menu_items=[
            {"name": "pizza", "price": 120.0, "available": True},
            {"name": "cola", "price": 20.0, "available": True},
        ],
    )


async def check_customer_request_tool_scenarios() -> None:
    import agent as agent_module

    submit_calls: list[str] = []
    fail_next_submit: set[str] = set()

    async def fake_takeaway(ud: UserData) -> dict:
        submit_calls.append("takeaway")
        if "takeaway" in fail_next_submit:
            fail_next_submit.remove("takeaway")
            return None
        _assert(not ud.customer_phone, "takeaway should not require phone")
        return {"order_id": "ORD-1", "estimated_time": 15}

    async def fake_delivery(ud: UserData) -> dict:
        submit_calls.append("delivery")
        if "delivery" in fail_next_submit:
            fail_next_submit.remove("delivery")
            return None
        _assert(not ud.customer_phone, "delivery should not require phone")
        return {"order_id": "ORD-2", "estimated_time": 35}

    async def fake_reservation(ud: UserData) -> dict:
        submit_calls.append("reservation")
        if "reservation" in fail_next_submit:
            fail_next_submit.remove("reservation")
            return None
        return {"reservation_id": "RES-1"}

    async def fake_complaint(ud: UserData, text: str, ctype: str) -> dict:
        submit_calls.append("complaint")
        if "complaint" in fail_next_submit:
            fail_next_submit.remove("complaint")
            return None
        _assert(bool(text), "complaint submit should carry complaint text")
        _assert(bool(ctype), "complaint submit should carry complaint type")
        return {"complaint_id": "CMP-1"}

    originals = {
        "submit_takeaway": agent_module.submit_takeaway,
        "submit_delivery": agent_module.submit_delivery,
        "submit_reservation": agent_module.submit_reservation,
        "submit_complaint": agent_module.submit_complaint,
        "_emit_event": agent_module._emit_event,
    }
    agent_module.submit_takeaway = fake_takeaway
    agent_module.submit_delivery = fake_delivery
    agent_module.submit_reservation = fake_reservation
    agent_module.submit_complaint = fake_complaint
    agent_module._emit_event = lambda *args, **kwargs: None

    try:
        cfg = _scenario_config()

        delivery_disabled = UserData(
            call_id="delivery-disabled",
            restaurant=RestaurantConfig(name="QA", delivery_enabled=False),
        )
        delivery_disabled_ctx = SimpleNamespace(userdata=delivery_disabled)
        delivery_disabled_reply = await restaurant_tools.set_intent.__wrapped__(
            "delivery", delivery_disabled_ctx
        )
        _assert(
            not delivery_disabled.active_flow,
            "disabled delivery should not set active flow",
        )
        _assert(
            delivery_disabled_reply,
            "disabled delivery should return a user-facing fallback",
        )

        unavailable = UserData(
            call_id="unavailable",
            restaurant=RestaurantConfig(
                name="QA",
                menu_items=[{"name": "pizza", "price": 120.0, "available": False}],
            ),
        )
        unavailable_ctx = SimpleNamespace(userdata=unavailable)
        unavailable_reply = await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="pizza", qty=1)], unavailable_ctx
        )
        _assert(not unavailable.order, "unavailable item should not enter order")
        _assert(
            not unavailable.order_validated,
            "unavailable item should not validate order",
        )
        _assert(unavailable_reply, "unavailable item should produce a reply")

        missing = UserData(call_id="missing", restaurant=cfg)
        missing_ctx = SimpleNamespace(userdata=missing)
        await restaurant_tools.set_intent.__wrapped__("takeaway", missing_ctx)
        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="pizza", qty=1)], missing_ctx
        )
        missing_reply = await restaurant_tools.confirm_and_submit.__wrapped__(missing_ctx)
        _assert(missing_reply, "missing-slot validation should return a reply")
        _assert(not missing.order_confirmed, "missing name should block submit")
        _assert(submit_calls == [], "missing-slot validation must not hit backend")

        takeaway = UserData(call_id="takeaway", restaurant=cfg)
        takeaway_ctx = SimpleNamespace(userdata=takeaway)
        await restaurant_tools.set_intent.__wrapped__("takeaway", takeaway_ctx)
        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="pizza", qty=2)], takeaway_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Ali", takeaway_ctx)
        await restaurant_tools.confirm_and_submit.__wrapped__(takeaway_ctx)
        _assert(takeaway.order_confirmed, "takeaway should confirm")
        _assert(takeaway.order_id == "ORD-1", "takeaway should store order id")

        await restaurant_tools.clear_order.__wrapped__(takeaway_ctx)
        _assert(takeaway.order == [], "clear_order should remove existing order")
        _assert(not takeaway.order_confirmed, "clear_order should reset confirmation")

        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="cola", qty=1)], takeaway_ctx
        )
        _assert(takeaway.order == ["cola"], "order change should accept new item")
        _assert(takeaway.order_total == 20.0, "order change should recalculate total")
        await restaurant_tools.confirm_and_submit.__wrapped__(takeaway_ctx)
        _assert(
            submit_calls.count("takeaway") == 2,
            "changed takeaway order should submit once after reconfirmation",
        )
        await restaurant_tools.confirm_and_submit.__wrapped__(takeaway_ctx)
        _assert(
            submit_calls.count("takeaway") == 2,
            "duplicate takeaway confirmation must not submit twice",
        )

        min_order = UserData(
            call_id="min-order",
            restaurant=RestaurantConfig(
                name="QA",
                delivery_enabled=True,
                min_order=100.0,
                menu_items=[{"name": "cola", "price": 20.0, "available": True}],
            ),
        )
        min_order_ctx = SimpleNamespace(userdata=min_order)
        await restaurant_tools.set_intent.__wrapped__("delivery", min_order_ctx)
        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="cola", qty=1)], min_order_ctx
        )
        await restaurant_tools.set_delivery_info.__wrapped__(
            "12 Market Street", "", min_order_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Nour", min_order_ctx)
        min_order_reply = await restaurant_tools.confirm_and_submit.__wrapped__(
            min_order_ctx
        )
        _assert(not min_order.order_confirmed, "min order should block delivery")
        _assert(
            submit_calls.count("delivery") == 0,
            "min order failure must not hit delivery backend",
        )
        _assert(min_order_reply, "min order failure should produce a reply")

        delivery = UserData(call_id="delivery", restaurant=cfg)
        delivery_ctx = SimpleNamespace(userdata=delivery)
        await restaurant_tools.set_intent.__wrapped__("delivery", delivery_ctx)
        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="cola", qty=3)], delivery_ctx
        )
        await restaurant_tools.set_delivery_info.__wrapped__(
            "12 Market Street", "", delivery_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Mona", delivery_ctx)
        await restaurant_tools.confirm_and_submit.__wrapped__(delivery_ctx)
        _assert(delivery.order_confirmed, "delivery should confirm")
        _assert(delivery.delivery_address, "delivery should keep address")
        await restaurant_tools.confirm_and_submit.__wrapped__(delivery_ctx)
        _assert(
            submit_calls.count("delivery") == 1,
            "duplicate delivery confirmation must not submit twice",
        )

        branch_default_cfg = _scenario_config()
        branch_default_cfg.branches = [{"name": "A"}, {"name": "B"}]
        branch_default = UserData(call_id="branch-default", restaurant=branch_default_cfg)
        branch_default_ctx = SimpleNamespace(userdata=branch_default)
        await restaurant_tools.set_intent.__wrapped__("reservation", branch_default_ctx)
        await restaurant_tools.set_reservation_info.__wrapped__(
            "tomorrow 8 pm", 4, "", branch_default_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Laila", branch_default_ctx)
        branch_reply = await restaurant_tools.confirm_and_submit.__wrapped__(
            branch_default_ctx
        )
        _assert(
            branch_default.selected_branch == "A",
            "multi-branch reservation should default to first branch",
        )
        _assert(
            branch_default.reservation_confirmed,
            "defaulted multi-branch reservation should confirm",
        )
        _assert(
            submit_calls.count("reservation") == 1,
            "defaulted reservation should hit reservation backend once",
        )
        _assert(branch_reply, "defaulted branch should produce a reply")

        reservation = UserData(call_id="reservation", restaurant=cfg)
        reservation_ctx = SimpleNamespace(userdata=reservation)
        await restaurant_tools.set_intent.__wrapped__("reservation", reservation_ctx)
        await restaurant_tools.set_reservation_info.__wrapped__(
            "tomorrow 8 pm", 4, "", reservation_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Sara", reservation_ctx)
        await restaurant_tools.confirm_and_submit.__wrapped__(reservation_ctx)
        _assert(reservation.reservation_confirmed, "reservation should confirm")
        _assert(reservation.reservation_id == "RES-1", "reservation should store id")
        await restaurant_tools.confirm_and_submit.__wrapped__(reservation_ctx)
        _assert(
            submit_calls.count("reservation") == 2,
            "duplicate reservation confirmation must not submit twice",
        )

        complaint = UserData(call_id="complaint", restaurant=cfg)
        complaint_ctx = SimpleNamespace(userdata=complaint)
        await restaurant_tools.set_intent.__wrapped__("complaint", complaint_ctx)
        await restaurant_tools.set_complaint.__wrapped__(
            "food arrived cold", "quality", complaint_ctx
        )
        await restaurant_tools.confirm_and_submit.__wrapped__(complaint_ctx)
        _assert(complaint.complaint_logged, "complaint should log")
        await restaurant_tools.confirm_and_submit.__wrapped__(complaint_ctx)
        _assert(
            submit_calls.count("complaint") == 1,
            "duplicate complaint confirmation must not submit twice",
        )

        backend_failure = UserData(call_id="backend-failure", restaurant=cfg)
        backend_failure_ctx = SimpleNamespace(userdata=backend_failure)
        await restaurant_tools.set_intent.__wrapped__("takeaway", backend_failure_ctx)
        await restaurant_tools.update_order.__wrapped__(
            [restaurant_tools.OrderItemSpec(name="pizza", qty=1)], backend_failure_ctx
        )
        await restaurant_tools.set_name.__wrapped__("Omar", backend_failure_ctx)
        fail_next_submit.add("takeaway")
        failure_reply = await restaurant_tools.confirm_and_submit.__wrapped__(
            backend_failure_ctx
        )
        _assert(
            not backend_failure.order_confirmed,
            "backend failure must not mark order confirmed",
        )
        _assert(failure_reply, "backend failure should produce a fallback reply")

        _assert(
            submit_calls == [
                "takeaway",
                "takeaway",
                "delivery",
                "reservation",
                "reservation",
                "complaint",
                "takeaway",
            ],
            f"unexpected backend submit calls: {submit_calls!r}",
        )
    finally:
        for name, value in originals.items():
            setattr(agent_module, name, value)


def main() -> int:
    checks = [
        check_slot_question_classifier,
        check_repeated_question_tracking,
        check_explicit_cancel_or_done_handling,
        check_state_snapshot_includes_asked_once,
        check_latency_slo_wiring,
        check_dotenv_loaded_before_telemetry_setup,
        check_hamsa_prewarm_matches_livekit_adapter,
        check_env_example_has_qa_settings,
        check_backend_env_example_has_prod_settings,
        check_service_preflight_checks_demo_session,
        check_idle_health_report_is_ready,
        check_provider_warmup_prefix_stripping,
        check_qa_telemetry_gate,
        check_live_qa_preflight,
        check_live_qa_alerts,
        check_qa_matrix_sync,
        check_qa_progress_summary,
        check_qa_transcript_review,
        check_market_readiness_gate,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    asyncio.run(check_customer_request_tool_scenarios())
    print("PASS check_customer_request_tool_scenarios")
    print(f"PASS {len(checks) + 1} production readiness checks")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)

