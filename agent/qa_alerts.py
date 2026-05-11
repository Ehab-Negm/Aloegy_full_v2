"""Operational alerts for live voice-agent QA telemetry.

Use this during the 50-call batch to catch market-blocking issues early. The
final release decision still belongs to `qa_telemetry_gate.py`, which enforces
completed-call count, required flow coverage, and p95 latency.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from qa_telemetry_gate import DEFAULT_TARGET_MS, _p95, load_telemetry_events


@dataclass
class AlertReport:
    passed: bool
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_events: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    repeated_question_events: int = 0
    latency_breach_events: int = 0
    backend_circuit_open_events: int = 0
    backend_queue_events: int = 0
    backend_queue_dropped_items: int = 0
    p95_latency_ms: float | None = None


def _call_end_success(event: dict[str, Any]) -> bool:
    return bool(
        event.get("order_confirmed")
        or event.get("reservation_confirmed")
        or event.get("complaint_logged")
    )


def evaluate_alerts(
    events: Iterable[dict[str, Any]],
    *,
    target_ms: float = DEFAULT_TARGET_MS,
) -> AlertReport:
    materialized = list(events)
    call_end_events = [
        event for event in materialized
        if event.get("event") == "call.end"
    ]
    failed_call_events = [
        event for event in call_end_events
        if not _call_end_success(event)
        and str(event.get("close_reason") or "").strip() not in {
            "end_call:cancelled",
            "end_call:customer_done",
        }
    ]
    repeated_events = [
        event for event in materialized
        if event.get("event") == "slot.repeated_question_detected"
    ]
    latency_breach_events = [
        event for event in materialized
        if event.get("event") == "latency.e2e" and bool(event.get("breach"))
    ]
    latencies = [
        float(event["user_to_first_audio_ms"])
        for event in materialized
        if event.get("event") == "latency.e2e"
        and isinstance(event.get("user_to_first_audio_ms"), (int, float))
    ]
    circuit_open_events = [
        event for event in materialized
        if event.get("event") == "backend.circuit"
        and str(event.get("state") or "").lower() == "open"
    ]
    queue_events = [
        event for event in materialized
        if event.get("event") == "backend.queue"
    ]
    queue_dropped = sum(
        int(event.get("dropped") or 0)
        for event in queue_events
        if isinstance(event.get("dropped") or 0, int)
    )
    p95_latency = _p95(latencies)

    alerts: list[str] = []
    warnings: list[str] = []
    if repeated_events:
        alerts.append(f"repeated slot questions detected: {len(repeated_events)}")
    if latency_breach_events:
        alerts.append(f"first-audio latency breach events: {len(latency_breach_events)}")
    if p95_latency is not None and p95_latency > target_ms:
        alerts.append(f"current p95 first-audio latency {p95_latency:.0f}ms > target {target_ms:.0f}ms")
    if circuit_open_events:
        alerts.append(f"backend circuit opened: {len(circuit_open_events)} event(s)")
    if queue_dropped:
        alerts.append(f"backend queue dropped items: {queue_dropped}")
    if failed_call_events:
        warnings.append(f"calls ended without successful outcome: {len(failed_call_events)}")
    if queue_events:
        warnings.append(f"backend writes queued: {len(queue_events)} event(s)")
    if not call_end_events:
        warnings.append("no completed call.end events observed yet")
    if not latencies:
        warnings.append("no latency.e2e events observed yet")

    return AlertReport(
        passed=not alerts,
        alerts=alerts,
        warnings=warnings,
        total_events=len(materialized),
        completed_calls=len({
            str(event.get("call_id") or "")
            for event in call_end_events
            if str(event.get("call_id") or "").strip()
        }),
        failed_calls=len(failed_call_events),
        repeated_question_events=len(repeated_events),
        latency_breach_events=len(latency_breach_events),
        backend_circuit_open_events=len(circuit_open_events),
        backend_queue_events=len(queue_events),
        backend_queue_dropped_items=queue_dropped,
        p95_latency_ms=p95_latency,
    )


def _render_report(report: AlertReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "alerts": report.alerts,
        "warnings": report.warnings,
        "total_events": report.total_events,
        "completed_calls": report.completed_calls,
        "failed_calls": report.failed_calls,
        "repeated_question_events": report.repeated_question_events,
        "latency_breach_events": report.latency_breach_events,
        "backend_circuit_open_events": report.backend_circuit_open_events,
        "backend_queue_events": report.backend_queue_events,
        "backend_queue_dropped_items": report.backend_queue_dropped_items,
        "p95_latency_ms": report.p95_latency_ms,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate live QA telemetry alerts.")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS)
    parser.add_argument("--watch", action="store_true", help="Poll the telemetry file until interrupted")
    parser.add_argument("--interval", type=float, default=5.0, help="Watch interval in seconds")
    return parser


def _evaluate_path(path: Path, *, target_ms: float) -> AlertReport:
    if not path.exists():
        return AlertReport(
            passed=False,
            alerts=[f"telemetry file not found: {path}"],
        )
    return evaluate_alerts(load_telemetry_events(path), target_ms=target_ms)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    exit_code = 0
    while True:
        report = _evaluate_path(args.telemetry, target_ms=args.target_ms)
        print(json.dumps(_render_report(report), ensure_ascii=False, indent=2))
        exit_code = 0 if report.passed else 1
        if not args.watch:
            return exit_code
        time.sleep(max(1.0, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
