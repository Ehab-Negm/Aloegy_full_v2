"""QA gate for live/recorded voice-agent telemetry.

Usage:
    python qa_telemetry_gate.py --telemetry .runtime/prod/telemetry.log --min-calls 50

The input may be raw JSONL events or normal log lines that contain the JSON
payload emitted by `restaurant.telemetry`, for example:

    2026-... | INFO | restaurant.telemetry | {"event":"latency.e2e", ...}

The gate fails when:
- fewer than `--min-calls` completed calls (`call.end`) are present;
- required flows are not represented by `call.end` events;
- required flows do not have at least one successful `call.end` outcome;
- any `slot.repeated_question_detected` event exists;
- any `latency.e2e` breach event exists;
- p95 `latency.e2e.user_to_first_audio_ms` is above `--target-ms`.
- p95 first-audio latency for any required flow is above `--target-ms`.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


DEFAULT_TARGET_MS = 1000.0
DEFAULT_REQUIRED_FLOWS = ("takeaway", "delivery", "reservation", "complaint")


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    total_events: int = 0
    calls_seen: int = 0
    completed_calls: int = 0
    latency_events: int = 0
    repeated_question_events: int = 0
    latency_breach_events: int = 0
    required_flows: list[str] = field(default_factory=list)
    flows_seen: list[str] = field(default_factory=list)
    missing_flows: list[str] = field(default_factory=list)
    successful_flows: list[str] = field(default_factory=list)
    missing_successful_flows: list[str] = field(default_factory=list)
    p95_latency_ms: float | None = None
    per_flow_p95_latency_ms: dict[str, float] = field(default_factory=dict)
    missing_latency_flows: list[str] = field(default_factory=list)
    slow_latency_flows: dict[str, float] = field(default_factory=dict)


def _extract_json_object(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    candidates = [text]
    brace_index = text.find("{")
    if brace_index > 0:
        candidates.append(text[brace_index:])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def load_telemetry_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            event = _extract_json_object(line)
            if event is not None:
                events.append(event)
    return events


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _call_end_successful_for_flow(event: dict[str, Any], flow: str) -> bool:
    if flow in {"takeaway", "delivery"}:
        return bool(event.get("order_confirmed"))
    if flow == "reservation":
        return bool(event.get("reservation_confirmed"))
    if flow == "complaint":
        return bool(event.get("complaint_logged"))
    return False


def evaluate_events(
    events: Iterable[dict[str, Any]],
    *,
    min_calls: int,
    target_ms: float = DEFAULT_TARGET_MS,
    required_flows: Iterable[str] = DEFAULT_REQUIRED_FLOWS,
) -> GateResult:
    materialized = list(events)
    required_flow_set = {
        str(flow).strip().lower()
        for flow in required_flows
        if str(flow).strip()
    }
    completed_call_ids = {
        str(event.get("call_id") or "")
        for event in materialized
        if event.get("event") == "call.end"
        and str(event.get("call_id") or "").strip()
    }
    flows_seen = {
        str(event.get("flow") or "").strip().lower()
        for event in materialized
        if event.get("event") == "call.end"
        and str(event.get("flow") or "").strip()
    }
    successful_flows = {
        flow
        for event in materialized
        if event.get("event") == "call.end"
        for flow in [str(event.get("flow") or "").strip().lower()]
        if flow and _call_end_successful_for_flow(event, flow)
    }
    call_flow_by_id = {
        str(event.get("call_id") or ""): str(event.get("flow") or "").strip().lower()
        for event in materialized
        if event.get("event") == "call.end"
        and str(event.get("call_id") or "").strip()
        and str(event.get("flow") or "").strip()
    }
    latencies = [
        float(event["user_to_first_audio_ms"])
        for event in materialized
        if event.get("event") == "latency.e2e"
        and isinstance(event.get("user_to_first_audio_ms"), (int, float))
    ]
    per_flow_latencies: dict[str, list[float]] = {}
    for event in materialized:
        if event.get("event") != "latency.e2e":
            continue
        latency_value = event.get("user_to_first_audio_ms")
        if not isinstance(latency_value, (int, float)):
            continue
        flow = str(event.get("flow") or "").strip().lower()
        if not flow:
            flow = call_flow_by_id.get(str(event.get("call_id") or ""), "")
        if flow:
            per_flow_latencies.setdefault(flow, []).append(float(latency_value))
    repeated = [
        event for event in materialized
        if event.get("event") == "slot.repeated_question_detected"
    ]
    explicit_breaches = [
        event for event in materialized
        if event.get("event") == "latency.e2e" and bool(event.get("breach"))
    ]
    p95_latency = _p95(latencies)

    reasons: list[str] = []
    if len(completed_call_ids) < min_calls:
        reasons.append(f"completed_calls {len(completed_call_ids)} < min_calls {min_calls}")
    missing_flows = sorted(required_flow_set - flows_seen)
    if missing_flows:
        reasons.append(f"missing required flows: {', '.join(missing_flows)}")
    missing_successful_flows = sorted(required_flow_set - successful_flows)
    if missing_successful_flows:
        reasons.append(
            f"missing successful required flows: {', '.join(missing_successful_flows)}"
        )
    if not latencies:
        reasons.append("no latency.e2e events found")
    if repeated:
        reasons.append(f"repeated slot question events found: {len(repeated)}")
    if explicit_breaches:
        reasons.append(f"explicit latency breach events found: {len(explicit_breaches)}")
    if p95_latency is not None and p95_latency > target_ms:
        reasons.append(f"p95 latency {p95_latency:.0f}ms > target {target_ms:.0f}ms")
    per_flow_p95 = {
        flow: latency
        for flow, values in per_flow_latencies.items()
        for latency in [_p95(values)]
        if latency is not None
    }
    missing_latency_flows = sorted(required_flow_set - set(per_flow_p95))
    if missing_latency_flows:
        reasons.append(f"missing latency for required flows: {', '.join(missing_latency_flows)}")
    slow_latency_flows = {
        flow: latency
        for flow, latency in per_flow_p95.items()
        if flow in required_flow_set and latency > target_ms
    }
    if slow_latency_flows:
        rendered = ", ".join(
            f"{flow}={latency:.0f}ms" for flow, latency in sorted(slow_latency_flows.items())
        )
        reasons.append(f"per-flow p95 latency above target: {rendered}")

    return GateResult(
        passed=not reasons,
        reasons=reasons,
        total_events=len(materialized),
        calls_seen=len(completed_call_ids),
        completed_calls=len(completed_call_ids),
        latency_events=len(latencies),
        repeated_question_events=len(repeated),
        latency_breach_events=len(explicit_breaches),
        required_flows=sorted(required_flow_set),
        flows_seen=sorted(flows_seen),
        missing_flows=missing_flows,
        successful_flows=sorted(successful_flows),
        missing_successful_flows=missing_successful_flows,
        p95_latency_ms=p95_latency,
        per_flow_p95_latency_ms={flow: round(latency, 3) for flow, latency in sorted(per_flow_p95.items())},
        missing_latency_flows=missing_latency_flows,
        slow_latency_flows={flow: round(latency, 3) for flow, latency in sorted(slow_latency_flows.items())},
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate voice-agent telemetry QA gates.")
    parser.add_argument("--telemetry", required=True, type=Path, help="Telemetry log/JSONL path")
    parser.add_argument("--min-calls", type=int, default=50, help="Minimum unique calls required")
    parser.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS, help="p95 first-audio target")
    parser.add_argument(
        "--require-flows",
        default=",".join(DEFAULT_REQUIRED_FLOWS),
        help="Comma-separated call.end flows that must be represented; use empty string to disable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.telemetry.exists():
        summary = {
            "passed": False,
            "total_events": 0,
            "calls_seen": 0,
            "completed_calls": 0,
            "latency_events": 0,
            "repeated_question_events": 0,
            "latency_breach_events": 0,
            "required_flows": [],
            "flows_seen": [],
            "missing_flows": [],
            "successful_flows": [],
            "missing_successful_flows": [],
            "p95_latency_ms": None,
            "per_flow_p95_latency_ms": {},
            "missing_latency_flows": [],
            "slow_latency_flows": {},
            "reasons": [f"telemetry file not found: {args.telemetry}"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1

    events = load_telemetry_events(args.telemetry)
    required_flows = [
        flow.strip()
        for flow in str(args.require_flows or "").split(",")
        if flow.strip()
    ]
    result = evaluate_events(
        events,
        min_calls=args.min_calls,
        target_ms=args.target_ms,
        required_flows=required_flows,
    )
    summary = {
        "passed": result.passed,
        "total_events": result.total_events,
        "calls_seen": result.calls_seen,
        "completed_calls": result.completed_calls,
        "latency_events": result.latency_events,
        "repeated_question_events": result.repeated_question_events,
        "latency_breach_events": result.latency_breach_events,
        "required_flows": result.required_flows,
        "flows_seen": result.flows_seen,
        "missing_flows": result.missing_flows,
        "successful_flows": result.successful_flows,
        "missing_successful_flows": result.missing_successful_flows,
        "p95_latency_ms": result.p95_latency_ms,
        "per_flow_p95_latency_ms": result.per_flow_p95_latency_ms,
        "missing_latency_flows": result.missing_latency_flows,
        "slow_latency_flows": result.slow_latency_flows,
        "reasons": result.reasons,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
