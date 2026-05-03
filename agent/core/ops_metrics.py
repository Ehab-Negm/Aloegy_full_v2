"""Operational counters + alerting hooks for production rollouts.

The deterministic engine emits structured telemetry events but
production teams need *aggregated* signals to operate the agent:

- fallback rate over the last N turns (alert if too high → STT
  degradation, menu staleness, regional dialect drift),
- duplicate-confirm attempts blocked (alert if non-zero → frontend
  bug, voice repeats, LLM agent bug),
- backend submit failures (alert if rate climbs → backend incident),
- p95 turn latency rolling window.

Counters are in-process; ship them to Prometheus / Datadog / Loki via
``OpsMetrics.snapshot()`` from a periodic worker. Alerts can be wired
by registering an ``alert_callback``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from core.telemetry import emit_event


AlertCallback = Callable[[str, str, dict], None]


@dataclass
class _Window:
    """Rolling window of timestamped values for percentile / rate math."""

    max_age_seconds: float = 600.0
    samples: deque = field(default_factory=deque)

    def add(self, value: float) -> None:
        now = time.time()
        self.samples.append((now, value))
        cutoff = now - self.max_age_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()

    def values(self) -> list[float]:
        cutoff = time.time() - self.max_age_seconds
        return [v for ts, v in self.samples if ts >= cutoff]


@dataclass
class OpsMetrics:
    """Per-process aggregation of operational signals.

    Thread-safe — mutators take an internal lock so concurrent worker
    threads don't corrupt the windows. Reads are also locked so the
    snapshot() return is internally consistent.
    """

    _lock: threading.Lock = field(default_factory=threading.Lock)

    turns_total: int = 0
    turns_fast_path: int = 0
    turns_llm_fallback: int = 0

    submits_attempted: int = 0
    submits_accepted: int = 0
    submits_failed: int = 0
    submits_blocked_duplicate: int = 0
    submits_blocked_in_flight: int = 0

    repeated_question_blocks: int = 0
    write_unavailable_count: int = 0

    latency_engine_ms: _Window = field(default_factory=lambda: _Window())
    latency_total_turn_ms: _Window = field(default_factory=lambda: _Window())

    alert_callbacks: list[AlertCallback] = field(default_factory=list)

    fallback_rate_alert_threshold: float = 0.20  # 20 % rolling fallback
    duplicate_alert_threshold: int = 3
    write_failure_alert_threshold: int = 3

    # Phase 3.4 — graceful-degradation flag. When true, flows should
    # skip the LLM-fallback path and emit a canned holding phrase
    # instead. Toggled by the dashboard or auto-tripped by alerting.
    _degraded_mode: bool = False
    _degraded_reason: str = ""

    def register_alert_callback(self, cb: AlertCallback) -> None:
        with self._lock:
            self.alert_callbacks.append(cb)

    def record_turn(self, *, fast_path: bool, latency_ms: float | None = None) -> None:
        with self._lock:
            self.turns_total += 1
            if fast_path:
                self.turns_fast_path += 1
            else:
                self.turns_llm_fallback += 1
            if latency_ms is not None:
                self.latency_total_turn_ms.add(float(latency_ms))
        self._maybe_alert_fallback_rate()

    def record_engine_decision(self, latency_ms: float) -> None:
        with self._lock:
            self.latency_engine_ms.add(float(latency_ms))

    def record_submit_attempt(self) -> None:
        with self._lock:
            self.submits_attempted += 1

    def record_submit_accepted(self) -> None:
        with self._lock:
            self.submits_accepted += 1

    def record_submit_failed(self) -> None:
        with self._lock:
            self.submits_failed += 1
        self._maybe_alert_failures()

    def record_submit_blocked(self, reason: str) -> None:
        with self._lock:
            if reason == "already_accepted":
                self.submits_blocked_duplicate += 1
            elif reason == "in_flight":
                self.submits_blocked_in_flight += 1
        if reason == "already_accepted":
            self._maybe_alert_duplicate()

    def record_repeated_question_block(self) -> None:
        with self._lock:
            self.repeated_question_blocks += 1

    def record_write_unavailable(self) -> None:
        with self._lock:
            self.write_unavailable_count += 1

    def is_degraded(self) -> bool:
        with self._lock:
            return self._degraded_mode

    def degraded_reason(self) -> str:
        with self._lock:
            return self._degraded_reason

    def set_degraded(self, *, enabled: bool, reason: str = "manual") -> None:
        """Flip the degraded-mode flag. Emits a telemetry event so ops
        teams can see the toggle in the dashboard timeline.
        """
        changed = False
        with self._lock:
            if self._degraded_mode != enabled:
                changed = True
            self._degraded_mode = enabled
            self._degraded_reason = reason if enabled else ""
        if changed:
            emit_event(
                "ops.degraded_mode",
                state="on" if enabled else "off",
                reason=reason,
            )

    def fallback_rate(self) -> float:
        with self._lock:
            if self.turns_total == 0:
                return 0.0
            return self.turns_llm_fallback / self.turns_total

    def snapshot(self) -> dict:
        with self._lock:
            engine_samples = self.latency_engine_ms.values()
            turn_samples = self.latency_total_turn_ms.values()
            return {
                "turns_total": self.turns_total,
                "turns_fast_path": self.turns_fast_path,
                "turns_llm_fallback": self.turns_llm_fallback,
                "fallback_rate": (
                    self.turns_llm_fallback / self.turns_total
                    if self.turns_total
                    else 0.0
                ),
                "submits_attempted": self.submits_attempted,
                "submits_accepted": self.submits_accepted,
                "submits_failed": self.submits_failed,
                "submits_blocked_duplicate": self.submits_blocked_duplicate,
                "submits_blocked_in_flight": self.submits_blocked_in_flight,
                "repeated_question_blocks": self.repeated_question_blocks,
                "write_unavailable_count": self.write_unavailable_count,
                "engine_decision_p50_ms": _percentile(engine_samples, 50.0),
                "engine_decision_p95_ms": _percentile(engine_samples, 95.0),
                "turn_total_p50_ms": _percentile(turn_samples, 50.0),
                "turn_total_p95_ms": _percentile(turn_samples, 95.0),
                "engine_decision_samples": len(engine_samples),
                "turn_samples": len(turn_samples),
            }

    def _maybe_alert_fallback_rate(self) -> None:
        with self._lock:
            if self.turns_total < 20:
                return
            rate = self.turns_llm_fallback / self.turns_total
            threshold = self.fallback_rate_alert_threshold
            callbacks = list(self.alert_callbacks)
        if rate >= threshold:
            self._fire_alerts(
                callbacks,
                kind="fallback_rate_high",
                detail=f"fallback_rate={rate*100:.1f}% threshold={threshold*100:.0f}%",
                context={"rate": rate, "threshold": threshold},
            )

    def _maybe_alert_duplicate(self) -> None:
        with self._lock:
            count = self.submits_blocked_duplicate
            threshold = self.duplicate_alert_threshold
            callbacks = list(self.alert_callbacks)
        if count >= threshold:
            self._fire_alerts(
                callbacks,
                kind="duplicate_submit_attempts",
                detail=f"duplicate_count={count} threshold={threshold}",
                context={"count": count, "threshold": threshold},
            )

    def _maybe_alert_failures(self) -> None:
        with self._lock:
            count = self.submits_failed
            threshold = self.write_failure_alert_threshold
            callbacks = list(self.alert_callbacks)
        if count >= threshold:
            self._fire_alerts(
                callbacks,
                kind="submit_failures_high",
                detail=f"failed={count} threshold={threshold}",
                context={"count": count, "threshold": threshold},
            )

    def _fire_alerts(
        self,
        callbacks: list[AlertCallback],
        *,
        kind: str,
        detail: str,
        context: dict,
    ) -> None:
        emit_event(
            "ops.alert",
            kind=kind,
            detail=detail,
            **context,
        )
        for cb in callbacks:
            try:
                cb(kind, detail, context)
            except Exception:  # pragma: no cover - alert handler bugs must not break flows
                pass


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(
        0,
        min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))),
    )
    return sorted_values[rank]


# Module-level singleton — workers call ``METRICS.record_*`` directly.
METRICS = OpsMetrics()


__all__ = ["AlertCallback", "METRICS", "OpsMetrics"]
