"""Daily review CLI for the production JSONL trace.

Consumes ``CALL_TRACE_PATH`` (or any JSONL file passed via ``--input``)
and produces a summary suitable for the team's morning standup. The
summary covers:

- calls handled, fast-path vs. LLM-fallback split,
- per-flow breakdown,
- duplicate-confirm attempts blocked,
- backend submit failures,
- p50 / p95 turn-handler latency,
- top low-confidence captures (worth reviewing for extractor tuning),
- top ambiguous order phrases (worth aliasing in ``menu_index``).

Usage:

    python tests/daily_review.py --input /var/log/agent/call_traces.jsonl
    python tests/daily_review.py --input traces.jsonl --json   # for CI alerts
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[rank]


def _iter_records(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build_report(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"trace file not found: {path}")

    fast_path = 0
    llm_fallback = 0
    by_flow: Counter[str] = Counter()
    fallback_by_flow: Counter[str] = Counter()
    submits_attempted = 0
    submits_accepted = 0
    submits_failed = 0
    submits_blocked: Counter[str] = Counter()
    duplicate_attempts = 0

    turn_latencies: list[float] = []
    engine_latencies: list[float] = []
    low_confidence_phrases: Counter[str] = Counter()
    ambiguous_phrases: Counter[str] = Counter()
    repeated_questions: Counter[str] = Counter()
    intent_kinds: Counter[str] = Counter()
    mutation_kinds: Counter[str] = Counter()
    calls_seen: set[str] = set()

    for record in _iter_records(path):
        event = record.get("event")
        call_id = record.get("call_id") or ""
        if call_id:
            calls_seen.add(call_id)
        flow = record.get("flow") or "unknown"

        if event == "turn.trace":
            by_flow[flow] += 1
            if record.get("fast_path"):
                fast_path += 1
            else:
                llm_fallback += 1
                fallback_by_flow[flow] += 1
            latencies = record.get("latency_ms", {}) or {}
            tot = latencies.get("turn_handler_total")
            if isinstance(tot, (int, float)):
                turn_latencies.append(float(tot))
            eng = latencies.get("engine_decision")
            if isinstance(eng, (int, float)):
                engine_latencies.append(float(eng))

        elif event == "turn.signals":
            intent_kinds[record.get("intent_kind") or "unknown"] += 1
            mutation_kinds[record.get("mutation_kind") or "unknown"] += 1
            order_conf = record.get("order_confidence", 0.0)
            if isinstance(order_conf, (int, float)) and 0 < order_conf < 0.85:
                low_confidence_phrases[
                    f"order@{round(float(order_conf), 2)}"
                ] += 1
            for phrase in record.get("ambiguous_phrases", []) or []:
                ambiguous_phrases[str(phrase)] += 1

        elif event == "slot.repeated_question_blocked":
            repeated_questions[record.get("question_category") or "unknown"] += 1

        elif event == "submission.gate":
            if record.get("allow"):
                submits_attempted += 1
            else:
                reason = record.get("reason") or "unknown"
                submits_blocked[reason] += 1
                if reason == "already_submitted" or reason == "already_accepted":
                    duplicate_attempts += 1

        elif event == "submission.outcome":
            outcome = record.get("outcome")
            if outcome == "accepted":
                submits_accepted += 1
            elif outcome == "failed":
                submits_failed += 1

    total_turns = fast_path + llm_fallback
    fallback_rate = llm_fallback / total_turns if total_turns else 0.0

    return {
        "input_path": str(path),
        "calls_seen": len(calls_seen),
        "turns_total": total_turns,
        "turns_fast_path": fast_path,
        "turns_llm_fallback": llm_fallback,
        "fallback_rate": fallback_rate,
        "by_flow": dict(by_flow),
        "fallback_by_flow": dict(fallback_by_flow),
        "submits_attempted": submits_attempted,
        "submits_accepted": submits_accepted,
        "submits_failed": submits_failed,
        "submits_blocked": dict(submits_blocked),
        "duplicate_attempts_blocked": duplicate_attempts,
        "engine_decision_p50_ms": _percentile(engine_latencies, 50.0),
        "engine_decision_p95_ms": _percentile(engine_latencies, 95.0),
        "turn_handler_p50_ms": _percentile(turn_latencies, 50.0),
        "turn_handler_p95_ms": _percentile(turn_latencies, 95.0),
        "intent_distribution": dict(intent_kinds.most_common()),
        "mutation_distribution": dict(mutation_kinds.most_common()),
        "top_repeated_question_categories": dict(repeated_questions.most_common(10)),
        "top_ambiguous_phrases": dict(ambiguous_phrases.most_common(10)),
        "top_low_confidence_buckets": dict(low_confidence_phrases.most_common(10)),
    }


def emit_text_report(report: dict) -> None:
    print("=" * 72)
    print(f"Daily review — {report['input_path']}")
    print("=" * 72)
    print(f"calls_seen:               {report['calls_seen']}")
    print(f"turns:                    {report['turns_total']}")
    print(f"  fast_path:              {report['turns_fast_path']}")
    print(f"  llm_fallback:           {report['turns_llm_fallback']}  ({report['fallback_rate']*100:.1f}%)")
    print()
    print("by_flow:")
    for flow, count in sorted(report["by_flow"].items(), key=lambda x: -x[1]):
        fb = report["fallback_by_flow"].get(flow, 0)
        print(f"  {flow:14s} turns={count:5d}  llm_fallback={fb}")
    print()
    print(f"submits_attempted:        {report['submits_attempted']}")
    print(f"  accepted:               {report['submits_accepted']}")
    print(f"  failed:                 {report['submits_failed']}")
    print(f"  duplicate_blocked:      {report['duplicate_attempts_blocked']}")
    if report["submits_blocked"]:
        print("  blocked_breakdown:")
        for reason, count in sorted(report["submits_blocked"].items(), key=lambda x: -x[1]):
            print(f"    {reason:20s} {count}")
    print()
    print("latency:")
    print(f"  engine_decision p50/p95:  {report['engine_decision_p50_ms']:.2f}ms / {report['engine_decision_p95_ms']:.2f}ms")
    print(f"  turn_handler   p50/p95:  {report['turn_handler_p50_ms']:.2f}ms / {report['turn_handler_p95_ms']:.2f}ms")
    print()
    if report["intent_distribution"]:
        print("intent_distribution:")
        for kind, count in list(report["intent_distribution"].items())[:8]:
            print(f"  {kind:30s} {count}")
        print()
    if report["top_ambiguous_phrases"]:
        print("top_ambiguous_phrases (consider aliasing):")
        for phrase, count in report["top_ambiguous_phrases"].items():
            print(f"  {phrase:30s} {count}")
        print()
    if report["top_repeated_question_categories"]:
        print("top_repeated_question_blocks:")
        for category, count in report["top_repeated_question_categories"].items():
            print(f"  {category:30s} {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="JSONL trace file")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--alert-fallback-rate",
        type=float,
        default=None,
        help="Exit non-zero if fallback_rate exceeds this (e.g. 0.15)",
    )
    parser.add_argument(
        "--alert-duplicate-attempts",
        type=int,
        default=None,
        help="Exit non-zero if duplicate_attempts_blocked exceeds this",
    )
    args = parser.parse_args(argv)

    report = build_report(args.input)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        emit_text_report(report)

    rc = 0
    if (
        args.alert_fallback_rate is not None
        and report["fallback_rate"] > args.alert_fallback_rate
    ):
        print(
            f"\nALERT: fallback_rate {report['fallback_rate']*100:.1f}% > "
            f"{args.alert_fallback_rate*100:.0f}% threshold",
            file=sys.stderr,
        )
        rc = 2
    if (
        args.alert_duplicate_attempts is not None
        and report["duplicate_attempts_blocked"] > args.alert_duplicate_attempts
    ):
        print(
            f"\nALERT: duplicate_attempts_blocked {report['duplicate_attempts_blocked']} > "
            f"{args.alert_duplicate_attempts} threshold",
            file=sys.stderr,
        )
        rc = 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
