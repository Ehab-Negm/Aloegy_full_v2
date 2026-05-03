"""
Voice Agent لمطعم — production ready
Agents: Greeter → Reservation / Takeaway / Delivery / Complaint
كل بيانات المطعم بتيجي من الباك اند، مفيش hardcode.
"""

import asyncio
import contextlib
import hashlib
import json as _json
import logging
import os
import atexit
import random as _random
import re
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from livekit.agents import StopResponse, cli, llm, tts as lk_tts
from livekit.agents.voice import AgentSession, RunContext
from livekit.plugins import google, openai, silero
try:
    from livekit.plugins import soniox
except ImportError:
    soniox = None
try:
    from livekit.plugins import deepgram
except ImportError:
    deepgram = None

from backend.config import CachedConfigEntry, RestaurantConfig
from nlp.arabic import (
    AR_DIGITS as _AR_DIGITS,
    contains_normalized_phrase as _contains_normalized_phrase,
    normalize_ar as _normalize_ar,
    normalized_phrase_present as _normalized_phrase_present,
)
from nlp.name_extract import (
    extract_name_candidate as _extract_name_candidate_impl,
    is_likely_non_name_response as _is_likely_non_name_response_impl,
)
from nlp.phone_extract import (
    is_phone_like_text as _is_phone_like_text,
    is_plausible_partial_phone_digits as _is_plausible_partial_phone_digits,
    local_phone_digits as _local_phone_digits,
    merge_phone_digits as _merge_phone_digits,
    phone_digits_only as _phone_digits_only,
    validate_phone,
)
from state.user_data import CallWriteHealth, UserData
from state.worker_context import (
    BackendCircuitState,
    RuntimeHealth,
    WorkerContext,
    build_worker_context,
)
import backend.client as _backend_client
from backend.client import (
    cleanup_http_client as _cleanup_http_client_impl,
    exc_log_fields as _exc_log_fields,
    get_http_client as _get_http_client_base,
    response_snippet as _response_snippet,
    retry_delay as _retry_delay,
    should_retry_backend_error as _should_retry_backend_error,
)
from core.telemetry import emit_event as _core_emit_event
from utils.money import _int_to_ar, money2ar, num2ar, phone2ar
from utils.voice import _voice_safe_text

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("restaurant.agent")
_WORKER_HEALTH_SNAPSHOT_LOCK = threading.Lock()

# ── Structured telemetry ─────────────────────────────────────────────────────
# Emits JSON events for key call lifecycle moments.
# Separate logger so it can be routed to a different handler (file, stdout, etc.)
_TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() in {"1", "true", "yes"}


def _emit_event(event: str, *, call_id: str = "", flow: str = "", **kwargs: Any) -> None:
    """Emit a structured telemetry event as JSON."""
    if not _TELEMETRY_ENABLED:
        return
    _core_emit_event(event, call_id=call_id, flow=flow, **kwargs)
try:
    CAIRO_TZ = ZoneInfo("Africa/Cairo")
except Exception:
    CAIRO_TZ = timezone(timedelta(hours=2))

AGENT_DIR = Path(__file__).resolve().parent


def _get_env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("invalid env float | name=%s | raw=%r | using=%s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning(
            "env float below minimum | name=%s | raw=%r | min=%s | using=%s",
            name, raw, min_value, default,
        )
        return default
    return value


def _get_env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid env int | name=%s | raw=%r | using=%s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        logger.warning(
            "env int below minimum | name=%s | raw=%r | min=%s | using=%s",
            name, raw, min_value, default,
        )
        return default
    return value


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("invalid env bool | name=%s | raw=%r | using=%s", name, raw, default)
    return default

# ─────────────────────────────────────────────────────────────────────────────
# Env — fail fast
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv(AGENT_DIR / ".env")

REQUIRED_ENV_VARS = [
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "BACKEND_BASE_URL",
    "BACKEND_API_KEY",
]
_missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
if _missing:
    logging.critical("Missing env vars: %s", _missing)
    sys.exit(1)

BACKEND_BASE   = os.getenv("BACKEND_BASE_URL", "").rstrip("/")
BACKEND_APIKEY = os.getenv("BACKEND_API_KEY", "")
APP_ENV = re.sub(r"[^a-z0-9_-]", "", os.getenv("APP_ENV", "dev").strip().lower()) or "dev"

HTTP_TIMEOUT_SECONDS         = _get_env_float("HTTP_TIMEOUT_SECONDS", 3.5, min_value=0.05)
HTTP_CONNECT_TIMEOUT_SECONDS = _get_env_float("HTTP_CONNECT_TIMEOUT_SECONDS", 1.0, min_value=0.05)
HTTP_READ_TIMEOUT_SECONDS    = _get_env_float("HTTP_READ_TIMEOUT_SECONDS", 3.0, min_value=0.05)
HTTP_WRITE_TIMEOUT_SECONDS   = _get_env_float("HTTP_WRITE_TIMEOUT_SECONDS", 3.0, min_value=0.05)
BACKEND_MAX_RETRIES          = _get_env_int("BACKEND_MAX_RETRIES", 2, min_value=1)
BACKEND_RETRY_BASE_SECONDS   = _get_env_float("BACKEND_RETRY_BASE_SECONDS", 0.2, min_value=0.01)
CONFIG_FETCH_RETRIES         = _get_env_int("CONFIG_FETCH_RETRIES", 2, min_value=1)
CONFIG_FETCH_BACKOFF_SECONDS = _get_env_float("CONFIG_FETCH_BACKOFF_SECONDS", 0.15, min_value=0.01)
CONFIG_FETCH_TOTAL_BUDGET_SECONDS = _get_env_float("CONFIG_FETCH_TOTAL_BUDGET_SECONDS", 0.6, min_value=0.1)
CONFIG_REFRESH_INTERVAL_SECONDS  = _get_env_float("CONFIG_REFRESH_INTERVAL_SECONDS", 300.0, min_value=30.0)
MAX_CONCURRENT_SESSIONS          = _get_env_int("MAX_CONCURRENT_SESSIONS", 100, min_value=1)
MAX_TURNS_PER_SESSION            = _get_env_int("MAX_TURNS_PER_SESSION", 50, min_value=10)
TURN_CAP_WARNING_TURNS           = _get_env_int("TURN_CAP_WARNING_TURNS", 5, min_value=1)
TURN_CAP_GRACE_TURNS             = _get_env_int("TURN_CAP_GRACE_TURNS", 3, min_value=0)
PROMPT_HISTORY_ITEMS         = _get_env_int("PROMPT_HISTORY_ITEMS", 12, min_value=2)
TURN_CHAT_CTX_MAX_ITEMS      = _get_env_int("TURN_CHAT_CTX_MAX_ITEMS", 36, min_value=8)
MAX_TOOL_STEPS               = _get_env_int("MAX_TOOL_STEPS", 10, min_value=6)
MIN_INTERRUPTION_DURATION_SECONDS = _get_env_float("MIN_INTERRUPTION_DURATION_SECONDS", 0.35, min_value=0.0)
MIN_ENDPOINTING_DELAY_SECONDS     = _get_env_float("MIN_ENDPOINTING_DELAY_SECONDS", 0.2, min_value=0.05)
MAX_ENDPOINTING_DELAY_SECONDS     = _get_env_float("MAX_ENDPOINTING_DELAY_SECONDS", 0.55, min_value=0.1)
FALSE_INTERRUPTION_TIMEOUT_SECONDS = _get_env_float("FALSE_INTERRUPTION_TIMEOUT_SECONDS", 0.8, min_value=0.1)
USER_AWAY_TIMEOUT_SECONDS         = _get_env_float("USER_AWAY_TIMEOUT_SECONDS", 9.0, min_value=0.5)
NO_SPEECH_PROMPT_SECONDS          = _get_env_float("NO_SPEECH_PROMPT_SECONDS", 12.0, min_value=1.0)
NO_SPEECH_CLOSE_SECONDS           = _get_env_float("NO_SPEECH_CLOSE_SECONDS", 28.0, min_value=2.0)
NO_SPEECH_REPROMPT_LIMIT          = _get_env_int("NO_SPEECH_REPROMPT_LIMIT", 2, min_value=1)
NO_SPEECH_REPROMPT_GAP_SECONDS    = _get_env_float("NO_SPEECH_REPROMPT_GAP_SECONDS", 8.0, min_value=0.5)
SESSION_TTS_MODEL            = os.getenv("SESSION_TTS_MODEL", "hamsa/tts-realtime")
SESSION_TTS_VOICE            = os.getenv("SESSION_TTS_VOICE", "Nermin")
SESSION_TTS_LANGUAGE         = os.getenv("SESSION_TTS_LANGUAGE", "ar")
SESSION_TTS_DIALECT          = os.getenv("SESSION_TTS_DIALECT", "egy")
SESSION_TTS_MULAW            = _get_env_bool("SESSION_TTS_MULAW", False)
SESSION_TTS_STREAMING_ENABLED = _get_env_bool("SESSION_TTS_STREAMING_ENABLED", True)
SESSION_TTS_STREAM_PACING    = _get_env_bool("SESSION_TTS_STREAM_PACING", False)
SESSION_STT_LANGUAGE         = os.getenv("SESSION_STT_LANGUAGE", "ar")
SESSION_STT_MODEL            = os.getenv("SESSION_STT_MODEL", "stt-rt-v4")
SESSION_STT_BASE_URL         = os.getenv("SESSION_STT_BASE_URL", "wss://stt-rt.soniox.com/transcribe-websocket").strip()
SESSION_STT_LANGUAGE_HINTS_STRICT = _get_env_bool("SESSION_STT_LANGUAGE_HINTS_STRICT", True)
SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION = _get_env_bool("SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION", True)
SESSION_STT_KEYTERM_LIMIT    = _get_env_int("SESSION_STT_KEYTERM_LIMIT", 40, min_value=5)
SESSION_STT_EXTRA_KEYTERMS   = os.getenv("SESSION_STT_EXTRA_KEYTERMS", "")
# Phase 1.2 — Deepgram Nova-3 A/B branch.
#   SESSION_STT_PROVIDER=soniox       (default — current production)
#   SESSION_STT_PROVIDER=deepgram     (Nova-3 with menu-keyterm prompting)
# A/B percentage (0-100) is applied per-call: when set, that fraction of
# calls is routed to Deepgram even if the default provider is Soniox. This
# lets us collect comparison data on a small slice of production traffic
# without flipping every call. The picked provider is stamped on every
# turn.received event via SESSION_STT_PROVIDER (mutable module global).
SESSION_STT_PROVIDER_DEFAULT = (os.getenv("SESSION_STT_PROVIDER", "soniox") or "soniox").strip().lower()
SESSION_STT_DEEPGRAM_AB_PERCENT = _get_env_int("SESSION_STT_DEEPGRAM_AB_PERCENT", 0, min_value=0)
SESSION_STT_DEEPGRAM_MODEL = os.getenv("SESSION_STT_DEEPGRAM_MODEL", "nova-3").strip() or "nova-3"
SESSION_STT_DEEPGRAM_LANGUAGE = (os.getenv("SESSION_STT_DEEPGRAM_LANGUAGE", "multi") or "multi").strip()
SESSION_STT_DEEPGRAM_ENDPOINTING_MS = _get_env_int("SESSION_STT_DEEPGRAM_ENDPOINTING_MS", 200, min_value=10)
SESSION_LLM_MODEL            = os.getenv("SESSION_LLM_MODEL", "gemini-2.5-flash")
SESSION_LLM_MAX_COMPLETION_TOKENS = _get_env_int("SESSION_LLM_MAX_COMPLETION_TOKENS", 260, min_value=32)
SESSION_LLM_TEMPERATURE      = _get_env_float("SESSION_LLM_TEMPERATURE", 0.25, min_value=0.0)
SESSION_LLM_TOP_P            = _get_env_float("SESSION_LLM_TOP_P", 0.85, min_value=0.0)
SESSION_LLM_THINKING_BUDGET  = _get_env_int("SESSION_LLM_THINKING_BUDGET", 0, min_value=0)
SESSION_PREEMPTIVE_GENERATION = _get_env_bool("SESSION_PREEMPTIVE_GENERATION", False)
CONFIG_SHARED_CACHE_ENABLED  = _get_env_bool("CONFIG_SHARED_CACHE_ENABLED", True)
CONFIG_SHARED_CACHE_PATH     = os.getenv("CONFIG_SHARED_CACHE_PATH", f".runtime/{APP_ENV}/config_cache.json")
BACKEND_WRITE_QUEUE_ENABLED  = _get_env_bool("BACKEND_WRITE_QUEUE_ENABLED", True)
BACKEND_WRITE_QUEUE_PATH     = os.getenv("BACKEND_WRITE_QUEUE_PATH", f".runtime/{APP_ENV}/backend_write_queue.jsonl")
BACKEND_WRITE_QUEUE_MAX_ITEMS = _get_env_int("BACKEND_WRITE_QUEUE_MAX_ITEMS", 500, min_value=1)
BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES = _get_env_int("BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES", 500, min_value=1)
BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS = _get_env_float("BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS", 5.0, min_value=0.5)
BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD = _get_env_int("BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD", 3, min_value=1)
BACKEND_WRITE_CIRCUIT_OPEN_SECONDS = _get_env_float("BACKEND_WRITE_CIRCUIT_OPEN_SECONDS", 8.0, min_value=0.5)
BACKEND_POST_TIMEOUT_SECONDS = _get_env_float("BACKEND_POST_TIMEOUT_SECONDS", 3.0, min_value=0.05)
AGENT_IDLE_PROCESSES         = _get_env_int("AGENT_IDLE_PROCESSES", 2 if APP_ENV == "prod" else 1, min_value=0)
AGENT_HEALTH_SNAPSHOT_DIR    = os.getenv("AGENT_HEALTH_SNAPSHOT_DIR", f".runtime/{APP_ENV}/worker_health")
AGENT_HEALTH_SNAPSHOT_STALE_SECONDS = _get_env_float("AGENT_HEALTH_SNAPSHOT_STALE_SECONDS", 90.0, min_value=5.0)

_WORKER_CONTEXT = build_worker_context(BACKEND_WRITE_QUEUE_MAX_ITEMS)


def worker_context() -> WorkerContext:
    return _WORKER_CONTEXT


