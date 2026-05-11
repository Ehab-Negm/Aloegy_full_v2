"""Read telemetry.jsonl and print latency breakdowns per stage.

Usage:
    python qa_latency_stats.py [--telemetry path/to/telemetry.jsonl]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / ".runtime/prod/telemetry.jsonl"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", default=str(DEFAULT_PATH))
    parser.add_argument("--last-n-calls", type=int, default=0,
                        help="if >0, only consider the last N call_ids")
    args = parser.parse_args()

    path = Path(args.telemetry)
    if not path.exists():
        print(f"telemetry file not found: {path}", file=sys.stderr)
        return 2

    sys.stdout.reconfigure(encoding="utf-8")

    e2e_events: list[dict] = []
    stage_events: dict[str, list[dict]] = {"stt": [], "llm": [], "tts": [], "eou": []}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev_type = evt.get("event") or evt.get("type") or ""
        if ev_type == "latency.e2e":
            e2e_events.append(evt)
        elif ev_type == "latency.stage":
            stage = str(evt.get("stage") or "").lower()
            if stage in stage_events:
                stage_events[stage].append(evt)

    if args.last_n_calls > 0:
        recent_call_ids = []
        seen: set[str] = set()
        for evt in reversed(e2e_events):
            cid = evt.get("call_id")
            if cid and cid not in seen:
                seen.add(cid)
                recent_call_ids.append(cid)
                if len(recent_call_ids) >= args.last_n_calls:
                    break
        keep = set(recent_call_ids)
        e2e_events = [e for e in e2e_events if e.get("call_id") in keep]
        for k in stage_events:
            stage_events[k] = [e for e in stage_events[k] if e.get("call_id") in keep]

    print(f"telemetry: {path}")
    print(f"e2e events: {len(e2e_events)}")
    print()

    if not e2e_events:
        print("no latency.e2e events; place some calls first")
        return 0

    print(f"{'stage':<28}{'p50':>8}{'p95':>10}{'p99':>10}{'max':>10}{'n':>8}")
    print("-" * 74)

    breach_count = 0
    target_ms = 2000.0
    for evt in e2e_events:
        if evt.get("user_to_first_audio_ms", 0) > target_ms:
            breach_count += 1

    fields = [
        ("eou_ms", "eou_ms"),
        ("stt_ms", "stt_ms"),
        ("llm_ttft_ms", "llm_ttft_ms"),
        ("tts_ttfb_ms", "tts_ttfb_ms"),
        ("user_to_first_audio_ms", "TOTAL E2E"),
    ]
    for key, label in fields:
        vals = [float(e[key]) for e in e2e_events if key in e]
        if not vals:
            continue
        p50 = statistics.median(vals)
        p95 = percentile(vals, 95)
        p99 = percentile(vals, 99)
        mx = max(vals)
        print(f"{label:<28}{p50:>8.0f}{p95:>10.0f}{p99:>10.0f}{mx:>10.0f}{len(vals):>8}")

    print()
    print(f"breaches over 2,000ms: {breach_count}/{len(e2e_events)} ({100*breach_count/len(e2e_events):.1f}%)")
    print()

    # Stage-level (separate from e2e) for sanity
    for stage, evts in stage_events.items():
        if not evts:
            continue
        if stage == "llm":
            ttft = [float(e["ttft_ms"]) for e in evts if "ttft_ms" in e]
            if ttft:
                print(f"llm.ttft_ms (stage events) p50={statistics.median(ttft):.0f} p95={percentile(ttft,95):.0f} n={len(ttft)}")
        elif stage == "stt":
            dur = [float(e["duration_ms"]) for e in evts if "duration_ms" in e]
            if dur:
                print(f"stt.duration_ms (stage events) p50={statistics.median(dur):.0f} p95={percentile(dur,95):.0f} n={len(dur)}")
        elif stage == "tts":
            ttfb = [float(e["ttfb_ms"]) for e in evts if "ttfb_ms" in e]
            if ttfb:
                print(f"tts.ttfb_ms (stage events) p50={statistics.median(ttfb):.0f} p95={percentile(ttfb,95):.0f} n={len(ttfb)}")
        elif stage == "eou":
            ed = [float(e["eou_delay_ms"]) for e in evts if "eou_delay_ms" in e]
            if ed:
                print(f"eou.delay_ms (stage events) p50={statistics.median(ed):.0f} p95={percentile(ed,95):.0f} n={len(ed)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
