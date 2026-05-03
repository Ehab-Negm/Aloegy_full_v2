"""Phase 7 launch checklist — labelling kit for 50 real LiveKit/SIP calls.

This module does **not** initiate real calls. It produces the structured
review file the QA team fills in while listening to the recorded calls
and reading their JSONL traces (see ``CALL_TRACE_PATH`` in ``telemetry.py``).
The roadmap's launch gate measures success against this file.

Usage:

    python tests/real_call_qa_template.py --out qa_50_calls.json

The output is a JSON file with one record per call. After you label all
50, run::

    python tests/real_call_qa_template.py --check qa_50_calls.json

to compute the launch metrics:

- successful_handled_rate
- repeated_required_slot_question_rate
- duplicate_submission_count
- wrong_order_count
- p95_first_response_latency_ms (from JSONL traces)

The launch gate from ``docs/voice-agent-production-roadmap-2026-04-27.md``:

- 45/50 calls complete successfully without human intervention.
- 0 wrong submitted orders.
- 0 duplicate submissions.
- Repeated required-slot question rate = 0.
- p95 first response latency under target.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


@dataclass
class CallReview:
    call_id: str
    started_at: str = ""
    duration_seconds: float = 0.0
    primary_flow: str = ""  # takeaway / delivery / reservation / complaint
    outcome: str = ""  # success / partial_success / failed
    repeated_required_slot_question: bool = False
    wrong_order_submitted: bool = False
    duplicate_submission: bool = False
    slow_response: bool = False
    bad_stt: bool = False
    bad_tts: bool = False
    backend_issue: bool = False
    customer_interrupted: bool = False
    customer_changed_order: bool = False
    customer_phone_in_chunks: bool = False
    customer_address_before_order: bool = False
    customer_complained_mid_order: bool = False
    asked_menu_then_ordered: bool = False
    unsupported_zone: bool = False
    unavailable_item: bool = False
    backend_down: bool = False
    transcript_excerpt: str = ""
    notes: str = ""
    p95_first_response_ms: float = 0.0


@dataclass
class QAReport:
    calls: list[CallReview] = field(default_factory=list)


def write_template(path: Path, count: int = 50) -> None:
    report = QAReport(
        calls=[CallReview(call_id=f"call_{i:03d}") for i in range(1, count + 1)]
    )
    payload = {"calls": [asdict(c) for c in report.calls]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {count} blank reviews to {path}")
    print(
        "label each with outcome ∈ {success, partial_success, failed}, "
        "set wrong_order_submitted / duplicate_submission / repeated_* "
        "where applicable, then re-run with --check to compute the gate."
    )


def check(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    calls = [CallReview(**c) for c in raw.get("calls", [])]
    if not calls:
        print(f"no calls found in {path}")
        return 2

    total = len(calls)
    successful = [c for c in calls if c.outcome == "success"]
    success_rate = len(successful) / total
    repeated = sum(1 for c in calls if c.repeated_required_slot_question)
    duplicate = sum(1 for c in calls if c.duplicate_submission)
    wrong_order = sum(1 for c in calls if c.wrong_order_submitted)
    latencies = [c.p95_first_response_ms for c in calls if c.p95_first_response_ms > 0]
    p95_latency = _percentile(latencies, 95.0)

    print("=" * 72)
    print("Phase 7 — Real Voice QA report")
    print("=" * 72)
    print(f"calls reviewed:                     {total}")
    print(f"successful_handled_rate:            {success_rate*100:.1f}% ({len(successful)}/{total})")
    print(f"repeated_required_slot_question:    {repeated}")
    print(f"duplicate_submission_count:         {duplicate}")
    print(f"wrong_order_submitted_count:        {wrong_order}")
    print(f"p95_first_response_latency_ms:      {p95_latency:.0f}")

    failures: list[str] = []
    if total < 50:
        failures.append(f"need at least 50 reviewed calls, got {total}")
    if len(successful) < 45:
        failures.append(f"successful_handled_rate gate: need ≥45/50, got {len(successful)}/{total}")
    if repeated > 0:
        failures.append(f"repeated_required_slot_question must be 0, got {repeated}")
    if duplicate > 0:
        failures.append(f"duplicate_submission_count must be 0, got {duplicate}")
    if wrong_order > 0:
        failures.append(f"wrong_order_submitted_count must be 0, got {wrong_order}")
    if not latencies:
        failures.append("no p95_first_response_latency_ms data — populate from JSONL traces")
    elif p95_latency >= 1500:
        # The roadmap's hard gate is 1.2s for the deterministic path; we
        # use 1.5s here because real calls also include LLM fallback time.
        failures.append(f"p95_first_response_latency_ms gate: ≤1500ms, got {p95_latency:.0f}")

    if failures:
        print()
        print("LAUNCH GATE: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1

    print()
    print("LAUNCH GATE: PASS — Phase 7 acceptance gate met.")
    return 0


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[rank]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, help="Write a 50-call template to this path.")
    parser.add_argument("--check", type=Path, help="Validate a labelled QA file against the launch gate.")
    parser.add_argument("--count", type=int, default=50, help="Number of blank reviews when --out is set.")
    args = parser.parse_args(argv)

    if args.out:
        write_template(args.out, count=args.count)
        return 0
    if args.check:
        return check(args.check)
    parser.error("must pass either --out or --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
