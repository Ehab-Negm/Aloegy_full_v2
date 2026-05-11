"""Sync completed telemetry calls into the live QA call matrix.

This helper reduces manual QA bookkeeping. It does not approve calls: newly
added rows always use audio_reviewed=false so a human still has to review the
recording and scenario labels before the market gate can pass.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from qa_call_matrix import load_matrix
from qa_telemetry_gate import load_telemetry_events


DEFAULT_COLUMNS = ("call_id", "scenarios", "audio_reviewed", "notes")


def _parse_scenarios(value: Any) -> list[str]:
    raw_values = str(value or "").replace(";", ",").split(",")
    return [
        str(item).strip().lower()
        for item in raw_values
        if str(item).strip()
    ]


def _render_scenarios(values: Iterable[str]) -> str:
    return ",".join(sorted({str(value).strip().lower() for value in values if str(value).strip()}))


def _completed_calls_from_events(events: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    calls_by_id: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for event in events:
        if event.get("event") != "call.end":
            continue
        call_id = str(event.get("call_id") or "").strip()
        if not call_id:
            continue
        if call_id not in calls_by_id:
            order.append(call_id)
        calls_by_id[call_id] = {
            "call_id": call_id,
            "flow": str(event.get("flow") or "").strip().lower(),
            "close_reason": str(event.get("close_reason") or "").strip(),
        }
    return [calls_by_id[call_id] for call_id in order]


def sync_matrix_rows(
    matrix_rows: Iterable[dict[str, Any]],
    telemetry_events: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    completed_calls = _completed_calls_from_events(telemetry_events)
    completed_by_id = {call["call_id"]: call for call in completed_calls}
    output_rows: list[dict[str, str]] = []
    template_rows: list[dict[str, str]] = []
    seen_matrix_call_ids: set[str] = set()
    updated_calls: list[str] = []

    for row in matrix_rows:
        call_id = str(row.get("call_id") or row.get("id") or "").strip()
        if not call_id:
            template_rows.append(
                {
                    column: str(row.get(column) or "").strip()
                    for column in DEFAULT_COLUMNS
                }
            )
            continue
        existing = {
            column: str(row.get(column) or "").strip()
            for column in DEFAULT_COLUMNS
        }
        existing["call_id"] = call_id
        scenarios = set(_parse_scenarios(existing.get("scenarios")))
        telemetry_call = completed_by_id.get(call_id)
        flow = str((telemetry_call or {}).get("flow") or "").strip().lower()
        if flow and flow not in scenarios:
            scenarios.add(flow)
            updated_calls.append(call_id)
        existing["scenarios"] = _render_scenarios(scenarios)
        if not existing["audio_reviewed"]:
            existing["audio_reviewed"] = "false"
        output_rows.append(existing)
        seen_matrix_call_ids.add(call_id)

    completed_flows = {
        str(call.get("flow") or "").strip().lower()
        for call in completed_calls
        if str(call.get("flow") or "").strip()
    }
    remaining_template_rows: list[dict[str, str]] = []
    for row in template_rows:
        scenarios = set(_parse_scenarios(row.get("scenarios")))
        if scenarios and scenarios <= completed_flows:
            continue
        remaining_template_rows.append(row)

    added_calls: list[str] = []
    for call in completed_calls:
        call_id = call["call_id"]
        if call_id in seen_matrix_call_ids:
            continue
        flow = call.get("flow", "")
        notes = "sync: review audio and add scenario labels"
        if call.get("close_reason"):
            notes = f"{notes}; close_reason={call['close_reason']}"
        output_rows.append(
            {
                "call_id": call_id,
                "scenarios": flow,
                "audio_reviewed": "false",
                "notes": notes,
            }
        )
        added_calls.append(call_id)

    output_rows.extend(remaining_template_rows)
    summary = {
        "telemetry_completed_calls": len(completed_calls),
        "matrix_calls": len(output_rows),
        "template_rows_preserved": len(remaining_template_rows),
        "added_calls": added_calls,
        "updated_calls": sorted(set(updated_calls)),
        "calls_missing_from_telemetry": sorted(
            call_id for call_id in seen_matrix_call_ids if call_id not in completed_by_id
        ),
    }
    return output_rows, summary


def write_matrix(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(DEFAULT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: str(row.get(column) or "") for column in DEFAULT_COLUMNS})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync completed telemetry calls into a QA matrix CSV.")
    parser.add_argument("--telemetry", required=True, type=Path, help="Telemetry JSONL/log path")
    parser.add_argument("--matrix", required=True, type=Path, help="QA matrix CSV path to update")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.telemetry.exists():
        print(json.dumps({"passed": False, "reason": f"telemetry file not found: {args.telemetry}"}, indent=2))
        return 1
    matrix_rows = load_matrix(args.matrix) if args.matrix.exists() else []
    rows, summary = sync_matrix_rows(matrix_rows, load_telemetry_events(args.telemetry))
    write_matrix(args.matrix, rows)
    summary["matrix"] = str(args.matrix)
    summary["passed"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
