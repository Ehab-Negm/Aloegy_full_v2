"""Tests for the operational metrics counters and daily-review CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


from core.ops_metrics import OpsMetrics  # noqa: E402


_FAILURES: list[tuple[str, str]] = []
_PASSED = 0
_TOTAL = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _TOTAL
    _TOTAL += 1
    if condition:
        _PASSED += 1
    else:
        _FAILURES.append((name, detail))


# ---------------------------------------------------------------------------
# OpsMetrics counters
# ---------------------------------------------------------------------------


def test_metrics_record_turn() -> None:
    m = OpsMetrics()
    m.record_turn(fast_path=True, latency_ms=10.0)
    m.record_turn(fast_path=False, latency_ms=300.0)
    snap = m.snapshot()
    _check("metrics_turn:total", snap["turns_total"] == 2)
    _check("metrics_turn:fast", snap["turns_fast_path"] == 1)
    _check("metrics_turn:llm", snap["turns_llm_fallback"] == 1)
    _check("metrics_turn:fallback_rate", abs(snap["fallback_rate"] - 0.5) < 0.001)


def test_metrics_engine_percentiles() -> None:
    m = OpsMetrics()
    for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
        m.record_engine_decision(v)
    snap = m.snapshot()
    _check("metrics_engine:samples", snap["engine_decision_samples"] == 10)
    _check("metrics_engine:p50", 5.0 <= snap["engine_decision_p50_ms"] <= 6.0, str(snap))
    _check("metrics_engine:p95", snap["engine_decision_p95_ms"] >= 9.0, str(snap))


def test_metrics_alert_callback_fires_on_high_fallback() -> None:
    m = OpsMetrics()
    fired: list[tuple[str, str, dict]] = []
    m.register_alert_callback(lambda kind, detail, ctx: fired.append((kind, detail, ctx)))
    # 25 turns with 80% fallback → above 20% threshold.
    for i in range(25):
        m.record_turn(fast_path=(i % 5 == 0), latency_ms=10.0)
    _check("metrics_alert:fired", any(kind == "fallback_rate_high" for kind, _, _ in fired), str(fired))


def test_metrics_no_alert_below_warmup() -> None:
    m = OpsMetrics()
    fired: list[str] = []
    m.register_alert_callback(lambda kind, _detail, _ctx: fired.append(kind))
    for _ in range(10):
        m.record_turn(fast_path=False, latency_ms=10.0)
    _check("metrics_alert:no_fire_below_20", "fallback_rate_high" not in fired)


def test_metrics_duplicate_alert() -> None:
    m = OpsMetrics()
    fired: list[str] = []
    m.register_alert_callback(lambda kind, _detail, _ctx: fired.append(kind))
    for _ in range(3):
        m.record_submit_blocked("already_accepted")
    _check("metrics_dup:alert", "duplicate_submit_attempts" in fired)


def test_metrics_failures_alert() -> None:
    m = OpsMetrics()
    fired: list[str] = []
    m.register_alert_callback(lambda kind, _detail, _ctx: fired.append(kind))
    for _ in range(3):
        m.record_submit_failed()
    _check("metrics_fail:alert", "submit_failures_high" in fired)


def test_metrics_thread_safety_smoke() -> None:
    """Concurrent threads recording counters must not corrupt totals."""
    import threading

    m = OpsMetrics()

    def worker() -> None:
        for _ in range(200):
            m.record_turn(fast_path=True, latency_ms=1.0)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _check("metrics_thread:total", m.snapshot()["turns_total"] == 1600)


# ---------------------------------------------------------------------------
# Daily review CLI
# ---------------------------------------------------------------------------


def _make_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_daily_review_summarizes_traces() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_local_daily_review",
        Path(__file__).resolve().parent / "tests" / "daily_review.py",
    )
    daily_review = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(daily_review)  # type: ignore[union-attr]

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "traces.jsonl"
        records = [
            {
                "event": "turn.trace",
                "call_id": "c1",
                "flow": "takeaway",
                "fast_path": True,
                "llm_fallback": False,
                "latency_ms": {"engine_decision": 5, "turn_handler_total": 10},
            },
            {
                "event": "turn.trace",
                "call_id": "c1",
                "flow": "takeaway",
                "fast_path": False,
                "llm_fallback": True,
                "latency_ms": {"engine_decision": 8, "turn_handler_total": 350},
            },
            {
                "event": "turn.signals",
                "call_id": "c1",
                "intent_kind": "takeaway",
                "mutation_kind": "add",
                "ambiguous_phrases": ["برجر"],
                "order_confidence": 0.7,
            },
            {
                "event": "submission.gate",
                "call_id": "c1",
                "flow": "takeaway",
                "allow": True,
                "reason": "ok",
            },
            {
                "event": "submission.outcome",
                "call_id": "c1",
                "flow": "takeaway",
                "outcome": "accepted",
            },
            {
                "event": "submission.gate",
                "call_id": "c1",
                "flow": "takeaway",
                "allow": False,
                "reason": "already_submitted",
            },
        ]
        _make_jsonl(path, records)
        report = daily_review.build_report(path)

    _check("daily_review:calls", report["calls_seen"] == 1)
    _check("daily_review:turns", report["turns_total"] == 2)
    _check("daily_review:fast", report["turns_fast_path"] == 1)
    _check("daily_review:llm", report["turns_llm_fallback"] == 1)
    _check("daily_review:fallback_rate", abs(report["fallback_rate"] - 0.5) < 0.001)
    _check("daily_review:submits_attempted", report["submits_attempted"] == 1)
    _check("daily_review:submits_accepted", report["submits_accepted"] == 1)
    _check("daily_review:duplicate_blocked", report["duplicate_attempts_blocked"] == 1)
    _check("daily_review:ambiguous_captured", "برجر" in report["top_ambiguous_phrases"])


def test_daily_review_handles_missing_file() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_local_daily_review",
        Path(__file__).resolve().parent / "tests" / "daily_review.py",
    )
    daily_review = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(daily_review)  # type: ignore[union-attr]

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing.jsonl"
        try:
            daily_review.build_report(path)
            ok = False
        except FileNotFoundError:
            ok = True
    _check("daily_review:missing_file_raises", ok)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _all_test_functions():
    g = globals()
    return [
        (name, g[name])
        for name in sorted(g)
        if name.startswith("test_") and callable(g[name])
    ]


def main() -> int:
    for name, fn in _all_test_functions():
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((name, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1
    print(f"OPS_METRICS_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