def _setup_worker_process(proc: Any) -> None:
    global _WORKER_CONTEXT
    # Called once per worker process by LiveKit AgentServer.
    # Replaces the module-level default context with a process-specific one.
    _WORKER_CONTEXT = build_worker_context(BACKEND_WRITE_QUEUE_MAX_ITEMS)
    proc.userdata["worker_context"] = _WORKER_CONTEXT
    logger.info("worker process setup | pid=%d | context_id=%d", os.getpid(), id(_WORKER_CONTEXT))
    _write_worker_health_snapshot_sync(reason="worker_setup")
    # Prewarm the default-restaurant config in a background thread so the
    # first call doesn't pay the 1-1.5s backend round-trip. Best-effort —
    # any failure just means the first call falls through to the regular
    # fetch path. Default key (`__default__`) is what fetch_config uses
    # when no restaurant_id is in the room metadata.
    if _get_env_bool("CONFIG_PREWARM_ENABLED", True):
        def _prewarm_default_config() -> None:
            try:
                started = time.monotonic()
                asyncio.run(fetch_config(call_id="prewarm", restaurant_id=""))
                logger.info(
                    "config prewarm complete | pid=%d | took_ms=%d",
                    os.getpid(), int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:
                logger.warning("config prewarm failed | pid=%d | %s", os.getpid(), exc)
        threading.Thread(target=_prewarm_default_config, daemon=True, name="config_prewarm").start()

    # Note: TTS prewarm was tried here but broke the Google Cloud TTS
    # plugin. Running the synth in a background thread's asyncio.run()
    # binds the gRPC channel to the wrong loop; every subsequent synth
    # fails with "Unsupported audio encoding" / cross-loop Future
    # errors. The right place to prewarm TTS is inside the worker's
    # actual event loop on first call, but the cold-start cost (~200ms)
    # is small enough that we accept it on call #1 and rely on the
    # provider's connection-pool reuse for calls #2+. Don't reintroduce
    # a prewarm without solving the loop-affinity problem.

# ─────────────────────────────────────────────────────────────────────────────
# TTS/STT/LLM — session-level فقط، مفيش per-agent overrides
# ─────────────────────────────────────────────────────────────────────────────
def _session_stt_language_hints() -> list[str] | None:
    hint = re.sub(r"\s+", "", SESSION_STT_LANGUAGE or "")
    if not hint or hint.lower() in {"auto", "multi", "*"}:
        return None
    hint = hint.replace("_", "-").lower()
    if "-" in hint:
        hint = hint.split("-", 1)[0]
    return [hint]


def _session_stt_context(context_terms: list[str] | None = None) -> Any:
    if not context_terms:
        return None

    terms = context_terms[:SESSION_STT_KEYTERM_LIMIT]
    if not terms:
        return None

    context_text = ", ".join(terms)
    if soniox is None:
        return context_text

    return soniox.ContextObject(
        general=[soniox.ContextGeneralItem(key="domain", value="restaurant ordering")],
        text=context_text,
        terms=terms,
    )


def _session_stt_options(*, context_terms: list[str] | None = None, client_reference_id: str | None = None) -> Any:
    language_hints = _session_stt_language_hints()
    context = _session_stt_context(context_terms)
    option_kwargs = {
        "model": SESSION_STT_MODEL,
        "language_hints": language_hints,
        "language_hints_strict": SESSION_STT_LANGUAGE_HINTS_STRICT,
        "context": context,
        "enable_language_identification": SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION,
        "client_reference_id": client_reference_id,
    }
    # Soniox enforces ``max_endpoint_delay_ms`` ∈ [500, 3000]. Setting the
    # floor (500) commits final transcripts as fast as Soniox allows.
    # End-of-utterance delays >500ms in production are then network/STT
    # processing latency we can't tune from the client side. Override via
    # env if you want a longer wait window for hesitant speakers.
    max_endpoint_delay_ms = _get_env_int("SESSION_STT_MAX_ENDPOINT_DELAY_MS", 500, min_value=500)
    if soniox is not None:
        option_kwargs["max_endpoint_delay_ms"] = max_endpoint_delay_ms
        return soniox.STTOptions(**option_kwargs)
    option_kwargs["max_endpoint_delay_ms"] = max_endpoint_delay_ms
    return SimpleNamespace(**option_kwargs)


def _resolve_stt_provider(*, override: str | None = None) -> str:
    """Resolve which STT provider this call should use.

    ``override`` wins absolutely (used by tests / dashboard). Otherwise
    ``SESSION_STT_DEEPGRAM_AB_PERCENT`` rolls a per-call dice; if the
    primary provider is Soniox and the dice falls below the percent, the
    call goes to Deepgram. When the chosen provider isn't installed or
    has no API key we fall back to whichever provider is ready, so the
    call still goes through.
    """
    requested = (override or SESSION_STT_PROVIDER_DEFAULT or "soniox").strip().lower()
    if requested not in {"soniox", "deepgram"}:
        requested = "soniox"
    if (
        override is None
        and requested == "soniox"
        and SESSION_STT_DEEPGRAM_AB_PERCENT > 0
        and _random.randint(1, 100) <= SESSION_STT_DEEPGRAM_AB_PERCENT
    ):
        requested = "deepgram"

    def _ready(provider: str) -> bool:
        if provider == "soniox":
            return soniox is not None and bool(os.getenv("SONIOX_API_KEY"))
        if provider == "deepgram":
            return deepgram is not None and bool(os.getenv("DEEPGRAM_API_KEY"))
        return False

    if _ready(requested):
        return requested
    fallback = "soniox" if requested == "deepgram" else "deepgram"
    if _ready(fallback):
        logger.warning(
            "STT provider %s unavailable — falling back to %s", requested, fallback,
        )
        return fallback
    return requested  # Will surface as a not-ready error below.


def _stt_provider_ready_reason(provider: str | None = None) -> str | None:
    target = (provider or SESSION_STT_PROVIDER_DEFAULT or "soniox").strip().lower()
    if target == "deepgram":
        if deepgram is None:
            return "livekit-plugins-deepgram is not installed"
        if not os.getenv("DEEPGRAM_API_KEY"):
            return "DEEPGRAM_API_KEY is missing"
        return None
    if soniox is None:
        return "livekit-plugins-soniox is not installed"
    if not os.getenv("SONIOX_API_KEY"):
        return "SONIOX_API_KEY is missing"
    return None


async def prewarm_stt_connection(stt_instance: Any) -> None:
    """Pre-open a Soniox WebSocket so DNS/TLS/auth are cached before the
    user's first audio arrives.

    Production logs were showing 2.7-3.3 s end-of-utterance delay on the
    first turn (with a "Timeout during Soniox … connection/initialization"
    error and an automatic retry). After the first connection succeeded the
    EOU dropped to ~870 ms, which is normal. Opening a throwaway stream at
    call start absorbs that cold-start cost in parallel with config fetch
    so the customer's first turn doesn't pay it.

    Best-effort: silently swallows failures because this is a latency
    optimisation, not a correctness requirement.
    """
    if soniox is None:
        return
    target = stt_instance
    # When STT is wrapped in a FallbackAdapter (Phase 3.2), reach
    # inside to find the soniox.STT primary so the prewarm still helps.
    if not isinstance(target, soniox.STT):
        for attr in ("_stt", "_primary", "_stts", "stts"):
            inner = getattr(stt_instance, attr, None)
            if isinstance(inner, soniox.STT):
                target = inner
                break
            if isinstance(inner, (list, tuple)):
                for entry in inner:
                    if isinstance(entry, soniox.STT):
                        target = entry
                        break
                if isinstance(target, soniox.STT):
                    break
        if not isinstance(target, soniox.STT):
            return
    try:
        from livekit import rtc as _rtc  # noqa: E402

        stream = target.stream()
        # 100 ms of silence at 16 kHz mono 16-bit = 1600 samples * 2 bytes
        silence_frame = _rtc.AudioFrame(
            data=b"\x00" * 3200,
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=1600,
        )
        stream.push_frame(silence_frame)
        # Wait briefly for the WebSocket handshake to land, then tear
        # down. The aiohttp connection pool keeps DNS + TLS state cached
        # for the real call's stream that opens a moment later.
        await asyncio.sleep(0.6)
        with contextlib.suppress(Exception):
            await stream.aclose()
        logger.debug("Soniox STT pre-warm completed")
    except Exception as exc:
        logger.debug("Soniox STT pre-warm skipped | %s", exc)


from hamsa_tts import TTS as HamsaTTS  # noqa: E402
from xai_tts import TTS as XAITTS  # noqa: E402


class _ManagedTTSStreamAdapter(lk_tts.StreamAdapter):
    """StreamAdapter that also closes the wrapped provider when the session closes."""

    async def aclose(self) -> None:
        try:
            await super().aclose()
        finally:
            await self._wrapped_tts.aclose()


def _build_base_session_tts() -> Any:
    model_name = SESSION_TTS_MODEL.strip().lower()
    if model_name.startswith("xai/") or model_name.startswith("xai-") or model_name == "xai":
        if not os.getenv("XAI_API_KEY"):
            logger.warning("XAI_API_KEY is not set — TTS will fail")
        return XAITTS(
            api_key=os.getenv("XAI_API_KEY", ""),
            voice=SESSION_TTS_VOICE,
            language=SESSION_TTS_LANGUAGE,
        )

    # Phase 1.3 — Azure Cognitive Services TTS branch.
    # Triggered by SESSION_TTS_MODEL starting with "azure" (e.g. "azure",
    # "azure-neural"). Voice comes from SESSION_TTS_VOICE — for Egyptian
    # Arabic use ``ar-EG-SalmaNeural`` (female) or ``ar-EG-ShakirNeural``
    # (male). Both are MOS ~4.3 on Cairo dialect — purpose-built for
    # ar-EG, which Gemini Aoede is not.
    if model_name.startswith("azure"):
        try:
            from livekit.plugins import azure as _azure_plugin  # noqa: E402
        except ImportError as exc:
            raise RuntimeError(
                "livekit-plugins-azure is not installed; "
                "`pip install livekit-plugins-azure` to enable Azure TTS"
            ) from exc
        speech_key = os.getenv("AZURE_SPEECH_KEY", "").strip()
        speech_region = os.getenv("AZURE_SPEECH_REGION", "").strip()
        if not speech_key or not speech_region:
            logger.warning(
                "AZURE_SPEECH_KEY/AZURE_SPEECH_REGION not set — Azure TTS will fail",
            )
        # Default voice if the user left SESSION_TTS_VOICE on a Gemini
        # name like ``Aoede``. SalmaNeural is the strongest Egyptian
        # female voice; flip to ShakirNeural for male.
        voice = SESSION_TTS_VOICE.strip()
        if not voice or voice in {"Aoede", "Kore", "Sulafat", "Charon", "Puck"}:
            voice = "ar-EG-SalmaNeural"
        language = (SESSION_TTS_LANGUAGE or "ar-EG").strip() or "ar-EG"
        if "-" not in language and language.lower() == "ar":
            language = "ar-EG"
        return _azure_plugin.TTS(
            voice=voice,
            language=language,
            speech_key=speech_key or None,
            speech_region=speech_region or None,
        )

    if model_name.startswith("gemini"):
        # Three paths for Gemini TTS, picked by model name:
        #
        #   1. **Gemini Live API** (streaming via websocket, target <600ms ttfb)
        #      Triggered when the model id contains "live" — e.g.
        #      ``gemini-3.1-flash-live-preview``. We open a Live session,
        #      send text via ``send_realtime_input`` and stream PCM audio
        #      chunks through. Per Google's docs the classic speech-
        #      generation REST endpoint does NOT stream — the Live API is
        #      the only sub-second TTFB path for 3.1.
        #
        #   2. **Google Cloud TTS streaming** (~200-400ms ttfb) — requires a
        #      service-account JSON via GOOGLE_APPLICATION_CREDENTIALS.
        #      Supported models: gemini-2.5-flash-tts (GA),
        #      gemini-2.5-flash-lite-preview-tts, gemini-2.5-pro-tts.
        #
        #   3. **Gemini API beta** (non-streaming, ~2.5s ttfb) — uses
        #      GOOGLE_API_KEY. Models: gemini-2.5-flash-preview-tts,
        #      gemini-2.5-pro-preview-tts. Last-resort fallback.
        #
        # Routing precedence: model name contains "live" → (1); else if
        # service-account creds are present → (2); else → (3).
        if "live" in model_name:
            from gemini_live_tts import GeminiLiveTTS  # noqa: E402

            instructions = os.getenv("SESSION_TTS_INSTRUCTIONS") or None
            language = (SESSION_TTS_LANGUAGE or "ar-EG").strip() or "ar-EG"
            if "-" not in language and language.lower() == "ar":
                language = "ar-EG"
            if not os.getenv("GOOGLE_API_KEY"):
                logger.warning("GOOGLE_API_KEY is not set — Gemini Live TTS will fail")
            return GeminiLiveTTS(
                model=SESSION_TTS_MODEL,
                voice_name=SESSION_TTS_VOICE,
                language=language,
                instructions=instructions,
            )
        cloud_creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        use_cloud_tts = bool(cloud_creds_file) and os.path.isfile(cloud_creds_file)
        if use_cloud_tts:
            from livekit.plugins.google import TTS as GoogleCloudTTS  # noqa: E402

            instructions = os.getenv("SESSION_TTS_INSTRUCTIONS") or (
                "Speak in clear Egyptian Arabic (Cairo dialect) with a warm, "
                "friendly customer-service tone. Don't add or omit any words."
            )
            language = (SESSION_TTS_LANGUAGE or "ar-EG").strip() or "ar-EG"
            if "-" not in language and language.lower() == "ar":
                language = "ar-EG"
            return GoogleCloudTTS(
                model_name=SESSION_TTS_MODEL,
                voice_name=SESSION_TTS_VOICE,
                language=language,
                prompt=instructions,
                credentials_file=cloud_creds_file,
                use_streaming=True,
            )

        if not os.getenv("GOOGLE_API_KEY"):
            logger.warning("GOOGLE_API_KEY is not set — Gemini TTS will fail")
        from livekit.plugins.google.beta import GeminiTTS  # noqa: E402

        instructions = os.getenv("SESSION_TTS_INSTRUCTIONS") or (
            "Speak in clear Egyptian Arabic (Cairo dialect) with a warm, "
            "friendly customer-service tone. Don't add or omit any words."
        )
        return GeminiTTS(
            model=SESSION_TTS_MODEL,
            voice_name=SESSION_TTS_VOICE,
            instructions=instructions,
        )

    if not os.getenv("HAMSA_API_KEY"):
        logger.warning("HAMSA_API_KEY is not set — TTS will fail")
    return HamsaTTS(
        api_key=os.getenv("HAMSA_API_KEY", ""),
        voice=SESSION_TTS_VOICE,
        dialect=SESSION_TTS_DIALECT,
        language_id=SESSION_TTS_LANGUAGE,
        mulaw=SESSION_TTS_MULAW,
    )


def _build_session_tts() -> Any:
    base_tts = _build_base_session_tts()
    capabilities = getattr(base_tts, "capabilities", None)
    if not SESSION_TTS_STREAMING_ENABLED:
        logger.info("TTS streaming disabled | model=%s", SESSION_TTS_MODEL)
        return base_tts
    if bool(capabilities and capabilities.streaming):
        logger.info("TTS native streaming enabled | model=%s", SESSION_TTS_MODEL)
        return base_tts

    logger.info(
        "TTS streaming adapter enabled | model=%s | pacing=%s",
        SESSION_TTS_MODEL,
        SESSION_TTS_STREAM_PACING,
    )
    return _ManagedTTSStreamAdapter(
        tts=base_tts,
        text_pacing=SESSION_TTS_STREAM_PACING,
    )


def _classify_tts_provider(model_name: str) -> str:
    name = (model_name or "").strip().lower()
    if name.startswith("xai"):
        return "xai"
    if name.startswith("gemini"):
        if "live" in name:
            return "gemini-live"
        cloud_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if cloud_creds and os.path.isfile(cloud_creds):
            return "gemini-cloud"
        return "gemini-beta"
    if name.startswith("azure"):
        return "azure"
    if name.startswith("hamsa") or not name:
        return "hamsa"
    return name


SESSION_TTS = _build_session_tts()
# Mutable: set per-call by ``_build_session_stt`` so telemetry can stamp
# every turn with the actual provider that handled it (matters for the
# Phase 1.2 A/B comparison).
SESSION_STT_PROVIDER = SESSION_STT_PROVIDER_DEFAULT
# Phase 1.3 — TTS provider tag for telemetry / dashboard split.
SESSION_TTS_PROVIDER = _classify_tts_provider(SESSION_TTS_MODEL)
# Optional Azure A/B branch. When SESSION_TTS_AZURE_AB_PERCENT > 0 we
# build a second TTS instance up-front so per-call routing is a cheap
# pointer swap, not a cold-start. Skipped when the primary already is
# Azure (no swap needed) or when Azure creds aren't configured.
SESSION_TTS_AZURE_AB_PERCENT = _get_env_int("SESSION_TTS_AZURE_AB_PERCENT", 0, min_value=0)
SESSION_TTS_AZURE_VOICE = os.getenv("SESSION_TTS_AZURE_VOICE", "ar-EG-SalmaNeural").strip() or "ar-EG-SalmaNeural"
SESSION_TTS_AZURE: Any = None
if (
    SESSION_TTS_AZURE_AB_PERCENT > 0
    and SESSION_TTS_PROVIDER != "azure"
    and os.getenv("AZURE_SPEECH_KEY")
    and os.getenv("AZURE_SPEECH_REGION")
):
    try:
        from livekit.plugins import azure as _azure_plugin  # noqa: E402
        _azure_lang = (SESSION_TTS_LANGUAGE or "ar-EG").strip() or "ar-EG"
        if "-" not in _azure_lang and _azure_lang.lower() == "ar":
            _azure_lang = "ar-EG"
        _azure_base = _azure_plugin.TTS(
            voice=SESSION_TTS_AZURE_VOICE,
            language=_azure_lang,
            speech_key=os.getenv("AZURE_SPEECH_KEY", "").strip() or None,
            speech_region=os.getenv("AZURE_SPEECH_REGION", "").strip() or None,
        )
        # Honor the same streaming-adapter logic as SESSION_TTS so latency
        # behavior matches between the A and B legs.
        if SESSION_TTS_STREAMING_ENABLED:
            _azure_caps = getattr(_azure_base, "capabilities", None)
            if _azure_caps and _azure_caps.streaming:
                SESSION_TTS_AZURE = _azure_base
            else:
                SESSION_TTS_AZURE = _ManagedTTSStreamAdapter(
                    tts=_azure_base, text_pacing=SESSION_TTS_STREAM_PACING,
                )
        else:
            SESSION_TTS_AZURE = _azure_base
        logger.info(
            "Azure TTS A/B armed | voice=%s | language=%s | percent=%d",
            SESSION_TTS_AZURE_VOICE, _azure_lang, SESSION_TTS_AZURE_AB_PERCENT,
        )
    except Exception as _azure_exc:
        logger.warning("Azure TTS A/B init failed | %s", _azure_exc)
        SESSION_TTS_AZURE = None


def pick_session_tts(*, provider_override: str | None = None) -> tuple[Any, str]:
    """Return ``(tts_instance, provider_label)`` for this call.

    Prefers the explicit override; otherwise rolls the Azure A/B dice.
    Falls back to the primary TTS when Azure isn't armed.
    """
    if provider_override:
        target = provider_override.strip().lower()
        if target == "azure" and SESSION_TTS_AZURE is not None:
            return SESSION_TTS_AZURE, "azure"
        return SESSION_TTS, SESSION_TTS_PROVIDER
    if (
        SESSION_TTS_AZURE is not None
        and SESSION_TTS_AZURE_AB_PERCENT > 0
        and _random.randint(1, 100) <= SESSION_TTS_AZURE_AB_PERCENT
    ):
        return SESSION_TTS_AZURE, "azure"
    return SESSION_TTS, SESSION_TTS_PROVIDER

# ── LLM routing ──────────────────────────────────────────────────────────
# Three providers supported:
#   1. OpenRouter (model contains "/", e.g. "qwen/qwen3-32b-instruct")
#      → OpenAI-compatible API at https://openrouter.ai/api/v1, key
#        OPENROUTER_API_KEY. Lets you swap LLMs (Qwen, Claude, Gemini,
#        Llama, …) with just an env change — same plugin, same code path.
#   2. OpenAI direct (model starts with "gpt-" / "o1" / "o3" / "o4")
#   3. Google Gemini direct (anything else — typically "gemini-2.5-flash")
_openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
_is_openrouter = "/" in SESSION_LLM_MODEL and bool(_openrouter_key)

# Qwen3 family (qwen3-32b, qwen3-14b, qwen3-235b-a22b, …) defaults to
# "thinking" mode on OpenRouter — the model emits a hidden chain-of-thought
# before responding, which adds 5-7 s of TTFT. That's catastrophic for a
# voice agent. The /no_think directive in the system prompt switches the
# model into the fast "non-thinking" mode used for general conversation.
# base_agent.on_enter checks this flag and prepends /no_think to the
# per-flow state snapshot when set.
SESSION_LLM_NO_THINK = (
    _is_openrouter
    and SESSION_LLM_MODEL.startswith(("qwen/qwen3-", "qwen/qwen3.5", "qwen/qwen3.6"))
)

if _is_openrouter:
    # OpenRouter speaks the OpenAI Chat Completions API verbatim, so the
    # ``openai.LLM`` plugin works against it via ``base_url`` override.
    # Optional ``HTTP-Referer`` + ``X-Title`` headers help OpenRouter's
    # analytics; provide a stable identity for the agent.
    _openrouter_referer = os.getenv("OPENROUTER_HTTP_REFERER", "https://aloegy.local").strip()
    _openrouter_title = os.getenv("OPENROUTER_X_TITLE", "AloEgy Voice Agent").strip()
    _openai_kwargs: dict[str, Any] = {
        "model": SESSION_LLM_MODEL,
        "api_key": _openrouter_key,
        "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
        "max_completion_tokens": SESSION_LLM_MAX_COMPLETION_TOKENS,
        "parallel_tool_calls": False,
        "temperature": SESSION_LLM_TEMPERATURE,
        "top_p": SESSION_LLM_TOP_P,
    }
    # Inject OpenRouter analytics headers if the plugin supports a default-
    # headers kwarg; some versions accept ``client`` instead. Best-effort.
    try:
        SESSION_LLM = openai.LLM(
            **_openai_kwargs,
            default_headers={
                "HTTP-Referer": _openrouter_referer,
                "X-Title": _openrouter_title,
            },
        )
    except TypeError:
        SESSION_LLM = openai.LLM(**_openai_kwargs)
    logger.info(
        "LLM provider: OpenRouter | model=%s | temp=%.2f | top_p=%.2f",
        SESSION_LLM_MODEL, SESSION_LLM_TEMPERATURE, SESSION_LLM_TOP_P,
    )
elif SESSION_LLM_MODEL.startswith(("gpt-", "o1", "o3", "o4")):
    # OpenAI direct branch. ``parallel_tool_calls=False`` forces one tool
    # per turn — protects against the "race the engine" bug where the
    # model would call ``update_name`` with garbage while ``update_order``
    # was still resolving. gpt-4o(-mini) is not a reasoning model, so it
    # uses temperature/top_p (reasoning_effort / verbosity are only valid
    # for o-series and gpt-5.x and would 400 here).
    _openai_kwargs = {
        "model": SESSION_LLM_MODEL,
        "max_completion_tokens": SESSION_LLM_MAX_COMPLETION_TOKENS,
        "parallel_tool_calls": False,
        "temperature": SESSION_LLM_TEMPERATURE,
        "top_p": SESSION_LLM_TOP_P,
    }
    SESSION_LLM = openai.LLM(**_openai_kwargs)
    logger.info(
        "LLM provider: OpenAI | model=%s | temp=%.2f | top_p=%.2f",
        SESSION_LLM_MODEL, SESSION_LLM_TEMPERATURE, SESSION_LLM_TOP_P,
    )
else:
    from google.genai import types as _genai_types  # noqa: E402
    _gemini_kwargs: dict[str, Any] = {
        "model": SESSION_LLM_MODEL,
        "temperature": SESSION_LLM_TEMPERATURE,
        "top_p": SESSION_LLM_TOP_P,
        "max_output_tokens": SESSION_LLM_MAX_COMPLETION_TOKENS,
    }
    if SESSION_LLM_MODEL.startswith("gemini-2.5"):
        _gemini_kwargs["thinking_config"] = _genai_types.ThinkingConfig(
            thinking_budget=SESSION_LLM_THINKING_BUDGET,
        )
    SESSION_LLM = google.LLM(**_gemini_kwargs)
    logger.info(
        "LLM provider: Google | model=%s | temp=%.2f | top_p=%.2f | thinking_budget=%d",
        SESSION_LLM_MODEL, SESSION_LLM_TEMPERATURE, SESSION_LLM_TOP_P, SESSION_LLM_THINKING_BUDGET,
    )

# Phase 3.2 — LLM fallback. Set SESSION_LLM_FALLBACK_MODEL to a backup
# model id; when the primary 5xxs or times out, livekit's
# ``llm.FallbackAdapter`` retries against the secondary. Provider for
# the fallback is inferred from the model name the same way as primary.
SESSION_LLM_FALLBACK_MODEL = os.getenv("SESSION_LLM_FALLBACK_MODEL", "").strip()
SESSION_LLM_FALLBACK_PROVIDER = (os.getenv("SESSION_LLM_FALLBACK_PROVIDER", "") or "").strip().lower()


def _build_llm_for_model(model_id: str, *, provider_hint: str = "") -> Any:
    """Build a single LLM instance for ``model_id``.

    Provider routing mirrors the primary block above (OpenRouter when the
    id contains ``/`` AND OPENROUTER_API_KEY is set, OpenAI for ``gpt-`` /
    ``o1`` / ``o3`` / ``o4`` prefixes, Google otherwise) — overridable
    via ``provider_hint`` for forced routing.
    """
    provider = (provider_hint or "").lower()
    if not provider:
        if "/" in model_id and os.getenv("OPENROUTER_API_KEY"):
            provider = "openrouter"
        elif model_id.startswith(("gpt-", "o1", "o3", "o4")):
            provider = "openai"
        else:
            provider = "google"
    if provider == "openrouter":
        kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
            "base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
            "max_completion_tokens": SESSION_LLM_MAX_COMPLETION_TOKENS,
            "parallel_tool_calls": False,
            "temperature": SESSION_LLM_TEMPERATURE,
            "top_p": SESSION_LLM_TOP_P,
        }
        return openai.LLM(**kwargs)
    if provider == "openai":
        return openai.LLM(
            model=model_id,
            max_completion_tokens=SESSION_LLM_MAX_COMPLETION_TOKENS,
            parallel_tool_calls=False,
            temperature=SESSION_LLM_TEMPERATURE,
            top_p=SESSION_LLM_TOP_P,
        )
    # Google
    from google.genai import types as _genai_types  # noqa: E402
    _kwargs: dict[str, Any] = {
        "model": model_id,
        "temperature": SESSION_LLM_TEMPERATURE,
        "top_p": SESSION_LLM_TOP_P,
        "max_output_tokens": SESSION_LLM_MAX_COMPLETION_TOKENS,
    }
    if model_id.startswith("gemini-2.5"):
        _kwargs["thinking_config"] = _genai_types.ThinkingConfig(
            thinking_budget=SESSION_LLM_THINKING_BUDGET,
        )
    return google.LLM(**_kwargs)


if SESSION_LLM_FALLBACK_MODEL:
    try:
        _fallback_llm = _build_llm_for_model(
            SESSION_LLM_FALLBACK_MODEL, provider_hint=SESSION_LLM_FALLBACK_PROVIDER,
        )
        from livekit.agents.llm import FallbackAdapter as _LLMFallbackAdapter  # noqa: E402
        SESSION_LLM = _LLMFallbackAdapter([SESSION_LLM, _fallback_llm])
        logger.info(
            "LLM fallback chain armed | primary=%s | fallback=%s",
            SESSION_LLM_MODEL, SESSION_LLM_FALLBACK_MODEL,
        )
    except Exception as _llm_fallback_exc:
        logger.warning(
            "LLM fallback init failed | %s — primary only", _llm_fallback_exc,
        )

# ── Realtime mode (Gemini Live API) ──────────────────────────────────────────
# When SESSION_REALTIME_ENABLED=1 the session uses a single RealtimeModel
# instead of the STT → LLM → TTS pipeline. The realtime model owns audio
# in/out and tool calling. main.py inspects SESSION_REALTIME and, if set,
# wires it as ``llm=`` to AgentSession (no stt/tts/vad).
#
# 3.1 (gemini-3.1-flash-live-preview) has documented limits: ``send_client_content``
# is restricted to initial history seeding only; ``generate_reply``,
# ``update_instructions``, ``update_chat_ctx`` are not reliably honoured after
# the first model turn. base_agent.on_enter is hardened to swallow those
# failures so multi-flow handoff still works (state flows via tool results).
SESSION_REALTIME_ENABLED     = _get_env_bool("SESSION_REALTIME_ENABLED", False)
SESSION_REALTIME_MODEL       = os.getenv("SESSION_REALTIME_MODEL", "gemini-3.1-flash-live-preview").strip()
SESSION_REALTIME_VOICE       = os.getenv("SESSION_REALTIME_VOICE", "Aoede").strip()
SESSION_REALTIME_LANGUAGE    = os.getenv("SESSION_REALTIME_LANGUAGE", "ar-EG").strip()
SESSION_REALTIME_TEMPERATURE = _get_env_float("SESSION_REALTIME_TEMPERATURE", 0.6, min_value=0.0)
SESSION_REALTIME_INSTRUCTIONS = os.getenv(
    "SESSION_REALTIME_INSTRUCTIONS",
    "أنت موظف مصري بترد على تليفون مطعم. اتكلم بس باللهجة المصرية القاهرية، "
    "زي راجل حقيقي مش بوت. متستخدمش إنجليزي أبداً. "
    "\n\n"
    "خطوات المكالمة:\n"
    "1) سلّم على الزبون باسم المطعم.\n"
    "2) اسأله عايز تكلَيمي/توصيل/حجز/شكوى — وحوّله للـ flow المناسب باستخدام "
    "to_takeaway / to_delivery / to_reservation / to_complaint.\n"
    "3) خد منه الاسم والتليفون والعنوان (لو توصيل) والطلب — واحدة واحدة، "
    "وكل ما يقولك حاجة استخدم الـ tool المناسب فوراً (update_name، "
    "update_phone، update_address، update_order).\n"
    "4) بعد كل tool call، أكِّد للزبون اللي سجلته في جملة قصيرة بالعربي "
    "(مثلاً: \"تمام، اتسجلت بيتزا مارجريتا، الإجمالي مية وعشرين جنيه — تحب "
    "تضيف حاجة تانية؟\"). الزبون مش هيسمع تأكيد لو إنت مأكدتش.\n"
    "5) لما الطلب يكتمل، اقفله بـ submit_order أو submit_reservation.\n\n"
    "قواعد الكلام:\n"
    "- جملة واحدة قصيرة، أقصى 15 كلمة.\n"
    "- ابدأ بكلمة تأكيد صغيرة (تمام/ماشي/حاضر/اه) قبل ما تكمل.\n"
    "- قول \"يا فندم\" مرة أو اتنين فقط في المكالمة كلها.\n"
    "- متكررش كلام الزبون حرفي.\n"
    "- مش فاهم؟ قول \"معلش، ممكن تعيدها؟\" — متخمّنش.\n"
    "- لو الـ tool واخد وقت قول \"ثانية بس\" بدل ما تسكت.",
).strip()

