"""Per-call metrics tracking for production monitoring."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("call_metrics")

# JSONL sink — one line per finalized call. Set CALL_METRICS_PATH to disable
# (empty string) or to redirect. Default lives under .runtime/ so it rotates
# with the rest of the agent's runtime state and isn't checked into git.
_DEFAULT_METRICS_PATH = Path(__file__).resolve().parents[1] / ".runtime" / "call_metrics.jsonl"
_METRICS_PATH_ENV = os.getenv("CALL_METRICS_PATH")
if _METRICS_PATH_ENV is None:
    _METRICS_PATH: Path | None = _DEFAULT_METRICS_PATH
elif _METRICS_PATH_ENV.strip() == "":
    _METRICS_PATH = None
else:
    _METRICS_PATH = Path(_METRICS_PATH_ENV).expanduser()

# Append-only writes from multiple call coroutines + the asyncio thread pool;
# a process-level lock keeps lines from interleaving on small writes.
_SINK_LOCK = threading.Lock()


def _persist_metrics(payload: dict[str, Any]) -> None:
    """Append one JSONL record to the metrics sink. Best-effort — failures
    are logged at WARNING and do not propagate, because losing a metric line
    must never crash a call."""
    if _METRICS_PATH is None:
        return
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False)
        with _SINK_LOCK:
            with _METRICS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:  # pragma: no cover — sink failure path
        logger.warning("call_metrics sink write failed: %s", exc)


@dataclass
class CallMetrics:
    call_id: str
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None

    user_turns: int = 0
    agent_turns: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    tool_failures: dict[str, int] = field(default_factory=dict)
    flow_transitions: list[tuple[str, str]] = field(default_factory=list)

    repetition_detected: bool = False
    repeated_questions: list[str] = field(default_factory=list)
    backend_failures: int = 0

    completed: bool = False
    completion_reason: str = ""
    final_intent: str = ""

    avg_llm_latency_ms: float = 0.0
    avg_stt_latency_ms: float = 0.0
    avg_tts_latency_ms: float = 0.0

    def record_tool_call(self, name: str, success: bool = True) -> None:
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
        if not success:
            self.tool_failures[name] = self.tool_failures.get(name, 0) + 1

    def record_flow_transition(self, from_flow: str, to_flow: str) -> None:
        self.flow_transitions.append((from_flow, to_flow))

    def record_repetition(self, category: str) -> None:
        self.repetition_detected = True
        self.repeated_questions.append(category)

    def finalize(self, reason: str = "unknown") -> None:
        self.ended_at = time.time()
        self.completion_reason = reason
        self.completed = reason in {"order_submitted", "reservation_submitted", "complaint_submitted"}

    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds()
        return d

    def emit(self) -> None:
        payload = self.to_dict()
        logger.info("CALL_METRICS | %s", json.dumps(payload, ensure_ascii=False))
        _persist_metrics(payload)


_active_calls: dict[str, CallMetrics] = {}


def get_or_create(call_id: str) -> CallMetrics:
    if not call_id:
        call_id = "-"
    metrics = _active_calls.get(call_id)
    if metrics is None:
        metrics = CallMetrics(call_id=call_id)
        _active_calls[call_id] = metrics
    return metrics


def finalize_call(call_id: str, reason: str = "unknown") -> None:
    if not call_id:
        return
    metrics = _active_calls.pop(call_id, None)
    if metrics is None:
        return
    metrics.finalize(reason)
    metrics.emit()
