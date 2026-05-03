"""Phase 2.1 — Mid-speech backchannel emitter.

Real Cairo-Egyptian phone calls are dense with backchannels: the
listener emits "اه" / "تمام" / "حاضر" / "ماشي" every 1-2 seconds while
the speaker is talking, signalling "I'm following, keep going". Voice
agents that don't do this sound dead.

This module attaches to a LiveKit ``AgentSession`` and, while the user
is speaking continuously past a configurable threshold, fires one
short backchannel through the TTS at the next natural pause.

Safety rails (the plan §2.1 spelled these out — they matter):

- **Cap to ≤1 backchannel per ``min_gap_seconds``** so we never spam.
- **Suppress while the agent is speaking** — interrupting our own
  utterance to say "تمام" is the worst possible failure mode.
- **Suppress immediately after the user stops** — let the engine pick
  the actual response. We're a soft acknowledgement during their turn,
  not a reply.
- **Feature-flag default OFF.** Set ``BACKCHANNEL_EMITTER_ENABLED=1``
  to enable. First 50 production calls should be reviewed manually.
- **add_to_chat_ctx=False** — backchannels must NOT enter the LLM
  history. They're sound effects, not dialog turns.

Usage::

    from backchannel_emitter import attach_backchannel_emitter
    attach_backchannel_emitter(session)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("restaurant.agent")


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_env_float(name: str, default: float, min_value: float = 0.0) -> float:
    try:
        return max(min_value, float(os.getenv(name, "") or default))
    except ValueError:
        return default


@dataclass
class BackchannelConfig:
    enabled: bool
    min_speech_seconds: float
    min_gap_seconds: float
    phrases: tuple[str, ...]


# Egyptian listener-side acknowledgements. Kept short (≤2 syllables) so
# they overlay naturally without blocking the customer.
_DEFAULT_PHRASES: tuple[str, ...] = (
    "اه",
    "تمام",
    "ماشي",
    "حاضر",
    "ممم",
    "اه يا فندم",
)


def load_config() -> BackchannelConfig:
    return BackchannelConfig(
        enabled=_get_env_bool("BACKCHANNEL_EMITTER_ENABLED", False),
        min_speech_seconds=_get_env_float("BACKCHANNEL_MIN_SPEECH_SECONDS", 1.5, min_value=0.3),
        min_gap_seconds=_get_env_float("BACKCHANNEL_MIN_GAP_SECONDS", 4.0, min_value=0.5),
        phrases=_DEFAULT_PHRASES,
    )


def attach_backchannel_emitter(session: Any, *, config: BackchannelConfig | None = None) -> None:
    """Wire the emitter to ``session``. No-op if the feature flag is off."""
    cfg = config or load_config()
    if not cfg.enabled:
        return

    state = {
        "user_speaking_since": 0.0,
        "last_backchannel_at": 0.0,
        "agent_speaking": False,
    }

    def _now() -> float:
        return time.monotonic()

    def _on_user_state(ev: Any) -> None:
        new_state = getattr(ev, "new_state", None)
        if new_state == "speaking":
            state["user_speaking_since"] = _now()
        elif new_state in {"listening", "away"}:
            state["user_speaking_since"] = 0.0

    def _on_agent_state(ev: Any) -> None:
        new_state = getattr(ev, "new_state", None)
        state["agent_speaking"] = new_state == "speaking"

    def _on_partial_transcript(ev: Any) -> None:
        # We only want to fire while the user is mid-utterance, not on
        # the final transcript (the engine takes over there).
        if getattr(ev, "is_final", False):
            return
        if state["agent_speaking"]:
            return
        speaking_since = state["user_speaking_since"]
        if speaking_since <= 0:
            return
        elapsed = _now() - speaking_since
        if elapsed < cfg.min_speech_seconds:
            return
        gap = _now() - state["last_backchannel_at"]
        if gap < cfg.min_gap_seconds:
            return

        phrase = random.choice(cfg.phrases)
        state["last_backchannel_at"] = _now()
        # ``session.say`` returns an awaitable — fire and forget. The
        # ``add_to_chat_ctx=False`` keeps it out of the LLM history so
        # the model doesn't see the agent acknowledging mid-turn.
        async def _fire() -> None:
            try:
                handle = session.say(phrase, allow_interruptions=True, add_to_chat_ctx=False)
                # Some versions return an awaitable; tolerate both.
                if asyncio.iscoroutine(handle) or asyncio.isfuture(handle):
                    await handle
            except Exception as exc:
                logger.debug("backchannel emit failed | %s", exc)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_fire(), name="backchannel_emit")
        except RuntimeError:
            # No running loop — skip silently. Backchannels are best-effort.
            pass

    session.on("user_state_changed", _on_user_state)
    session.on("agent_state_changed", _on_agent_state)
    session.on("user_input_transcribed", _on_partial_transcript)

    logger.info(
        "backchannel emitter attached | min_speech_s=%.2f | min_gap_s=%.2f | phrases=%d",
        cfg.min_speech_seconds, cfg.min_gap_seconds, len(cfg.phrases),
    )


__all__ = [
    "BackchannelConfig",
    "attach_backchannel_emitter",
    "load_config",
]
