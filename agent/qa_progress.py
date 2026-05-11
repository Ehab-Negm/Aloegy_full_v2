"""Compact progress report for the live market-readiness QA batch."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from qa_call_matrix import DEFAULT_REQUIRED_SCENARIOS, evaluate_matrix, load_matrix
from qa_telemetry_gate import DEFAULT_REQUIRED_FLOWS, DEFAULT_TARGET_MS, evaluate_events, load_telemetry_events
from qa_transcript_review import evaluate_transcript_events


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _next_items(
    *,
    telemetry: dict[str, Any],
    transcript: dict[str, Any],
    matrix: dict[str, Any] | None,
) -> list[str]:
    items: list[str] = []
    for reason in telemetry.get("reasons", []):
        items.append(str(reason))
    for reason in transcript.get("reasons", []):
        items.append(str(reason))
    if matrix is not None:
        for reason in matrix.get("reasons", []):
            items.append(str(reason))
    return items[:12]


def evaluate_progress(
    events: Iterable[dict[str, Any]],
    *,
    min_calls: int = 50,
    target_ms: float = DEFAULT_TARGET_MS,
    required_flows: Iterable[str] = DEFAULT_REQUIRED_FLOWS,
    matrix_rows: Iterable[dict[str, Any]] | None = None,
    required_scenarios: Iterable[str] = DEFAULT_REQUIRED_SCENARIOS,
) -> dict[str, Any]:
    materialized = list(events)
    telemetry_result = evaluate_events(
        materialized,
        min_calls=min_calls,
        target_ms=target_ms,
        required_flows=required_flows,
    )
    transcript_result = evaluate_transcript_events(materialized)
    matrix_result = (
        evaluate_matrix(matrix_rows, materialized, required_scenarios=required_scenarios)
        if matrix_rows is not None
        else None
    )
    telemetry = asdict(telemetry_result)
    transcript = asdict(transcript_result)
    matrix = asdict(matrix_result) if matrix_result is not None else None
    completed_calls = int(telemetry.get("completed_calls") or 0)
    progress = {
        "passed": bool(
            telemetry_result.passed
            and transcript_result.passed
            and (matrix_result is None or matrix_result.passed)
        ),
        "completed_calls": completed_calls,
        "min_calls": min_calls,
        "remaining_calls": max(0, min_calls - completed_calls),
        "flows_seen": telemetry.get("flows_seen", []),
        "missing_flows": telemetry.get("missing_flows", []),
        "successful_flows": telemetry.get("successful_flows", []),
        "missing_successful_flows": telemetry.get("missing_successful_flows", []),
        "p95_latency_ms": telemetry.get("p95_latency_ms"),
        "slow_latency_flows": telemetry.get("slow_latency_flows", {}),
        "repeated_question_events": telemetry.get("repeated_question_events", 0),
        "latency_breach_events": telemetry.get("latency_breach_events", 0),
        "transcript_events": transcript.get("transcript_events", 0),
        "transcript_passed": transcript.get("passed", False),
        "matrix_calls": matrix.get("manifest_calls", 0) if matrix else None,
        "missing_scenarios": matrix.get("missing_scenarios", []) if matrix else None,
        "audio_unreviewed_calls": matrix.get("audio_unreviewed_calls", []) if matrix else None,
    }
    progress["next_items"] = _next_items(
        telemetry=telemetry,
        transcript=transcript,
        matrix=matrix,
    )
    return progress


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show compact live QA progress.")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--min-calls", type=int, default=50)
    parser.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS)
    parser.add_argument("--require-flows", default=",".join(DEFAULT_REQUIRED_FLOWS))
    parser.add_argument("--require-scenarios", default=",".join(DEFAULT_REQUIRED_SCENARIOS))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.telemetry.exists():
        result = {
            "passed": False,
            "next_items": [f"telemetry file not found: {args.telemetry}"],
        }
    else:
        matrix_rows = load_matrix(args.matrix) if args.matrix and args.matrix.exists() else None
        result = evaluate_progress(
            load_telemetry_events(args.telemetry),
            min_calls=args.min_calls,
            target_ms=args.target_ms,
            required_flows=_parse_csv(args.require_flows),
            matrix_rows=matrix_rows,
            required_scenarios=_parse_csv(args.require_scenarios),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
