"""Preflight checks before running a 50-call live QA batch.

This script does not call providers or the backend by default. It verifies that
the worker environment has the credentials and telemetry path needed to collect
evidence for `qa_telemetry_gate.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dependency is present in the agent env.
    load_dotenv = None

try:
    from qa_telemetry_gate import DEFAULT_REQUIRED_FLOWS, DEFAULT_TARGET_MS, evaluate_events, load_telemetry_events
except Exception:  # pragma: no cover - keeps --help usable if imports are broken.
    DEFAULT_REQUIRED_FLOWS = ("takeaway", "delivery", "reservation", "complaint")
    DEFAULT_TARGET_MS = 1000.0
    evaluate_events = None
    load_telemetry_events = None


REQUIRED_INFRA_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "BACKEND_BASE_URL",
    "BACKEND_API_KEY",
)


@dataclass
class PreflightReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)


def _looks_like_placeholder_secret(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        return True
    placeholder_tokens = {
        "mock",
        "placeholder",
        "changeme",
        "change_me",
        "secret",
        "your",
        "example",
        "test",
        "todo",
    }
    if normalized in {"mock_secret_key", "your_backend_api_key"}:
        return True
    return any(token in normalized for token in placeholder_tokens)


def _required_llm_key(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("cerebras/"):
        return "CEREBRAS_API_KEY"
    if normalized.startswith("groq/"):
        return "GROQ_API_KEY"
    if "/" in normalized:
        return "OPENROUTER_API_KEY"
    if normalized.startswith(("gpt-", "o1", "o3", "o4", "o5")):
        return "OPENAI_API_KEY"
    return "GOOGLE_API_KEY"


def _required_tts_key(model: str) -> str:
    normalized = model.strip().lower()
    if normalized.startswith("xai/"):
        return "XAI_API_KEY"
    if normalized.startswith("gemini") or "chirp" in normalized or normalized.startswith("cloud"):
        return "GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS"
    return "HAMSA_API_KEY"


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _env_group_present(group: str) -> bool:
    return any(_env_present(part.strip()) for part in group.split(" or "))


def _check_env_var(report: PreflightReport, name: str, *, placeholder_is_error: bool = True) -> None:
    value = os.getenv(name, "").strip()
    report.checks.setdefault("env", {})[name] = bool(value)
    if not value:
        report.errors.append(f"{name} is missing")
        return
    if placeholder_is_error and _looks_like_placeholder_secret(value):
        report.errors.append(f"{name} looks like a placeholder")


def _check_env_group(report: PreflightReport, group: str) -> None:
    if _env_group_present(group):
        report.checks.setdefault("env", {})[group] = True
        return
    report.checks.setdefault("env", {})[group] = False
    report.errors.append(f"{group} is missing")


def _check_telemetry(report: PreflightReport, telemetry_path: Path, *, min_calls: int, target_ms: float) -> None:
    report.checks["telemetry_path"] = str(telemetry_path)
    parent = telemetry_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    write_test = parent / f".qa-preflight-write-{uuid.uuid4().hex}.tmp"
    try:
        write_test.write_text("ok", encoding="utf-8")
        write_test.unlink()
        report.checks["telemetry_parent_writable"] = True
    except Exception as exc:
        report.checks["telemetry_parent_writable"] = False
        report.errors.append(f"telemetry directory is not writable: {parent} ({exc})")

    if not telemetry_path.exists():
        report.checks["telemetry_exists"] = False
        report.warnings.append(
            "telemetry file does not exist yet; start the agent with TELEMETRY_LOG_PATH before the QA batch"
        )
        return

    report.checks["telemetry_exists"] = True
    report.checks["telemetry_size_bytes"] = telemetry_path.stat().st_size
    if evaluate_events is None or load_telemetry_events is None:
        report.warnings.append("qa_telemetry_gate import failed; telemetry progress was not evaluated")
        return
    events = load_telemetry_events(telemetry_path)
    result = evaluate_events(events, min_calls=min_calls, target_ms=target_ms)
    report.checks["telemetry_progress"] = {
        "passed_gate": result.passed,
        "completed_calls": result.completed_calls,
        "latency_events": result.latency_events,
        "flows_seen": result.flows_seen,
        "p95_latency_ms": result.p95_latency_ms,
        "repeated_question_events": result.repeated_question_events,
        "reasons": result.reasons,
    }


def run_preflight(*, telemetry_path: Path, min_calls: int, target_ms: float) -> PreflightReport:
    if load_dotenv is not None:
        load_dotenv(Path(__file__).with_name(".env"))

    report = PreflightReport(passed=True)
    app_env = os.getenv("APP_ENV", "dev").strip().lower() or "dev"
    report.checks["app_env"] = app_env
    if app_env != "prod":
        report.warnings.append(f"APP_ENV is {app_env!r}; use APP_ENV=prod for market QA")

    for name in REQUIRED_INFRA_ENV:
        _check_env_var(report, name)

    _check_env_var(report, "SONIOX_API_KEY")
    llm_model = os.getenv("SESSION_LLM_MODEL", "gemini-2.5-flash").strip()
    tts_model = os.getenv("SESSION_TTS_MODEL", "gemini-3.1-flash-tts-preview").strip()
    report.checks["session_llm_model"] = llm_model
    report.checks["session_tts_model"] = tts_model
    _check_env_group(report, _required_llm_key(llm_model))
    _check_env_group(report, _required_tts_key(tts_model))

    streaming_tts = os.getenv("SESSION_TTS_STREAMING_ENABLED", "true").strip().lower()
    report.checks["session_tts_streaming_enabled"] = streaming_tts
    if streaming_tts not in {"1", "true", "yes"}:
        report.warnings.append("SESSION_TTS_STREAMING_ENABLED is not true; first-audio latency may miss 1s")

    preemptive = os.getenv("SESSION_PREEMPTIVE_GENERATION", "false").strip().lower()
    report.checks["session_preemptive_generation"] = preemptive
    if preemptive not in {"1", "true", "yes"}:
        report.warnings.append("SESSION_PREEMPTIVE_GENERATION is not true; verify p95 latency carefully")

    telemetry_env = os.getenv("TELEMETRY_LOG_PATH", "").strip()
    report.checks["telemetry_log_path_env_set"] = bool(telemetry_env)
    if not telemetry_env:
        report.warnings.append("TELEMETRY_LOG_PATH is not set in the current environment")

    transcript_events = os.getenv("QA_TRANSCRIPT_EVENTS_ENABLED", "false").strip().lower()
    report.checks["qa_transcript_events_enabled"] = transcript_events
    if transcript_events not in {"1", "true", "yes"}:
        report.warnings.append(
            "QA_TRANSCRIPT_EVENTS_ENABLED is not true; transcript review will not have evidence"
        )
    _check_telemetry(report, telemetry_path, min_calls=min_calls, target_ms=target_ms)

    report.passed = not report.errors
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preflight a live voice-agent QA batch.")
    parser.add_argument(
        "--telemetry",
        type=Path,
        default=Path(".runtime/prod/telemetry.jsonl"),
        help="Telemetry JSONL path that the agent will write during QA",
    )
    parser.add_argument("--min-calls", type=int, default=50)
    parser.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only JSON output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = run_preflight(
        telemetry_path=args.telemetry,
        min_calls=args.min_calls,
        target_ms=args.target_ms,
    )
    payload = {
        "passed": report.passed,
        "errors": report.errors,
        "warnings": report.warnings,
        "checks": report.checks,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if report.passed:
            print("\nPreflight passed. Collect the QA batch, then run qa_telemetry_gate.py.")
        else:
            print("\nPreflight failed. Fix errors before collecting the QA batch.")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
