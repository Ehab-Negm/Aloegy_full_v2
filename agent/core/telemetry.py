"""Structured telemetry and logging."""
import json as _json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("restaurant.agent")

_telemetry_logger = logging.getLogger("restaurant.telemetry")
_TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() in {"1", "true", "yes"}


def emit_event(event: str, *, call_id: str = "", flow: str = "", **kwargs: Any) -> None:
    """Emit a structured telemetry event as JSON."""
    if not _TELEMETRY_ENABLED:
        return
    payload = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        "call_id": call_id,
    }
    if flow:
        payload["flow"] = flow
    payload.update(kwargs)
    _telemetry_logger.info(_json.dumps(payload, ensure_ascii=False))


__all__ = ["logger", "emit_event"]