SESSION_REALTIME: Any | None = None
if SESSION_REALTIME_ENABLED:
    if not os.getenv("GOOGLE_API_KEY"):
        logger.warning("GOOGLE_API_KEY is not set — Realtime mode will fail")
    from livekit.plugins.google.realtime import RealtimeModel as _GoogleRealtimeModel  # noqa: E402

    _realtime_kwargs: dict[str, Any] = {
        "model": SESSION_REALTIME_MODEL,
        "voice": SESSION_REALTIME_VOICE,
        "instructions": SESSION_REALTIME_INSTRUCTIONS,
        "temperature": SESSION_REALTIME_TEMPERATURE,
    }
    # ``proactivity`` + ``enable_affective_dialog`` force the plugin onto the
    # ``v1alpha`` API. ``gemini-3.1-flash-live-preview`` is only served on
    # ``v1beta`` and v1alpha returns WebSocket close 1011 (internal error)
    # for it. Only enable these flags for the 2.5 native-audio family which
    # supports them. Operators can override by setting
    # SESSION_REALTIME_PROACTIVITY=1 explicitly if Google extends 3.1.
    _is_25_native = "2.5" in SESSION_REALTIME_MODEL and "native-audio" in SESSION_REALTIME_MODEL
    _proactivity_default = "1" if _is_25_native else "0"
    if os.getenv("SESSION_REALTIME_PROACTIVITY", _proactivity_default).strip() == "1":
        _realtime_kwargs["proactivity"] = True
        _realtime_kwargs["enable_affective_dialog"] = True
    if SESSION_REALTIME_LANGUAGE and SESSION_REALTIME_LANGUAGE.lower() not in {"auto", "multi", ""}:
        _realtime_kwargs["language"] = SESSION_REALTIME_LANGUAGE
    SESSION_REALTIME = _GoogleRealtimeModel(**_realtime_kwargs)
    logger.info(
        "Realtime provider: Google Live | model=%s | voice=%s | language=%s | temp=%.2f",
        SESSION_REALTIME_MODEL, SESSION_REALTIME_VOICE,
        SESSION_REALTIME_LANGUAGE or "(auto)", SESSION_REALTIME_TEMPERATURE,
    )

    # ── Kickoff seed for 3.1 (which cannot proactively speak) ────────────────
    # Per the Gemini 3.1 Live docs, the model is reactive-only — there is no
    # ``proactivity`` flag and ``generate_reply`` is rejected. The plugin's
    # connect path seeds initial chat history with ``turn_complete=False``,
    # so even a seeded user message doesn't trigger a response. The
    # workaround is to push one synthetic user turn into the session's
    # message channel after connect with ``turn_complete=True`` — the model
    # sees it as the caller's first utterance and responds with a greeting
    # generated from ``SESSION_REALTIME_INSTRUCTIONS``. Disable with
    # SESSION_REALTIME_KICKOFF_ENABLED=0 if Google later adds a native
    # speak-first mode.
    SESSION_REALTIME_KICKOFF_ENABLED = _get_env_bool(
        "SESSION_REALTIME_KICKOFF_ENABLED",
        SESSION_REALTIME_MODEL == "gemini-3.1-flash-live-preview",
    )
    SESSION_REALTIME_KICKOFF_TEXT = os.getenv(
        "SESSION_REALTIME_KICKOFF_TEXT",
        "ألو، اتصلت دلوقتي. سلّم عليّ وقولي أقدر أطلب منك إيه.",
    ).strip()
    if SESSION_REALTIME_KICKOFF_ENABLED and SESSION_REALTIME_KICKOFF_TEXT:
        from google.genai import types as _gemini_live_types  # noqa: E402
        _orig_session_factory = SESSION_REALTIME.session

        # Per-call gate: ``RealtimeModel.session()`` is invoked once at call
        # start AND again on every multi-agent handoff (greeter → delivery,
        # etc.). The kickoff "ألو، اتصلت دلوقتي" is correct only for the
        # opening session — replaying it on handoff makes the next agent
        # think a fresh customer just called and act on stale carried-over
        # state. The counter is reset by main.py via
        # ``reset_realtime_kickoff()`` at the start of each call.
        SESSION_REALTIME._kickoff_count = 0  # type: ignore[attr-defined]

        async def _kickoff_realtime(sess: Any) -> None:
            # Wait until the WebSocket session is actually live before pushing
            # the seed turn — otherwise it gets dropped.
            deadline = time.monotonic() + 8.0
            while getattr(sess, "_active_session", None) is None:
                if time.monotonic() > deadline:
                    logger.warning("realtime kickoff: session never became active")
                    return
                if getattr(sess, "_msg_ch", None) is None or sess._msg_ch.closed:
                    return
                await asyncio.sleep(0.1)
            try:
                kickoff = _gemini_live_types.LiveClientContent(
                    turns=[
                        _gemini_live_types.Content(
                            role="user",
                            parts=[_gemini_live_types.Part(text=SESSION_REALTIME_KICKOFF_TEXT)],
                        )
                    ],
                    turn_complete=True,
                )
                await sess._msg_ch.send(kickoff)
                logger.info("realtime kickoff: seeded greeting trigger turn")
            except Exception as exc:
                logger.warning("realtime kickoff failed | %s", exc)

        def _patched_session_factory() -> Any:
            sess = _orig_session_factory()
            count = getattr(SESSION_REALTIME, "_kickoff_count", 0) + 1
            SESSION_REALTIME._kickoff_count = count  # type: ignore[attr-defined]
            if count == 1:
                asyncio.create_task(_kickoff_realtime(sess), name="gemini-realtime-kickoff")
            else:
                logger.info(
                    "realtime kickoff: skipped (handoff session #%d, model has carried context)",
                    count,
                )
            return sess

        def reset_realtime_kickoff() -> None:
            """Reset the per-call kickoff counter. Called by main.py at the
            start of each new call so the next session triggers the seed.
            """
            SESSION_REALTIME._kickoff_count = 0  # type: ignore[attr-defined]

        SESSION_REALTIME.session = _patched_session_factory  # type: ignore[method-assign]
        SESSION_REALTIME.reset_kickoff_counter = reset_realtime_kickoff  # type: ignore[attr-defined]
        logger.info("realtime kickoff enabled | text=%r", SESSION_REALTIME_KICKOFF_TEXT[:60])

SESSION_VAD = silero.VAD.load(
    min_silence_duration=0.25,
    prefix_padding_duration=0.2,
    activation_threshold=0.5,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared httpx client — delegates to backend.client singleton
# ─────────────────────────────────────────────────────────────────────────────

async def _get_http_client() -> httpx.AsyncClient:
    # Tests inject mock clients via agent._http_client — check that first
    test_client = _backend_client._http_client
    if test_client is not None and not test_client.is_closed:
        return test_client
    return await _get_http_client_base(
        timeout=HTTP_TIMEOUT_SECONDS,
        connect_timeout=HTTP_CONNECT_TIMEOUT_SECONDS,
        read_timeout=HTTP_READ_TIMEOUT_SECONDS,
        write_timeout=HTTP_WRITE_TIMEOUT_SECONDS,
        api_key=BACKEND_APIKEY,
    )


def _backend_failure_user_message(ud: "UserData") -> str:
    cfg = ud.restaurant
    health = ud.write_health
    contact = f" أو اتصل على {spoken_phone(cfg.phone)}" if cfg.phone else ""
    if health.last_write_failure_kind in {"ConnectTimeout", "ReadTimeout", "ConnectError", "RemoteProtocolError"}:
        return _voice_safe_text(f"في تأخير مؤقت في النظام يا فندم، حاول تاني بعد شوية{contact}.")
    if health.last_write_status_code and health.last_write_status_code >= 500:
        return _voice_safe_text(f"في مشكلة مؤقتة في النظام يا فندم، حاول تاني{contact}.")
    if health.last_write_status_code and 400 <= health.last_write_status_code < 500:
        return _voice_safe_text(f"في مشكلة في تسجيل البيانات يا فندم، راجعها وحاول تاني{contact}.")
    return _voice_safe_text(f"في مشكلة مؤقتة في النظام يا فندم، حاول تاني{contact}.")


def _backend_queued_user_message(kind: str) -> str:
    if kind == "complaint":
        return _voice_safe_text("الشكوى محفوظة مؤقتًا، وهنتابعها أول ما النظام يثبت.")
    if kind == "reservation":
        return _voice_safe_text("الحجز محفوظ مؤقتًا، وهنتأكد منه أول ما النظام يثبت.")
    return _voice_safe_text("الطلب محفوظ مؤقتًا، وهنتأكد منه أول ما النظام يثبت.")


def _degraded_user_message(cfg: "RestaurantConfig") -> str:
    contact = f" أو اتصل على {spoken_phone(cfg.phone)}" if cfg.phone else ""
    return _voice_safe_text(
        f"أهلاً بيك يا فندم، في تحديث مؤقت في النظام دلوقتي، تقدر تقولّي طلبك{contact}.",
        max_chars=140,
    )


def _available_menu_items(cfg: "RestaurantConfig") -> list[dict]:
    return [item for item in cfg.menu_items if item.get("available", True)]


def _menu_unavailable_user_message(cfg: "RestaurantConfig") -> str:
    contact = f" أو اتصل على {spoken_phone(cfg.phone)}" if cfg.phone else ""
    return _voice_safe_text(
        f"المنيو مش ظاهرة عندي دلوقتي يا فندم. لو في صنف في بالك قولهولي وأسجله مبدئيًا{contact}.",
        max_chars=160,
    )


def _order_validation_user_message(cfg: "RestaurantConfig") -> str:
    contact = f" أو اتصل على {spoken_phone(cfg.phone)}" if cfg.phone else ""
    return _voice_safe_text(
        f"أنا سجلت الطلب مبدئيًا بس محتاج المنيو ترجع علشان أثبّته صح{contact}.",
        max_chars=150,
    )


def _delivery_unavailable_user_message(cfg: "RestaurantConfig") -> str:
    if cfg.degraded_mode:
        return _voice_safe_text("التوصيل محتاج تأكيد من النظام يا فندم. قولي طلبك الأول وأنا أمشي معاك خطوة خطوة.")
    return _voice_safe_text("للأسف التوصيل مش متاح دلوقتي يا فندم. تحب تيجي تاخده من عندنا؟")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
def spoken_phone(phone: str | None) -> str:
    valid = validate_phone(phone or "")
    return phone2ar(valid) if valid else "رقم المطعم"

# ─────────────────────────────────────────────────────────────────────────────
# RestaurantConfig
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ParsedReservationTime:
    raw_text: str
    scheduled_at: datetime
    normalized_text: str

def _clone_config_with_source(cfg: RestaurantConfig, source: Literal["backend", "cache_fresh", "cache_stale", "degraded_fallback"]) -> RestaurantConfig:
    return replace(cfg, config_source=source)


def _config_cache_age_seconds(entry: CachedConfigEntry | None) -> float | None:
    if entry is None:
        return None
    return max(0.0, time.monotonic() - entry.fetched_at_monotonic)


def _degraded_config() -> RestaurantConfig:
    return RestaurantConfig(
        name="المطعم",
        phone="",
        address="",
        is_open=True,
        closed_reason="",
        hours={},
        menu_items=[],
        delivery_enabled=False,
        wait_minutes=20,
        min_guests=1,
        max_guests=20,
        degraded_mode=True,
        config_source="degraded_fallback",
    )


def _runtime_file_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = AGENT_DIR / path
    return path


async def _ensure_parent_dir(path: Path) -> None:
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)


def _worker_health_dir() -> Path:
    return _runtime_file_path(AGENT_HEALTH_SNAPSHOT_DIR)


def _worker_health_file_path(pid: int | None = None) -> Path:
    return _worker_health_dir() / f"{pid or os.getpid()}.json"


def _build_worker_health_snapshot(*, reason: str = "") -> dict[str, Any]:
    ctx = worker_context()
    now_monotonic = time.monotonic()
    queue = ctx.backend_write_queue
    circuits_open = sorted(
        endpoint
        for endpoint, state in ctx.backend_circuits.items()
        if state.open_until_monotonic > now_monotonic
    )
    return {
        "pid": os.getpid(),
        "updated_at_epoch": time.time(),
        "reason": reason,
        "active_sessions": ctx.active_sessions,
        "max_concurrent_sessions": ctx.max_concurrent_sessions,
        "config_available": ctx.runtime_health.config_available,
        "last_config_error": ctx.runtime_health.last_config_error,
        "circuits_open": circuits_open,
        "write_queue_size": queue.qsize(),
        "write_queue_max_items": getattr(queue, "maxsize", BACKEND_WRITE_QUEUE_MAX_ITEMS),
        "queue_worker_running": bool(ctx.backend_queue_worker and not ctx.backend_queue_worker.done()),
        "config_refresh_worker_running": bool(ctx.config_refresh_worker and not ctx.config_refresh_worker.done()),
        "turn_count_sessions": len(ctx.turn_counts),
    }


def _write_worker_health_snapshot_sync(*, reason: str = "") -> None:
    path = _worker_health_file_path()
    payload = _json.dumps(_build_worker_health_snapshot(reason=reason), ensure_ascii=False)
    tmp_path = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
    with _WORKER_HEALTH_SNAPSHOT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError as exc:
                if attempt == 4:
                    logger.warning(
                        "worker health snapshot replace failed | path=%s | reason=%s | %s",
                        path,
                        reason,
                        _exc_log_fields(exc),
                    )
                    with contextlib.suppress(FileNotFoundError):
                        tmp_path.unlink()
                    return
                time.sleep(0.02 * (attempt + 1))


async def _write_worker_health_snapshot(*, reason: str = "") -> None:
    try:
        await asyncio.to_thread(_write_worker_health_snapshot_sync, reason=reason)
    except Exception as exc:
        logger.warning(
            "worker health snapshot write failed | reason=%s | %s",
            reason,
            _exc_log_fields(exc),
        )


def _schedule_worker_health_snapshot(reason: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _write_worker_health_snapshot(reason=reason),
        name=f"worker_health_{reason[:24]}",
    )


def _remove_worker_health_snapshot_sync(pid: int | None = None) -> None:
    with _WORKER_HEALTH_SNAPSHOT_LOCK:
        with contextlib.suppress(FileNotFoundError):
            _worker_health_file_path(pid).unlink()


def _read_worker_health_snapshots() -> list[dict[str, Any]]:
    directory = _worker_health_dir()
    if not directory.exists():
        return []

    snapshots: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("worker health snapshot read failed | path=%s | %s", path, _exc_log_fields(exc))
            continue
        if not isinstance(raw, dict):
            continue
        raw = dict(raw)
        raw["path"] = str(path)
        snapshots.append(raw)
    return snapshots


def _backend_recovery_queue_count() -> int:
    path = _backend_queue_path()
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except Exception as exc:
        logger.warning("backend recovery queue count failed | path=%s | %s", path, _exc_log_fields(exc))
        return 0


def build_agent_health_report(
    *,
    server_connection_failed: bool = False,
    active_jobs: int = 0,
) -> tuple[int, dict[str, Any]]:
    now_epoch = time.time()
    snapshots = _read_worker_health_snapshots()
    fresh_snapshots: list[dict[str, Any]] = []
    stale_snapshots: list[dict[str, Any]] = []

    for snapshot in snapshots:
        updated_at = float(snapshot.get("updated_at_epoch", 0.0) or 0.0)
        age_seconds = max(0.0, now_epoch - updated_at) if updated_at else float("inf")
        decorated = dict(snapshot)
        decorated["age_seconds"] = round(age_seconds, 3)
        if updated_at and age_seconds <= AGENT_HEALTH_SNAPSHOT_STALE_SECONDS:
            fresh_snapshots.append(decorated)
        else:
            stale_snapshots.append(decorated)

    active_sessions = (
        sum(int(snapshot.get("active_sessions", 0) or 0) for snapshot in fresh_snapshots)
        if fresh_snapshots
        else int(active_jobs)
    )
    circuits_open = sorted(
        {
            str(endpoint)
            for snapshot in fresh_snapshots
            for endpoint in snapshot.get("circuits_open", [])
            if endpoint
        }
    )
    config_available = (
        all(bool(snapshot.get("config_available", False)) for snapshot in fresh_snapshots)
        if fresh_snapshots
        else None
    )
    queue_workers_running = (
        all(bool(snapshot.get("queue_worker_running", False)) for snapshot in fresh_snapshots)
        if fresh_snapshots
        else None
    )
    config_refresh_workers_running = (
        all(bool(snapshot.get("config_refresh_worker_running", False)) for snapshot in fresh_snapshots)
        if fresh_snapshots
        else None
    )
    in_memory_queue_size = sum(int(snapshot.get("write_queue_size", 0) or 0) for snapshot in fresh_snapshots)
    recovery_queue_items = _backend_recovery_queue_count()

    reasons: list[str] = []
    status = "ok"
    if server_connection_failed:
        status = "unhealthy"
        reasons.append("livekit_connection_failed")
    else:
        if fresh_snapshots:
            if config_available is False:
                reasons.append("config_unavailable")
            if circuits_open:
                reasons.append("circuits_open")
            if queue_workers_running is False:
                reasons.append("backend_queue_worker_stopped")
            if config_refresh_workers_running is False:
                reasons.append("config_refresh_worker_stopped")
            if recovery_queue_items > 0 or in_memory_queue_size > 0:
                reasons.append("write_queue_backlog")
            if reasons:
                status = "degraded"
        elif active_jobs > 0:
            status = "degraded"
            reasons.append("worker_health_unavailable")

    http_status = 200 if status == "ok" else 503
    payload = {
        "status": status,
        "active_sessions": active_sessions,
        "active_jobs": int(active_jobs),
        "livekit_connected": not server_connection_failed,
        "config_available": config_available,
        "circuits_open": circuits_open,
        "worker_snapshots": {
            "fresh": len(fresh_snapshots),
            "stale": len(stale_snapshots),
            "stale_pids": [snapshot.get("pid") for snapshot in stale_snapshots],
        },
        "write_queue": {
            "in_memory_size": in_memory_queue_size,
            "recovery_items": recovery_queue_items,
            "queue_worker_running": queue_workers_running,
            "config_refresh_worker_running": config_refresh_workers_running,
        },
        "reasons": reasons,
    }
    if fresh_snapshots:
        payload["workers"] = [
            {
                "pid": snapshot.get("pid"),
                "age_seconds": snapshot.get("age_seconds"),
                "active_sessions": snapshot.get("active_sessions"),
                "config_available": snapshot.get("config_available"),
                "circuits_open": snapshot.get("circuits_open", []),
                "write_queue_size": snapshot.get("write_queue_size"),
            }
            for snapshot in fresh_snapshots
        ]
    return http_status, payload


def _config_to_dict(cfg: RestaurantConfig) -> dict:
    return {
        "name": cfg.name,
        "phone": cfg.phone,
        "address": cfg.address,
        "branches": cfg.branches,
        "hours": cfg.hours,
        "menu_items": cfg.menu_items,
        "upsell_rules": cfg.upsell_rules,
        "is_open": cfg.is_open,
        "closed_reason": cfg.closed_reason,
        "degraded_mode": cfg.degraded_mode,
        "config_source": cfg.config_source,
        "wait_minutes": cfg.wait_minutes,
        "min_guests": cfg.min_guests,
        "max_guests": cfg.max_guests,
        "delivery_enabled": cfg.delivery_enabled,
        "delivery_minutes": cfg.delivery_minutes,
        "delivery_fee": cfg.delivery_fee,
        "min_order": cfg.min_order,
        "delivery_zones": cfg.delivery_zones,
    }


def _config_from_dict(data: dict) -> RestaurantConfig:
    return RestaurantConfig(
        name=data.get("name", ""),
        phone=data.get("phone", ""),
        address=data.get("address", ""),
        branches=data.get("branches", []),
        hours=data.get("hours", {}),
        menu_items=data.get("menu_items", []),
        upsell_rules=data.get("upsell_rules", []),
        is_open=data.get("is_open", True),
        closed_reason=data.get("closed_reason", ""),
        degraded_mode=data.get("degraded_mode", False),
        config_source=data.get("config_source", "backend"),
        wait_minutes=data.get("wait_minutes", 20),
        min_guests=data.get("min_guests", 1),
        max_guests=data.get("max_guests", 20),
        delivery_enabled=data.get("delivery_enabled", False),
        delivery_minutes=data.get("delivery_minutes", 45),
        delivery_fee=float(data.get("delivery_fee", 0.0)),
        min_order=float(data.get("min_order", 0.0)),
        delivery_zones=data.get("delivery_zones", []),
    )


async def _read_shared_cache_map() -> dict:
    if not CONFIG_SHARED_CACHE_ENABLED:
        return {}
    path = _runtime_file_path(CONFIG_SHARED_CACHE_PATH)
    if not await asyncio.to_thread(path.exists):
        return {}
    try:
        async with worker_context().shared_cache_lock:
            raw = _json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        logger.warning("shared config cache read failed | path=%s | %s", path, _exc_log_fields(exc))
        return {}


def _shared_cache_entry_age_seconds(entry: dict | None) -> float | None:
    if not entry:
        return None
    fetched_at_epoch = float(entry.get("fetched_at_epoch", 0.0) or 0.0)
    if fetched_at_epoch <= 0:
        return None
    return max(0.0, time.time() - fetched_at_epoch)


async def _read_shared_cache_entry(cache_key: str) -> tuple[RestaurantConfig, float] | None:
    shared_map = await _read_shared_cache_map()
    entry = shared_map.get(cache_key)
    if not isinstance(entry, dict):
        return None
    age = _shared_cache_entry_age_seconds(entry)
    config_data = entry.get("config")
    if not isinstance(config_data, dict) or age is None:
        return None
    return _config_from_dict(config_data), age


async def _write_shared_cache_entry(cache_key: str, cfg: RestaurantConfig) -> None:
    if not CONFIG_SHARED_CACHE_ENABLED:
        return
    path = _runtime_file_path(CONFIG_SHARED_CACHE_PATH)
    await _ensure_parent_dir(path)
    async with worker_context().shared_cache_lock:
        shared_map: dict[str, Any] = {}
        if await asyncio.to_thread(path.exists):
            try:
                shared_map = _json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                if not isinstance(shared_map, dict):
                    shared_map = {}
            except Exception:
                shared_map = {}
        shared_map[cache_key] = {
            "fetched_at_epoch": time.time(),
            "source": "backend",
            "config": _config_to_dict(cfg),
        }
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        await asyncio.to_thread(tmp_path.write_text, _json.dumps(shared_map, ensure_ascii=False), encoding="utf-8")
        await asyncio.to_thread(os.replace, tmp_path, path)


def _stt_context_terms_for_config(cfg: RestaurantConfig) -> list[str]:
    candidates = [cfg.name]
    candidates.extend(str(item.get("name", "")).strip() for item in cfg.menu_items[:SESSION_STT_KEYTERM_LIMIT])
    candidates.extend(str(branch.get("name", "")).strip() for branch in cfg.branches)
    candidates.extend(str(zone).strip() for zone in cfg.delivery_zones)
    candidates.extend([
        "كوشري", "تيكاواي", "توصيل", "دليفري", "حجز", "ترابيزة",
        "شكوى", "أوردر", "اسم", "رقم الموبايل",
    ])
    candidates.extend(part.strip() for part in SESSION_STT_EXTRA_KEYTERMS.split(","))

    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        text = re.sub(r"\s+", " ", item or "").strip()
        if len(text) < 2:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= SESSION_STT_KEYTERM_LIMIT:
            break
    return result


