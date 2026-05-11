"""Single market-readiness gate for a collected QA telemetry batch."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from qa_alerts import evaluate_alerts
from qa_call_matrix import DEFAULT_REQUIRED_SCENARIOS, evaluate_matrix, load_matrix
from qa_telemetry_gate import DEFAULT_REQUIRED_FLOWS, DEFAULT_TARGET_MS, evaluate_events, load_telemetry_events
from qa_transcript_review import evaluate_transcript_events


def _parse_flows(value: str) -> list[str]:
    return [flow.strip() for flow in str(value or "").split(",") if flow.strip()]


def evaluate_market_readiness(
    events: Iterable[dict[str, Any]],
    *,
    min_calls: int = 50,
    target_ms: float = DEFAULT_TARGET_MS,
    required_flows: Iterable[str] = DEFAULT_REQUIRED_FLOWS,
    matrix_rows: Iterable[dict[str, Any]] | None = None,
    required_scenarios: Iterable[str] = DEFAULT_REQUIRED_SCENARIOS,
    max_assistant_words: int = 35,
    max_questions_per_turn: int = 1,
) -> dict[str, Any]:
    materialized = list(events)
    telemetry_gate = evaluate_events(
        materialized,
        min_calls=min_calls,
        target_ms=target_ms,
        required_flows=required_flows,
    )
    alerts = evaluate_alerts(materialized, target_ms=target_ms)
    transcript_review = evaluate_transcript_events(
        materialized,
        max_assistant_words=max_assistant_words,
        max_questions_per_turn=max_questions_per_turn,
    )
    matrix_result = None
    if matrix_rows is not None:
        matrix_result = evaluate_matrix(
            matrix_rows,
            materialized,
            required_scenarios=required_scenarios,
        )
    passed = (
        telemetry_gate.passed
        and alerts.passed
        and transcript_review.passed
        and (matrix_result is None or matrix_result.passed)
    )
    result = {
        "passed": passed,
        "telemetry_gate": asdict(telemetry_gate),
        "alerts": asdict(alerts),
        "transcript_review": asdict(transcript_review),
    }
    if matrix_result is not None:
        result["call_matrix"] = asdict(matrix_result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full market-readiness QA gate.")
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--min-calls", type=int, default=50)
    parser.add_argument("--target-ms", type=float, default=DEFAULT_TARGET_MS)
    parser.add_argument(
        "--require-flows",
        default=",".join(DEFAULT_REQUIRED_FLOWS),
        help="Comma-separated required call.end flows",
    )
    parser.add_argument("--max-assistant-words", type=int, default=35)
    parser.add_argument("--max-questions-per-turn", type=int, default=1)
    parser.add_argument("--matrix", required=True, type=Path, help="JSON/CSV QA call matrix")
    parser.add_argument(
        "--require-scenarios",
        default=",".join(DEFAULT_REQUIRED_SCENARIOS),
        help="Comma-separated required scenario names when --matrix is provided",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    missing_reasons: list[str] = []
    if not args.telemetry.exists():
        missing_reasons.append(f"telemetry file not found: {args.telemetry}")
    if not args.matrix.exists():
        missing_reasons.append(f"matrix file not found: {args.matrix}")
    if missing_reasons:
        result = {
            "passed": False,
            "reasons": missing_reasons,
        }
    else:
        result = evaluate_market_readiness(
            load_telemetry_events(args.telemetry),
            min_calls=args.min_calls,
            target_ms=args.target_ms,
            required_flows=_parse_flows(args.require_flows),
            matrix_rows=load_matrix(args.matrix) if args.matrix is not None else None,
            required_scenarios=_parse_flows(args.require_scenarios),
            max_assistant_words=args.max_assistant_words,
            max_questions_per_turn=args.max_questions_per_turn,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
