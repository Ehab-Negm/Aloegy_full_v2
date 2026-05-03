from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from backend.config import CachedConfigEntry


@dataclass
class RuntimeHealth:
    config_available: bool = False
    last_config_error: str = ""


@dataclass
class BackendCircuitState:
    consecutive_failures: int = 0
    open_until_monotonic: float = 0.0
    last_error: str = ""


@dataclass
class WorkerContext:
    config_cache: dict[str, CachedConfigEntry] = field(default_factory=dict)
    runtime_health: RuntimeHealth = field(default_factory=RuntimeHealth)
    backend_circuits: dict[str, BackendCircuitState] = field(default_factory=dict)
    circuit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    config_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    config_refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    shared_cache_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    backend_queue_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    backend_write_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    backend_queue_worker: asyncio.Task[Any] | None = None
    config_refresh_worker: asyncio.Task[Any] | None = None
    active_sessions: int = 0
    active_sessions_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    max_concurrent_sessions: int = 100
    max_turns_per_session: int = 50
    turn_counts: dict[str, int] = field(default_factory=dict)


def build_worker_context(queue_max_items: int) -> WorkerContext:
    ctx = WorkerContext()
    ctx.backend_write_queue = asyncio.Queue(maxsize=max(1, int(queue_max_items or 1)))
    return ctx