def _build_stt_for_provider(
    provider: str,
    cfg: RestaurantConfig,
    *,
    client_reference_id: str | None = None,
) -> Any:
    """Construct a single STT instance for ``provider``.

    Caller must have already validated readiness via
    ``_stt_provider_ready_reason``.
    """
    context_terms = _stt_context_terms_for_config(cfg)
    if provider == "deepgram":
        keyterms = list(context_terms)[:120] or None
        kwargs: dict[str, Any] = {
            "model": SESSION_STT_DEEPGRAM_MODEL,
            "language": SESSION_STT_DEEPGRAM_LANGUAGE,
            "interim_results": True,
            "punctuate": True,
            "smart_format": True,
            "no_delay": True,
            "endpointing_ms": SESSION_STT_DEEPGRAM_ENDPOINTING_MS,
            "filler_words": True,
            "api_key": os.getenv("DEEPGRAM_API_KEY", ""),
        }
        if keyterms:
            try:
                return deepgram.STT(keyterm=keyterms, **kwargs)
            except TypeError:
                return deepgram.STT(keyterms=keyterms, **kwargs)
        return deepgram.STT(**kwargs)

    return soniox.STT(
        base_url=SESSION_STT_BASE_URL,
        params=_session_stt_options(
            context_terms=context_terms,
            client_reference_id=client_reference_id,
        ),
    )


# Phase 3.2 — STT fallback chain. When enabled, the call's STT is wrapped
# in livekit's ``FallbackAdapter`` so a primary outage (Soniox websocket
# timeout, Deepgram API 5xx) automatically falls through to the secondary
# provider without dropping the call.
SESSION_STT_FALLBACK_ENABLED = _get_env_bool("SESSION_STT_FALLBACK_ENABLED", False)


def _build_session_stt(
    cfg: RestaurantConfig,
    *,
    client_reference_id: str | None = None,
    provider_override: str | None = None,
) -> Any:
    """Build the STT instance for this call.

    Phase 1.2: support a Deepgram Nova-3 branch alongside Soniox. The
    chosen provider is recorded on ``SESSION_STT_PROVIDER`` so telemetry
    + the dashboard can split metrics by provider.

    Phase 3.2: when ``SESSION_STT_FALLBACK_ENABLED=1`` AND the *other*
    provider is also configured, wrap both in ``FallbackAdapter`` so a
    transient primary outage doesn't drop the call.
    """
    global SESSION_STT_PROVIDER
    provider = _resolve_stt_provider(override=provider_override)
    not_ready_reason = _stt_provider_ready_reason(provider)
    if not_ready_reason:
        raise RuntimeError(not_ready_reason)

    SESSION_STT_PROVIDER = provider
    primary = _build_stt_for_provider(provider, cfg, client_reference_id=client_reference_id)

    if not SESSION_STT_FALLBACK_ENABLED:
        return primary

    secondary_provider = "deepgram" if provider == "soniox" else "soniox"
    if _stt_provider_ready_reason(secondary_provider) is not None:
        return primary  # No usable secondary; degrade silently.

    try:
        secondary = _build_stt_for_provider(
            secondary_provider, cfg, client_reference_id=client_reference_id,
        )
    except Exception as exc:
        logger.warning(
            "STT secondary build failed | provider=%s | %s", secondary_provider, exc,
        )
        return primary

    try:
        from livekit.agents.stt import FallbackAdapter as _STTFallbackAdapter  # noqa: E402
        adapter = _STTFallbackAdapter([primary, secondary])
        logger.info(
            "STT fallback chain armed | primary=%s | secondary=%s",
            provider, secondary_provider,
        )
        return adapter
    except Exception as exc:
        logger.warning("STT FallbackAdapter init failed — using primary only | %s", exc)
        return primary


def backend_config_available() -> bool:
    return worker_context().runtime_health.config_available


def backend_write_available(health: "CallWriteHealth | None" = None) -> bool:
    if health is None:
        return True
    if health.write_blocked_until_monotonic > time.monotonic():
        return False
    return health.write_available


def session_dependencies_ready() -> bool:
    return all([SESSION_LLM, SESSION_TTS, SESSION_VAD]) and _stt_provider_ready_reason() is None


def _can_attempt_backend_write(ud: "UserData") -> bool:
    if not BACKEND_BASE or not BACKEND_APIKEY:
        return False
    if not backend_write_available(ud.write_health):
        return False
    # degraded config means we lost config read-path, but we may still try write-path
    # later if health says it is available. So we don't block degraded mode by itself.
    return True


async def fetch_config(call_id: str, restaurant_id: str = "") -> RestaurantConfig:
    """
    جلب إعدادات المطعم مع أولوية واضحة:
    fresh cache -> stale cache -> backend fetch ضمن budget -> degraded fallback.
    """
    ctx = worker_context()
    cache_key = restaurant_id or "__default__"
    endpoint = f"{BACKEND_BASE}/restaurant/config"
    stale_fallback: RestaurantConfig | None = None
    cache_age: float | None = None

    async with ctx.config_lock:
        cached_entry = ctx.config_cache.get(cache_key)
        cache_age = _config_cache_age_seconds(cached_entry)
        if cached_entry is None:
            logger.info("call=%s | config cache MISS | restaurant=%s", call_id, cache_key)
            _emit_event("config.cache", call_id=call_id, state="miss", restaurant=cache_key)
        elif cache_age is not None and cache_age <= CONFIG_CACHE_TTL:
            logger.info(
                "call=%s | config cache HIT fresh | restaurant=%s | age=%.2fs",
                call_id, cache_key, cache_age,
            )
            _emit_event("config.cache", call_id=call_id, state="hit_fresh", restaurant=cache_key, age_s=round(cache_age, 3))
            cfg = _clone_config_with_source(cached_entry.config, "cache_fresh")
            ctx.runtime_health.config_available = True
            _schedule_worker_health_snapshot("config_cache_hit_fresh")
            logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
            return cfg
        elif cached_entry is not None:
            logger.warning(
                "call=%s | config cache HIT stale | restaurant=%s | age=%.2fs | action=refresh_backend",
                call_id, cache_key, cache_age or 0.0,
            )
            _emit_event("config.cache", call_id=call_id, state="hit_stale", restaurant=cache_key, age_s=round(cache_age or 0.0, 3))
            stale_fallback = _clone_config_with_source(cached_entry.config, "cache_stale")

    async with ctx.config_refresh_lock:
        async with ctx.config_lock:
            cached_entry = ctx.config_cache.get(cache_key)
            cache_age = _config_cache_age_seconds(cached_entry)
            if cached_entry is not None and cache_age is not None and cache_age <= CONFIG_CACHE_TTL:
                logger.info(
                    "call=%s | config cache HIT fresh-after-wait | restaurant=%s | age=%.2fs",
                    call_id, cache_key, cache_age,
                )
                _emit_event("config.cache", call_id=call_id, state="hit_fresh_after_wait", restaurant=cache_key, age_s=round(cache_age, 3))
                cfg = _clone_config_with_source(cached_entry.config, "cache_fresh")
                ctx.runtime_health.config_available = True
                _schedule_worker_health_snapshot("config_cache_hit_after_wait")
                logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
                return cfg

        shared_entry = await _read_shared_cache_entry(cache_key)
        shared_cfg = shared_entry[0] if shared_entry else None
        shared_age = shared_entry[1] if shared_entry else None

        if shared_cfg is not None and shared_age is not None:
            if shared_age <= CONFIG_CACHE_TTL:
                logger.info(
                    "call=%s | shared config cache HIT fresh | restaurant=%s | age=%.2fs",
                    call_id, cache_key, shared_age,
                )
                _emit_event("config.cache", call_id=call_id, state="shared_hit_fresh", restaurant=cache_key, age_s=round(shared_age, 3))
                cfg = _clone_config_with_source(shared_cfg, "cache_fresh")
                async with ctx.config_lock:
                    ctx.config_cache[cache_key] = CachedConfigEntry(
                        fetched_at_monotonic=time.monotonic() - shared_age,
                        config=_clone_config_with_source(shared_cfg, "backend"),
                        source="shared_cache",
                    )
                    ctx.runtime_health.config_available = True
                _schedule_worker_health_snapshot("config_shared_cache_hit_fresh")
                logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
                return cfg
            logger.warning(
                "call=%s | shared config cache HIT stale | restaurant=%s | age=%.2fs | action=refresh_backend",
                call_id, cache_key, shared_age,
            )
            _emit_event("config.cache", call_id=call_id, state="shared_hit_stale", restaurant=cache_key, age_s=round(shared_age, 3))
            if stale_fallback is None or (cache_age is not None and shared_age < cache_age):
                stale_fallback = _clone_config_with_source(shared_cfg, "cache_stale")

        headers: dict[str, str] = {"X-Runtime-Source": "voice-agent"}
        params: dict[str, str] = {}
        if restaurant_id:
            headers["X-Restaurant-ID"] = restaurant_id
            params["restaurant_id"] = restaurant_id

        deadline = time.monotonic() + CONFIG_FETCH_TOTAL_BUDGET_SECONDS
        last_exc: Exception | None = None
        for attempt in range(CONFIG_FETCH_RETRIES):
            remaining_budget = deadline - time.monotonic()
            if remaining_budget <= 0:
                logger.warning(
                    "call=%s | config fetch budget exhausted | budget=%.2fs | attempts=%d",
                    call_id, CONFIG_FETCH_TOTAL_BUDGET_SECONDS, attempt,
                )
                break

            try:
                t0 = time.monotonic()
                client = await _get_http_client()
                logger.info(
                    "call=%s | config fetch start | attempt=%d | endpoint=%s | budget_left=%.2fs",
                    call_id, attempt + 1, endpoint, remaining_budget,
                )
                res = await client.get(
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=httpx.Timeout(timeout=max(0.05, min(HTTP_TIMEOUT_SECONDS, remaining_budget))),
                )
                res.raise_for_status()
                d = res.json()
                latency_ms = int((time.monotonic() - t0) * 1000)

                cfg = RestaurantConfig(
                    name=d["name"],
                    phone=d.get("phone", ""),
                    address=d.get("address", ""),
                    branches=d.get("branches", []),
                    hours=d.get("hours", {}),
                    menu_items=d.get("menu_items", []),
                    upsell_rules=d.get("upsell_rules", []),
                    is_open=d.get("is_open", True),
                    closed_reason=d.get("closed_reason", ""),
                    wait_minutes=d.get("wait_minutes", 20),
                    min_guests=d.get("min_guests", 1),
                    max_guests=d.get("max_guests", 20),
                    delivery_enabled=d.get("delivery_enabled", False),
                    delivery_minutes=d.get("delivery_minutes", 45),
                    delivery_fee=float(d.get("delivery_fee", 0.0)),
                    min_order=float(d.get("min_order", 0.0)),
                    delivery_zones=d.get("delivery_zones", []),
                    degraded_mode=False,
                    config_source="backend",
                )
                async with ctx.config_lock:
                    ctx.config_cache[cache_key] = CachedConfigEntry(
                        fetched_at_monotonic=time.monotonic(),
                        config=cfg,
                        source="backend",
                    )
                    ctx.runtime_health.config_available = True
                    ctx.runtime_health.last_config_error = ""
                _emit_event(
                    "config.cache",
                    call_id=call_id,
                    state="backend_loaded",
                    restaurant=cache_key,
                    latency_ms=latency_ms,
                )
                _schedule_worker_health_snapshot("config_backend_loaded")
                try:
                    await _write_shared_cache_entry(cache_key, cfg)
                except Exception as exc:
                    logger.warning(
                        "call=%s | shared cache write skipped | restaurant=%s | %s",
                        call_id, cache_key, _exc_log_fields(exc),
                    )
                logger.info(
                    "call=%s | config loaded | restaurant=%s | source=backend | open=%s | delivery=%s | items=%d | latency=%dms",
                    call_id, cache_key, cfg.is_open, cfg.delivery_enabled, len(cfg.menu_items), latency_ms,
                )
                logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
                return cfg

            except Exception as exc:
                last_exc = exc
                ctx.runtime_health.last_config_error = _exc_log_fields(exc)
                _schedule_worker_health_snapshot("config_backend_failed")
                wait = _retry_delay(attempt, CONFIG_FETCH_BACKOFF_SECONDS)
                logger.warning(
                    "call=%s | config fetch failed | attempt=%d | endpoint=%s | %s | retry in %.2fs",
                    call_id, attempt + 1, endpoint, _exc_log_fields(exc), wait,
                )
                if attempt < CONFIG_FETCH_RETRIES - 1 and (deadline - time.monotonic()) > wait:
                    await asyncio.sleep(wait)

        if stale_fallback is not None:
            ctx.runtime_health.config_available = True
            _emit_event(
                "config.cache",
                call_id=call_id,
                state="stale_fallback",
                restaurant=cache_key,
                last_error=_exc_log_fields(last_exc) if last_exc else "",
            )
            _schedule_worker_health_snapshot("config_stale_fallback")
            logger.warning(
                "call=%s | stale cache used fallback | restaurant=%s | age=%.2fs | last_error=%s",
                call_id, cache_key, cache_age or 0.0,
                _exc_log_fields(last_exc) if last_exc else "none",
            )
            logger.info("call=%s | config source chosen | source=%s", call_id, stale_fallback.config_source)
            return stale_fallback

        degraded_cfg = _degraded_config()
        ctx.runtime_health.config_available = False
        _emit_event(
            "config.cache",
            call_id=call_id,
            state="degraded_fallback",
            restaurant=cache_key,
            last_error=_exc_log_fields(last_exc) if last_exc else "",
        )
        _schedule_worker_health_snapshot("config_degraded")
        logger.error(
            "call=%s | degraded mode entered | restaurant=%s | source=%s | last_error=%s",
            call_id, cache_key, degraded_cfg.config_source,
            _exc_log_fields(last_exc) if last_exc else "none",
        )
        logger.info("call=%s | config source chosen | source=%s", call_id, degraded_cfg.config_source)
        return degraded_cfg


# ─────────────────────────────────────────────────────────────────────────────
# Backend helpers — retry + idempotency + latency logging
# ─────────────────────────────────────────────────────────────────────────────
def _backend_queue_lock_instance() -> asyncio.Lock:
    return worker_context().backend_queue_lock


def _backend_queue_instance() -> asyncio.Queue[dict[str, Any]]:
    return worker_context().backend_write_queue


def _backend_queue_path() -> Path:
    return _runtime_file_path(BACKEND_WRITE_QUEUE_PATH)


def _normalize_backend_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    idempotency_key = str(normalized.get("idempotency_key", "")).strip()
    if not idempotency_key:
        call_id = str(normalized.get("call_id", "")).strip()
        action = str(normalized.get("idempotency_action", "")).strip()
        payload = normalized.get("payload", {})
        if call_id and action and isinstance(payload, dict):
            idempotency_key = _idempotency_key(call_id, action, payload)
    if idempotency_key:
        normalized["idempotency_key"] = idempotency_key
    return normalized


def _dedupe_backend_queue_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    skipped = 0
    for item in items:
        normalized = _normalize_backend_queue_item(item)
        key = str(normalized.get("idempotency_key", "")).strip()
        if key:
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)
        deduped.append(normalized)
    return deduped, skipped


def _parse_backend_queue_recovery_lines(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    invalid = 0
    for line in lines:
        try:
            parsed = _json.loads(line)
        except Exception:
            invalid += 1
            continue
        if isinstance(parsed, dict):
            items.append(_normalize_backend_queue_item(parsed))
        else:
            invalid += 1
    return items, invalid


def _cap_backend_queue_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES)
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _backend_endpoint_class(endpoint: str) -> str:
    return endpoint.strip().lower() or "unknown"


def _get_backend_circuit(endpoint: str) -> BackendCircuitState:
    """Get or create circuit state for an endpoint class.

    SAFETY: must only be called while holding ``ctx.circuit_lock``.
    """
    ctx = worker_context()
    key = _backend_endpoint_class(endpoint)
    state = ctx.backend_circuits.get(key)
    if state is None:
        state = BackendCircuitState()
        ctx.backend_circuits[key] = state
    return state


async def _backend_circuit_is_open(endpoint: str) -> bool:
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        return state.open_until_monotonic > time.monotonic()


async def _record_backend_circuit_success(endpoint: str) -> None:
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        was_open = state.open_until_monotonic > time.monotonic()
        had_failures = state.consecutive_failures > 0
        state.consecutive_failures = 0
        state.open_until_monotonic = 0.0
        state.last_error = ""
    if was_open or had_failures:
        _emit_event("backend.circuit", endpoint=endpoint, state="closed")
        _schedule_worker_health_snapshot("circuit_closed")


async def _record_backend_circuit_failure(endpoint: str, exc: Exception) -> None:
    if not _should_retry_backend_error(exc):
        return
    async with worker_context().circuit_lock:
        state = _get_backend_circuit(endpoint)
        state.consecutive_failures += 1
        state.last_error = _exc_log_fields(exc)
        if state.consecutive_failures >= BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD:
            state.open_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS
            logger.warning(
                "backend circuit opened | endpoint=%s | failures=%d | open_for=%.2fs | %s",
                endpoint, state.consecutive_failures, BACKEND_WRITE_CIRCUIT_OPEN_SECONDS, state.last_error,
            )
            _emit_event(
                "backend.circuit",
                endpoint=endpoint,
                state="open",
                failures=state.consecutive_failures,
                open_for_s=BACKEND_WRITE_CIRCUIT_OPEN_SECONDS,
                error=state.last_error,
            )
            _schedule_worker_health_snapshot("circuit_open")


def _mark_backend_circuit_open(health: CallWriteHealth | None) -> None:
    if health is None:
        return
    health.write_available = False
    health.last_write_error = "type=CircuitOpen"
    health.last_write_failure_kind = "CircuitOpen"
    health.last_write_status_code = None
    health.write_blocked_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS
    _schedule_worker_health_snapshot("circuit_blocked")


