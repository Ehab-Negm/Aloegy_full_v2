"""Validate scenario coverage for a live QA batch.

The telemetry gate proves flow outcomes and latency. This matrix validator
proves that the batch actually covered the edge cases needed for market QA.
Provide a JSON file with call IDs mapped to scenario names, for example:

{
  "calls": [
    {"call_id": "abc123", "scenarios": ["takeaway", "change_order"], "audio_reviewed": true},
    {"call_id": "def456", "scenarios": ["delivery", "noisy_stt"], "audio_reviewed": true}
  ]
}
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from qa_telemetry_gate import load_telemetry_events


DEFAULT_REQUIRED_SCENARIOS = (
    "takeaway",
    "delivery",
    "reservation",
    "complaint",
    "cancel_order",
    "change_order",
    "no_speech",
    "interruption",
    "noisy_stt",
    "backend_failure",
)


@dataclass
class MatrixResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    required_scenarios: list[str] = field(default_factory=list)
    scenarios_seen: list[str] = field(default_factory=list)
    missing_scenarios: list[str] = field(default_factory=list)
    manifest_calls: int = 0
    telemetry_completed_calls: int = 0
    manifest_calls_missing_from_telemetry: list[str] = field(default_factory=list)
    telemetry_calls_missing_from_manifest: list[str] = field(default_factory=list)
    audio_unreviewed_calls: list[str] = field(default_factory=list)


def _parse_scenarios(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value or "").replace(";", ",").split(",")
    return [
        str(item).strip().lower()
        for item in raw_values
        if str(item).strip()
    ]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "pass", "passed", "reviewed"}


def load_matrix(path: Path) -> list[dict[str, Any]]:
    suffixes = {suffix.lower() for suffix in path.suffixes}
    if ".csv" in suffixes:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("calls"), list):
        return [item for item in data["calls"] if isinstance(item, dict)]
    raise ValueError("matrix must be a JSON list, a JSON object with calls[], or a CSV file")


def evaluate_matrix(
    rows: Iterable[dict[str, Any]],
    telemetry_events: Iterable[dict[str, Any]],
    *,
    required_scenarios: Iterable[str] = DEFAULT_REQUIRED_SCENARIOS,
) -> MatrixResult:
    materialized_rows = list(rows)
    required = {
        str(scenario).strip().lower()
        for scenario in required_scenarios
        if str(scenario).strip()
    }
    manifest_by_call: dict[str, set[str]] = {}
    audio_review_by_call: dict[str, bool] = {}
    for row in materialized_rows:
        call_id = str(row.get("call_id") or row.get("id") or "").strip()
        if not call_id:
            continue
        scenarios = set(_parse_scenarios(row.get("scenarios") or row.get("scenario")))
        manifest_by_call.setdefault(call_id, set()).update(scenarios)
        audio_review_by_call[call_id] = _truthy(
            row.get("audio_reviewed")
            or row.get("human_reviewed")
            or row.get("reviewed")
            or row.get("audio_review_passed")
        )

    completed_call_ids = {
        str(event.get("call_id") or "").strip()
        for event in telemetry_events
        if event.get("event") == "call.end"
        and str(event.get("call_id") or "").strip()
    }
    scenarios_seen = {
        scenario
        for scenarios in manifest_by_call.values()
        for scenario in scenarios
    }
    missing_scenarios = sorted(required - scenarios_seen)
    manifest_call_ids = set(manifest_by_call)
    manifest_missing = sorted(manifest_call_ids - completed_call_ids)
    telemetry_missing = sorted(completed_call_ids - manifest_call_ids)
    audio_unreviewed = sorted(
        call_id for call_id in manifest_call_ids
        if not audio_review_by_call.get(call_id, False)
    )

    reasons: list[str] = []
    if not manifest_by_call:
        reasons.append("matrix has no calls")
    if missing_scenarios:
        reasons.append(f"missing required scenarios: {', '.join(missing_scenarios)}")
    if manifest_missing:
        reasons.append(
            f"matrix calls missing completed call.end telemetry: {', '.join(manifest_missing[:10])}"
        )
    if telemetry_missing:
        reasons.append(
            f"completed telemetry calls missing from matrix: {', '.join(telemetry_missing[:10])}"
        )
    if audio_unreviewed:
        reasons.append(
            f"matrix calls missing passed human audio review: {', '.join(audio_unreviewed[:10])}"
        )

    return MatrixResult(
        passed=not reasons,
        reasons=reasons,
        required_scenarios=sorted(required),
        scenarios_seen=sorted(scenarios_seen),
        missing_scenarios=missing_scenarios,
        manifest_calls=len(manifest_by_call),
        telemetry_completed_calls=len(completed_call_ids),
        manifest_calls_missing_from_telemetry=manifest_missing,
        telemetry_calls_missing_from_manifest=telemetry_missing,
        audio_unreviewed_calls=audio_unreviewed,
    )


def _render_result(result: MatrixResult) -> dict[str, Any]:
    return {
        "passed": result.passed,
        "reasons": result.reasons,
        "required_scenarios": result.required_scenarios,
        "scenarios_seen": result.scenarios_seen,
        "missing_scenarios": result.missing_scenarios,
        "manifest_calls": result.manifest_calls,
        "telemetry_completed_calls": result.telemetry_completed_calls,
        "manifest_calls_missing_from_telemetry": result.manifest_calls_missing_from_telemetry,
        "telemetry_calls_missing_from_manifest": result.telemetry_calls_missing_from_manifest,
        "audio_unreviewed_calls": result.audio_unreviewed_calls,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate live QA scenario matrix coverage.")
    parser.add_argument("--matrix", required=True, type=Path, help="JSON/CSV call matrix")
    parser.add_argument("--telemetry", required=True, type=Path, help="Telemetry JSONL/log path")
    parser.add_argument(
        "--require-scenarios",
        default=",".join(DEFAULT_REQUIRED_SCENARIOS),
        help="Comma-separated required scenario names",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.matrix.exists():
        result = MatrixResult(passed=False, reasons=[f"matrix file not found: {args.matrix}"])
    elif not args.telemetry.exists():
        result = MatrixResult(passed=False, reasons=[f"telemetry file not found: {args.telemetry}"])
    else:
        result = evaluate_matrix(
            load_matrix(args.matrix),
            load_telemetry_events(args.telemetry),
            required_scenarios=_parse_scenarios(args.require_scenarios),
        )
    print(json.dumps(_render_result(result), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
