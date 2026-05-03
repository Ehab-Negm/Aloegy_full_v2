"""Environment configuration and parsing."""
import logging
import os
import re
import sys
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

logger = logging.getLogger("restaurant.config")

AGENT_DIR = Path(__file__).resolve().parent.parent

try:
    CAIRO_TZ = ZoneInfo("Africa/Cairo")
except Exception:
    CAIRO_TZ = timezone(timedelta(hours=2))


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
CONFIG_REFRESH_INTERVAL_SECONDS   = _get_env_float("CONFIG_REFRESH_INTERVAL_SECONDS", 300.0, min_value=30.0)
CONFIG_CACHE_TTL = _get_env_float("CONFIG_CACHE_TTL", 60.0, min_value=1.0)
MAX_CONCURRENT_SESSIONS           = _get_env_int("MAX_CONCURRENT_SESSIONS", 100, min_value=1)
MAX_TURNS_PER_SESSION             = _get_env_int("MAX_TURNS_PER_SESSION", 50, min_value=10)
TURN_CAP_WARNING_TURNS            = _get_env_int("TURN_CAP_WARNING_TURNS", 5, min_value=1)
TURN_CAP_GRACE_TURNS              = _get_env_int("TURN_CAP_GRACE_TURNS", 3, min_value=0)
PROMPT_HISTORY_ITEMS         = _get_env_int("PROMPT_HISTORY_ITEMS", 2, min_value=2)
TURN_CHAT_CTX_MAX_ITEMS      = _get_env_int("TURN_CHAT_CTX_MAX_ITEMS", 10, min_value=8)
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
SESSION_TTS_VOICE            = os.getenv("SESSION_TTS_VOICE", "Salma")
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
SESSION_LLM_MODEL            = os.getenv("SESSION_LLM_MODEL", "gemini-2.5-flash").strip()

if "/" in SESSION_LLM_MODEL:
    SESSION_LLM_MODEL = SESSION_LLM_MODEL.split("/", 1)[1]
SESSION_LLM_REASONING_EFFORT = os.getenv("SESSION_LLM_REASONING_EFFORT", "low").strip().lower() or "low"
SESSION_LLM_VERBOSITY        = os.getenv("SESSION_LLM_VERBOSITY", "low").strip().lower() or "low"
SESSION_LLM_MAX_COMPLETION_TOKENS = _get_env_int("SESSION_LLM_MAX_COMPLETION_TOKENS", 160, min_value=32)
SESSION_LLM_TEMPERATURE      = _get_env_float("SESSION_LLM_TEMPERATURE", 0.85, min_value=0.0)
SESSION_LLM_TOP_P            = _get_env_float("SESSION_LLM_TOP_P", 0.95, min_value=0.0)
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

__all__ = [
    "AGENT_DIR",
    "CAIRO_TZ",
    "APP_ENV",
    "BACKEND_BASE",
    "BACKEND_APIKEY",
    "HTTP_TIMEOUT_SECONDS",
    "HTTP_CONNECT_TIMEOUT_SECONDS",
    "HTTP_READ_TIMEOUT_SECONDS",
    "HTTP_WRITE_TIMEOUT_SECONDS",
    "BACKEND_MAX_RETRIES",
    "BACKEND_RETRY_BASE_SECONDS",
    "CONFIG_FETCH_RETRIES",
    "CONFIG_FETCH_BACKOFF_SECONDS",
    "CONFIG_FETCH_TOTAL_BUDGET_SECONDS",
    "CONFIG_REFRESH_INTERVAL_SECONDS",
    "CONFIG_CACHE_TTL",
    "MAX_CONCURRENT_SESSIONS",
    "MAX_TURNS_PER_SESSION",
    "TURN_CAP_WARNING_TURNS",
    "TURN_CAP_GRACE_TURNS",
    "PROMPT_HISTORY_ITEMS",
    "TURN_CHAT_CTX_MAX_ITEMS",
    "MAX_TOOL_STEPS",
    "MIN_INTERRUPTION_DURATION_SECONDS",
    "MIN_ENDPOINTING_DELAY_SECONDS",
    "MAX_ENDPOINTING_DELAY_SECONDS",
    "FALSE_INTERRUPTION_TIMEOUT_SECONDS",
    "USER_AWAY_TIMEOUT_SECONDS",
    "NO_SPEECH_PROMPT_SECONDS",
    "NO_SPEECH_CLOSE_SECONDS",
    "NO_SPEECH_REPROMPT_LIMIT",
    "NO_SPEECH_REPROMPT_GAP_SECONDS",
    "SESSION_TTS_MODEL",
    "SESSION_TTS_VOICE",
    "SESSION_TTS_LANGUAGE",
    "SESSION_TTS_DIALECT",
    "SESSION_TTS_MULAW",
    "SESSION_TTS_STREAMING_ENABLED",
    "SESSION_TTS_STREAM_PACING",
    "SESSION_STT_LANGUAGE",
    "SESSION_STT_MODEL",
    "SESSION_STT_BASE_URL",
    "SESSION_STT_LANGUAGE_HINTS_STRICT",
    "SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION",
    "SESSION_STT_KEYTERM_LIMIT",
    "SESSION_STT_EXTRA_KEYTERMS",
    "SESSION_LLM_MODEL",
    "SESSION_LLM_REASONING_EFFORT",
    "SESSION_LLM_VERBOSITY",
    "SESSION_LLM_MAX_COMPLETION_TOKENS",
    "SESSION_LLM_TEMPERATURE",
    "SESSION_LLM_TOP_P",
    "SESSION_LLM_THINKING_BUDGET",
    "SESSION_PREEMPTIVE_GENERATION",
    "CONFIG_SHARED_CACHE_ENABLED",
    "CONFIG_SHARED_CACHE_PATH",
    "BACKEND_WRITE_QUEUE_ENABLED",
    "BACKEND_WRITE_QUEUE_PATH",
    "BACKEND_WRITE_QUEUE_MAX_ITEMS",
    "BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES",
    "BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS",
    "BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD",
    "BACKEND_WRITE_CIRCUIT_OPEN_SECONDS",
    "BACKEND_POST_TIMEOUT_SECONDS",
    "AGENT_IDLE_PROCESSES",
    "AGENT_HEALTH_SNAPSHOT_DIR",
    "AGENT_HEALTH_SNAPSHOT_STALE_SECONDS",
]