async def _enqueue_backend_write(
    endpoint: str,
    payload: dict,
    call_id: str,
    *,
    idempotency_action: str,
    idempotency_key: str = "",
) -> bool:
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return False

    item = {
        "endpoint": endpoint,
        "payload": payload,
        "call_id": call_id,
        "idempotency_action": idempotency_action,
        "idempotency_key": idempotency_key,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    queue = _backend_queue_instance()
    try:
        queue.put_nowait(item)
        logger.warning("call=%s | backend write queued | endpoint=%s", call_id, endpoint)
        _emit_event(
            "backend.queue",
            call_id=call_id,
            endpoint=endpoint,
            target="memory",
            size=queue.qsize(),
        )
        _schedule_worker_health_snapshot("queue_memory")
        return True
    except asyncio.QueueFull:
        logger.error("backend in-memory queue full | endpoint=%s | size=%d", endpoint, queue.qsize())
        return await _append_backend_queue_recovery_items([item], call_id=call_id, endpoint=endpoint)


async def _read_backend_queue_recovery_lines() -> list[str]:
    queue_path = _backend_queue_path()
    if not await asyncio.to_thread(queue_path.exists):
        return []
    async with _backend_queue_lock_instance():
        if not await asyncio.to_thread(queue_path.exists):
            return []
        raw = await asyncio.to_thread(queue_path.read_text, encoding="utf-8")
    return [line for line in raw.splitlines() if line.strip()]


async def _rewrite_backend_queue_recovery_lines(lines: list[str]) -> None:
    queue_path = _backend_queue_path()
    async with _backend_queue_lock_instance():
        if not lines:
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(queue_path.unlink)
            return
        await _ensure_parent_dir(queue_path)
        tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
        payload = "\n".join(lines) + "\n"
        await asyncio.to_thread(tmp_path.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(os.replace, tmp_path, queue_path)


async def _append_backend_queue_recovery_items(
    items: list[dict[str, Any]],
    *,
    call_id: str,
    endpoint: str,
) -> bool:
    if not items:
        return True
    queue_path = _backend_queue_path()
    async with _backend_queue_lock_instance():
        existing_items: list[dict[str, Any]] = []
        invalid_existing = 0
        if await asyncio.to_thread(queue_path.exists):
            raw = await asyncio.to_thread(queue_path.read_text, encoding="utf-8")
            existing_items, invalid_existing = _parse_backend_queue_recovery_lines(
                [line for line in raw.splitlines() if line.strip()]
            )
        if invalid_existing:
            logger.warning(
                "backend recovery queue dropped invalid lines during append | path=%s | count=%d",
                queue_path,
                invalid_existing,
            )
        await _ensure_parent_dir(queue_path)
        combined_items, skipped_duplicates = _dedupe_backend_queue_items(existing_items + items)
        capped_items, dropped_due_cap = _cap_backend_queue_items(combined_items)
        if skipped_duplicates:
            logger.warning(
                "backend recovery queue skipped duplicates | path=%s | count=%d",
                queue_path,
                skipped_duplicates,
            )
        if dropped_due_cap:
            logger.error(
                "backend recovery queue cap reached | path=%s | cap=%d | dropped=%d",
                queue_path,
                BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES,
                dropped_due_cap,
            )
        new_lines = [_json.dumps(item, ensure_ascii=False) for item in capped_items]
        tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
        payload = "\n".join(new_lines) + "\n"
        await asyncio.to_thread(tmp_path.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(os.replace, tmp_path, queue_path)
    logger.warning("call=%s | backend write queued to recovery file | endpoint=%s", call_id, endpoint)
    _emit_event(
        "backend.queue",
        call_id=call_id,
        endpoint=endpoint,
        target="recovery_file",
        queued_items=len(capped_items),
        skipped_duplicates=skipped_duplicates,
        dropped=dropped_due_cap,
    )
    _schedule_worker_health_snapshot("queue_recovery")
    return dropped_due_cap == 0


async def _submit_queued_backend_write(item: dict[str, Any]) -> bool:
    endpoint = str(item.get("endpoint", "")).strip()
    if not endpoint or await _backend_circuit_is_open(endpoint):
        return False
    result = await _post(
        endpoint,
        item.get("payload", {}),
        str(item.get("call_id", "queued-write")),
        idempotency_action=str(item.get("idempotency_action", "")),
        max_retries=1,
        write_health=None,
        enqueue_on_retryable_failure=False,
    )
    return result is not None


async def _drain_backend_write_queue_once() -> None:
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    queue = _backend_queue_instance()
    in_memory_items: list[dict[str, Any]] = []
    while True:
        try:
            in_memory_items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    recovery_lines = await _read_backend_queue_recovery_lines()
    recovery_items, invalid_recovery_lines = _parse_backend_queue_recovery_lines(recovery_lines)
    if invalid_recovery_lines:
        logger.warning(
            "backend queue dropped invalid recovery lines | path=%s | count=%d",
            _backend_queue_path(),
            invalid_recovery_lines,
        )

    remaining_items: list[dict[str, Any]] = []
    pending_items, skipped_duplicates = _dedupe_backend_queue_items(recovery_items + in_memory_items)
    if skipped_duplicates:
        logger.warning(
            "backend queue replay skipped duplicates | count=%d",
            skipped_duplicates,
        )
    for item in pending_items:
        try:
            success = await _submit_queued_backend_write(item)
        except Exception:
            logger.exception("backend queue worker submit error")
            success = False
        if not success:
            remaining_items.append(item)

    for _ in in_memory_items:
        with contextlib.suppress(ValueError):
            queue.task_done()

    deduped_remaining_items, skipped_remaining_duplicates = _dedupe_backend_queue_items(remaining_items)
    if skipped_remaining_duplicates:
        logger.warning(
            "backend queue remaining-items dedupe skipped duplicates | count=%d",
            skipped_remaining_duplicates,
        )
    capped_remaining_items, dropped_remaining_due_cap = _cap_backend_queue_items(deduped_remaining_items)
    if dropped_remaining_due_cap:
        logger.error(
            "backend recovery queue cap reached during rewrite | path=%s | cap=%d | dropped=%d",
            _backend_queue_path(),
            BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES,
            dropped_remaining_due_cap,
        )
    recovery_remaining_lines = [_json.dumps(item, ensure_ascii=False) for item in capped_remaining_items]
    await _rewrite_backend_queue_recovery_lines(recovery_remaining_lines)
    _schedule_worker_health_snapshot("queue_drain")


async def _backend_queue_worker_loop() -> None:
    _BATCH_SIZE = 10
    _MAX_BACKOFF = 60.0
    backoff = BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS
    while True:
        try:
            queue = _backend_queue_instance()
            # Wait for at least one item
            first_item = await queue.get()
            batch: list[dict[str, Any]] = [first_item]
            # Drain up to _BATCH_SIZE more items that are already queued
            for _ in range(_BATCH_SIZE - 1):
                try:
                    batch.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            failed_items: list[dict[str, Any]] = []
            for item in batch:
                try:
                    success = await _submit_queued_backend_write(item)
                except Exception:
                    logger.exception("backend queue worker submit error")
                    success = False
                if not success:
                    failed_items.append(item)
                with contextlib.suppress(ValueError):
                    queue.task_done()

            if failed_items:
                await _append_backend_queue_recovery_items(
                    failed_items,
                    call_id="batch-retry",
                    endpoint="batch",
                )

            # Also drain recovery file items
            await _drain_backend_write_queue_once()
            # Reset backoff on successful cycle
            backoff = BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS
        except asyncio.CancelledError:
            # Graceful shutdown — flush remaining queue to recovery file
            leftovers: list[dict[str, Any]] = []
            queue = _backend_queue_instance()
            while True:
                try:
                    leftovers.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if leftovers:
                with contextlib.suppress(Exception):
                    await _append_backend_queue_recovery_items(
                        leftovers,
                        call_id="worker-shutdown",
                        endpoint="batch",
                    )
            logger.info("backend queue worker shutting down | flushed=%d items", len(leftovers))
            raise
        except Exception:
            logger.exception("backend queue worker error | backoff=%.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)


async def _ensure_backend_queue_worker_started() -> None:
    ctx = worker_context()
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    if ctx.backend_queue_worker is not None and not ctx.backend_queue_worker.done():
        return
    ctx.backend_queue_worker = asyncio.create_task(
        _backend_queue_worker_loop(),
        name="backend_write_queue_worker",
    )
    logger.info("backend queue worker started | path=%s", _backend_queue_path())
    _schedule_worker_health_snapshot("queue_worker_started")
    await _drain_backend_write_queue_once()


# ─────────────────────────────────────────────────────────────────────────────
# Config auto-refresh — proactively keeps cache warm so calls never wait
# ─────────────────────────────────────────────────────────────────────────────

async def _config_refresh_loop() -> None:
    """Background task that refreshes the config cache at regular intervals."""
    while True:
        try:
            await asyncio.sleep(CONFIG_REFRESH_INTERVAL_SECONDS)
            ctx = worker_context()
            async with ctx.config_lock:
                keys = list(ctx.config_cache.keys())
            for key in keys:
                try:
                    restaurant_id = key if key != "__default__" else ""
                    cfg = await fetch_config(f"refresh-{key}", restaurant_id=restaurant_id)
                    logger.info("config refresh | key=%s | source=%s", key, cfg.config_source)
                except Exception:
                    logger.warning("config refresh failed | key=%s", key, exc_info=True)
        except asyncio.CancelledError:
            logger.info("config refresh worker shutting down")
            raise
        except Exception:
            logger.exception("config refresh loop error")
            await asyncio.sleep(60.0)


async def _ensure_config_refresh_started() -> None:
    ctx = worker_context()
    if ctx.config_refresh_worker is not None and not ctx.config_refresh_worker.done():
        return
    ctx.config_refresh_worker = asyncio.create_task(
        _config_refresh_loop(),
        name="config_refresh_worker",
    )
    logger.info("config refresh worker started | interval=%.0fs", CONFIG_REFRESH_INTERVAL_SECONDS)
    _schedule_worker_health_snapshot("config_refresh_started")


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiting — protect against session floods and runaway loops
# ─────────────────────────────────────────────────────────────────────────────

async def _acquire_session_slot(call_id: str) -> bool:
    """Try to acquire a session slot. Returns False if at capacity."""
    ctx = worker_context()
    async with ctx.active_sessions_lock:
        if ctx.active_sessions >= MAX_CONCURRENT_SESSIONS:
            logger.warning(
                "call=%s | session rejected | reason=at_capacity | active=%d | max=%d",
                call_id, ctx.active_sessions, MAX_CONCURRENT_SESSIONS,
            )
            return False
        ctx.active_sessions += 1
        logger.info("call=%s | session acquired | active=%d", call_id, ctx.active_sessions)
        _schedule_worker_health_snapshot("session_acquired")
        return True


async def _release_session_slot(call_id: str) -> None:
    """Release a session slot."""
    ctx = worker_context()
    async with ctx.active_sessions_lock:
        ctx.active_sessions = max(0, ctx.active_sessions - 1)
        logger.info("call=%s | session released | active=%d", call_id, ctx.active_sessions)
        _schedule_worker_health_snapshot("session_released")


def _increment_turn_count(call_id: str) -> int:
    """Increment and return turn count for a session. Returns new count.

    SAFETY: atomic under CPython GIL — no awaits or yields between the dict
    read and write.  Do NOT insert async operations between the ``get()``
    and ``[call_id] = count`` lines; doing so would create a race condition
    under concurrent coroutines.
    """
    ctx = worker_context()
    count = ctx.turn_counts.get(call_id, 0) + 1
    ctx.turn_counts[call_id] = count
    return count


def _cleanup_turn_count(call_id: str) -> None:
    """Remove turn count tracking for a finished session.

    SAFETY: single dict operation — atomic under CPython GIL.
    """
    ctx = worker_context()
    ctx.turn_counts.pop(call_id, None)


def _mark_backend_write_success(health: CallWriteHealth | None) -> None:
    if health is None:
        return
    health.write_available = True
    health.last_write_error = ""
    health.last_write_failure_kind = ""
    health.last_write_status_code = None
    health.write_blocked_until_monotonic = 0.0


def _mark_backend_write_failure(exc: Exception, health: CallWriteHealth | None) -> None:
    if health is None:
        return
    health.last_write_error = _exc_log_fields(exc)
    health.last_write_failure_kind = exc.__class__.__name__
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        health.last_write_status_code = status_code
        # 4xx means backend reachable; don't mark the current call write path unavailable.
        if 400 <= status_code < 500:
            health.write_available = True
            health.write_blocked_until_monotonic = 0.0
            return

    health.write_available = False
    health.write_blocked_until_monotonic = time.monotonic() + 5.0



async def _post(
    endpoint: str,
    payload: dict,
    call_id: str,
    *,
    idempotency_action: str = "",
    tool_timeout: float | None = None,
    max_retries: int = BACKEND_MAX_RETRIES,
    write_health: CallWriteHealth | None = None,
    enqueue_on_retryable_failure: bool = True,
) -> dict | None:
    """
    POST مع retry exponential backoff وإضافة Idempotency-Key تلقائياً.
    - max_retries: عدد المحاولات (default=3)
    - idempotency_action: اسم العملية للـ idempotency key (مثلاً 'takeaway')
    """
    full_url = f"{BACKEND_BASE}{endpoint}"
    headers: dict[str, str] = {"X-Runtime-Source": "voice-agent"}
    effective_timeout = BACKEND_POST_TIMEOUT_SECONDS if tool_timeout is None else max(0.05, float(tool_timeout))
    idempotency_key = ""
    if idempotency_action:
        idempotency_key = _idempotency_key(call_id, idempotency_action, payload)
        headers["Idempotency-Key"] = idempotency_key

    if await _backend_circuit_is_open(endpoint):
        _mark_backend_circuit_open(write_health)
        logger.warning("call=%s | POST blocked by circuit | endpoint=%s", call_id, endpoint)
        if enqueue_on_retryable_failure:
            queued = await _enqueue_backend_write(
                endpoint,
                payload,
                call_id,
                idempotency_action=idempotency_action,
                idempotency_key=idempotency_key,
            )
            if queued:
                return {"queued": True}
        return None

    last_exc: Exception | None = None
    retries = max(1, max_retries)
    attempts_made = 0
    for attempt in range(retries):
        try:
            attempts_made = attempt + 1
            t0 = time.monotonic()
            client = await _get_http_client()
            logger.info(
                "call=%s | POST start | endpoint=%s | attempt=%d | action=%s",
                call_id, endpoint, attempt + 1, idempotency_action or "-",
            )
            res = await client.post(
                full_url,
                json=payload,
                headers=headers,
                timeout=effective_timeout,
            )
            res.raise_for_status()
            data = res.json()
            latency_ms = int((time.monotonic() - t0) * 1000)
            _mark_backend_write_success(write_health)
            await _record_backend_circuit_success(endpoint)
            logger.info(
                "call=%s | POST end | endpoint=%s | attempt=%d | latency=%dms | status_code=%d | ref=%s",
                call_id, endpoint, attempt + 1, latency_ms,
                res.status_code,
                data.get("order_id") or data.get("reservation_id") or "",
            )
            return data
        except Exception as exc:
            last_exc = exc
            _mark_backend_write_failure(exc, write_health)
            should_retry = _should_retry_backend_error(exc)
            wait = _retry_delay(attempt, BACKEND_RETRY_BASE_SECONDS)
            logger.warning(
                "call=%s | POST failed | endpoint=%s | attempt=%d | retryable=%s | %s%s",
                call_id, endpoint, attempt + 1, should_retry, _exc_log_fields(exc),
                f" | retry in {wait:.2f}s" if should_retry and attempt < retries - 1 else "",
            )
            if not should_retry:
                await _record_backend_circuit_success(endpoint)
                break
            if attempt < retries - 1:
                await asyncio.sleep(wait)

    if last_exc is not None:
        await _record_backend_circuit_failure(endpoint, last_exc)
        if enqueue_on_retryable_failure and _should_retry_backend_error(last_exc):
            queued = await _enqueue_backend_write(
                endpoint,
                payload,
                call_id,
                idempotency_action=idempotency_action,
                idempotency_key=idempotency_key,
            )
            if queued:
                logger.warning("call=%s | POST deferred to queue | endpoint=%s", call_id, endpoint)
                return {"queued": True}

    logger.error(
        "call=%s | POST exhausted | endpoint=%s | attempts=%d | %s",
        call_id, endpoint, attempts_made, _exc_log_fields(last_exc) if last_exc else "unknown",
    )
    return None


async def submit_takeaway(ud: "UserData") -> dict | None:
    order_items = _build_order_items(ud.order or [], ud.restaurant.menu_items)
    return await _post("/orders", {
        "call_id":          ud.call_id,
        "type":             "takeaway",
        "customer_name":    ud.customer_name,
        "customer_phone":   ud.customer_phone,
        "order_items":      order_items,
        "special_requests": ud.special_requests,
        "order_time":       datetime.now(timezone.utc).isoformat(),
        "upsell_accepted":  ud.upsell_accepted,
        "channel":          "voice_agent",
    }, ud.call_id, idempotency_action="takeaway", write_health=ud.write_health)


async def submit_delivery(ud: "UserData") -> dict | None:
    order_items = _build_order_items(ud.order or [], ud.restaurant.menu_items)
    return await _post("/orders", {
        "call_id":           ud.call_id,
        "type":              "delivery",
        "customer_name":     ud.customer_name,
        "customer_phone":    ud.customer_phone,
        "order_items":       order_items,
        "special_requests":  ud.special_requests,
        "delivery_address":  ud.delivery_address,
        "delivery_zone":     ud.delivery_zone,
        "delivery_landmark": ud.delivery_landmark,
        "order_time":        datetime.now(timezone.utc).isoformat(),
        "upsell_accepted":   ud.upsell_accepted,
        "channel":           "voice_agent",
    }, ud.call_id, idempotency_action="delivery", write_health=ud.write_health)


async def submit_reservation(ud: "UserData") -> dict | None:
    return await _post("/reservations", {
        "call_id":          ud.call_id,
        "customer_name":    ud.customer_name,
        "customer_phone":   ud.customer_phone,
        "reservation_time": ud.reservation_time,
        "reservation_time_iso": ud.reservation_time_iso,
        "guests_count":     ud.guests_count,
        "branch":           ud.selected_branch,
        "notes":            ud.reservation_notes,
        "channel":          "voice_agent",
    }, ud.call_id, idempotency_action="reservation", write_health=ud.write_health)


async def submit_complaint(ud: "UserData", text: str, ctype: str) -> dict | None:
    return await _post("/complaints", {
        "call_id":        ud.call_id,
        "customer_name":  ud.customer_name,
        "customer_phone": ud.customer_phone,
        "complaint_text": text,
        "complaint_type": ctype,
        "logged_at":      datetime.now(timezone.utc).isoformat(),
        "channel":        "voice_agent",
    }, ud.call_id, idempotency_action="complaint", write_health=ud.write_health)


def _call_outcome_and_failure_reason(ud: "UserData", close_reason: str) -> tuple[str, str]:
    if ud.order_confirmed:
        return "order_confirmed", ""
    if ud.reservation_confirmed:
        return "reservation_confirmed", ""
    if ud.complaint_logged:
        return "complaint_logged", ""
    if ud.handoff_target:
        return "handoff", ""

    if close_reason == "inactivity_timeout":
        return "abandoned", "customer_inactive"
    if close_reason == "call_timeout":
        return "failed", "max_duration_reached"
    if close_reason == "session_error":
        return "failed", "session_error"

    flow = (ud.active_flow or "").strip().lower()
    if flow in {"takeaway", "delivery"}:
        if not ud.order:
            return "closed_without_action", "no_order_items"
        if not ud.customer_name:
            return "closed_without_action", "missing_name"
        if not ud.customer_phone:
            return "closed_without_action", "missing_phone"
        if flow == "delivery" and not ud.delivery_address:
            return "closed_without_action", "missing_address"
        return "closed_without_action", "order_not_confirmed"

    if flow == "reservation":
        if not ud.reservation_time:
            return "closed_without_action", "missing_reservation_time"
        if not ud.guests_count:
            return "closed_without_action", "missing_guests_count"
        if len(ud.restaurant.branches) > 1 and not ud.selected_branch:
            return "closed_without_action", "missing_branch"
        if not ud.customer_name:
            return "closed_without_action", "missing_name"
        if not ud.customer_phone:
            return "closed_without_action", "missing_phone"
        return "closed_without_action", "reservation_not_confirmed"

    if flow == "complaint":
        if not ud.complaint_text:
            return "closed_without_action", "missing_complaint_text"
        if not ud.complaint_type:
            return "closed_without_action", "missing_complaint_type"
        if not ud.customer_phone:
            return "closed_without_action", "missing_phone"
        return "closed_without_action", "complaint_not_submitted"

    return "closed_without_action", "ended_without_action"


def _call_review_status(outcome: str, failure_reason: str) -> str:
    if outcome in {"order_confirmed", "reservation_confirmed", "complaint_logged", "handoff"}:
        return "reviewed"
    if failure_reason in {"ended_without_action", ""}:
        return "needs_review"
    return "needs_review"


def _call_ai_response_summary(ud: "UserData", outcome: str, close_reason: str) -> str:
    summary = (ud.last_agent_message or "").strip()
    if summary:
        return summary
    if outcome == "order_confirmed":
        return f"تم تثبيت الطلب {ud.order_id or ''}".strip()
    if outcome == "reservation_confirmed":
        return f"تم تثبيت الحجز {ud.reservation_id or ''}".strip()
    if outcome == "complaint_logged":
        return "تم تسجيل الشكوى وهيتم المتابعة"
    if outcome == "handoff":
        return f"تم تحويل المكالمة إلى {ud.handoff_target}".strip()
    if close_reason == "inactivity_timeout":
        return "المكالمة اتقفلت بسبب عدم الرد"
    if close_reason == "call_timeout":
        return "المكالمة اتقفلت بعد انتهاء الوقت المتاح"
    if close_reason == "session_error":
        return "المكالمة اتقفلت بعد خطأ تقني"
    return "انتهت المكالمة بدون إجراء نهائي"


async def submit_call_log(
    ud: "UserData",
    *,
    close_reason: str,
    duration_seconds: int,
    started_at_iso: str,
    ended_at_iso: str,
) -> dict | None:
    outcome, failure_reason = _call_outcome_and_failure_reason(ud, close_reason)
    return await _post("/calls/upsert", {
        "call_id": ud.call_id,
        "customer_name": ud.customer_name or "",
        "customer_phone": ud.customer_phone or "",
        "flow": ud.active_flow or "",
        "transcript_excerpt": (ud.last_user_message or "")[:280],
        "agent_reply_excerpt": (ud.last_agent_message or "")[:280],
        "last_message": (ud.last_user_message or "")[:280],
        "ai_response": _call_ai_response_summary(ud, outcome, close_reason)[:280],
        "status": "closed",
        "order_total": float(ud.order_total or 0.0),
        "outcome": outcome,
        "failure_reason": failure_reason,
        "close_reason": close_reason,
        "review_status": _call_review_status(outcome, failure_reason),
        "review_notes": "",
        "handoff_target": ud.handoff_target or None,
        "duration_seconds": max(0, int(duration_seconds)),
        "started_at": started_at_iso,
        "ended_at": ended_at_iso,
    }, ud.call_id, idempotency_action="call-log", write_health=ud.write_health)


# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────
def _phone_capture_short_reply(ud: "UserData", partial_digits: str) -> str:
    local_digits = _local_phone_digits(partial_digits)
    remaining = max(0, 11 - len(local_digits))
    if remaining <= 0:
        return ""
    spoken = phone2ar(local_digits)
    if remaining <= 4:
        return f"معايا {spoken}، وآخر {_digit_count_ar(remaining)}؟"
    return f"معايا {spoken}، كمّل الباقي."


def _digit_count_ar(n: int) -> str:
    _MAP = {1: "رقم", 2: "رقمين", 3: "تلات أرقام", 4: "أربع أرقام",
            5: "خمس أرقام", 6: "ست أرقام", 7: "سبع أرقام", 8: "ثمن أرقام"}
    return _MAP.get(n, f"{n} أرقام")


def _phone_capture_failure_reply(ud: "UserData") -> str:
    if ud.phone_capture_failures <= 1:
        return "مسمعتش الرقم كويس يا فندم، ممكن تعيده تاني؟"
    if ud.phone_capture_failures == 2:
        return "الرقم لازم يكون 11 رقم يا فندم، ممكن تقوله تاني؟"
    return "الرقم 11 رقم ويبدأ بزيرو عشرة أو زيرو حداشر أو زيرو اتناشر أو زيرو خمستاشر."


def _set_phone_capture_mode(ud: "UserData", enabled: bool) -> None:
    ud.phone_capture_mode = enabled
    if not enabled:
        ud.phone_capture_turns = 0
        ud.phone_capture_failures = 0


async def _maybe_submit_pending_complaint_for_flow(ud: "UserData", flow_name: str) -> str:
    missing = _complaint_next_missing_slot(ud)
    if flow_name != "complaint":
        logger.info("call=%s | complaint_submit_skipped_reason=wrong_flow", ud.call_id)
        return ""
    if ud.complaint_logged:
        logger.info("call=%s | complaint_submit_skipped_reason=already_logged", ud.call_id)
        return ""
    if ud.complaint_submit_in_flight:
        logger.info("call=%s | complaint_submit_skipped_reason=in_flight", ud.call_id)
        return ""
    if missing:
        logger.info("call=%s | complaint_pending | missing=%s", ud.call_id, missing)
        return ""
    if not _can_attempt_backend_write(ud):
        logger.warning("call=%s | complaint_submit_skipped_reason=write_unavailable", ud.call_id)
        return " الشكوى محفوظة مبدئيًا ولسه محتاجة تثبيت في النظام."

    ud.complaint_submit_in_flight = True
    try:
        result = await submit_complaint(ud, ud.complaint_text, ud.complaint_type)
        if result is not None and result.get("queued"):
            logger.warning("call=%s | complaint_deferred_to_queue", ud.call_id)
            return f" {_backend_queued_user_message('complaint')}"
        if result is not None:
            ud.complaint_logged = True
            logger.info("call=%s | complaint_submitted", ud.call_id)
            _emit_event("complaint.submitted", call_id=ud.call_id, flow="complaint", complaint_type=ud.complaint_type)
            return " تم تثبيت الشكوى في النظام."
        logger.warning("call=%s | complaint_submit_skipped_reason=backend_failed", ud.call_id)
        return " الشكوى محفوظة مبدئيًا ولسه محتاجة تثبيت في النظام."
    finally:
        ud.complaint_submit_in_flight = False


def _next_step_hint_for_flow(flow: str, ud: "UserData") -> str:
    if flow == "takeaway":
        missing = _takeaway_next_missing_slot(ud)
        if missing:
            return f"المطلوب دلوقتي: اسأل عن {missing}."
        return "لو العميل أكد، ثبّت الطلب فورًا."

    if flow == "delivery":
        missing = _delivery_next_missing_slot(ud)
        if missing:
            return f"المطلوب دلوقتي: اسأل عن {missing}."
        return "لو العميل أكد، ثبّت الطلب فورًا."

    if flow == "reservation":
        missing = _reservation_next_missing_slot(ud, ud.restaurant)
        if missing:
            return f"المطلوب دلوقتي: اسأل عن {missing}."
        return "لو العميل أكد، ثبّت الحجز فورًا."

    if flow == "complaint":
        missing = _complaint_next_missing_slot(ud)
        if missing == "الشكوى":
            return "المطلوب دلوقتي: اسمع الشكوى وسجلها."
        if missing:
            return f"المطلوب دلوقتي: اسأل عن {missing}."
        return "اسأل لو محتاج حاجة تانية."

    return "خليك في خطوة واحدة وسؤال واحد بس."


def _spoken_order_items(order: list[str] | None) -> str:
    items = [item.strip() for item in (order or []) if item and item.strip()]
    return "، ".join(items)


def _takeaway_confirmation_prompt(ud: "UserData") -> str:
    from core.dialogue_engine import takeaway_confirmation_prompt
    return takeaway_confirmation_prompt(ud)


def _delivery_confirmation_prompt(ud: "UserData") -> str:
    from core.dialogue_engine import delivery_confirmation_prompt
    return delivery_confirmation_prompt(ud)


def _reservation_confirmation_prompt(ud: "UserData") -> str:
    from core.dialogue_engine import reservation_confirmation_prompt
    return reservation_confirmation_prompt(ud)


def _is_confirmation_prompt(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return False
    normalized = _normalize_ar(cleaned)
    return cleaned.endswith("صح؟") or (" باسم " in f" {normalized} " and " صح " in f" {normalized} ")


def _clean_followup_note(note: str) -> str:
    return re.sub(r"\s+", " ", (note or "")).strip(" .")


def _followup_after_name(flow: str, ud: "UserData") -> str:
    if flow == "complaint":
        return _ask_phone() if not ud.customer_phone else "تحب حاجة تانية؟"
    return _next_slot_question_for_flow(flow, ud)


def _followup_after_phone(flow: str, ud: "UserData") -> str:
    return _next_slot_question_for_flow(flow, ud)


def _next_slot_question_for_flow(flow: str, ud: "UserData") -> str:
    from core.dialogue_engine import DialogueEngine
    return DialogueEngine().next_question(flow, ud)


def _followup_after_special_request(flow: str, ud: "UserData") -> str:
    return _next_slot_question_for_flow(flow, ud)


def _special_request_followup_message(flow: str, ud: "UserData", *, accepted_item: str | None = None) -> str:
    next_question = _followup_after_special_request(flow, ud)
    if accepted_item:
        return _voice_safe_text(
            _join_user_phrases(f"تمام يا فندم، ضفت {accepted_item} وسجلت الملاحظة على الطلب", next_question),
            max_chars=180,
        )
    return _voice_safe_text(
        _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", next_question),
        max_chars=180,
    )


def _join_user_phrases(*parts: str) -> str:
    cleaned_parts = [re.sub(r"\s+", " ", (part or "")).strip(" .") for part in parts if (part or "").strip()]
    if not cleaned_parts:
        return ""
    text = ". ".join(cleaned_parts)
    if not text.endswith(("؟", ".", "!", "؟.")):
        text += "."
    return text


def _complaint_followup_question(ud: "UserData") -> str:
    missing = _complaint_next_missing_slot(ud)
    if missing == "الاسم":
        return _ask_name()
    if missing == "رقم الموبايل":
        return _ask_phone()
    return "تحب حاجة تانية؟"


async def _apply_phone_update(ud: "UserData", phone_text: str, *, flow_name: str) -> str:
    incoming_digits = _phone_digits_only(phone_text)
    if not incoming_digits:
        ud.phone_capture_failures += 1
        _set_phone_capture_mode(ud, True)
        _emit_event("phone.capture", call_id=ud.call_id, flow=flow_name, result="failure", reason="no_digits")
        return _phone_capture_failure_reply(ud)

    direct_valid = validate_phone(incoming_digits)
    combined_digits = _merge_phone_digits(ud.pending_phone_digits, incoming_digits)
    combined_valid = validate_phone(combined_digits)

    cleaned = direct_valid or combined_valid
    if cleaned:
        ud.customer_phone = cleaned
        was_chunked = bool(ud.pending_phone_digits)
        ud.pending_phone_digits = ""
        _set_phone_capture_mode(ud, False)
        logger.info("call=%s | phone set", ud.call_id)
        _emit_event(
            "phone.capture",
            call_id=ud.call_id,
            flow=flow_name,
            result="success",
            chunked=was_chunked,
        )
        complaint_note = await _maybe_submit_pending_complaint_for_flow(ud, flow_name)
        note = _clean_followup_note(complaint_note)
        followup = _followup_after_phone(flow_name, ud)
        critical_followup = _is_confirmation_prompt(followup) or _is_flow_ready_for_confirmation(flow_name, ud)
        with contextlib.suppress(Exception):
            ud.confirmation_pending = bool(critical_followup)
            if critical_followup:
                ud.confirmation_received = False
        phone_confirm = f"تمام، {phone2ar(cleaned)}" if was_chunked else _ack()
        if note:
            return _voice_safe_text(
                _join_user_phrases(note, followup),
                max_chars=200,
                critical=critical_followup,
            )
        if followup:
            return _voice_safe_text(
                _join_user_phrases(phone_confirm, followup),
                max_chars=200,
                critical=critical_followup,
            )
        return _voice_safe_text(f"{phone_confirm}.")

    partial_digits = combined_digits if _is_plausible_partial_phone_digits(combined_digits) else ""
    if partial_digits:
        ud.pending_phone_digits = partial_digits
        ud.phone_capture_turns += 1
        _set_phone_capture_mode(ud, True)
        logger.info("call=%s | phone_partial_buffered | digits=%d", ud.call_id, len(partial_digits))
        _emit_event(
            "phone.capture",
            call_id=ud.call_id,
            flow=flow_name,
            result="partial",
            buffered_digits=len(partial_digits),
        )
        return _phone_capture_short_reply(ud, partial_digits)

    ud.pending_phone_digits = ""
    ud.phone_capture_failures += 1
    _set_phone_capture_mode(ud, True)
    _emit_event("phone.capture", call_id=ud.call_id, flow=flow_name, result="failure", reason="invalid_number")
    return _phone_capture_failure_reply(ud)


async def _apply_name_update(ud: "UserData", name_text: str, *, flow_name: str) -> str:
    cleaned = _extract_name_candidate(name_text)
    if not cleaned:
        _emit_event("name.capture", call_id=ud.call_id, flow=flow_name, result="failure")
        return _voice_safe_text("الاسم مش واضح، قولي الاسم بس يا فندم.")
    ud.customer_name = cleaned
    logger.info("call=%s | name=%s", ud.call_id, cleaned)
    _emit_event("name.capture", call_id=ud.call_id, flow=flow_name, result="success")
    complaint_note = await _maybe_submit_pending_complaint_for_flow(ud, flow_name)
    note = _clean_followup_note(complaint_note)
    _set_phone_capture_mode(ud, _flow_missing_phone(flow_name, ud))
    followup = _followup_after_name(flow_name, ud)
    if note:
        return _voice_safe_text(_join_user_phrases(note, followup), max_chars=200)
    return _voice_safe_text(
        f"{_ack()} يا {cleaned}. {followup}",
        max_chars=200,
    )


_COMPLAINT_TYPE_ALIASES = {
    "order_issue": {
        "order_issue", "order", "طلب", "مشكله طلب", "مشكلة طلب", "غلط في الطلب",
        "اوردر", "الاوردر", "طلب غلط",
    },
    "quality": {
        "quality", "جوده", "جودة", "الاكل", "الأكل", "طعم", "جوده الاكل", "جودة الاكل",
    },
    "service": {
        "service", "خدمه", "خدمة", "معامله", "معاملة", "كول سنتر", "موظف", "الرد",
    },
    "delivery": {
        "delivery", "توصيل", "الدليفري", "دليفري", "المندوب", "الطيار", "العنوان",
    },
    "other": {"other", "اخرى", "أخرى", "غير كده", "غير ذلك", "مشكله", "مشكلة"},
}
_DATE_HINTS = {
    "النهارده", "اليوم", "بكره", "بكرة", "غدا", "بعد بكره", "بعد بكرة",
    "السبت", "الاحد", "الأحد", "الاثنين", "الاتنين", "الثلاثاء", "التلات",
    "الاربعاء", "الأربعاء", "الخميس", "الجمعه", "الجمعة",
}
_TIME_HINTS = {
    "الساعه", "الساعة", "ص", "م", "am", "pm", "صبح", "الصبح", "مساء",
    "بالليل", "ليل", "العصر", "الظهر", "المغرب", "الفجر",
}


def _normalize_complaint_type(value: str) -> str | None:
    norm = _normalize_ar(value)
    if not norm:
        return None
    for canonical, aliases in _COMPLAINT_TYPE_ALIASES.items():
        if norm in {_normalize_ar(alias) for alias in aliases}:
            return canonical
    return None


def _resolve_branch_name(branch: str, branches: list[dict]) -> str | None:
    target = _normalize_ar(branch)
    if not target:
        return None

    exact_match: str | None = None
    best_partial: tuple[int, str] | None = None
    for item in branches:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        norm_name = _normalize_ar(name)
        if norm_name == target:
            exact_match = name
            break
        if target in norm_name or norm_name in target:
            score = abs(len(norm_name) - len(target))
            if best_partial is None or score < best_partial[0]:
                best_partial = (score, name)

    if exact_match:
        return exact_match
    return best_partial[1] if best_partial else None


# ─────────────────────────────────────────────────────────────────────────────
# Config cache + runtime health
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_CACHE_TTL = _get_env_float("CONFIG_CACHE_TTL", 60.0, min_value=1.0)

# ─────────────────────────────────────────────────────────────────────────────
# Idempotency key
# ─────────────────────────────────────────────────────────────────────────────

def _idempotency_key(call_id: str, action: str, payload: dict) -> str:
    """ينشئ مفتاح idempotency فريد من call_id + action + hash(payload)."""
    h = hashlib.sha256(
        _json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    return f"{call_id}-{action}-{h}"

# ─────────────────────────────────────────────────────────────────────────────
RunContext_T = RunContext[UserData]

NEGATIVE_WORDS = {
    "لا", "لأ", "لاا", "مفيش", "مفيش طلب", "مفيش حاجه", "مفيش حاجة",
    "خلاص", "بس كده", "تمام كده", "لا تمام", "لا شكرا", "لا شكرًا",
    "ولا حاجه", "ولا حاجة", "no", "none",
    "آه لا", "اه لا", "آه لأ", "اه لأ", "لا مفيش", "لا خلاص", "لا كده تمام",
    "لا تمام كده", "آه مفيش", "اه مفيش",
}

_NEGATIVE_FORMS = frozenset(_normalize_ar(word) for word in NEGATIVE_WORDS)

POSITIVE_CONFIRMATION_WORDS = {
    "صح", "صح كده", "ايوه", "أيوه", "ماشي", "مظبوط", "تمام", "تمام كده",
    "تمام يا فندم", "أوكي", "اوكي", "yes",
}

UPSELL_ACCEPT_WORDS = {
    "ضيف", "ضيفها", "ضيفه", "ضيفهم", "حط", "حطها", "حطه", "حطهم",
    "زود", "زودها", "زوده", "زودهم", "هات", "هاتها", "هاته", "هاتهم",
    "عايزها", "عايزه", "عاوزها", "عاوزه", "ماشي ضيفها", "تمام ضيفها",
    "ايوه ضيفها", "أيوه ضيفها", "ايوه حطها", "أيوه حطها",
    "هضيف", "هضيفها", "هحط", "هحطها", "هزود", "هزودها", "اضيف", "أضيف",
}

UPSELL_ITEM_REQUEST_WORDS = {
    "عايز", "عاوزه", "عاوز", "ممكن", "هات", "ضيف", "حط", "زود",
}

UPSELL_REJECTION_WORDS = {
    "بلاش", "مش عايز", "مش عاوز", "مش محتاج", "مش عايزها", "مش عاوزها",
    "لا بلاش", "لا مش عايز", "لا مش عاوز", "لا شكرا", "لا شكرًا",
    "لا ميرسي", "لا مرسي", "مش دلوقتي", "خلينا كده", "كفاية كده",
}

THANKS_WORDS = {
    "شكرا", "شكرا جدا", "شكرا جزيلا", "متشكر", "متشكره", "ميرسي",
    "تسلم", "تسلمي", "thanks", "thank you",
}

ADDRESS_DETAIL_WORDS = {
    "شارع", "عمارة", "عماره", "برج", "بلوك", "دور", "شقة", "شقه", "فيلا",
    "بناية", "بنايه", "ميدان", "أمام", "امام", "قدام", "جنب", "خلف",
}


def _looks_empty_answer(text: str | None) -> bool:
    normalized = _normalize_ar(text or "")
    if not normalized:
        return True
    for word in _NEGATIVE_FORMS:
        if normalized == word:
            return True
        if normalized.startswith(f"{word} "):
            tail = normalized[len(word):].strip()
            if not tail:
                return True
            if all(token in _EMPTY_TAIL_WORDS for token in tail.split()):
                return True
    return False


_ACK_PHRASES = [
    "تمام يا فندم", "حاضر يا فندم", "ماشي يا فندم", "تمام",
    "أكيد", "حاضر", "طبعاً", "ماشي",
]
_ACK_GOT_IT = ["معايا", "سجلت", "أخدت", "تمام معايا", "حلو"]
_NEXT_NAME = [
    "اسمك إيه يا فندم؟", "الاسم إيه يا فندم؟", "ممكن اسم حضرتك؟",
    "والاسم إيه؟", "اسمك إيه؟",
]
_NEXT_PHONE = [
    "ورقم موبايلك؟", "رقم الموبايل يا فندم؟", "ورقم حضرتك؟",
    "ممكن رقم الموبايل؟", "ورقمك إيه؟", "والموبايل؟",
]
_NEXT_SPECIAL = [
    "في أي طلب خاص في التحضير؟", "حابب تضيف أي ملاحظة على الطلب؟",
    "عندك أي طلب خاص؟", "في أي ملاحظة معينة في التحضير؟",
]
_NEXT_ADDRESS = [
    "عنوانك إيه يا فندم؟", "العنوان إيه يا فندم؟", "ممكن العنوان؟",
    "فين هنوصلك؟", "العنوان إيه؟",
]
_EMPTY_TAIL_WORDS = {
    "تمام", "خلاص", "كده", "بس", "شكرا", "شكرًا", "ميرسي", "متشكر",
    "يا", "فندم", "لو", "سمحت", "حضرتك", "اوكي", "أوكي", "ماشي", "حاضر",
}
_SPECIAL_REQUEST_HINTS = {
    "من غير", "بدون", "سخنه", "سخنة", "حار", "بارد", "صوص", "شطه", "شطة",
    "كاتشب", "مايونيز", "جبنه", "جبنة", "بصل", "طماطم", "مخلل", "تقطيع",
    "مقطعه", "مقطعة", "مقرمشه", "مقرمشة", "زياده", "زيادة", "على جنب",
    "خلي", "خليها", "خليه", "تكون", "استواء", "رفيعه", "رفيعة", "ناشف",
    "طرية", "طريه", "زيادة جبنة", "من غير بصل", "من غير طماطم",
}

def _ack() -> str:
    return _random.choice(_ACK_PHRASES)

def _ack_got(thing: str) -> str:
    return f"{_random.choice(_ACK_GOT_IT)} {thing}"

def _ask_name() -> str:
    return _random.choice(_NEXT_NAME)

def _ask_phone() -> str:
    return _random.choice(_NEXT_PHONE)

def _ask_special() -> str:
    return _random.choice(_NEXT_SPECIAL)

def _ask_address() -> str:
    return _random.choice(_NEXT_ADDRESS)


def _format_order_item(name: str, qty: int) -> str:
    return name if qty <= 1 else f"{name} × {qty}"


def _is_thanks_message(text: str) -> bool:
    return _contains_normalized_phrase(text, THANKS_WORDS)


def _is_positive_confirmation(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized or _is_thanks_message(text):
        return False
    if _contains_normalized_phrase(text, POSITIVE_CONFIRMATION_WORDS):
        return len(normalized.split()) <= 4
    return False


def _is_explicit_upsell_acceptance(text: str, item_name: str | None) -> bool:
    normalized = _normalize_ar(text)
    if not normalized or _is_thanks_message(text) or _looks_empty_answer(text):
        return False

    if _contains_normalized_phrase(text, UPSELL_ACCEPT_WORDS):
        return True

    item_normalized = _normalize_ar(item_name or "")
    if not item_normalized:
        return False

    mentions_item = _normalized_phrase_present(normalized, item_normalized)
    if not mentions_item:
        return False

    if normalized == item_normalized:
        return True
    if _contains_normalized_phrase(text, POSITIVE_CONFIRMATION_WORDS | UPSELL_ITEM_REQUEST_WORDS):
        return True
    return False


def _is_explicit_upsell_rejection(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    if _looks_empty_answer(text):
        return True
    if _contains_normalized_phrase(text, UPSELL_REJECTION_WORDS):
        return True
    # "لا وبس لو البرجر يكون حار" — starts with a negative word, treat as rejection
    _REJECTION_PREFIXES = {_normalize_ar(w) for w in ("لا", "لأ", "لاا")}
    first_token = normalized.split()[0] if normalized else ""
    if first_token in _REJECTION_PREFIXES and len(normalized.split()) > 1:
        return True
    return False


_NEGATE_SPECIAL_PHRASES = {
    "مفيش طلبات", "مفيش طلب خاص", "مفيش حاجة خاصة", "مفيش ملاحظات",
    "مفيش طلبات خاصة", "لا مفيش", "من غير طلبات", "لا عادي",
    "لا مفيش حاجة", "كده وبس", "بس كده", "خلاص كده",
}

def _upsell_reply_negates_special(text: str) -> bool:
    """Check if the upsell reply also negates special requests — only when no real special request is present."""
    if _extract_special_request_candidate(text):
        return False
    normalized = _normalize_ar(text or "")
    return any(_normalize_ar(phrase) in normalized for phrase in _NEGATE_SPECIAL_PHRASES)


def _extract_special_request_candidate(text: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    if not raw or _looks_empty_answer(raw) or _is_thanks_message(raw):
        return None

    cleaned = re.sub(
        r"^\s*(?:بس|وبس|ولو|لو|طيب|طب|يعني|معلش|ممكن|حاضر|تمام يا فندم|تمام|ماشي|أيوه|ايوه)\b[\s،,]*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip(" ،,.")
    if not cleaned or _looks_empty_answer(cleaned):
        return None

    normalized = _normalize_ar(cleaned)
    if any(_normalize_ar(hint) in normalized for hint in _SPECIAL_REQUEST_HINTS):
        return cleaned
    return None


def _extract_special_request_after_upsell_reply(text: str, item_name: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    if not raw:
        return None

    cleaned = raw
    if item_name:
        cleaned = re.sub(re.escape(item_name), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:هضيف|هضيفها|هحط|هحطها|هزود|هزودها|ضيف|ضيفها|حط|حطها|زود|زودها|هات|هاتها|"
        r"أيوه|ايوه|تمام|ماشي|حاضر|لا|لأ|شكرا|شكرًا|ميرسي|لو سمحت|يا فندم|وبس|بس)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ،,.")
    return _extract_special_request_candidate(cleaned)


def _address_seems_specific(address: str) -> bool:
    normalized = _normalize_ar(address)
    if not normalized:
        return False

    raw = (address or "").translate(_AR_DIGITS)
    has_number = bool(re.search(r"\d", raw))
    detail_hits = sum(1 for word in ADDRESS_DETAIL_WORDS if _normalize_ar(word) in normalized)
    token_count = len(normalized.split())

    if has_number and detail_hits >= 1:
        return True
    if detail_hits >= 2:
        return True
    if "شارع" in normalized and token_count >= 3:
        return True
    return False


def _extract_zone_from_address(address: str, delivery_zones: list[str] | None) -> str:
    """Try to extract zone from address text by matching known delivery zones."""
    if not delivery_zones:
        return address.strip().split(",")[-1].strip() if "," in address else address.strip()
    addr_norm = _normalize_ar(address)
    for z in delivery_zones:
        if _normalize_ar(z) in addr_norm:
            return z
    # Fallback: use the last part of the address as zone guess
    parts = re.split(r"[،,\-]", address)
    return parts[-1].strip() if len(parts) > 1 else address.strip()


def _quantity_token_to_int(token: str | None) -> int | None:
    normalized = _normalize_ar(token or "")
    if len(normalized) > 1 and normalized.startswith("و"):
        normalized = normalized[1:]
    if not normalized:
        return None
    if re.fullmatch(r"\d{1,2}", normalized):
        value = int(normalized)
        return value if 1 <= value <= 20 else None
    from nlp.arabic import SPOKEN_DIGIT_MAP
    mapped = SPOKEN_DIGIT_MAP.get(normalized)
    if mapped and mapped.isdigit():
        value = int(mapped)
        return value if 1 <= value <= 20 else None
    return None


def _menu_token_matches_turn_token(turn_token: str, menu_token: str, *, first_menu_token: bool) -> bool:
    if turn_token == menu_token:
        return True
    return first_menu_token and len(turn_token) > 1 and turn_token.startswith("و") and turn_token[1:] == menu_token


def _quantity_before_menu_item(tokens: list[str], item_start: int) -> int | None:
    if item_start <= 0:
        return None
    raw_token = tokens[item_start - 1]
    qty = _quantity_token_to_int(raw_token)
    if qty is None:
        return None
    if raw_token.startswith("و"):
        return qty
    if item_start == 1:
        return qty
    previous = tokens[item_start - 2]
    if previous in {"عايز", "عاوزه", "عاوز", "عاوزة", "اطلب", "أطلب", "طلب", "هات", "خد", "ضيف", "زود"}:
        return qty
    return None


def _extract_menu_order_items_from_text(text: str, cfg: RestaurantConfig) -> list[str]:
    """Extract obvious menu-item mentions from a user turn as a deterministic fallback."""
    normalized = _normalize_ar(text)
    if not normalized:
        return []
    tokens = normalized.split()
    extracted: list[tuple[int, str]] = []
    seen: set[str] = set()

    for item in cfg.menu_items or []:
        if not item.get("available", True):
            continue
        name = str(item.get("name", "")).strip()
        menu_tokens = _normalize_ar(name).split()
        if not name or not menu_tokens:
            continue

        for idx in range(0, len(tokens) - len(menu_tokens) + 1):
            window = tokens[idx: idx + len(menu_tokens)]
            if not all(
                _menu_token_matches_turn_token(turn_token, menu_token, first_menu_token=pos == 0)
                for pos, (turn_token, menu_token) in enumerate(zip(window, menu_tokens))
            ):
                continue
            key = _normalize_ar(name)
            if key in seen:
                break
            before = _quantity_before_menu_item(tokens, idx)
            after_index = idx + len(menu_tokens)
            after = _quantity_token_to_int(tokens[after_index] if after_index < len(tokens) else None)
            qty = before or after or 1
            extracted.append((idx, _format_order_item(name, qty)))
            seen.add(key)
            break

    return [item for _, item in sorted(extracted, key=lambda pair: pair[0])]


def _turn_has_add_order_intent(text: str) -> bool:
    """Detect "add to existing order" intents on the deterministic path.

    Consults the Phase 2 mutation parser first so cues like
    "ضيفلي / زود / كمان / هاتلي كمان" are recognised consistently with
    the engine's mutation classifier. Falls back to the legacy hint
    sets so historical phrasings keep working.
    """
    if not (text or "").strip():
        return False
    from core.order_mutations import parse_mutation

    intent = parse_mutation(text)
    if intent.kind == "replace":
        return False
    if intent.kind in {"add", "increase"}:
        return True

    normalized = _normalize_ar(text)
    if not normalized:
        return False
    if _contains_normalized_phrase(normalized, _ORDER_REPLACE_HINTS):
        return False
    return (
        _contains_normalized_phrase(normalized, _ORDER_ADD_HINTS)
        or _contains_normalized_phrase(normalized, _ORDER_HINTS)
    )


_PHASE2_EXTRACTOR_DISABLED = os.getenv("PHASE2_ORDER_EXTRACTOR", "1") == "0"
_MENU_INDEX_CACHE: dict[int, "MenuIndex"] = {}


def _menu_index_for(cfg: RestaurantConfig) -> "MenuIndex":
    """Return a cached MenuIndex for this config; rebuild on identity change."""
    from core.menu_index import MenuIndex
    key = id(cfg)
    cached = _MENU_INDEX_CACHE.get(key)
    if cached is not None and cached.config_version == _menu_index_version(cfg):
        return cached
    index = MenuIndex.build(
        cfg.menu_items or [],
        config_version=_menu_index_version(cfg),
    )
    _MENU_INDEX_CACHE[key] = index
    return index


def _menu_index_version(cfg: RestaurantConfig) -> str:
    """Cheap fingerprint over the menu so cache invalidates on edits."""
    items = cfg.menu_items or []
    parts = [
        f"{item.get('name','')}:{item.get('price','')}:{int(bool(item.get('available', True)))}"
        for item in items
    ]
    return "|".join(parts)


def _phase2_extract_items(text: str, cfg: RestaurantConfig) -> list[str]:
    """Run the Phase 2 deterministic extractor over a turn.

    Returns formatted items (e.g. ``["برجر كبير × 2", "كولا"]``) when the
    extraction is confident and unambiguous. Otherwise returns an empty
    list so the caller can fall back to the legacy path or the LLM.
    """
    if _PHASE2_EXTRACTOR_DISABLED:
        return []
    from core.extractors.order_extractor import extract_order
    index = _menu_index_for(cfg)
    if index.is_empty():
        return []
    extraction = extract_order(text, index)
    if extraction.is_empty() or extraction.has_ambiguity():
        return []
    return extraction.formatted_items()


def _should_capture_order_turn(flow: str, ud: UserData, text: str, cfg: RestaurantConfig) -> list[str]:
    if flow not in {"takeaway", "delivery"} or ud.pending_upsell_item:
        return []

    # Prefer the LLM understanding (validated against the menu); fall
    # back to the deterministic extractor when no provider is configured
    # or the LLM call failed for this turn.
    items: list[str] | None = None
    try:
        from core.understanding import get_or_extract_for_turn
        from core.understanding_bridge import (
            mutation_from_understanding,
            order_items_from_understanding,
        )
        understanding = get_or_extract_for_turn(ud, text, flow)
        items = order_items_from_understanding(understanding, cfg)
        mutation = mutation_from_understanding(understanding)
    except Exception:
        items = None
        mutation = None

    if items is None:
        items = _phase2_extract_items(text, cfg)
        if not items:
            items = _extract_menu_order_items_from_text(text, cfg)
        mutation = None

    if not items:
        return []
    if not ud.order:
        return items
    if mutation in {"add", "increase"} or _turn_has_add_order_intent(text):
        return items
    if mutation == "replace":
        return items
    return []


def _append_order_followup_if_ready(flow: str, ud: UserData, message: str) -> str:
    if not ud.order or ud.pending_upsell_item:
        return message
    if "؟" in message or "?" in message:
        return message
    followup = _next_slot_question_for_flow(flow, ud)
    if not followup:
        return message
    return _voice_safe_text(_join_user_phrases(message, followup), max_chars=220)


def _looks_like_delivery_address_turn(text: str, cfg: RestaurantConfig) -> bool:
    """Decide whether a turn should be intercepted as a delivery address.

    Tries the Phase 3 ``extract_address`` first for cases the legacy
    substring scan misses (zones with the ``ال`` definite article,
    landmark words wrapped in punctuation, etc.). Falls back to the
    legacy heuristic when the new extractor is uncertain.
    """
    if not (text or "").strip():
        return False
    from core.extractors.address_extractor import (
        MEDIUM_CONFIDENCE,
        extract_address,
    )
    capture = extract_address(text, delivery_zones=tuple(cfg.delivery_zones or ()))
    if capture.value is not None and capture.confidence >= MEDIUM_CONFIDENCE:
        return True

    normalized = _normalize_ar(text)
    if not normalized or _is_phone_like_text(text):
        return False
    if _extract_name_candidate(text) and not any(_normalize_ar(w) in normalized for w in ADDRESS_DETAIL_WORDS):
        return False
    if any(_normalize_ar(word) in normalized for word in ADDRESS_DETAIL_WORDS):
        return True
    if cfg.delivery_zones:
        return any(_normalize_ar(zone) in normalized for zone in cfg.delivery_zones)
    return False


_DELIVERY_HINTS = {"توصيل", "دليفري", "الدليفري", "وصله", "يوصل", "delivery"}
_TAKEAWAY_HINTS = {"تيكاواي", "takeaway", "استلام", "اجي استلمه", "آجي استلمه", "هاخده", "اخده من المطعم"}
_ORDER_HINTS = {"اوردر", "أوردر", "طلب", "اطلب", "عايز اطلب", "أطلب"}
_MENU_HINTS = {
    "المنيو", "المتاح", "ايه المتاح", "إيه المتاح", "المتاح ايه", "إيه عندك",
    "ايه عندك", "عندك ايه", "عندكم ايه", "الاصناف", "الأصناف", "السعر",
    "الاسعار", "الأسعار", "ممكن اعرف المتاح",
}
_DELIVERY_ZONE_HINTS = {
    "فين متاح", "متاح فين", "بتوصلوا فين", "التوصيل فين", "المناطق", "المناطق المتاحه",
    "المناطق المتاحة", "ايه المناطق", "إيه المناطق", "فين التوصيل", "التوصيل متاح فين",
}
_RESERVATION_HINTS = {"حجز", "احجز", "ترابيزة", "ترابيزه", "رزيرفيشن"}
_COMPLAINT_HINTS = {"شكوى", "مشكلة", "المشكله", "اعتراض", "complaint"}
_NAME_INTENT_HINT_GROUPS = (
    _DELIVERY_HINTS,
    _TAKEAWAY_HINTS,
    _ORDER_HINTS,
    _MENU_HINTS,
    _RESERVATION_HINTS,
    _COMPLAINT_HINTS,
)
_TOTAL_HINTS = {
    "الحساب", "الإجمالي", "الاجمالي", "التوتال", "توتال", "التوتل",
    "التوتر", "التتر", "total", "بكام كله", "كام كله", "المجموع", "السعر كله",
}
_GREETING_HINTS = {
    "اهلا", "اهلا بيك", "أهلا", "السلام عليكم", "مساء الخير", "صباح الخير",
    "ازيك", "عامل ايه", "هاي", "hello", "hi",
}


def _contains_any_hint(normalized_text: str, hints: set[str]) -> bool:
    return any(_normalize_ar(hint) in normalized_text for hint in hints)


def _chat_message_text(message: llm.ChatMessage | None) -> str:
    if message is None:
        return ""
    with contextlib.suppress(Exception):
        return (message.text_content or "").strip()
    return ""


_FLOW_STYLE_PROMPT_MARKER = "[FLOW_STYLE_PROMPT]"
_FLOW_CONTEXT_PROMPT_MARKER = "[FLOW_CONTEXT_PROMPT]"
_TURN_GUARD_PROMPT_MARKER = "[TURN_GUARD_PROMPT]"
_TURN_CAP_PROMPT_MARKER = "[TURN_CAP_PROMPT]"
# Marker for the per-flow state snapshot system message added in
# ``base_agent.on_enter``. Carries ``ud.summarize()`` and gets refreshed at the
# top of every ``on_user_turn_completed`` so the LLM always sees the current
# captured state — not the frozen snapshot from when the agent first entered.
# Without the refresh, GPT-4o was reading "customer_phone: unknown" from the
# stale system block while chat history showed the phone was already captured,
# producing the "بينسي هو سأل علي إيه" re-asking bug.
_FLOW_STATE_PROMPT_MARKER = "[FLOW_STATE_PROMPT]"

_NON_ORDER_QUANTITY_TOKENS = frozenset(
    {
        "street",
        "road",
        "avenue",
        "building",
        "floor",
        "apartment",
        "apt",
        "block",
        "tower",
        "district",
        "zone",
        "area",
        "unit",
        "house",
        "address",
        "شارع",
        "طريق",
        "ميدان",
        "عماره",
        "عمارة",
        "بنايه",
        "بناية",
        "شقه",
        "شقة",
        "دور",
        "بلوك",
        "برج",
        "حي",
        "منطقه",
        "منطقة",
        "عنوان",
    }
)


def _chat_message_role(message: llm.ChatMessage | None) -> str:
    if message is None:
        return ""
    with contextlib.suppress(Exception):
        return str(message.role or "").strip().lower()
    return ""


def _is_marked_system_message(message: llm.ChatMessage | None, *markers: str) -> bool:
    if _chat_message_role(message) != "system":
        return False
    text = _chat_message_text(message)
    return any(text.startswith(marker) for marker in markers)


def _strip_marked_system_messages(chat_ctx: llm.ChatContext, *markers: str) -> None:
    if not markers:
        return
    chat_ctx.items[:] = [
        item
        for item in chat_ctx.items
        if not _is_marked_system_message(item, *markers)
    ]


def _chat_ctx_item_key(message: llm.ChatMessage | None) -> tuple[str, str]:
    message_id = getattr(message, "id", None)
    if message_id:
        return ("id", str(message_id))
    return ("object", str(id(message)))


def _recent_chat_ctx_non_system_items(
    chat_ctx: llm.ChatContext,
    *,
    max_items: int,
) -> list[llm.ChatMessage]:
    if max_items <= 0:
        return []
    non_system_items = [
        item for item in chat_ctx.items if _chat_message_role(item) != "system"
    ]
    if len(non_system_items) <= max_items:
        return non_system_items
    return non_system_items[-max_items:]


def _limit_chat_ctx_preserving_system(
    chat_ctx: llm.ChatContext,
    *,
    max_items: int | None = None,
    max_non_system_items: int | None = None,
) -> None:
    if max_items is None and max_non_system_items is None:
        return

    system_items = [item for item in chat_ctx.items if _chat_message_role(item) == "system"]
    non_system_items = [
        item for item in chat_ctx.items if _chat_message_role(item) != "system"
    ]
    keep_system = system_items
    keep_non_system = non_system_items

    if max_non_system_items is not None:
        if max_non_system_items <= 0:
            keep_non_system = []
        elif len(keep_non_system) > max_non_system_items:
            keep_non_system = keep_non_system[-max_non_system_items:]

    if max_items is not None:
        if max_items <= 0:
            keep_system = []
            keep_non_system = []
        elif len(keep_system) >= max_items:
            keep_system = keep_system[-max_items:]
            keep_non_system = []
        else:
            available_non_system_slots = max_items - len(keep_system)
            if available_non_system_slots <= 0:
                keep_non_system = []
            elif len(keep_non_system) > available_non_system_slots:
                keep_non_system = keep_non_system[-available_non_system_slots:]

    keep_keys = {_chat_ctx_item_key(item) for item in keep_system + keep_non_system}
    chat_ctx.items[:] = [
        item for item in chat_ctx.items if _chat_ctx_item_key(item) in keep_keys
    ]


def _is_greeting_only(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    if any(
        _contains_any_hint(normalized, hints)
        for hints in (
            _DELIVERY_HINTS,
            _TAKEAWAY_HINTS,
            _ORDER_HINTS,
            _MENU_HINTS,
            _RESERVATION_HINTS,
            _COMPLAINT_HINTS,
        )
    ):
        return False
    return any(_normalize_ar(hint) in normalized for hint in _GREETING_HINTS)


def _is_menu_question(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized or _is_delivery_zone_question(text):
        return False
    return _contains_any_hint(normalized, _MENU_HINTS)


def _is_delivery_zone_question(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    if _contains_any_hint(normalized, _DELIVERY_ZONE_HINTS):
        return True
    return "فين" in normalized and "متاح" in normalized


def _delivery_zone_user_message(cfg: "RestaurantConfig") -> str:
    if cfg.delivery_zones:
        return _voice_safe_text(f"التوصيل متاح في {cfg.delivery_zones_text()}. تحب تطلب إيه؟", max_chars=170)
    if cfg.delivery_enabled:
        return _voice_safe_text("التوصيل متاح يا فندم. تحب تطلب إيه؟", max_chars=140)
    return _delivery_unavailable_user_message(cfg)


def _is_likely_non_name_response(text: str) -> bool:
    return _is_likely_non_name_response_impl(
        text,
        looks_empty_answer=_looks_empty_answer,
    )


def _extract_name_candidate(text: str) -> str | None:
    return _extract_name_candidate_impl(
        text,
        looks_empty_answer=_looks_empty_answer,
        is_phone_like_text=_is_phone_like_text,
        contains_any_hint=_contains_any_hint,
        intent_hint_groups=_NAME_INTENT_HINT_GROUPS,
    )


def _menu_response_for_flow(flow: str, cfg: RestaurantConfig) -> str:
    base = cfg.menu_text() if _available_menu_items(cfg) else _menu_unavailable_user_message(cfg)
    if flow in {"takeaway", "delivery"}:
        return f"{base} تحب تطلب إيه؟"
    return base


def _is_total_question(text: str) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    return any(_normalize_ar(hint) in normalized for hint in _TOTAL_HINTS)


def _order_total_user_message(flow: str, ud: "UserData", cfg: RestaurantConfig) -> str:
    if not ud.order:
        return "قولّي طلبك الأول يا فندم."
    if not ud.order_validated or ud.order_total <= 0:
        return "لسه مقدرش أحسب الإجمالي بدقة غير لما أراجع الطلب من المنيو."
    if flow == "delivery":
        total = ud.order_total + max(0.0, float(cfg.delivery_fee or 0.0))
        if cfg.delivery_fee > 0:
            return f"إجمالي الطلب {money2ar(total)} جنيه شامل رسوم التوصيل."
        return f"إجمالي الطلب {money2ar(total)} جنيه."
    return f"إجمالي الطلب {money2ar(ud.order_total)} جنيه."


def _guess_request_intent(text: str, cfg: RestaurantConfig, ud: UserData | None = None) -> str:
    """Map a user turn to a greeter routing decision.

    Order of precedence:
    1. LLM-driven ``TurnUnderstanding`` (when available and the
       confidence tier is medium+).
    2. Phase 3 ``intent_extractor`` cue list (offline fallback).
    3. Legacy hint sets in this module.
    """
    if not (text or "").strip():
        return "unknown"

    # Try the LLM understanding first.
    if ud is not None:
        try:
            from core.understanding import get_or_extract_for_turn
            from core.understanding_bridge import intent_from_understanding
            understanding = get_or_extract_for_turn(ud, text, "greeter")
            mapped = intent_from_understanding(understanding)
            if mapped is not None and mapped != "unknown":
                if mapped == "delivery":
                    if cfg.degraded_mode:
                        return "delivery_degraded"
                    return "delivery" if cfg.delivery_enabled else "delivery_unavailable"
                if mapped in {"takeaway", "reservation", "complaint", "menu"}:
                    return mapped
        except Exception:
            pass

    from core.extractors.intent_extractor import detect_intent

    detection = detect_intent(text)
    kind = detection.kind
    if kind == "complaint":
        return "complaint"
    if kind == "reservation":
        return "reservation"
    if kind in {"menu_question", "delivery_zone_question"}:
        return "menu"
    if kind == "delivery":
        if cfg.degraded_mode:
            return "delivery_degraded"
        return "delivery" if cfg.delivery_enabled else "delivery_unavailable"
    if kind == "takeaway":
        return "takeaway"

    # Phase-3 detector returned greeting / post_completion / total /
    # unknown. Fall through to the legacy hint sets so we keep covering
    # bare "اوردر" / "طلب" → ``order_ambiguous`` and any phrasing not in
    # the new cue lists.
    normalized = _normalize_ar(text)
    if not normalized:
        return "unknown"
    if _contains_any_hint(normalized, _COMPLAINT_HINTS):
        return "complaint"
    if _contains_any_hint(normalized, _RESERVATION_HINTS):
        return "reservation"
    if _contains_any_hint(normalized, _MENU_HINTS):
        return "menu"
    if _contains_any_hint(normalized, _DELIVERY_HINTS):
        if cfg.degraded_mode:
            return "delivery_degraded"
        return "delivery" if cfg.delivery_enabled else "delivery_unavailable"
    if _contains_any_hint(normalized, _TAKEAWAY_HINTS):
        return "takeaway"
    if _contains_any_hint(normalized, _ORDER_HINTS):
        return "order_ambiguous"
    return "unknown"


@dataclass
class GreeterTurnDecision:
    action: Literal["say", "route"] = "say"
    message: str = ""
    target_agent: str | None = None
    reason: str = ""


def _greeter_turn_decision(
    text: str,
    cfg: RestaurantConfig,
    *,
    has_delivery_agent: bool,
    ud: UserData | None = None,
) -> GreeterTurnDecision:
    intent = _guess_request_intent(text, cfg, ud)
    if intent == "menu":
        message = cfg.menu_text() if _available_menu_items(cfg) else _menu_unavailable_user_message(cfg)
        return GreeterTurnDecision(message=message, reason="menu")
    if intent == "reservation":
        return GreeterTurnDecision(action="route", target_agent="reservation", reason="reservation")
    if intent == "complaint":
        return GreeterTurnDecision(action="route", target_agent="complaint", reason="complaint")
    if intent == "takeaway":
        return GreeterTurnDecision(action="route", target_agent="takeaway", reason="takeaway")
    if intent in {"delivery", "delivery_degraded"}:
        if has_delivery_agent:
            return GreeterTurnDecision(action="route", target_agent="delivery", reason=intent)
        return GreeterTurnDecision(
            message="قولي طلبك الأول يا فندم.",
            reason="delivery_route_missing",
        )
    if intent == "delivery_unavailable":
        return GreeterTurnDecision(
            message=_delivery_unavailable_user_message(cfg),
            reason="delivery_unavailable",
        )
    if intent == "order_ambiguous":
        if not has_delivery_agent and not cfg.degraded_mode:
            # Delivery not offered — the LLM would have no meaningful choice to
            # ask about; route directly to takeaway.
            return GreeterTurnDecision(
                action="route",
                target_agent="takeaway",
                reason="order_ambiguous_takeaway_only",
            )
        # Let the LLM ask the question naturally in its own words; no canned text.
        return GreeterTurnDecision(
            message="",
            reason="order_ambiguous",
        )
    # Any remaining case — greeting, identity question, off-topic — is handed
    # to the LLM to respond to naturally from the persona.
    return GreeterTurnDecision(
        message="",
        reason="unknown_passthrough",
    )


_WEEKDAY_ALIASES = {
    0: {"الاثنين", "الاتنين", "monday"},
    1: {"الثلاثاء", "التلات", "tuesday"},
    2: {"الأربعاء", "الاربعاء", "الاربع", "wednesday"},
    3: {"الخميس", "thursday"},
    4: {"الجمعة", "الجمعه", "friday"},
    5: {"السبت", "saturday"},
    6: {"الأحد", "الاحد", "الاحد", "sunday"},
}
_PM_HINTS = {"pm", "مساء", "بالليل", "ليل", "العصر", "المغرب"}
_AM_HINTS = {"am", "صباح", "الصبح", "الفجر"}


def _cairo_now() -> datetime:
    return datetime.now(CAIRO_TZ)


def _parse_clock_time(raw: str, normalized: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\b", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if minute > 59 or hour > 23:
        return None

    if any(hint in normalized for hint in _PM_HINTS):
        if 1 <= hour < 12:
            hour += 12
    elif any(hint in normalized for hint in _AM_HINTS):
        if hour == 12:
            hour = 0

    if hour > 23:
        return None
    return hour, minute


def _parse_calendar_date(raw: str, normalized: str, base_dt: datetime) -> datetime | None:
    if "بعد بكره" in normalized or "بعد بكرة" in normalized:
        return base_dt + timedelta(days=2)
    if "بكره" in normalized or "بكرة" in normalized or "غدا" in normalized:
        return base_dt + timedelta(days=1)
    if "النهارده" in normalized or "اليوم" in normalized:
        return base_dt

    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        try:
            return base_dt.replace(year=year, month=month, day=day)
        except ValueError:
            return None

    date_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", raw)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_raw = date_match.group(3)
        year = int(year_raw) if year_raw else base_dt.year
        if year < 100:
            year += 2000
        try:
            return base_dt.replace(year=year, month=month, day=day)
        except ValueError:
            return None

    for weekday, aliases in _WEEKDAY_ALIASES.items():
        if any(_normalize_ar(alias) in normalized for alias in aliases):
            delta = (weekday - base_dt.weekday()) % 7
            return base_dt + timedelta(days=delta)
    return None


def _hours_window_for_datetime(cfg: RestaurantConfig, dt_value: datetime) -> tuple[datetime, datetime] | None:
    if not cfg.hours:
        return None
    day_key = dt_value.strftime("%A").lower()
    slot = cfg.hours.get(day_key)
    if not isinstance(slot, dict) or slot.get("closed"):
        return None

    open_raw = str(slot.get("open", "")).translate(_AR_DIGITS).strip()
    close_raw = str(slot.get("close", "")).translate(_AR_DIGITS).strip()
    open_match = re.fullmatch(r"(\d{1,2}):(\d{2})", open_raw)
    close_match = re.fullmatch(r"(\d{1,2}):(\d{2})", close_raw)
    if not open_match or not close_match:
        return None

    open_dt = dt_value.replace(hour=int(open_match.group(1)), minute=int(open_match.group(2)), second=0, microsecond=0)
    close_dt = dt_value.replace(hour=int(close_match.group(1)), minute=int(close_match.group(2)), second=0, microsecond=0)
    if close_dt <= open_dt:
        close_dt += timedelta(days=1)
    return open_dt, close_dt


def _parse_reservation_time(value: str, cfg: RestaurantConfig) -> ParsedReservationTime | None:
    """
    Parser خفيف للحجز:
    - relative days: النهارده/بكره/بعد بكره
    - weekdays
    - dd/mm[/yyyy] و yyyy-mm-dd
    - ساعات بصيغة 8 / 8:30 مع am/pm أو صباح/مساء
    """
    raw = (value or "").translate(_AR_DIGITS).strip()
    if len(raw) < 3:
        return None

    normalized = _normalize_ar(raw)
    time_part = _parse_clock_time(raw, normalized)
    date_seed = _parse_calendar_date(raw, normalized, _cairo_now())
    if not time_part or not date_seed:
        return None

    parsed = date_seed.replace(hour=time_part[0], minute=time_part[1], second=0, microsecond=0)
    now_dt = _cairo_now()
    if parsed <= now_dt:
        for weekday, aliases in _WEEKDAY_ALIASES.items():
            if any(_normalize_ar(alias) in normalized for alias in aliases):
                parsed += timedelta(days=7)
                break
    if parsed <= now_dt:
        return None
    if parsed > now_dt + timedelta(days=180):
        return None

    window = _hours_window_for_datetime(cfg, parsed)
    if cfg.hours and window is None:
        return None
    if window is not None:
        open_dt, close_dt = window
        if not (open_dt <= parsed <= close_dt):
            return None

    normalized_text = parsed.strftime("%Y-%m-%d %H:%M")
    return ParsedReservationTime(raw_text=value.strip(), scheduled_at=parsed, normalized_text=normalized_text)


def _parse_order_item(item: str) -> tuple[str, int]:
    text = (item or "").translate(_AR_DIGITS).strip()
    if not text:
        return "", 1

    original_text = text
    qty = 1
    implicit_numeric_match = False
    patterns = [
        r"(?:[×xX*]\s*(\d+))$",
        r"^(\d+)\s+(.+)$",
        r"^(.+?)\s+[×xX*]\s*(\d+)$",
        r"^(.+?)\s+(\d+)$",
    ]

    for idx, pattern in enumerate(patterns):
        match = re.match(pattern, text)
        if not match:
            continue
        groups = [g for g in match.groups() if g is not None]
        implicit_numeric_match = idx in {1, 3}
        if len(groups) == 1:
            qty = int(groups[0])
            text = re.sub(pattern, "", text).strip()
        elif pattern == r"^(\d+)\s+(.+)$":
            qty = int(groups[0])
            text = groups[1].strip()
        elif groups[-1].isdigit():
            qty = int(groups[-1])
            text = groups[0].strip()
        break

    text = re.sub(r"\s+[×xX*]\s*\d+$", "", text).strip(" -،,")
    if implicit_numeric_match:
        normalized_tokens = set(_normalize_ar(original_text).split())
        if normalized_tokens & _NON_ORDER_QUANTITY_TOKENS:
            return original_text.strip(" -،,"), 1
    return text, max(1, qty)


def _token_overlap_score(target_tokens: set[str], menu_tokens: set[str]) -> float:
    """Score how well target tokens match menu item tokens.
    Returns 0.0-1.0 where 1.0 = perfect match. Returns 0 if no overlap."""
    if not target_tokens or not menu_tokens:
        return 0.0
    overlap = target_tokens & menu_tokens
    if not overlap:
        return 0.0
    # Jaccard-like: overlap / union, but weighted toward the menu item
    # so "بيتزا" alone doesn't strongly match "بيتزا مارجريتا"
    return len(overlap) / max(len(target_tokens), len(menu_tokens))


# Minimum token overlap score to accept a fuzzy match
_MENU_MATCH_THRESHOLD = 0.5
_SHORT_MENU_MATCH_THRESHOLD = 0.8


def _resolve_menu_item(item_name: str, menu_items: list[dict]) -> dict | None:
    target = _normalize_ar(item_name)
    if not target:
        return None

    target_tokens = set(target.split())
    threshold = _SHORT_MENU_MATCH_THRESHOLD if len(target_tokens) <= 1 else _MENU_MATCH_THRESHOLD
    exact_match: dict | None = None
    best_match: tuple[float, dict] | None = None

    for item in menu_items:
        if not item.get("available", True):
            continue
        norm_name = _normalize_ar(item.get("name", ""))
        if not norm_name:
            continue
        # Exact normalized match — highest priority
        if norm_name == target:
            exact_match = item
            break
        # Token-level scoring instead of substring matching
        menu_tokens = set(norm_name.split())
        score = _token_overlap_score(target_tokens, menu_tokens)
        if score >= threshold:
            if best_match is None or score > best_match[0]:
                best_match = (score, item)

    if exact_match:
        return exact_match
    return best_match[1] if best_match else None


def _get_upsell_suggestion(ud: "UserData", cfg: "RestaurantConfig") -> str | None:
    """Pick an upsell item not already in the order."""
    if ud.upsell_offered or not cfg.upsell_rules:
        return None
    order_normalized = {_normalize_ar(item or "") for item in (ud.order or [])}
    for rule in cfg.upsell_rules:
        item_name = rule.get("item", "")
        if _normalize_ar(item_name) not in order_normalized:
            price = rule.get("price")
            ud.upsell_offered = True
            ud.pending_upsell_item = item_name
            ud.pending_upsell_price = float(price) if price is not None else None
            _emit_event(
                "upsell.offer",
                call_id=ud.call_id,
                item=item_name,
                price=float(price) if price is not None else None,
            )
            if price:
                return f"تحب أضيف {item_name} بـ{_int_to_ar(int(price))} جنيه؟"
            return rule.get("suggestion", f"تحب أضيف {item_name}؟")
    return None


def _clear_pending_upsell(ud: "UserData", *, accepted: bool | None = None) -> None:
    ud.pending_upsell_item = None
    ud.pending_upsell_price = None
    if accepted is not None:
        ud.upsell_accepted = accepted


def _accept_pending_upsell(
    ud: "UserData",
    cfg: "RestaurantConfig",
    *,
    user_text: str | None = None,
) -> str | None:
    """Add the pending upsell item to the order.

    If the user's reply included a quantity ("ماشي زود لي 10 كولا") we
    honour it; otherwise we default to one. Discovered via real call
    QA — customers routinely accept the upsell with a quantity, and
    silently capping at 1 was producing wrong submitted orders.
    """
    item_name = (ud.pending_upsell_item or "").strip()
    if not item_name:
        return None

    qty = _quantity_from_upsell_reply(user_text, item_name) if user_text else 1

    current_items = list(ud.order or [])
    item_with_qty = _format_order_item(item_name, qty) if qty > 1 else item_name
    normalized_items, unknown, total = _normalize_order_items(
        current_items + [item_with_qty], cfg.menu_items
    )
    if unknown:
        ud.order = current_items + [item_with_qty]
        if ud.pending_upsell_price is not None:
            ud.order_total += float(ud.pending_upsell_price) * float(qty)
    else:
        ud.order = normalized_items
        ud.order_total = total
        ud.order_validated = True

    _clear_pending_upsell(ud, accepted=True)
    return item_name


def _quantity_from_upsell_reply(user_text: str | None, item_name: str) -> int:
    """Extract the quantity the customer asked for in an upsell reply.

    Handles common shapes:
      "ماشي زود لي 10 كولا"
      "آه، اتنين منها"
      "تمام، خمسة كمان"
      "اوكي، 3 لو سمحت"
    """
    if not user_text:
        return 1
    normalized = _normalize_ar(user_text)
    if not normalized:
        return 1
    tokens = normalized.split()
    item_norm = _normalize_ar(item_name)

    # Try after-item-name first ("زود كولا 5").
    if item_norm:
        item_tokens = item_norm.split()
        for idx in range(0, len(tokens) - len(item_tokens) + 1):
            if tokens[idx: idx + len(item_tokens)] == item_tokens:
                tail_idx = idx + len(item_tokens)
                if tail_idx < len(tokens):
                    qty = _quantity_token_to_int(tokens[tail_idx])
                    if qty:
                        return qty
                if idx > 0:
                    qty = _quantity_token_to_int(tokens[idx - 1])
                    if qty:
                        return qty

    # Otherwise look for the largest numeric token that isn't part of
    # an address ("شارع 5") and isn't the negative of confirming.
    best_qty = 0
    for i, token in enumerate(tokens):
        qty = _quantity_token_to_int(token)
        if not qty:
            continue
        prev_token = tokens[i - 1] if i > 0 else ""
        next_token = tokens[i + 1] if i + 1 < len(tokens) else ""
        if prev_token in _NON_ORDER_QUANTITY_TOKENS or next_token in _NON_ORDER_QUANTITY_TOKENS:
            continue
        if qty > best_qty:
            best_qty = qty
    return best_qty if best_qty > 0 else 1


def _normalize_order_items(items: list[str], menu_items: list[dict]) -> tuple[list[str], list[str], float]:
    aggregated: dict[str, int] = {}
    unknown: list[str] = []
    total = 0.0

    for raw in items:
        name, qty = _parse_order_item(raw)
        menu_item = _resolve_menu_item(name, menu_items)
        if not menu_item:
            if name:
                unknown.append(name)
            continue
        canonical_name = str(menu_item["name"]).strip()
        aggregated[canonical_name] = aggregated.get(canonical_name, 0) + qty
        total += float(menu_item.get("price", 0) or 0) * qty

    normalized = [_format_order_item(name, qty) for name, qty in aggregated.items()]
    return normalized, unknown, total


_ORDER_ADD_HINTS = {
    "ضيف", "زود", "كمان", "معاه", "معاها", "وخلي معاه", "وخلي معاها", "عايز كمان",
}
_ORDER_REPLACE_HINTS = {
    "بدل", "غير", "غيرلي", "خلي", "خليه", "خليها", "شيل", "امسح", "لأ", "لا مش",
}


def _order_update_is_incremental(user_text: str | None) -> bool:
    """Decide if a turn should *append* to the existing order.

    The Phase 2 mutation parser handles the rich set of Egyptian cues
    (replace / add / increase / decrease / keep / remove). Whatever the
    parser confidently classifies wins; we only fall back to the legacy
    hint sets when the parser returned ``unknown``.
    """
    if not (user_text or "").strip():
        return False
    from core.order_mutations import parse_mutation

    intent = parse_mutation(user_text)
    if intent.kind == "replace":
        return False
    if intent.kind in {"add", "increase"}:
        return True
    if intent.kind in {"keep", "remove", "decrease"}:
        # The intent is a mutation that the LLM tool should reflect on;
        # treat as non-incremental so the LLM resends the corrected order.
        return False

    normalized = _normalize_ar(user_text or "")
    if not normalized:
        return False
    if _contains_normalized_phrase(normalized, _ORDER_REPLACE_HINTS):
        return False
    return _contains_normalized_phrase(normalized, _ORDER_ADD_HINTS)


def _order_update_is_replace(user_text: str | None) -> bool:
    """Return True only when a turn clearly means "replace my order".

    In voice calls, customers often continue an order over multiple
    final transcripts ("... شاورما لحمة" then "بيتزا مارجريتا منها خمسة").
    Treating every later item turn as replace makes the agent look like
    it forgot earlier items. Replacement therefore requires an explicit
    correction cue.
    """
    if not (user_text or "").strip():
        return False
    from core.order_mutations import parse_mutation

    intent = parse_mutation(user_text)
    if intent.kind == "replace":
        return True
    if intent.kind in {"add", "increase", "keep", "remove", "decrease"}:
        return False

    normalized = _normalize_ar(user_text or "")
    if not normalized:
        return False
    return _contains_normalized_phrase(normalized, _ORDER_REPLACE_HINTS)


def _merge_incremental_order_items(
    current_order: list[str] | None,
    incoming_order: list[str],
    menu_items: list[dict],
) -> tuple[list[str], list[str], float]:
    existing = [item for item in (current_order or []) if item and item.strip()]
    current_norm = {_normalize_ar(item) for item in existing}
    additions = [
        item for item in incoming_order
        if item and item.strip() and _normalize_ar(item) not in current_norm
    ]
    return _normalize_order_items(existing + additions, menu_items)


def _merge_incremental_raw_order_items(
    current_order: list[str] | None,
    incoming_order: list[str],
) -> list[str]:
    existing = [item.strip() for item in (current_order or []) if item and item.strip()]
    current_norm = {_normalize_ar(item) for item in existing}
    additions = [
        item.strip() for item in incoming_order
        if item and item.strip() and _normalize_ar(item) not in current_norm
    ]
    return existing + additions


def _build_order_items(order: list[str], menu_items: list[dict]) -> list[dict]:
    result = []
    for raw in order:
        name, qty = _parse_order_item(raw)
        if not name:
            continue
        menu_item = _resolve_menu_item(name, menu_items)
        canonical_name = menu_item["name"] if menu_item else name
        price = float(menu_item.get("price", 0) or 0) if menu_item else 0.0
        result.append({"name": canonical_name, "qty": qty, "price": price})
    return result


def _current_flow_name(context: RunContext_T) -> str:
    return context.session.current_agent.__class__.__name__.lower()


def _takeaway_next_missing_slot(ud: UserData) -> str | None:
    from core.dialogue_engine import takeaway_missing_slot
    return takeaway_missing_slot(ud)


def _delivery_next_missing_slot(ud: UserData) -> str | None:
    from core.dialogue_engine import delivery_missing_slot
    return delivery_missing_slot(ud)


def _reservation_next_missing_slot(ud: UserData, cfg: RestaurantConfig) -> str | None:
    from core.dialogue_engine import reservation_missing_slot
    return reservation_missing_slot(ud, cfg)


def _complaint_next_missing_slot(ud: UserData) -> str | None:
    from core.dialogue_engine import complaint_missing_slot
    return complaint_missing_slot(ud)


def _is_takeaway_ready_for_confirmation(ud: UserData) -> bool:
    return _takeaway_next_missing_slot(ud) is None


def _is_delivery_ready_for_confirmation(ud: UserData) -> bool:
    return _delivery_next_missing_slot(ud) is None


def _is_reservation_ready_for_confirmation(ud: UserData, cfg: RestaurantConfig) -> bool:
    return _reservation_next_missing_slot(ud, cfg) is None


def _is_flow_ready_for_confirmation(flow: str, ud: UserData) -> bool:
    if flow == "takeaway":
        return _is_takeaway_ready_for_confirmation(ud)
    if flow == "delivery":
        return _is_delivery_ready_for_confirmation(ud)
    if flow == "reservation":
        return _is_reservation_ready_for_confirmation(ud, ud.restaurant)
    return False


def _is_near_turn_cap_completion(flow: str, ud: UserData) -> bool:
    if ud.order_confirmed or ud.reservation_confirmed or ud.complaint_logged:
        return True
    if flow == "takeaway":
        return _takeaway_next_missing_slot(ud) in {None, "رقم الموبايل"}
    if flow == "delivery":
        return _delivery_next_missing_slot(ud) in {None, "رقم الموبايل"}
    if flow == "reservation":
        return _reservation_next_missing_slot(ud, ud.restaurant) in {None, "رقم الموبايل"}
    if flow == "complaint":
        return _complaint_next_missing_slot(ud) in {None, "رقم الموبايل"}
    return False


def _turn_cap_system_message(flow: str, ud: UserData, *, in_grace: bool) -> str:
    missing = _next_step_hint_for_flow(flow, ud) or "المعلومة الأساسية الناقصة"
    if in_grace:
        return (
            "المكالمة دخلت آخر أدوار السماح. "
            f"كمّل بشكل مختصر جدًا وركّز على {missing} فقط. "
            "متفتحش مواضيع جديدة ومتعملش أب سيل إضافي."
        )
    return (
        "المكالمة قربت من الحد الأقصى للأدوار. "
        f"ركّز فقط على {missing} وخلي الرد قصير وطبيعي ومن غير تكرار."
    )


def _flow_missing_phone(flow: str, ud: UserData) -> bool:
    if flow == "takeaway":
        return _takeaway_next_missing_slot(ud) == "رقم الموبايل"
    if flow == "delivery":
        return _delivery_next_missing_slot(ud) == "رقم الموبايل"
    if flow == "reservation":
        return _reservation_next_missing_slot(ud, ud.restaurant) == "رقم الموبايل"
    if flow == "complaint":
        return _complaint_next_missing_slot(ud) == "رقم الموبايل"
    return False


def _flow_missing_name(flow: str, ud: UserData) -> bool:
    if flow == "takeaway":
        return _takeaway_next_missing_slot(ud) == "الاسم"
    if flow == "delivery":
        return _delivery_next_missing_slot(ud) == "الاسم"
    if flow == "reservation":
        return _reservation_next_missing_slot(ud, ud.restaurant) == "الاسم"
    if flow == "complaint":
        return _complaint_next_missing_slot(ud) == "الاسم"
    return False


def _turn_guard_signature(flow: str, guard: str) -> str:
    normalized_guard = " ".join((guard or "").split())
    if not normalized_guard:
        return ""
    payload = f"{flow}\n{normalized_guard}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _should_add_turn_guard(
    user_text: str,
    *,
    flow: str = "",
    current_guard: str = "",
    previous_guard_signature: str = "",
) -> bool:
    normalized = _normalize_ar(user_text)
    if not normalized:
        return False
    if current_guard:
        current_signature = _turn_guard_signature(flow, current_guard)
        if previous_guard_signature and current_signature == previous_guard_signature:
            return False
    return True


def _flow_turn_guard_message(flow: str, ud: UserData, user_text: str) -> str:
    normalized = _normalize_ar(user_text)

    # Post-confirmation: don't restart the flow
    if ud.order_confirmed or ud.reservation_confirmed or ud.complaint_logged:
        return (
            "خلاص كل حاجة اتسجلت. "
            "لو العميل بيشكر أو بيسلّم رد عليه بشكل لطيف. "
            "لو عايز حاجة تانية ساعده. بس متفتحش موضوع جديد من عندك."
        )

    if flow == "takeaway":
        missing = _takeaway_next_missing_slot(ud)
        if missing == "الطلب":
            return (
                "لسه مخدناش الطلب. حاول توجّه الكلام ناحية الأكل بشكل طبيعي. "
                "لو العميل بيسأل سؤال عادي جاوبه الأول وبعدين ارجع اسأله عن طلبه."
            )
        if missing == "الاسم":
            return (
                "الطلب اتسجل — محتاج الاسم دلوقتي. "
                "لو العميل قال لا أو مفيش على الطلب الخاص، اسأله عن اسمه."
            )
        if missing == "رقم الموبايل":
            return "الاسم اتسجل — محتاج رقم الموبايل. متأكدش الطلب قبل ما تاخد الرقم."
        return "كل البيانات جاهزة — لخّص الطلب واستنى التأكيد. متقولش الطلب اتسجل غير بعد نجاح confirm_order."

    if flow == "delivery":
        missing = _delivery_next_missing_slot(ud)
        if missing == "الطلب":
            return (
                "لسه مخدناش الطلب. وجّه الكلام ناحية الأكل بشكل طبيعي. "
                "لو العميل بيسأل حاجة تانية جاوبه وبعدين ارجع."
            )
        if missing == "العنوان والمنطقة":
            return (
                "الطلب اتسجل — محتاج العنوان دلوقتي. "
                "أول ما العميل يقول عنوانه استدعي update_delivery_address فوراً."
            )
        if missing == "الاسم":
            return (
                "العنوان اتسجل — محتاج الاسم دلوقتي. "
                "لو العميل رد على سؤال الطلبات الخاصة أو العلامة المميزة، انتقل للاسم."
            )
        if missing == "رقم الموبايل":
            return "الاسم اتسجل — محتاج رقم الموبايل. متأكدش الطلب قبل الرقم."
        return "كل البيانات جاهزة — لخّص الطلب واستنى التأكيد. متقولش اتسجل للتوصيل غير بعد نجاح confirm_delivery."

    if flow == "reservation":
        missing = _reservation_next_missing_slot(ud, ud.restaurant)
        if missing == "وقت الحجز":
            return "محتاج وقت الحجز — يوم وساعة. لو خارج المواعيد قوله واقترح بديل."
        if missing == "عدد الضيوف":
            return "الوقت اتسجل — محتاج عدد الضيوف."
        if missing == "الفرع":
            return "محتاج يحدد الفرع."
        if missing == "الاسم":
            return "محتاج الاسم دلوقتي."
        if missing == "رقم الموبايل":
            return "محتاج رقم الموبايل. متأكدش الحجز قبل ما تاخد الرقم."
        return "كل البيانات جاهزة — لخّص الحجز واستنى التأكيد. متقولش الحجز اتأكد غير بعد نجاح confirm_reservation."

    if flow == "complaint":
        missing = _complaint_next_missing_slot(ud)
        if missing == "الشكوى":
            return "اسمع الشكوى كويس وخلّي العميل يحكي."
        if missing == "نوع الشكوى":
            return "حاول تفهم نوع المشكلة: طلب أو جودة أو خدمة أو توصيل."
        if missing == "الاسم":
            return "الشكوى اتسجلت — محتاج الاسم."
        if missing == "رقم الموبايل":
            return "محتاج رقم الموبايل علشان يقدروا يتواصلوا مع العميل."
        return "الشكوى اتسجلت. لو في حاجة ناقصة كمّلها، بس متقولش 'اتثبتت' غير لو فعلاً نجحت."

    return ""


def _next_step_hint(context: RunContext_T) -> str:
    return _next_step_hint_for_flow(_current_flow_name(context), context.userdata)


_INACTIVITY_SOFT_CHECKIN = [
    "لسه معايا يا فندم؟",
    "أنا معاك يا فندم",
    "ألو؟ سامعني؟",
    "فيه حد على الخط؟",
]


def _inactivity_reprompt(ud: "UserData", flow: str = "", *, prompt_count: int = 1) -> str:
    """Flow-aware reprompt when the user goes silent.

    First reprompt is always a soft check-in — "are you still there?" —
    because jumping straight to re-asking a field feels abrupt when the
    customer may have been briefly distracted.  Subsequent reprompts
    (count >= 2) escalate to the specific missing-field question so we
    actually make progress.
    """
    if prompt_count <= 1:
        return _random.choice(_INACTIVITY_SOFT_CHECKIN)

    if flow == "takeaway":
        missing = _takeaway_next_missing_slot(ud)
    elif flow == "delivery":
        missing = _delivery_next_missing_slot(ud)
    elif flow == "reservation":
        missing = _reservation_next_missing_slot(ud, ud.restaurant)
    else:
        return _random.choice(_INACTIVITY_SOFT_CHECKIN)
    if missing == "الطلب":
        return _random.choice(["تحب تطلب إيه يا فندم؟", "قولي تحب إيه؟"])
    if missing == "الاسم":
        return _ask_name()
    if missing == "رقم الموبايل":
        return _ask_phone()
    if missing == "العنوان والمنطقة":
        return _ask_address()
    if missing == "وقت الحجز":
        return _random.choice(["امتى تحب تحجز يا فندم؟", "الحجز امتى؟"])
    if missing == "عدد الضيوف":
        return _random.choice(["كام فرد يا فندم؟", "هتكونوا كام؟"])
    if not missing:
        return _random.choice(["كده تمام يا فندم؟", "خلصنا يا فندم؟"])
    return _random.choice(_INACTIVITY_SOFT_CHECKIN)


async def _maybe_submit_pending_complaint(context: RunContext_T) -> str:
    return await _maybe_submit_pending_complaint_for_flow(
        context.userdata,
        _current_flow_name(context),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Re-exports — BaseAgent, shared tools, and flow classes
# (extracted to separate modules but re-exported here for backward compat)
# ─────────────────────────────────────────────────────────────────────────────
from base_agent import BaseAgent, RunContext_T, get_menu, to_greeter, update_name, update_phone  # noqa: F811,E402
from flows.greeter import Greeter  # noqa: E402
from flows.takeaway import Takeaway  # noqa: E402
from flows.delivery import Delivery  # noqa: E402
from flows.reservation import Reservation  # noqa: E402
from flows.complaint import Complaint  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Graceful shutdown — httpx cleanup via atexit (signal handling done by SDK)
# ─────────────────────────────────────────────────────────────────────────────
atexit.register(_remove_worker_health_snapshot_sync)
atexit.register(_cleanup_http_client_impl)


async def _safe_aclose_session_once(
    session: "AgentSession[UserData]",
    close_state: dict[str, bool],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    if close_state.get("closed"):
        return
    close_state["closed"] = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(session.aclose(), timeout=timeout_seconds)


async def _safe_close_session_once(
    session: "AgentSession[UserData]",
    close_state: dict[str, bool],
    *,
    farewell: str = "",
    timeout_seconds: float = 5.0,
) -> None:
    if close_state.get("closed"):
        return
    if farewell:
        with contextlib.suppress(Exception):
            session.userdata.last_agent_message = farewell
            await session.say(
                _voice_safe_text(farewell),
                allow_interruptions=False,
                add_to_chat_ctx=False,
            )
    await _safe_aclose_session_once(session, close_state, timeout_seconds=timeout_seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint — available via main.py (use `python main.py start` to run)
# For backward compat, `python agent.py start` still works via lazy import.
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Lazy import to avoid the circular dependency (main.py imports agent.py).
    # Both ``python agent.py dev`` and ``python main.py dev`` MUST give the
    # same runtime environment — including the dev dashboard + parent health
    # server — so the entrypoints stay symmetric. Without this, hitting the
    # dashboard URL via the agent.py entrypoint silently 404s because the
    # dashboard server was never started.
    from main import server, _start_dev_dashboard, _start_parent_health_server
    _start_parent_health_server()
    _start_dev_dashboard()
    cli.run_app(server)