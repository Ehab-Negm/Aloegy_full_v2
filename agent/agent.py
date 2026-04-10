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
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
import yaml
from dotenv import load_dotenv
from pydantic import Field

from livekit.agents import AgentServer, JobContext, StopResponse, cli, llm
from livekit.agents.llm import function_tool
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import Agent, AgentSession, RunContext
from livekit.agents import inference
from livekit.plugins import google, openai, silero
try:
    from livekit.plugins import soniox
except ImportError:
    soniox = None

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("restaurant.agent")
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
CONFIG_FETCH_TOTAL_BUDGET_SECONDS = _get_env_float("CONFIG_FETCH_TOTAL_BUDGET_SECONDS", 1.0, min_value=0.1)
PROMPT_HISTORY_ITEMS         = _get_env_int("PROMPT_HISTORY_ITEMS", 4, min_value=2)
TURN_CHAT_CTX_MAX_ITEMS      = _get_env_int("TURN_CHAT_CTX_MAX_ITEMS", 18, min_value=8)
MAX_TOOL_STEPS               = _get_env_int("MAX_TOOL_STEPS", 10, min_value=6)
MIN_INTERRUPTION_DURATION_SECONDS = _get_env_float("MIN_INTERRUPTION_DURATION_SECONDS", 0.6, min_value=0.0)
MIN_ENDPOINTING_DELAY_SECONDS     = _get_env_float("MIN_ENDPOINTING_DELAY_SECONDS", 0.35, min_value=0.05)
MAX_ENDPOINTING_DELAY_SECONDS     = _get_env_float("MAX_ENDPOINTING_DELAY_SECONDS", 1.0, min_value=0.1)
FALSE_INTERRUPTION_TIMEOUT_SECONDS = _get_env_float("FALSE_INTERRUPTION_TIMEOUT_SECONDS", 1.5, min_value=0.1)
USER_AWAY_TIMEOUT_SECONDS         = _get_env_float("USER_AWAY_TIMEOUT_SECONDS", 9.0, min_value=0.5)
NO_SPEECH_PROMPT_SECONDS          = _get_env_float("NO_SPEECH_PROMPT_SECONDS", 12.0, min_value=1.0)
NO_SPEECH_CLOSE_SECONDS           = _get_env_float("NO_SPEECH_CLOSE_SECONDS", 28.0, min_value=2.0)
NO_SPEECH_REPROMPT_LIMIT          = _get_env_int("NO_SPEECH_REPROMPT_LIMIT", 1, min_value=1)
NO_SPEECH_REPROMPT_GAP_SECONDS    = _get_env_float("NO_SPEECH_REPROMPT_GAP_SECONDS", 8.0, min_value=0.5)
VOICE_MENU_LIMIT             = _get_env_int("VOICE_MENU_LIMIT", 10, min_value=4)
MENU_PROMPT_LIMIT            = _get_env_int("MENU_PROMPT_LIMIT", 20, min_value=6)
SESSION_TTS_MODEL            = os.getenv("SESSION_TTS_MODEL", "xai/tts-1")
SESSION_TTS_VOICE            = os.getenv("SESSION_TTS_VOICE", "ara")
SESSION_TTS_LANGUAGE         = os.getenv("SESSION_TTS_LANGUAGE", "ar-EG")
SESSION_STT_LANGUAGE         = os.getenv("SESSION_STT_LANGUAGE", "ar")
SESSION_STT_MODEL            = os.getenv("SESSION_STT_MODEL", "stt-rt-v4")
SESSION_STT_BASE_URL         = os.getenv("SESSION_STT_BASE_URL", "wss://stt-rt.soniox.com/transcribe-websocket").strip()
SESSION_STT_LANGUAGE_HINTS_STRICT = _get_env_bool("SESSION_STT_LANGUAGE_HINTS_STRICT", True)
SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION = _get_env_bool("SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION", True)
SESSION_STT_KEYTERM_LIMIT    = _get_env_int("SESSION_STT_KEYTERM_LIMIT", 40, min_value=5)
SESSION_STT_EXTRA_KEYTERMS   = os.getenv("SESSION_STT_EXTRA_KEYTERMS", "")
SESSION_LLM_MODEL            = os.getenv("SESSION_LLM_MODEL", "gemini-2.5-flash")
SESSION_PREEMPTIVE_GENERATION = _get_env_bool("SESSION_PREEMPTIVE_GENERATION", True)
CONFIG_SHARED_CACHE_ENABLED  = _get_env_bool("CONFIG_SHARED_CACHE_ENABLED", True)
CONFIG_SHARED_CACHE_PATH     = os.getenv("CONFIG_SHARED_CACHE_PATH", f".runtime/{APP_ENV}/config_cache.json")
BACKEND_WRITE_QUEUE_ENABLED  = _get_env_bool("BACKEND_WRITE_QUEUE_ENABLED", True)
BACKEND_WRITE_QUEUE_PATH     = os.getenv("BACKEND_WRITE_QUEUE_PATH", f".runtime/{APP_ENV}/backend_write_queue.jsonl")
BACKEND_WRITE_QUEUE_MAX_ITEMS = _get_env_int("BACKEND_WRITE_QUEUE_MAX_ITEMS", 500, min_value=1)
BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS = _get_env_float("BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS", 5.0, min_value=0.5)
BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD = _get_env_int("BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD", 3, min_value=1)
BACKEND_WRITE_CIRCUIT_OPEN_SECONDS = _get_env_float("BACKEND_WRITE_CIRCUIT_OPEN_SECONDS", 8.0, min_value=0.5)

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
    if soniox is None:
        return SimpleNamespace(**option_kwargs)
    return soniox.STTOptions(**option_kwargs)


def _stt_provider_ready_reason() -> str | None:
    if soniox is None:
        return "livekit-plugins-soniox is not installed"
    if not os.getenv("SONIOX_API_KEY"):
        return "SONIOX_API_KEY is missing"
    return None


from xai_tts import TTS as XaiTTS  # noqa: E402

SESSION_TTS = XaiTTS(
    api_key=os.getenv("XAI_API_KEY", ""),
    voice=SESSION_TTS_VOICE,
    language=SESSION_TTS_LANGUAGE,
)
if not os.getenv("XAI_API_KEY"):
    logger.warning("XAI_API_KEY is not set — TTS will fail")
SESSION_STT_PROVIDER = "soniox"
if SESSION_LLM_MODEL.startswith("gpt-") or SESSION_LLM_MODEL.startswith("o"):
    SESSION_LLM = openai.LLM(model=SESSION_LLM_MODEL)
    logger.info("LLM provider: OpenAI | model=%s", SESSION_LLM_MODEL)
else:
    SESSION_LLM = google.LLM(model=SESSION_LLM_MODEL)
    logger.info("LLM provider: Google | model=%s", SESSION_LLM_MODEL)
SESSION_VAD = silero.VAD.load(
    min_silence_duration=0.25,
    prefix_padding_duration=0.2,
    activation_threshold=0.5,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared httpx client — reuse TCP/TLS connections instead of per-request
# ─────────────────────────────────────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _http_client_lock:
        if _http_client is not None and not _http_client.is_closed:
            return _http_client
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout=HTTP_TIMEOUT_SECONDS,
                connect=HTTP_CONNECT_TIMEOUT_SECONDS,
                read=HTTP_READ_TIMEOUT_SECONDS,
                write=HTTP_WRITE_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
            headers={
                "X-API-Key": BACKEND_APIKEY,
                "User-Agent": "restaurant-voice-agent/1.0",
            },
        )
        return _http_client


def _retry_delay(attempt: int, base_seconds: float) -> float:
    return max(0.05, base_seconds * (2 ** attempt))


def _response_snippet(response: httpx.Response | None, *, limit: int = 300) -> str:
    if response is None:
        return ""
    try:
        body = response.text or ""
    except Exception:
        return ""
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit]


def _exc_log_fields(exc: Exception) -> str:
    parts = [f"type={exc.__class__.__name__}", f"repr={exc!r}"]
    if isinstance(exc, httpx.HTTPStatusError):
        req = exc.request
        res = exc.response
        parts.extend(
            [
                f"method={req.method}",
                f"url={req.url}",
                f"status_code={res.status_code}",
            ]
        )
        snippet = _response_snippet(res)
        if snippet:
            parts.append(f"body={snippet!r}")
    elif isinstance(exc, httpx.RequestError):
        req = exc.request
        parts.extend([f"method={req.method}", f"url={req.url}"])
    return " | ".join(parts)


def _voice_safe_text(text: str, max_sentences: int = 2, max_chars: int = 120) -> str:
    """يقصّر النصوص الطويلة قبل الـ TTS من غير ما يغيّر المعنى الأساسي."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""

    sentences = [part.strip(" ،") for part in re.split(r"[.!؟\n]+", cleaned) if part.strip(" ،")]
    if sentences:
        cleaned = "، ".join(sentences[:max_sentences])
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip(" ،,.") + "…"
    return cleaned


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
    return _voice_safe_text("للأسف التوصيل مش متاح دلوقتي يا فندم. تحب تيكاواي بدل كده؟")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
DAYS_AR = {
    "saturday": "السبت", "sunday": "الأحد", "monday": "الاتنين",
    "tuesday": "التلات", "wednesday": "الأربع",
    "thursday": "الخميس", "friday": "الجمعة",
}

# ─── تحويل الأرقام لكلمات مصري ─────────────────────────────────────────────
_ONES = ["", "واحد", "اتنين", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة", "تمانية", "تسعة"]
_TEENS = ["عشرة", "حداشر", "اتناشر", "تلاتاشر", "أربعتاشر", "خمستاشر",
          "ستاشر", "سبعتاشر", "تمنتاشر", "تسعتاشر"]
_TENS = ["", "عشرة", "عشرين", "تلاتين", "أربعين", "خمسين", "ستين", "سبعين", "تمانين", "تسعين"]
_HUNDREDS = ["", "مية", "ميتين", "تلتمية", "ربعمية", "خمسمية",
             "ستمية", "سبعمية", "تمنمية", "تسعمية"]


def _int_to_ar(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 0:
        return f"سالب {_int_to_ar(-n)}"

    parts = []
    if n >= 1000:
        thousands = n // 1000
        if thousands == 1:
            parts.append("ألف")
        elif thousands == 2:
            parts.append("ألفين")
        elif 3 <= thousands <= 10:
            parts.append(f"{_ONES[thousands]} تآلاف")
        else:
            parts.append(f"{_int_to_ar(thousands)} ألف")
        n %= 1000

    if n >= 100:
        parts.append(_HUNDREDS[n // 100])
        n %= 100

    if n >= 20:
        ones = n % 10
        tens = n // 10
        if ones:
            parts.append(f"{_ONES[ones]} و{_TENS[tens]}")
        else:
            parts.append(_TENS[tens])
    elif n >= 10:
        parts.append(_TEENS[n - 10])
    elif n >= 1:
        parts.append(_ONES[n])

    return " و".join(parts)


def num2ar(n: int | float) -> str:
    """يحوّل أرقام صحيحة وكسور بسيطة لكلمات مصرية بدون فقدان الجزء العشري."""
    value = float(n)
    if value.is_integer():
        return _int_to_ar(int(value))

    sign = "سالب " if value < 0 else ""
    value = abs(value)
    whole = int(value)
    fractional = round(value - whole, 2)

    if fractional == 0:
        return f"{sign}{_int_to_ar(whole)}".strip()

    if whole == 0:
        if abs(fractional - 0.5) < 0.001:
            return f"{sign}نص".strip()
        if abs(fractional - 0.25) < 0.001:
            return f"{sign}ربع".strip()

    base = _int_to_ar(whole) if whole else ""
    if abs(fractional - 0.5) < 0.001:
        return f"{sign}{base} ونص".strip()
    if abs(fractional - 0.25) < 0.001:
        return f"{sign}{base} وربع".strip()

    fraction_hundredths = int(round(fractional * 100))
    fraction_text = _int_to_ar(fraction_hundredths)
    return f"{sign}{base} فاصلة {fraction_text}".strip()


def money2ar(value: float) -> str:
    """تمثيل أوضح للأسعار والرسوم مع الحفاظ على القيم العشرية المهمة."""
    return num2ar(float(value))


_DIGIT_AR = ["زيرو", "واحد", "اتنين", "تلاتة", "أربعة",
             "خمسة", "ستة", "سبعة", "تمانية", "تسعة"]
_PHONE_PREFIX_SPOKEN = {
    "010": "زيرو عشرة",
    "011": "زيرو حداشر",
    "012": "زيرو اتناشر",
    "015": "زيرو خمستاشر",
}

def phone2ar(phone: str) -> str:
    """ينطق رقم الموبايل بصيغة أقرب للمصريين."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    parts: list[str] = []
    if len(digits) >= 3 and digits[:3] in _PHONE_PREFIX_SPOKEN:
        parts.append(_PHONE_PREFIX_SPOKEN[digits[:3]])
        digits = digits[3:]
    parts.extend(_DIGIT_AR[int(d)] for d in digits)
    return " ".join(parts)


def spoken_phone(phone: str | None) -> str:
    valid = validate_phone(phone or "")
    return phone2ar(valid) if valid else "رقم المطعم"

# ─────────────────────────────────────────────────────────────────────────────
# RestaurantConfig
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RestaurantConfig:
    name:             str        = ""
    phone:            str        = ""
    address:          str        = ""
    branches:         list[dict] = field(default_factory=list)
    hours:            dict       = field(default_factory=dict)
    menu_items:       list[dict] = field(default_factory=list)
    upsell_rules:     list[dict] = field(default_factory=list)
    is_open:          bool       = True
    closed_reason:    str        = ""
    degraded_mode:    bool       = False
    config_source:    Literal["backend", "cache_fresh", "cache_stale", "degraded_fallback"] = "backend"

    # تيكاواي
    wait_minutes:     int        = 20

    # حجز
    min_guests:       int        = 1
    max_guests:       int        = 20

    # توصيل
    delivery_enabled: bool       = False
    delivery_minutes: int        = 45        # وقت التوصيل المتوقع
    delivery_fee:     float      = 0.0       # رسوم التوصيل
    min_order:        float      = 0.0       # أقل قيمة للطلب مع توصيل
    delivery_zones:   list[str]  = field(default_factory=list)  # مناطق التوصيل المتاحة

    # ── helpers ─────────────────────────────────────────────────────────────

    def hours_text(self) -> str:
        if self.degraded_mode and not self.hours:
            return "مواعيد المطعم غير متاحة مؤقتًا"
        if not self.hours:
            return "المواعيد غير محددة"
        parts = []
        for day, times in self.hours.items():
            label = DAYS_AR.get(day, day)
            if times.get("closed"):
                parts.append(f"{label}: مغلق")
            else:
                parts.append(f"{label}: {times.get('open','?')} - {times.get('close','?')}")
        return " | ".join(parts)

    def branch_names(self) -> str:
        return " / ".join(b.get("name", "") for b in self.branches)

    def delivery_zones_text(self) -> str:
        if not self.delivery_zones:
            return "جميع المناطق"
        return "، ".join(self.delivery_zones)

    def delivery_info_text(self) -> str:
        if self.degraded_mode and not self.delivery_enabled:
            return "بيانات التوصيل غير متاحة مؤقتًا"
        parts = [f"وقت التوصيل: {num2ar(self.delivery_minutes)} دقيقة"]
        if self.delivery_fee > 0:
            parts.append(f"رسوم التوصيل: {money2ar(self.delivery_fee)} جنيه")
        if self.min_order > 0:
            parts.append(f"أقل طلب للتوصيل: {money2ar(self.min_order)} جنيه")
        return " | ".join(parts)


    def menu_text(self) -> str:
        available = [i for i in self.menu_items if i.get("available", True)]
        if not available:
            return "المنيو مش متاح مؤقتًا دلوقتي" if self.degraded_mode else "المنيو مش متاح دلوقتي"
        max_shown = min(3, VOICE_MENU_LIMIT, len(available))
        extra_suffix = "، ولو عايز حاجة تانية قولهولي." if len(available) > max_shown else "."

        for shown_count in range(max_shown, 0, -1):
            shown = available[:shown_count]
            text = "المتاح دلوقتي: " + "، ".join(
                f"{i['name']} بـ{money2ar(i['price'])}" for i in shown
            )
            text += extra_suffix
            if len(text) <= 120:
                return text

        return "المتاح دلوقتي: كوشري صغير، وكوشري وسط، وكوشري كبير. ولو عايز حاجة تانية قولهولي."

    def menu_names(self) -> str:
        available = [i["name"] for i in self.menu_items if i.get("available", True)]
        if not available:
            return "المنيو مش متاح مؤقتًا" if self.degraded_mode else "المنيو مش متاح"
        shown = available[:MENU_PROMPT_LIMIT]
        text = "، ".join(shown)
        if len(available) > len(shown):
            text += f"، وغيرهم {num2ar(len(available) - len(shown))} أصناف"
        return text


@dataclass
class CachedConfigEntry:
    fetched_at_monotonic: float
    config: RestaurantConfig
    source: str = "backend"


@dataclass
class ParsedReservationTime:
    raw_text: str
    scheduled_at: datetime
    normalized_text: str


@dataclass
class RuntimeHealth:
    config_available: bool = False
    last_config_error: str = ""


@dataclass
class CallWriteHealth:
    write_available: bool = True
    last_write_error: str = ""
    last_write_failure_kind: str = ""
    last_write_status_code: int | None = None
    write_blocked_until_monotonic: float = 0.0


@dataclass
class BackendCircuitState:
    consecutive_failures: int = 0
    open_until_monotonic: float = 0.0
    last_error: str = ""

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


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


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
    if not path.exists():
        return {}
    try:
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
    _ensure_parent_dir(path)
    shared_map = await _read_shared_cache_map()
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


def _stt_keyterms_for_config(cfg: RestaurantConfig) -> list[str]:
    return _stt_context_terms_for_config(cfg)


def _build_session_stt(cfg: RestaurantConfig, *, client_reference_id: str | None = None) -> Any:
    # Soniox uses language hints + context instead of provider-specific keyterms.
    not_ready_reason = _stt_provider_ready_reason()
    if not_ready_reason:
        raise RuntimeError(not_ready_reason)

    context_terms = _stt_context_terms_for_config(cfg)
    return soniox.STT(
        base_url=SESSION_STT_BASE_URL,
        params=_session_stt_options(
            context_terms=context_terms,
            client_reference_id=client_reference_id,
        ),
    )


def backend_config_available() -> bool:
    return _runtime_health.config_available


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
    global _config_cache, _runtime_health

    cache_key = restaurant_id or "__default__"
    endpoint = f"{BACKEND_BASE}/restaurant/config"
    cached_entry = _config_cache.get(cache_key)
    cache_age = _config_cache_age_seconds(cached_entry)
    shared_entry = await _read_shared_cache_entry(cache_key)
    shared_cfg = shared_entry[0] if shared_entry else None
    shared_age = shared_entry[1] if shared_entry else None

    stale_fallback: RestaurantConfig | None = None

    if cached_entry is None:
        logger.info("call=%s | config cache MISS | restaurant=%s", call_id, cache_key)
    elif cache_age is not None and cache_age <= CONFIG_CACHE_TTL:
        logger.info(
            "call=%s | config cache HIT fresh | restaurant=%s | age=%.2fs",
            call_id, cache_key, cache_age,
        )
        cfg = _clone_config_with_source(cached_entry.config, "cache_fresh")
        _runtime_health.config_available = True
        logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
        return cfg
    elif cached_entry is not None:
        logger.warning(
            "call=%s | config cache HIT stale | restaurant=%s | age=%.2fs | action=refresh_backend",
            call_id, cache_key, cache_age or 0.0,
        )
        stale_fallback = _clone_config_with_source(cached_entry.config, "cache_stale")

    if shared_cfg is not None and shared_age is not None:
        if shared_age <= CONFIG_CACHE_TTL:
            logger.info(
                "call=%s | shared config cache HIT fresh | restaurant=%s | age=%.2fs",
                call_id, cache_key, shared_age,
            )
            cfg = _clone_config_with_source(shared_cfg, "cache_fresh")
            _config_cache[cache_key] = CachedConfigEntry(
                fetched_at_monotonic=time.monotonic() - shared_age,
                config=_clone_config_with_source(shared_cfg, "backend"),
                source="shared_cache",
            )
            _runtime_health.config_available = True
            logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
            return cfg
        logger.warning(
            "call=%s | shared config cache HIT stale | restaurant=%s | age=%.2fs | action=refresh_backend",
            call_id, cache_key, shared_age,
        )
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
            _config_cache[cache_key] = CachedConfigEntry(
                fetched_at_monotonic=time.monotonic(),
                config=cfg,
                source="backend",
            )
            try:
                await _write_shared_cache_entry(cache_key, cfg)
            except Exception as exc:
                logger.warning(
                    "call=%s | shared cache write skipped | restaurant=%s | %s",
                    call_id, cache_key, _exc_log_fields(exc),
                )
            _runtime_health.config_available = True
            _runtime_health.last_config_error = ""
            logger.info(
                "call=%s | config loaded | restaurant=%s | source=backend | open=%s | delivery=%s | items=%d | latency=%dms",
                call_id, cache_key, cfg.is_open, cfg.delivery_enabled, len(cfg.menu_items), latency_ms,
            )
            logger.info("call=%s | config source chosen | source=%s", call_id, cfg.config_source)
            return cfg

        except Exception as exc:
            last_exc = exc
            _runtime_health.last_config_error = _exc_log_fields(exc)
            wait = _retry_delay(attempt, CONFIG_FETCH_BACKOFF_SECONDS)
            logger.warning(
                "call=%s | config fetch failed | attempt=%d | endpoint=%s | %s | retry in %.2fs",
                call_id, attempt + 1, endpoint, _exc_log_fields(exc), wait,
            )
            if attempt < CONFIG_FETCH_RETRIES - 1 and (deadline - time.monotonic()) > wait:
                await asyncio.sleep(wait)

    if stale_fallback is not None:
        _runtime_health.config_available = True
        logger.warning(
            "call=%s | stale cache used fallback | restaurant=%s | age=%.2fs | last_error=%s",
            call_id, cache_key, cache_age or 0.0,
            _exc_log_fields(last_exc) if last_exc else "none",
        )
        logger.info("call=%s | config source chosen | source=%s", call_id, stale_fallback.config_source)
        return stale_fallback

    degraded_cfg = _degraded_config()
    _runtime_health.config_available = False
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
    return _backend_queue_lock


def _backend_queue_path() -> Path:
    return _runtime_file_path(BACKEND_WRITE_QUEUE_PATH)


def _backend_endpoint_class(endpoint: str) -> str:
    return endpoint.strip().lower() or "unknown"


def _get_backend_circuit(endpoint: str) -> BackendCircuitState:
    key = _backend_endpoint_class(endpoint)
    state = _backend_circuits.get(key)
    if state is None:
        state = BackendCircuitState()
        _backend_circuits[key] = state
    return state


def _backend_circuit_is_open(endpoint: str) -> bool:
    state = _get_backend_circuit(endpoint)
    return state.open_until_monotonic > time.monotonic()


def _record_backend_circuit_success(endpoint: str) -> None:
    state = _get_backend_circuit(endpoint)
    state.consecutive_failures = 0
    state.open_until_monotonic = 0.0
    state.last_error = ""


def _record_backend_circuit_failure(endpoint: str, exc: Exception) -> None:
    if not _should_retry_backend_error(exc):
        return
    state = _get_backend_circuit(endpoint)
    state.consecutive_failures += 1
    state.last_error = _exc_log_fields(exc)
    if state.consecutive_failures >= BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD:
        state.open_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS
        logger.warning(
            "backend circuit opened | endpoint=%s | failures=%d | open_for=%.2fs | %s",
            endpoint, state.consecutive_failures, BACKEND_WRITE_CIRCUIT_OPEN_SECONDS, state.last_error,
        )


def _mark_backend_circuit_open(health: CallWriteHealth | None) -> None:
    if health is None:
        return
    health.write_available = False
    health.last_write_error = "type=CircuitOpen"
    health.last_write_failure_kind = "CircuitOpen"
    health.last_write_status_code = None
    health.write_blocked_until_monotonic = time.monotonic() + BACKEND_WRITE_CIRCUIT_OPEN_SECONDS


async def _enqueue_backend_write(
    endpoint: str,
    payload: dict,
    call_id: str,
    *,
    idempotency_action: str,
) -> bool:
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return False

    queue_path = _backend_queue_path()
    _ensure_parent_dir(queue_path)
    item = {
        "endpoint": endpoint,
        "payload": payload,
        "call_id": call_id,
        "idempotency_action": idempotency_action,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    lock = _backend_queue_lock_instance()
    async with lock:
        existing_lines: list[str] = []
        if queue_path.exists():
            existing_lines = queue_path.read_text(encoding="utf-8").splitlines()
        if len(existing_lines) >= BACKEND_WRITE_QUEUE_MAX_ITEMS:
            logger.error("backend queue full | path=%s | size=%d", queue_path, len(existing_lines))
            return False
        with queue_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(item, ensure_ascii=False) + "\n")
    logger.warning("call=%s | backend write queued | endpoint=%s", call_id, endpoint)
    return True


async def _drain_backend_write_queue_once() -> None:
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    queue_path = _backend_queue_path()
    if not queue_path.exists():
        return

    lock = _backend_queue_lock_instance()
    async with lock:
        lines = [line for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return
        remaining: list[str] = []
        for line in lines:
            try:
                item = _json.loads(line)
            except Exception:
                logger.warning("backend queue dropped invalid line | path=%s", queue_path)
                continue
            endpoint = str(item.get("endpoint", "")).strip()
            if not endpoint or _backend_circuit_is_open(endpoint):
                remaining.append(line)
                continue
            result = await _post(
                endpoint,
                item.get("payload", {}),
                str(item.get("call_id", "queued-write")),
                idempotency_action=str(item.get("idempotency_action", "")),
                max_retries=1,
                write_health=None,
                enqueue_on_retryable_failure=False,
            )
            if result is None:
                remaining.append(line)
                break

        tmp_path = queue_path.with_suffix(queue_path.suffix + ".tmp")
        if remaining:
            tmp_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            os.replace(tmp_path, queue_path)
        else:
            with contextlib.suppress(FileNotFoundError):
                queue_path.unlink()


async def _backend_queue_worker_loop() -> None:
    while True:
        await asyncio.sleep(BACKEND_WRITE_QUEUE_RETRY_INTERVAL_SECONDS)
        try:
            await _drain_backend_write_queue_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("backend queue worker error")


async def _ensure_backend_queue_worker_started() -> None:
    global _backend_queue_worker
    if not BACKEND_WRITE_QUEUE_ENABLED:
        return
    if _backend_queue_worker is not None and not _backend_queue_worker.done():
        return
    _backend_queue_worker = asyncio.create_task(
        _backend_queue_worker_loop(),
        name="backend_write_queue_worker",
    )
    logger.info("backend queue worker started | path=%s", _backend_queue_path())


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


def _should_retry_backend_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(
        exc,
        (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError),
    )


async def _post(
    endpoint: str,
    payload: dict,
    call_id: str,
    *,
    idempotency_action: str = "",
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
    if idempotency_action:
        headers["Idempotency-Key"] = _idempotency_key(call_id, idempotency_action, payload)

    if _backend_circuit_is_open(endpoint):
        _mark_backend_circuit_open(write_health)
        logger.warning("call=%s | POST blocked by circuit | endpoint=%s", call_id, endpoint)
        if enqueue_on_retryable_failure:
            queued = await _enqueue_backend_write(
                endpoint,
                payload,
                call_id,
                idempotency_action=idempotency_action,
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
            )
            res.raise_for_status()
            data = res.json()
            latency_ms = int((time.monotonic() - t0) * 1000)
            _mark_backend_write_success(write_health)
            _record_backend_circuit_success(endpoint)
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
                _record_backend_circuit_success(endpoint)
                break
            if attempt < retries - 1:
                await asyncio.sleep(wait)

    if last_exc is not None:
        _record_backend_circuit_failure(endpoint, last_exc)
        if enqueue_on_retryable_failure and _should_retry_backend_error(last_exc):
            queued = await _enqueue_backend_write(
                endpoint,
                payload,
                call_id,
                idempotency_action=idempotency_action,
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


# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_PHONE_PREFIXES = ("010", "011", "012", "015")

def validate_phone(phone: str) -> str | None:
    """يتحقق من صحة رقم الموبايل المصري — يقبل أرقام عربية/إنجليزية مع مسافات."""
    # حوّل الأرقام العربية للإنجليزية
    cleaned = phone.translate(_AR_DIGITS)
    # شيل أي حاجة مش رقم ومش + (مسافات، شرط، أقواس، حروف)
    cleaned = re.sub(r"[^\d+]", "", cleaned)
    # لو بيبدأ بـ 2010/2011/2012/2015 بدون + ضيف +
    if re.fullmatch(r"201[0125]\d{8}", cleaned):
        cleaned = "+" + cleaned
    return cleaned if re.fullmatch(r"(01[0125]\d{8}|\+201[0125]\d{8})", cleaned) else None


def _phone_digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone.translate(_AR_DIGITS))


def _is_phone_like_text(text: str) -> bool:
    translated = (text or "").translate(_AR_DIGITS)
    digits = re.sub(r"\D", "", translated)
    if not digits:
        return False
    non_phone = re.sub(r"[\d\s()+\-]", "", translated).strip()
    return len(non_phone) <= 4


def _is_plausible_partial_phone_digits(digits: str) -> bool:
    if not digits:
        return False
    local_digits = _local_phone_digits(digits)
    if not local_digits:
        return False
    if len(local_digits) >= 11:
        return bool(validate_phone(local_digits))
    if any(prefix.startswith(local_digits) for prefix in _PHONE_PREFIXES):
        return True
    if len(local_digits) >= 3 and any(local_digits.startswith(prefix) for prefix in _PHONE_PREFIXES):
        return True
    return False


def _merge_phone_digits(buffered: str, incoming: str) -> str:
    if not buffered:
        return incoming
    if not incoming:
        return buffered
    if incoming.startswith("01") or incoming.startswith("20") or incoming.startswith("201"):
        return incoming
    return buffered + incoming


def _local_phone_digits(digits: str) -> str:
    normalized = re.sub(r"\D", "", (digits or "").translate(_AR_DIGITS))
    if normalized.startswith("20") and len(normalized) >= 12:
        normalized = "0" + normalized[2:]
    return normalized


def _phone_capture_short_reply(ud: "UserData", partial_digits: str) -> str:
    local_digits = _local_phone_digits(partial_digits)
    remaining = max(0, 11 - len(local_digits))
    if remaining <= 0:
        return ""
    if remaining <= 4:
        return "آخر أربع أرقام."
    if ud.phone_capture_turns >= 2:
        return "كمّل."
    return ""


def _phone_capture_failure_reply(ud: "UserData") -> str:
    if ud.phone_capture_failures <= 1:
        return "الرقم ناقص."
    if ud.phone_capture_failures == 2:
        return "كمّل بس أرقام."
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
    return f"{_spoken_order_items(ud.order)} باسم {ud.customer_name}، صح؟"


def _delivery_confirmation_prompt(ud: "UserData") -> str:
    return f"{_spoken_order_items(ud.order)} لعنوان {ud.delivery_address} باسم {ud.customer_name}، صح؟"


def _reservation_confirmation_prompt(ud: "UserData") -> str:
    return f"حجز {num2ar(ud.guests_count or 0)} ضيوف يوم {ud.reservation_time} باسم {ud.customer_name}، صح؟"


def _clean_followup_note(note: str) -> str:
    return re.sub(r"\s+", " ", (note or "")).strip(" .")


def _followup_after_name(flow: str, ud: "UserData") -> str:
    if flow == "complaint":
        return _ask_phone() if not ud.customer_phone else "تحب حاجة تانية؟"
    return _ask_phone()


def _followup_after_phone(flow: str, ud: "UserData") -> str:
    if flow == "takeaway":
        return _takeaway_confirmation_prompt(ud) if _is_takeaway_ready_for_confirmation(ud) else "تحب تطلب إيه؟"
    if flow == "delivery":
        return _delivery_confirmation_prompt(ud) if _is_delivery_ready_for_confirmation(ud) else "تحب تطلب إيه؟"
    if flow == "reservation":
        return _reservation_confirmation_prompt(ud) if _is_reservation_ready_for_confirmation(ud, ud.restaurant) else "عايز تحجز إمتى يا فندم؟"
    if flow == "complaint":
        return "تحب حاجة تانية يا فندم؟"
    return ""


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
        return _phone_capture_failure_reply(ud)

    direct_valid = validate_phone(incoming_digits)
    combined_digits = _merge_phone_digits(ud.pending_phone_digits, incoming_digits)
    combined_valid = validate_phone(combined_digits)

    cleaned = direct_valid or combined_valid
    if cleaned:
        ud.customer_phone = cleaned
        ud.pending_phone_digits = ""
        _set_phone_capture_mode(ud, False)
        logger.info("call=%s | phone set", ud.call_id)
        complaint_note = await _maybe_submit_pending_complaint_for_flow(ud, flow_name)
        note = _clean_followup_note(complaint_note)
        followup = _followup_after_phone(flow_name, ud)
        if note:
            return _voice_safe_text(_join_user_phrases(note, followup), max_chars=180)
        if followup:
            return _voice_safe_text(_join_user_phrases(_ack(), followup), max_chars=180)
        return _voice_safe_text(f"{_ack()}.")

    partial_digits = combined_digits if _is_plausible_partial_phone_digits(combined_digits) else ""
    if partial_digits:
        ud.pending_phone_digits = partial_digits
        ud.phone_capture_turns += 1
        _set_phone_capture_mode(ud, True)
        logger.info("call=%s | phone_partial_buffered | digits=%d", ud.call_id, len(partial_digits))
        return _phone_capture_short_reply(ud, partial_digits)

    ud.pending_phone_digits = ""
    ud.phone_capture_failures += 1
    _set_phone_capture_mode(ud, True)
    return _phone_capture_failure_reply(ud)


async def _apply_name_update(ud: "UserData", name_text: str, *, flow_name: str) -> str:
    cleaned = _extract_name_candidate(name_text)
    if not cleaned:
        return _voice_safe_text("الاسم مش واضح، قولي الاسم بس يا فندم.")
    ud.customer_name = cleaned
    logger.info("call=%s | name=%s", ud.call_id, cleaned)
    complaint_note = await _maybe_submit_pending_complaint_for_flow(ud, flow_name)
    note = _clean_followup_note(complaint_note)
    _set_phone_capture_mode(ud, _flow_missing_phone(flow_name, ud))
    if note:
        return _voice_safe_text(_join_user_phrases(note, _followup_after_name(flow_name, ud)), max_chars=180)
    return _voice_safe_text(
        _join_user_phrases(
            _random.choice([f"أهلاً يا {cleaned}", f"نورت يا {cleaned}", f"{_ack()} يا {cleaned}"]),
            _followup_after_name(flow_name, ud),
        ),
        max_chars=180,
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


def _looks_like_reservation_time(value: str) -> bool:
    # Heuristic validator only: بنقوي اكتشاف التاريخ/الساعة من غير parser كامل.
    raw = (value or "").translate(_AR_DIGITS).strip()
    if len(raw) < 3:
        return False

    norm = _normalize_ar(raw)
    has_day_hint = any(_normalize_ar(hint) in norm for hint in _DATE_HINTS)
    has_time_hint = any(_normalize_ar(hint) in norm for hint in _TIME_HINTS)
    has_clock = bool(re.search(r"\b\d{1,2}(:\d{2})?\b", raw))
    has_date = bool(re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", raw))
    has_iso = bool(re.search(r"\b\d{4}-\d{2}-\d{2}(?:[ t]\d{1,2}:\d{2})?\b", raw))
    has_relative_combo = any(word in norm for word in ["بعد", "بكره", "بكرة", "النهارده", "اليوم"])
    has_day_period = any(word in norm for word in ["الصبح", "العصر", "بالليل", "مساء", "الظهر"])
    return has_iso or has_date or ((has_day_hint or has_relative_combo) and (has_time_hint or has_clock or has_day_period))

# ─────────────────────────────────────────────────────────────────────────────
# Arabic text normalization + fuzzy menu matching
# ─────────────────────────────────────────────────────────────────────────────

def _menu_match(item_name: str, available_names: set[str]) -> bool:
    """يتحقق من وجود صنف في المنيو مع مطابقة مرنة للعربي."""
    norm = _normalize_ar(item_name)
    for a in available_names:
        norm_a = _normalize_ar(a)
        if norm in norm_a or norm_a in norm:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Config cache + runtime health
# ─────────────────────────────────────────────────────────────────────────────
_config_cache: dict[str, CachedConfigEntry] = {}
_runtime_health = RuntimeHealth()
CONFIG_CACHE_TTL = _get_env_float("CONFIG_CACHE_TTL", 60.0, min_value=1.0)
_backend_circuits: dict[str, BackendCircuitState] = {}
_backend_queue_worker: asyncio.Task | None = None
_backend_queue_lock = asyncio.Lock()

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
# UserData
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class UserData:
    # بيانات العميل
    customer_name:         str | None       = None
    customer_phone:        str | None       = None
    pending_phone_digits:  str              = ""
    phone_capture_mode:    bool             = False
    phone_capture_turns:   int              = 0
    phone_capture_failures: int             = 0

    # طلب (مشترك بين تيكاواي وتوصيل)
    order:                 list[str] | None = None
    order_validated:       bool             = False
    order_total:           float            = 0.0
    special_requests:      str | None       = None
    upsell_offered:        bool             = False
    upsell_accepted:       bool             = False
    pending_upsell_item:   str | None       = None
    pending_upsell_price:  float | None     = None
    order_confirmed:       bool             = False
    order_id:              str | None       = None
    order_submit_in_flight: bool           = False

    # توصيل
    delivery_address:      str | None       = None   # العنوان كامل
    delivery_zone:         str | None       = None   # المنطقة / الحي
    delivery_landmark:     str | None       = None   # علامة مميزة
    landmark_asked:        bool             = False  # هل اتسأل عن العلامة

    # حجز
    reservation_time:      str | None       = None
    reservation_time_iso:  str | None       = None
    guests_count:          int | None       = None
    selected_branch:       str | None       = None
    reservation_notes:     str | None       = None
    reservation_confirmed: bool             = False
    reservation_id:        str | None       = None
    reservation_submit_in_flight: bool     = False

    # شكوى
    complaint_text:        str | None       = None
    complaint_type:        str | None       = None
    complaint_logged:      bool             = False
    complaint_submit_in_flight: bool       = False

    # session internals
    agents:     dict[str, Agent] = field(default_factory=dict)
    prev_agent: Agent | None     = None
    call_id:    str | None       = None
    restaurant: RestaurantConfig = field(default_factory=RestaurantConfig)
    write_health: CallWriteHealth = field(default_factory=CallWriteHealth)
    session_transitional_state: bool = False

    def summarize(self) -> str:
        return yaml.dump({
            "name":             self.customer_name     or "—",
            "phone":            self.customer_phone    or "—",
            "order":            self.order             or "—",
            "special_requests": self.special_requests  or "—",
            "pending_upsell":   self.pending_upsell_item or "—",
            "upsell_accepted":  self.upsell_accepted,
            "delivery_address": self.delivery_address  or "—",
            "delivery_zone":    self.delivery_zone     or "—",
            "reservation_time": self.reservation_time  or "—",
            "guests_count":     self.guests_count      or "—",
            "branch":           self.selected_branch   or "—",
        }, allow_unicode=True)


RunContext_T = RunContext[UserData]

NEGATIVE_WORDS = {
    "لا", "لأ", "لاا", "مفيش", "مفيش طلب", "مفيش حاجه", "مفيش حاجة",
    "خلاص", "بس كده", "تمام كده", "لا تمام", "لا شكرا", "لا شكرًا",
    "ولا حاجه", "ولا حاجة", "no", "none",
    "آه لا", "اه لا", "آه لأ", "اه لأ", "لا مفيش", "لا خلاص", "لا كده تمام",
    "لا تمام كده", "آه مفيش", "اه مفيش",
}

POSITIVE_CONFIRMATION_WORDS = {
    "صح", "صح كده", "ايوه", "أيوه", "ماشي", "مظبوط", "تمام", "تمام كده",
    "تمام يا فندم", "أوكي", "اوكي", "yes",
}

UPSELL_ACCEPT_WORDS = {
    "ضيف", "ضيفها", "ضيفه", "ضيفهم", "حط", "حطها", "حطه", "حطهم",
    "زود", "زودها", "زوده", "زودهم", "هات", "هاتها", "هاته", "هاتهم",
    "عايزها", "عايزه", "عاوزها", "عاوزه", "ماشي ضيفها", "تمام ضيفها",
    "ايوه ضيفها", "أيوه ضيفها", "ايوه حطها", "أيوه حطها",
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
    negative_forms = {_normalize_ar(word) for word in NEGATIVE_WORDS}
    return any(normalized == word or normalized.startswith(f"{word} ") for word in negative_forms)


_ACK_PHRASES = ["تمام", "حاضر", "ماشي", "أوكي", "تمام يا فندم", "حاضر يا فندم"]
_ACK_GOT_IT = ["سجلت", "معايا", "خدت", "حطيت"]
_NEXT_NAME = ["اسمك إيه؟", "الاسم إيه يا فندم؟", "ممكن اسمك؟"]
_NEXT_PHONE = ["ورقم موبايلك؟", "وإيه رقم الموبايل؟", "والموبايل يا فندم؟"]
_NEXT_SPECIAL = ["في أي طلب خاص في التحضير؟", "عندك أي طلب خاص؟", "حابب حاجة معينة في التحضير؟"]
_NEXT_ADDRESS = ["عنوانك إيه يا فندم؟", "فين هنوصلك يا فندم؟", "قولي العنوان يا فندم."]

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


def _normalize_ar(text: str) -> str:
    text = (text or "").translate(_AR_DIGITS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[إأآا]", "ا", text)
    text = re.sub(r"[ى]", "ي", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _contains_normalized_phrase(text: str, phrases: set[str]) -> bool:
    normalized = _normalize_ar(text)
    if not normalized:
        return False
    wrapped = f" {normalized} "
    return any(f" {_normalize_ar(phrase)} " in wrapped for phrase in phrases)


def _normalized_phrase_present(normalized_text: str, phrase: str) -> bool:
    if not normalized_text:
        return False
    return f" {_normalize_ar(phrase)} " in f" {normalized_text} "


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
    return _contains_normalized_phrase(text, UPSELL_REJECTION_WORDS)


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


_DELIVERY_HINTS = {"توصيل", "دليفري", "الدليفري", "وصله", "يوصل", "delivery"}
_TAKEAWAY_HINTS = {"تيكاواي", "takeaway", "استلام", "اجي استلمه", "آجي استلمه", "هاخده", "اخده من المطعم"}
_ORDER_HINTS = {"اوردر", "أوردر", "طلب", "اطلب", "عايز اطلب", "أطلب"}
_MENU_HINTS = {
    "المنيو", "المتاح", "ايه المتاح", "إيه المتاح", "المتاح ايه", "إيه عندك",
    "ايه عندك", "عندك ايه", "عندكم ايه", "الاصناف", "الأصناف", "السعر",
    "الاسعار", "الأسعار", "ممكن اعرف المتاح", "اين متاح",
}
_RESERVATION_HINTS = {"حجز", "احجز", "ترابيزة", "ترابيزه", "رزيرفيشن"}
_COMPLAINT_HINTS = {"شكوى", "مشكلة", "المشكله", "اعتراض", "complaint"}
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
    return bool(normalized and _contains_any_hint(normalized, _MENU_HINTS))


_FILLER_STARTS = {"آ", "آآ", "آه", "اه", "أه", "امم", "ام", "ممم", "هم", "اهم"}

_NON_NAME_PATTERNS = {
    "آه لا", "اه لا", "آه لأ", "اه لأ", "آه مفيش", "اه مفيش",
    "آآ لا", "آآ لأ", "آآ مفيش", "آآ تمام",
    "لا مفيش", "مفيش", "لا خلاص", "خلاص", "تمام", "تمام كده",
    "لا تمام", "بس كده", "كده تمام", "اوكي", "أوكي", "ماشي",
}

def _is_likely_non_name_response(text: str) -> bool:
    """Detect filler/negative responses that should not be captured as names."""
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    normalized = _normalize_ar(cleaned)
    if not normalized:
        return True
    # Check against known non-name patterns
    for pattern in _NON_NAME_PATTERNS:
        np = _normalize_ar(pattern)
        if normalized == np or normalized.startswith(np + " "):
            return True
    # Starts with filler sound (آ, آآ, آه, اه, امم...) followed by anything
    first_token = cleaned.split()[0]
    if first_token in _FILLER_STARTS or _normalize_ar(first_token) in {_normalize_ar(f) for f in _FILLER_STARTS}:
        return True
    # Also reject if it's just a negative word
    if _looks_empty_answer(cleaned):
        return True
    return False


def _extract_name_candidate(text: str) -> str | None:
    cleaned = re.sub(r"[.!؟،,]+", " ", (text or "").translate(_AR_DIGITS)).strip()
    if not cleaned or _is_phone_like_text(cleaned):
        return None

    # Strip common intro phrases before the spoken name.
    for pattern in (
        r"^(?:انا|أنا)\s+(?:اسمي|اسمى)\s+",
        r"^(?:اسمي|اسمى|الاسم|اسم)\s+",
        r"^(?:انا|أنا)\s+",
        r"^(?:معاك|معاكي)\s+",
    ):
        updated = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
        if updated != cleaned:
            cleaned = updated
            break

    cleaned = re.sub(r"\s+(?:يا\s*فندم|لو\s*سمحت|من\s*فضلك)\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    normalized = _normalize_ar(cleaned)
    if not normalized or _looks_empty_answer(cleaned):
        return None
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
        return None

    tokens = cleaned.split()
    if not tokens or len(tokens) > 3:
        return None
    if any(re.search(r"\d", token) for token in tokens):
        return None

    blocked_tokens = {
        _normalize_ar(word)
        for word in ("تمام", "صح", "ماشي", "أيوه", "ايوه", "عنوان", "شارع", "منطقة", "مدينه", "مدينة")
    }
    if any(_normalize_ar(token) in blocked_tokens for token in tokens):
        return None
    return cleaned


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


def _guess_request_intent(text: str, cfg: RestaurantConfig) -> str:
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
) -> GreeterTurnDecision:
    if _is_greeting_only(text):
        return GreeterTurnDecision(
            message=_random.choice([
                "أهلاً! تحب تطلب أكل، تحجز، ولا في حاجة تانية؟",
                "يا هلا! عايز تطلب ولا تحجز ولا إيه؟",
                "أهلاً وسهلاً! تحب تطلب، تحجز، ولا محتاج حاجة؟",
            ]),
            reason="greeting_only",
        )

    intent = _guess_request_intent(text, cfg)
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
        return GreeterTurnDecision(
            message=_random.choice(["تيكاواي ولا توصيل يا فندم؟", "هتيجي تاخده ولا نوصلهولك؟"]),
            reason="order_ambiguous",
        )
    return GreeterTurnDecision(
        message=_random.choice([
            "تحب تطلب أكل، تحجز، ولا حاجة تانية؟",
            "عايز تطلب ولا تحجز ولا إيه يا فندم؟",
        ]),
        reason="unknown",
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

    qty = 1
    patterns = [
        r"(?:[×xX*]\s*(\d+))$",
        r"^(\d+)\s+(.+)$",
        r"^(.+?)\s+[×xX*]\s*(\d+)$",
        r"^(.+?)\s+(\d+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        groups = [g for g in match.groups() if g is not None]
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
    return text, max(1, qty)


def _resolve_menu_item(item_name: str, menu_items: list[dict]) -> dict | None:
    target = _normalize_ar(item_name)
    if not target:
        return None

    exact_match: dict | None = None
    best_partial: tuple[int, dict] | None = None

    for item in menu_items:
        if not item.get("available", True):
            continue
        norm_name = _normalize_ar(item.get("name", ""))
        if not norm_name:
            continue
        if norm_name == target:
            exact_match = item
            break
        if target in norm_name or norm_name in target:
            score = abs(len(norm_name) - len(target))
            if best_partial is None or score < best_partial[0]:
                best_partial = (score, item)

    if exact_match:
        return exact_match
    return best_partial[1] if best_partial else None


def _get_upsell_suggestion(ud: "UserData", cfg: "RestaurantConfig") -> str | None:
    """Pick an upsell item not already in the order."""
    if ud.upsell_offered or not cfg.upsell_rules:
        return None
    order_lower = {(item or "").lower() for item in (ud.order or [])}
    for rule in cfg.upsell_rules:
        item_name = rule.get("item", "")
        if item_name.lower() not in order_lower:
            price = rule.get("price")
            ud.upsell_offered = True
            ud.pending_upsell_item = item_name
            ud.pending_upsell_price = float(price) if price is not None else None
            if price:
                return f"ولو تحب، أزودلك {item_name} مع الطلب بـ{_int_to_ar(int(price))} جنيه؟"
            return rule.get("suggestion", f"ولو تحب، أزودلك {item_name} مع الطلب؟")
    return None


def _clear_pending_upsell(ud: "UserData", *, accepted: bool | None = None) -> None:
    ud.pending_upsell_item = None
    ud.pending_upsell_price = None
    if accepted is not None:
        ud.upsell_accepted = accepted


def _accept_pending_upsell(ud: "UserData", cfg: "RestaurantConfig") -> str | None:
    item_name = (ud.pending_upsell_item or "").strip()
    if not item_name:
        return None

    current_items = list(ud.order or [])
    normalized_items, unknown, total = _normalize_order_items(current_items + [item_name], cfg.menu_items)
    if unknown:
        ud.order = current_items + [item_name]
        if ud.pending_upsell_price is not None:
            ud.order_total += float(ud.pending_upsell_price)
    else:
        ud.order = normalized_items
        ud.order_total = total
        ud.order_validated = True

    _clear_pending_upsell(ud, accepted=True)
    return item_name


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
    if not ud.order:
        return "الطلب"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def _delivery_next_missing_slot(ud: UserData) -> str | None:
    if not ud.order:
        return "الطلب"
    if not ud.delivery_address:
        return "العنوان والمنطقة"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def _reservation_next_missing_slot(ud: UserData, cfg: RestaurantConfig) -> str | None:
    if not ud.reservation_time:
        return "وقت الحجز"
    if ud.guests_count is None:
        return "عدد الضيوف"
    if len(cfg.branches) > 1 and not ud.selected_branch:
        return "الفرع"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def _complaint_next_missing_slot(ud: UserData) -> str | None:
    if not ud.complaint_text:
        return "الشكوى"
    if not ud.complaint_type:
        return "نوع الشكوى"
    if not ud.customer_name:
        return "الاسم"
    if not ud.customer_phone:
        return "رقم الموبايل"
    return None


def _is_takeaway_ready_for_confirmation(ud: UserData) -> bool:
    return _takeaway_next_missing_slot(ud) is None


def _is_delivery_ready_for_confirmation(ud: UserData) -> bool:
    return _delivery_next_missing_slot(ud) is None


def _is_reservation_ready_for_confirmation(ud: UserData, cfg: RestaurantConfig) -> bool:
    return _reservation_next_missing_slot(ud, cfg) is None


def _is_complaint_ready_for_submission(ud: UserData) -> bool:
    return _complaint_next_missing_slot(ud) is None


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


def _should_add_turn_guard(user_text: str) -> bool:
    normalized = _normalize_ar(user_text)
    if not normalized:
        return False
    if _is_greeting_only(user_text) or _looks_empty_answer(user_text):
        return True

    tokens = normalized.split()
    short_ack_tokens = {_normalize_ar(word) for word in ("صح", "تمام", "ماشي", "ايوه", "أيوه", "كده")}
    if len(tokens) <= 2:
        return True
    if len(tokens) <= 4 and any(token in short_ack_tokens for token in tokens):
        return True
    return False


def _flow_turn_guard_message(flow: str, ud: UserData, user_text: str) -> str:
    normalized = _normalize_ar(user_text)
    if flow == "takeaway":
        missing = _takeaway_next_missing_slot(ud)
        if missing == "الطلب":
            return (
                "هذه مرحلة تيكاواي. المطلوب الآن الطلب فقط. "
                "لا تطلب الاسم أو الرقم أو التأكيد قبل تسجيل الطلب. "
                "لو كلام العميل مجرد تحية أو موافقة قصيرة فاسأله: تحب تطلب إيه؟"
            )
        if missing == "الاسم":
            return (
                "هذه مرحلة تيكاواي. المطلوب الآن الاسم فقط. "
                "لا تطلب الرقم قبل الاسم. "
                "لو آخر كلام من العميل كان نفيًا مثل لا أو مفيش على الطلب الخاص، انتقل للاسم فورًا."
            )
        if missing == "رقم الموبايل":
            return (
                "هذه مرحلة تيكاواي. المطلوب الآن رقم الموبايل فقط. "
                "لا تؤكد الطلب ولا تقل تم استلامه قبل تسجيل الرقم."
            )
        return (
            "هذه مرحلة تيكاواي. المطلوب الآن تأكيد الطلب فقط. "
            "لا تقل تم استلام الطلب أو تم تسجيله إلا بعد نجاح confirm_order."
        )

    if flow == "delivery":
        missing = _delivery_next_missing_slot(ud)
        if missing == "الطلب":
            return (
                "هذه مرحلة توصيل. المطلوب الآن الطلب فقط. "
                "لا تطلب العنوان أو الاسم أو الرقم قبل تسجيل الطلب."
            )
        if missing == "العنوان والمنطقة":
            return (
                "هذه مرحلة توصيل. المطلوب الآن العنوان والمنطقة فقط. "
                "لا تطلب الاسم أو الرقم قبل العنوان."
            )
        if missing == "الاسم":
            return (
                "هذه مرحلة توصيل. المطلوب الآن الاسم فقط. "
                "لو العميل قال لا أو مفيش على الطلبات الخاصة أو العلامة المميزة، انتقل للاسم فورًا."
            )
        if missing == "رقم الموبايل":
            return (
                "هذه مرحلة توصيل. المطلوب الآن رقم الموبايل فقط. "
                "لا تؤكد الطلب قبل تسجيل الرقم."
            )
        return (
            "هذه مرحلة توصيل. المطلوب الآن تأكيد الطلب فقط. "
            "لا تقل تم استلام الطلب أو اتسجل للتوصيل إلا بعد نجاح confirm_delivery."
        )

    if flow == "reservation":
        missing = _reservation_next_missing_slot(ud, ud.restaurant)
        if missing == "وقت الحجز":
            return (
                "هذه مرحلة حجز. المطلوب الآن وقت الحجز فقط بصيغة يوم وساعة. "
                "لا تطلب عدد الضيوف قبل تثبيت الوقت."
            )
        if missing == "عدد الضيوف":
            return (
                "هذه مرحلة حجز. المطلوب الآن عدد الضيوف فقط. "
                "لا تطلب الاسم أو الرقم قبل العدد."
            )
        if missing == "الفرع":
            return (
                "هذه مرحلة حجز. المطلوب الآن اسم الفرع فقط. "
                "لا تنتقل للاسم قبل تحديد الفرع."
            )
        if missing == "الاسم":
            return "هذه مرحلة حجز. المطلوب الآن الاسم فقط."
        if missing == "رقم الموبايل":
            return (
                "هذه مرحلة حجز. المطلوب الآن رقم الموبايل فقط. "
                "لا تؤكد الحجز قبل تسجيل الرقم."
            )
        return (
            "هذه مرحلة حجز. المطلوب الآن تأكيد الحجز فقط. "
            "لا تقل الحجز اتأكد إلا بعد نجاح confirm_reservation."
        )

    if flow == "complaint":
        missing = _complaint_next_missing_slot(ud)
        if missing == "الشكوى":
            return "هذه مرحلة شكوى. المطلوب الآن تفاصيل الشكوى فقط."
        if missing == "نوع الشكوى":
            return "هذه مرحلة شكوى. المطلوب الآن نوع الشكوى فقط: طلب أو جودة أو خدمة أو توصيل."
        if missing == "الاسم":
            return "هذه مرحلة شكوى. المطلوب الآن الاسم فقط."
        if missing == "رقم الموبايل":
            return (
                "هذه مرحلة شكوى. المطلوب الآن رقم الموبايل فقط. "
                "لا تقل الشكوى اتثبتت إلا بعد نجاح التسجيل."
            )
        return (
            "هذه مرحلة شكوى. لو التسجيل لم ينجح فقل محفوظة مبدئيًا فقط. "
            "ولا تقل اتثبتت إلا بعد نجاح submit complaint."
        )

    if _is_greeting_only(user_text) or normalized in {"تمام", "ماشي", "ايوه", "أيوه"}:
        return "لو كلام العميل تحية أو موافقة قصيرة فقط، اسأله عن الخطوة المطلوبة الحالية ولا ترتجل."
    return ""


def _next_step_hint(context: RunContext_T) -> str:
    return _next_step_hint_for_flow(_current_flow_name(context), context.userdata)


async def _maybe_submit_pending_complaint(context: RunContext_T) -> str:
    return await _maybe_submit_pending_complaint_for_flow(
        context.userdata,
        _current_flow_name(context),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Shared tools
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def update_name(
    name: Annotated[str, Field(description="اسم العميل واكتبه بالعربي الصوتي حتى لو اتقال بالإنجليزي")],
    context: RunContext_T,
) -> str:
    return await _apply_name_update(
        context.userdata,
        name,
        flow_name=_current_flow_name(context),
    )


@function_tool()
async def update_phone(
    phone: Annotated[str, Field(description="رقم موبايل مصري بالأرقام فقط مثل 01012345678")],
    context: RunContext_T,
) -> str:
    return await _apply_phone_update(
        context.userdata,
        phone,
        flow_name=_current_flow_name(context),
    )


@function_tool()
async def get_menu(context: RunContext_T) -> str:
    return _menu_response_for_flow(_current_flow_name(context), context.userdata.restaurant)


@function_tool()
async def to_greeter(context: RunContext_T) -> tuple[Agent, str]:
    curr: BaseAgent = context.session.current_agent
    return await curr._transfer("greeter", context)


class BaseAgent(Agent):
    # subclass يحدد الجملة الأولى — بتتقال مباشرة عبر TTS بدون LLM (أسرع)
    # لو فضلت فاضية يُستخدم generate_reply عادي
    _opening: str = ""

    def _sync_phone_capture_mode(self) -> None:
        ud: UserData = self.session.userdata
        desired_preemptive = SESSION_PREEMPTIVE_GENERATION and not ud.phone_capture_mode
        if self.session.options.preemptive_generation == desired_preemptive:
            return
        self.session.options.preemptive_generation = desired_preemptive
        logger.info(
            "call=%s | phone_capture_mode=%s | preemptive=%s",
            ud.call_id,
            ud.phone_capture_mode,
            desired_preemptive,
        )

    async def on_enter(self) -> None:
        ud: UserData = self.session.userdata
        logger.info("call=%s | agent=%s", ud.call_id, self.__class__.__name__)
        desired_phone_mode = _flow_missing_phone(self.__class__.__name__.lower(), ud)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)

        chat_ctx = self.chat_ctx.copy()
        if isinstance(ud.prev_agent, Agent):
            prev_ctx = ud.prev_agent.chat_ctx.copy(
                exclude_instructions=True,
                exclude_function_call=False,
                exclude_handoff=True,
                exclude_config_update=True,
            ).truncate(max_items=PROMPT_HISTORY_ITEMS)
            seen = {item.id for item in chat_ctx.items}
            chat_ctx.items.extend(i for i in prev_ctx.items if i.id not in seen)

        chat_ctx.add_message(
            role="system",
            content=(
                "أسلوب الكلام:\n"
                "- اتكلم زي موظف مطعم مصري طبيعي، ودود وعفوي.\n"
                "- خليك مختصر بس مش جاف — كلمة لطيفة هنا وهنا عادي.\n"
                "- كمّل على أول معلومة ناقصة، ومتكررش حاجة العميل قالها قبل كده.\n"
                "- متضيفش أصناف أو كميات من عندك.\n"
                "- الأرقام بالكلام والأسماء بالعربي الصوتي.\n"
                f"بيانات العميل: {ud.summarize()}"
            ),
        )
        chat_ctx.add_message(
            role="system",
            content=(
                f"أنت دلوقتي في {self.__class__.__name__}. "
                "رد طبيعي بالمصري وكمّل على اللي ناقص."
            ),
        )
        await self.update_chat_ctx(chat_ctx)
        self._sync_phone_capture_mode()

        if self._opening:
            # bypass LLM — قول الجملة مباشرة عبر TTS — بيوفر ~500ms لكل انتقال
            await self.session.say(self._opening, add_to_chat_ctx=True)
        else:
            self.session.generate_reply(tool_choice="none")

    async def _say_and_stop(self, text: str) -> None:
        self._sync_phone_capture_mode()
        await self.session.say(
            _voice_safe_text(text, max_chars=180),
            allow_interruptions=True,
            add_to_chat_ctx=True,
        )
        raise StopResponse()

    def _tool_context(self) -> SimpleNamespace:
        return SimpleNamespace(userdata=self.session.userdata, session=self.session)

    def _transfer_live(self, name: str) -> bool:
        ud: UserData = self.session.userdata
        current = self.session.current_agent
        current_name = current.__class__.__name__.lower()
        if current_name == name:
            logger.warning("call=%s | live transfer skipped | reason=self | agent=%s", ud.call_id, name)
            return False
        target = ud.agents.get(name)
        if target is None:
            logger.error("call=%s | live transfer target missing: %s", ud.call_id, name)
            return False
        logger.info("call=%s | live transfer | %s -> %s", ud.call_id, self.__class__.__name__, name)
        ud.prev_agent = current
        self.session.update_agent(target)
        return True

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        return False

    def _turn_guard_message(self, user_text: str) -> str:
        flow = self.__class__.__name__.lower()
        return _flow_turn_guard_message(flow, self.session.userdata, user_text)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        user_text = _chat_message_text(new_message)
        flow = self.__class__.__name__.lower()
        ud = self.session.userdata
        if not _normalize_ar(user_text):
            logger.info("call=%s | empty transcript ignored | flow=%s | text=%r", ud.call_id, flow, user_text)
            raise StopResponse()
        desired_phone_mode = _flow_missing_phone(flow, ud) or bool(ud.pending_phone_digits)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)
        self._sync_phone_capture_mode()

        if _flow_missing_name(flow, ud) and not _is_likely_non_name_response(user_text):
            candidate = _extract_name_candidate(user_text)
            if candidate:
                logger.info("call=%s | name turn intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
                await self._say_and_stop(await _apply_name_update(ud, candidate, flow_name=flow))

        if _flow_missing_phone(flow, ud) and _is_phone_like_text(user_text):
            logger.info("call=%s | phone turn intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
            phone_reply = await _apply_phone_update(ud, user_text, flow_name=flow)
            if phone_reply:
                await self._say_and_stop(phone_reply)
            raise StopResponse()

        if flow in {"takeaway", "delivery"} and _is_total_question(user_text):
            logger.info("call=%s | total turn intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
            await self._say_and_stop(_order_total_user_message(flow, ud, ud.restaurant))

        if flow in {"takeaway", "delivery"} and _is_menu_question(user_text):
            logger.info("call=%s | menu turn intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
            await self._say_and_stop(_menu_response_for_flow(flow, ud.restaurant))

        if ud.order_confirmed or ud.reservation_confirmed or ud.complaint_logged:
            if _is_thanks_message(user_text):
                logger.info("call=%s | post_completion_thanks_intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
                await self._say_and_stop(_random.choice(["العفو يا فندم!", "ولا يهمك!", "بالهنا والشفا!"]))
            if _is_positive_confirmation(user_text):
                logger.info("call=%s | post_completion_ack_intercepted | flow=%s | text=%r", ud.call_id, flow, user_text)
                await self._say_and_stop(_random.choice(["تحت أمرك!", "في أي حاجة تانية يا فندم؟"]))

        if await self._maybe_handle_turn_deterministically(user_text):
            raise StopResponse()

        if len(turn_ctx.items) > TURN_CHAT_CTX_MAX_ITEMS:
            turn_ctx.items[:] = turn_ctx.truncate(max_items=TURN_CHAT_CTX_MAX_ITEMS).items

        guard = self._turn_guard_message(user_text) if _should_add_turn_guard(user_text) else ""
        if guard:
            turn_ctx.add_message(
                role="system",
                content=(
                    f"آخر كلام واضح من العميل: {user_text or '—'}\n"
                    f"{guard}\n"
                    "رد بشكل طبيعي وكمّل على اللي ناقص."
                ),
            )

    async def _transfer(self, name: str, context: RunContext_T) -> tuple[Agent, str]:
        ud = context.userdata
        current = context.session.current_agent
        current_name = current.__class__.__name__.lower()
        if current_name == name:
            logger.warning("call=%s | skipped self-transfer for agent=%s", ud.call_id, name)
            return current, ""
        if name not in ud.agents:
            logger.error("call=%s | transfer target missing: %s", ud.call_id, name)
            return current, "الخدمة دي مش متاحة دلوقتي."
        logger.info("call=%s | %s → %s", ud.call_id, self.__class__.__name__, name)
        ud.prev_agent = current
        return ud.agents[name], ""

# ─────────────────────────────────────────────────────────────────────────────
# Greeter
# ─────────────────────────────────────────────────────────────────────────────

class Greeter(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg

        if cfg.degraded_mode:
            instructions = (
                f"أنت موظف استقبال في '{cfg.name}' والنظام في وضع degraded مؤقت.\n"
                "قول بالظبط: 'أهلاً بيك يا فندم، في تحديث مؤقت في النظام دلوقتي، تقدر تقولّي طلبك أو تتصل بالمطعم مباشرة.'\n"
                "لا تدّعي منيو أو مواعيد أو توصيل غير مؤكدة.\n"
                "لو العميل عايز يكمل اطلب منه يحدد طلبه أو شكواه بشكل مختصر."
            )
        elif not cfg.is_open:
            reason = cfg.closed_reason or "خارج المواعيد"
            instructions = (
                f"أنت موظف استقبال في '{cfg.name}'، المطعم مقفول حالياً.\n"
                f"قول بالظبط: 'أهلاً بيك يا فندم، معاك {cfg.name}، للأسف إحنا مقفولين دلوقتي، {reason}.'\n"
                f"ثم قول المواعيد: {cfg.hours_text()}\n"
                f"لو سألك على حاجة تانية قوله يتصل على {cfg.phone}."
            )
        else:
            delivery_line = "• طلب توصيل → to_delivery\n" if cfg.delivery_enabled else ""
            instructions = (
                f"أنت موظف استقبال في مطعم '{cfg.name}'.\n\n"
                "أول ما تفهم نية العميل حوّله للمكان الصح على طول.\n"
                "لو العميل قال توصيل أو تيكاواي صراحة، حوّله فوراً من غير أسئلة زيادة.\n"
                "لو سأل على المنيو أو سعر → get_menu.\n\n"
                "التحويلات:\n"
                "• طلب أكل / تيكاواي → to_takeaway\n"
                f"{delivery_line}"
                "• حجز ترابيزة → to_reservation\n"
                "• شكوى أو مشكلة → to_complaint\n"
                "• مش واضح → اسأله بشكل عفوي\n"
                "• استخدم resolve_request لو الكلام مش واضح أو الـ STT ملخبطة\n\n"
                f"أصناف متاحة: {cfg.menu_names()}\n\n"
                "اتكلم زي موظف مصري طبيعي — ودود وعفوي. متاخدش طلبات ولا تحسب إجمالي."
            )

        super().__init__(
            instructions=instructions,
            tools=[get_menu],
        )
        self._delivery_enabled = cfg.delivery_enabled
        self._opening = (
            _degraded_user_message(cfg)
            if cfg.degraded_mode else
            f"أهلاً بيك يا فندم، معاك {cfg.name}. تحب تطلب، تحجز، ولا في حاجة تانية؟"
            if cfg.is_open else
            f"أهلاً بيك يا فندم، معاك {cfg.name}، للأسف إحنا مقفولين دلوقتي."
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        decision = _greeter_turn_decision(
            user_text,
            self.cfg,
            has_delivery_agent="delivery" in self.session.userdata.agents,
        )
        logger.info(
            "call=%s | greeter turn decision | reason=%s | action=%s | target=%s | text=%r",
            self.session.userdata.call_id,
            decision.reason,
            decision.action,
            decision.target_agent or "-",
            user_text,
        )
        if decision.action == "route" and decision.target_agent:
            if self._transfer_live(decision.target_agent):
                return True
            await self._say_and_stop("الخدمة دي مش متاحة دلوقتي يا فندم.")
        if decision.message:
            await self._say_and_stop(decision.message)
        return False

    @function_tool()
    async def to_reservation(self, context: RunContext_T) -> tuple[Agent, str]:
        """يُستدعى لما العميل يريد حجز ترابيزة."""
        return await self._transfer("reservation", context)

    @function_tool()
    async def to_takeaway(self, context: RunContext_T) -> tuple[Agent, str]:
        """يُستدعى لما العميل يريد استلام طلبه من المطعم (تيكاواي)."""
        return await self._transfer("takeaway", context)

    @function_tool()
    async def to_delivery(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لما العميل يريد توصيل الطلب لعنوانه."""
        if not self._delivery_enabled and not self.cfg.degraded_mode:
            return _delivery_unavailable_user_message(self.cfg)
        return await self._transfer("delivery", context)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> tuple[Agent, str]:
        """يُستدعى لما العميل عنده شكوى أو مشكلة."""
        return await self._transfer("complaint", context)

    @function_tool()
    async def resolve_request(
        self,
        user_text: Annotated[str, Field(description="آخر كلام واضح قاله العميل")],
        context: RunContext_T,
    ) -> str | tuple[Agent, str]:
        intent = _guess_request_intent(user_text, context.userdata.restaurant)
        if intent in {"delivery", "delivery_degraded"}:
            return await self._transfer("delivery", context)
        if intent == "delivery_unavailable":
            return _delivery_unavailable_user_message(context.userdata.restaurant)
        if intent == "takeaway":
            return await self._transfer("takeaway", context)
        if intent == "reservation":
            return await self._transfer("reservation", context)
        if intent == "complaint":
            return await self._transfer("complaint", context)
        if intent == "menu":
            return await get_menu(context)
        if intent == "order_ambiguous":
            return "تيكاواي ولا توصيل يا فندم؟"
        return "معلش يا فندم، طلب أكل ولا حجز ولا شكوى؟"

# ─────────────────────────────────────────────────────────────────────────────
# Takeaway
# ─────────────────────────────────────────────────────────────────────────────

class Takeaway(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg

        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"
        super().__init__(
            instructions=(
                f"أنت موظف طلبات تيكاواي في '{cfg.name}'.\n"
                f"وقت الاستلام: {num2ar(cfg.wait_minutes)} دقيقة.\n"
                f"أصناف: {cfg.menu_names()}\n\n"
                "اتكلم زي موظف مطعم مصري حقيقي — عفوي وودود، مش بتقرأ من سكريبت.\n"
                "نوّع في كلامك، متقولش نفس الجمل كل مرة.\n"
                "الترتيب العام:\n"
                "١. خُد الطلب واستدعِ update_order.\n"
                "٢. اسأل لو فيه طلب خاص، ولو قال لأ كمّل.\n"
                "٣. خُد الاسم ورقم الموبايل.\n"
                "٤. لخّص الطلب مرة واحدة، ولو وافق استدعِ confirm_order.\n"
                "لو العميل قال معلومة من خطوة جاية سجّلها وكمّل عادي.\n\n"
                "- لو سأل عن سعر → get_menu\n"
                "- لو عنده شكوى → to_complaint\n"
                "- متضيفش أصناف من عندك.\n"
                "- اقتراح الإضافة مرة واحدة بس وبشكل عفوي.\n"
                "- 'أيوه' أو 'تمام' مش موافقة على الإضافة إلا لو ذكر الصنف.\n"
                "- لا تكرر التأكيد أكتر من مرة واحدة"
            ),
            tools=[
                update_name,
                update_phone,
                to_greeter,
                get_menu,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        ud = self.session.userdata
        context = self._tool_context()

        if ud.pending_upsell_item:
            pending_item = ud.pending_upsell_item
            if _is_explicit_upsell_acceptance(user_text, pending_item):
                logger.info("call=%s | takeaway upsell accepted | item=%s", ud.call_id, pending_item)
                accepted_item = _accept_pending_upsell(ud, self.cfg) or "الإضافة"
                await self._say_and_stop(
                    _voice_safe_text(f"{_ack()}، ضفت {accepted_item}. {_ask_special()}", max_chars=180)
                )
            if _is_positive_confirmation(user_text) or _is_explicit_upsell_rejection(user_text):
                logger.info("call=%s | takeaway upsell skipped | item=%s | text=%r", ud.call_id, pending_item, user_text)
                _clear_pending_upsell(ud, accepted=False)
                await self._say_and_stop(f"{_ack()}. {_ask_special()}")
            logger.info("call=%s | takeaway pending upsell cleared for next turn | item=%s", ud.call_id, pending_item)
            _clear_pending_upsell(ud, accepted=False)

        if ud.order and not ud.customer_name and _looks_empty_answer(user_text):
            logger.info("call=%s | takeaway optional_empty_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_special_requests(requests=user_text, context=context))

        if _is_takeaway_ready_for_confirmation(ud) and _is_positive_confirmation(user_text):
            logger.info("call=%s | takeaway confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_order(context=context))

        return False

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> tuple[Agent, str]:
        """يُستدعى لو العميل عنده شكوى."""
        return await self._transfer("complaint", context)

    @function_tool()
    async def update_order(
        self,
        items: Annotated[
            list[str],
            Field(description="القايمة الكاملة للطلب مع الكميات مثل ['كشري كبير × 2', 'بيبسي']"),
        ],
        context: RunContext_T,
    ) -> str:
        if not items:
            return _voice_safe_text("الطلب فاضي.")
        if not _available_menu_items(self.cfg):
            normalized_items = [item.strip() for item in items if item.strip()]
            if not normalized_items:
                return _menu_unavailable_user_message(self.cfg)
            context.userdata.order = normalized_items
            context.userdata.order_validated = False
            context.userdata.order_total = 0.0
            logger.warning("call=%s | takeaway order captured without menu validation", context.userdata.call_id)
            return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {_ask_special()}")
        normalized_items, unknown, total = _normalize_order_items(items, self.cfg.menu_items)
        if unknown:
            return _voice_safe_text(f"'{', '.join(unknown)}' مش في المنيو. المتاح: {self.cfg.menu_text()}", max_chars=170)
        context.userdata.order = normalized_items
        context.userdata.order_validated = True
        context.userdata.order_total = total
        upsell = _get_upsell_suggestion(context.userdata, self.cfg)
        if upsell:
            return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {upsell}")
        return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {_ask_special()}")

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير أو مفيش")],
        context: RunContext_T,
    ) -> str:
        _clear_pending_upsell(context.userdata)
        if _looks_empty_answer(requests):
            context.userdata.special_requests = None
            return _voice_safe_text(f"{_ack()}. {_ask_name()}")
        context.userdata.special_requests = requests.strip()
        return _voice_safe_text(f"{_ack()}، سجلت الطلب الخاص. {_ask_name()}")

    @function_tool()
    async def confirm_order(self, context: RunContext_T) -> str:
        ud = context.userdata
        if ud.order_confirmed:
            logger.info("call=%s | takeaway submit skipped | reason=already_confirmed", ud.call_id)
            return _voice_safe_text(f"الطلب متسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")
        if ud.order_submit_in_flight:
            logger.warning("call=%s | takeaway submit skipped | reason=in_flight", ud.call_id)
            return _voice_safe_text("ثانية واحدة يا فندم، بسجل الطلب دلوقتي.")
        missing = _takeaway_next_missing_slot(ud)
        if missing:
            return _voice_safe_text(f"لسه محتاج: {missing}.")
        if not ud.order_validated:
            logger.warning("call=%s | takeaway submit skipped | reason=order_not_validated", ud.call_id)
            return _order_validation_user_message(self.cfg)
        if not _can_attempt_backend_write(ud):
            logger.warning("call=%s | takeaway submit skipped | reason=write_unavailable", ud.call_id)
            return _backend_failure_user_message(ud)

        ud.order_submit_in_flight = True
        try:
            result = await submit_takeaway(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("order")

        ud.order_id = result.get("order_id", "")
        ud.order_confirmed = True
        wait = result.get("estimated_time", self.cfg.wait_minutes)
        return f"تمام يا {ud.customer_name}، الطلب اتسجل. هيبقى جاهز خلال {num2ar(wait)} دقيقة."

# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────

class Delivery(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg
        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"

        zones_info = f" | مناطق: {cfg.delivery_zones_text()}" if cfg.delivery_zones else ""

        super().__init__(
            instructions=(
                f"أنت موظف طلبات توصيل في '{cfg.name}'.\n"
                f"{cfg.delivery_info_text()}{zones_info}\n"
                f"أصناف: {cfg.menu_names()}\n\n"
                "اتكلم زي موظف مطعم مصري حقيقي — عفوي وودود.\n"
                "نوّع في كلامك، متقولش نفس الجمل كل مرة.\n"
                "الترتيب العام:\n"
                "١. خُد الطلب واستدعِ update_order.\n"
                "٢. اسأل لو فيه طلب خاص، ولو قال لأ كمّل.\n"
                "٣. خُد العنوان، وبعده العلامة المميزة لو محتاج توضيح.\n"
                "٤. خُد الاسم ورقم الموبايل.\n"
                "٥. لخّص الطلب مرة واحدة، ولو وافق استدعِ confirm_delivery.\n"
                "لو العميل قال معلومة من خطوة جاية سجّلها وكمّل عادي.\n\n"
                "- لو سأل عن سعر → get_menu\n"
                "- لو عنده شكوى → to_complaint\n"
                "- متضيفش أصناف من عندك.\n"
                "- اقتراح الإضافة مرة واحدة بس وبشكل عفوي.\n"
                "- 'أيوه' أو 'تمام' مش موافقة على الإضافة إلا لو ذكر الصنف."
            ),
            tools=[
                update_name,
                update_phone,
                to_greeter,
                get_menu,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        ud = self.session.userdata
        context = self._tool_context()

        if ud.pending_upsell_item:
            pending_item = ud.pending_upsell_item
            if _is_explicit_upsell_acceptance(user_text, pending_item):
                logger.info("call=%s | delivery upsell accepted | item=%s", ud.call_id, pending_item)
                accepted_item = _accept_pending_upsell(ud, self.cfg) or "الإضافة"
                await self._say_and_stop(
                    _voice_safe_text(f"{_ack()}، ضفت {accepted_item}. {_ask_special()}", max_chars=180)
                )
            if _is_positive_confirmation(user_text) or _is_explicit_upsell_rejection(user_text):
                logger.info("call=%s | delivery upsell skipped | item=%s | text=%r", ud.call_id, pending_item, user_text)
                _clear_pending_upsell(ud, accepted=False)
                await self._say_and_stop(f"{_ack()}. {_ask_special()}")
            logger.info("call=%s | delivery pending upsell cleared for next turn | item=%s", ud.call_id, pending_item)
            _clear_pending_upsell(ud, accepted=False)

        if ud.order and not ud.delivery_address and _looks_empty_answer(user_text):
            logger.info("call=%s | delivery optional_empty_intercepted | step=special_requests | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_special_requests(requests=user_text, context=context))

        if ud.delivery_address and not ud.landmark_asked and _looks_empty_answer(user_text):
            logger.info("call=%s | delivery optional_empty_intercepted | step=landmark | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_delivery_landmark(landmark=user_text, context=context))

        if _is_delivery_ready_for_confirmation(ud) and _is_positive_confirmation(user_text):
            logger.info("call=%s | delivery confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_delivery(context=context))

        return False

    @function_tool()
    async def update_order(
        self,
        items: Annotated[
            list[str],
            Field(description="القائمة الكاملة للطلب مع الكميات مثلاً ['كوشري كبير × 2', 'عصير ليمون']"),
        ],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يطلب أو يعدّل. ابعت القائمة كاملة دايماً."""
        if not items:
            return _voice_safe_text("الطلب فاضي.")
        if not _available_menu_items(self.cfg):
            normalized_items = [item.strip() for item in items if item.strip()]
            if not normalized_items:
                return _menu_unavailable_user_message(self.cfg)
            context.userdata.order = normalized_items
            context.userdata.order_validated = False
            context.userdata.order_total = 0.0
            logger.warning("call=%s | delivery order captured without menu validation", context.userdata.call_id)
            return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {_ask_special()}")
        normalized_items, unknown, total = _normalize_order_items(items, self.cfg.menu_items)
        if unknown:
            return _voice_safe_text(f"'{', '.join(unknown)}' مش في المنيو. المتاح: {self.cfg.menu_text()}", max_chars=170)

        if self.cfg.min_order > 0:
            if total < self.cfg.min_order:
                return _voice_safe_text(
                    f"أقل طلب للتوصيل {money2ar(self.cfg.min_order)} جنيه. "
                    f"طلبك دلوقتي {money2ar(total)} جنيه. "
                    "تحب تضيف حاجة؟"
                )

        context.userdata.order = normalized_items
        context.userdata.order_validated = True
        context.userdata.order_total = total
        upsell = _get_upsell_suggestion(context.userdata, self.cfg)
        if upsell:
            return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {upsell}")
        return _voice_safe_text(f"{_ack()}، {_ack_got(', '.join(normalized_items))}. {_ask_special()}")

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر طلبات خاصة."""
        _clear_pending_upsell(context.userdata)
        if _looks_empty_answer(requests):
            context.userdata.special_requests = None
            return _voice_safe_text(f"{_ack()}. {_ask_address()}")
        context.userdata.special_requests = requests.strip()
        return _voice_safe_text(f"{_ack()}، سجلت الطلب الخاص. {_ask_address()}")

    @function_tool()
    async def update_delivery_address(
        self,
        address: Annotated[str, Field(description="العنوان كامل: الشارع والرقم والمنطقة")],
        zone: Annotated[str, Field(description="المنطقة أو الحي")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يقول عنوانه. أكد العنوان قبل الاستدعاء."""
        # تحقق من منطقة التوصيل — مطابقة عربية مرنة
        if self.cfg.delivery_zones:
            zone_norm = _normalize_ar(zone)
            covered = any(
                zone_norm in _normalize_ar(z) or _normalize_ar(z) in zone_norm
                for z in self.cfg.delivery_zones
            )
            if not covered:
                return _voice_safe_text(
                    f"للأسف مش بنوصل {zone} دلوقتي. "
                    f"المتاح {self.cfg.delivery_zones_text()}. "
                    "تحب تيكاواي بدل كده؟"
                )

        context.userdata.delivery_address = address.strip()
        context.userdata.delivery_zone    = zone.strip()
        context.userdata.delivery_landmark = None
        logger.info("call=%s | delivery_address=%s zone=%s",
                    context.userdata.call_id, address, zone)
        if _address_seems_specific(address):
            context.userdata.landmark_asked = True
            return _voice_safe_text(f"{_ack()}، {_ack_got('العنوان')}. {_ask_name()}", max_chars=180)
        context.userdata.landmark_asked = False
        return _voice_safe_text(f"{_ack()}، {_ack_got('العنوان')}. في علامة مميزة قريبة منك؟", max_chars=180)

    @function_tool()
    async def update_delivery_landmark(
        self,
        landmark: Annotated[str, Field(description="علامة مميزة قريبة من العنوان")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر علامة مميزة."""
        context.userdata.landmark_asked = True
        if _looks_empty_answer(landmark):
            context.userdata.delivery_landmark = None
            return _voice_safe_text(f"{_ack()}. {_ask_name()}")
        explicit_name_reply = re.match(r"^\s*(?:انا|أنا|اسمي|اسمى|الاسم|اسم|معاك|معاكي)\b", landmark, flags=re.IGNORECASE)
        if explicit_name_reply and not context.userdata.customer_name:
            name_candidate = _extract_name_candidate(landmark)
            if name_candidate:
                context.userdata.delivery_landmark = None
                context.userdata.customer_name = name_candidate
                return _voice_safe_text(f"{_ack()} يا {name_candidate}. {_ask_phone()}", max_chars=180)
        context.userdata.delivery_landmark = landmark.strip()
        return _voice_safe_text(f"{_ack()}، سجلت العلامة. {_ask_name()}", max_chars=180)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> tuple[Agent, str]:
        """يُستدعى لو العميل عنده شكوى."""
        return await self._transfer("complaint", context)

    @function_tool()
    async def confirm_delivery(self, context: RunContext_T) -> str:
        """يُستدعى بعد تأكيد الطلب والعنوان والاسم والرقم كاملاً."""
        ud = context.userdata
        if ud.order_confirmed:
            logger.info("call=%s | delivery submit skipped | reason=already_confirmed", ud.call_id)
            return _voice_safe_text(f"الطلب مسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")
        if ud.order_submit_in_flight:
            logger.warning("call=%s | delivery submit skipped | reason=in_flight", ud.call_id)
            return _voice_safe_text("ثانية واحدة يا فندم، بسجل الطلب دلوقتي.")
        missing = _delivery_next_missing_slot(ud)
        if missing:
            return _voice_safe_text(f"لسه محتاج: {missing}.")
        if not ud.order_validated:
            logger.warning("call=%s | delivery submit skipped | reason=order_not_validated", ud.call_id)
            return _order_validation_user_message(self.cfg)
        if not _can_attempt_backend_write(ud):
            logger.warning("call=%s | delivery submit skipped | reason=write_unavailable", ud.call_id)
            return _backend_failure_user_message(ud)

        ud.order_submit_in_flight = True
        try:
            result = await submit_delivery(ud)
        finally:
            ud.order_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("order")

        ud.order_id        = result.get("order_id", "")
        ud.order_confirmed = True
        wait               = result.get("estimated_time", self.cfg.delivery_minutes)

        msg = f"تمام يا {ud.customer_name}، الطلب اتسجل للتوصيل."
        if self.cfg.delivery_fee > 0:
            msg += f" رسوم التوصيل {money2ar(self.cfg.delivery_fee)} جنيه."
        msg += f" هيوصلك خلال {num2ar(wait)} دقيقة."
        return msg

# ─────────────────────────────────────────────────────────────────────────────
# Reservation
# ─────────────────────────────────────────────────────────────────────────────

class Reservation(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg
        self._opening = "عايز تحجز إمتى يا فندم؟"

        branch_note = f" | فروع: {cfg.branch_names()}" if len(cfg.branches) > 1 else ""

        super().__init__(
            instructions=(
                f"أنت موظف حجوزات في '{cfg.name}'.\n"
                f"مواعيد: {cfg.hours_text()}\n"
                f"الحجز: من {num2ar(cfg.min_guests)} لـ{num2ar(cfg.max_guests)} ضيف{branch_note}\n\n"
                "اتكلم زي موظف مصري طبيعي — ودود وعفوي.\n"
                "الترتيب العام:\n"
                "١. اسأل عن الوقت → update_reservation_time\n"
                "   لو خارج المواعيد → قول المواعيد واقترح بديل\n"
                "٢. اسأل عن عدد الضيوف → update_guests_count\n"
                "٣. اسأل لو في مناسبة → update_reservation_notes\n"
                + (f"٤. اسأل عن الفرع: {cfg.branch_names()} → update_branch\n" if len(cfg.branches) > 1 else "")
                + ("٥" if len(cfg.branches) > 1 else "٤") + ". خُد الاسم → update_name\n"
                + ("٦" if len(cfg.branches) > 1 else "٥") + ". خُد رقم الموبايل → update_phone\n"
                + ("٧" if len(cfg.branches) > 1 else "٦") + ". لخّص الحجز، ولو أكّد → confirm_reservation\n\n"
                "لو حاجة خارج نطاقك → to_greeter"
            ),
            tools=[
                update_name,
                update_phone,
                to_greeter,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        ud = self.session.userdata
        context = self._tool_context()

        if ud.reservation_time and ud.guests_count is not None and not ud.customer_name and _looks_empty_answer(user_text):
            logger.info("call=%s | reservation optional_empty_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_reservation_notes(notes=user_text, context=context))

        if _is_reservation_ready_for_confirmation(ud, self.cfg) and _is_positive_confirmation(user_text):
            logger.info("call=%s | reservation confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_reservation(context=context))

        return False

    @function_tool()
    async def update_reservation_time(
        self,
        time: Annotated[str, Field(description="وقت وتاريخ الحجز")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يحدد وقت الحجز."""
        time = time.strip()
        parsed = _parse_reservation_time(time, self.cfg)
        if parsed is None:
            if self.cfg.hours:
                return _voice_safe_text(
                    f"الوقت مش واضح أو خارج المواعيد. مواعيدنا {self.cfg.hours_text()}. قول اليوم والساعة مع بعض.",
                    max_chars=170,
                )
            return _voice_safe_text("الوقت مش واضح. قول اليوم والساعة مع بعض، زي بكرة الساعة 8 بالليل.")
        context.userdata.reservation_time = parsed.raw_text
        context.userdata.reservation_time_iso = parsed.normalized_text
        return _voice_safe_text(f"{_ack()}، {parsed.raw_text}. كام شخص هتكونوا؟", max_chars=180)

    @function_tool()
    async def update_guests_count(
        self,
        count: Annotated[int, Field(description="عدد الضيوف", ge=1)],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يقول عدد الضيوف."""
        if count < self.cfg.min_guests:
            return _voice_safe_text(f"أقل عدد للحجز {num2ar(self.cfg.min_guests)} أشخاص.")
        if count > self.cfg.max_guests:
            return _voice_safe_text(f"أكتر عدد في حجز واحد {num2ar(self.cfg.max_guests)}، اتصل على {spoken_phone(self.cfg.phone)} مباشرة لو أكتر.")
        context.userdata.guests_count = count
        return _voice_safe_text(f"{_ack()}، {num2ar(count)} أشخاص. في مناسبة معينة ولا عادي؟")

    @function_tool()
    async def update_branch(
        self,
        branch: Annotated[str, Field(description="اسم الفرع")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يختار فرع."""
        resolved = _resolve_branch_name(branch, self.cfg.branches)
        if not resolved:
            return _voice_safe_text(f"الفرع ده مش واضح. الفروع المتاحة: {self.cfg.branch_names()}.", max_chars=170)
        context.userdata.selected_branch = resolved
        return _voice_safe_text(f"{_ack()}، فرع {resolved}. {_ask_name()}")

    @function_tool()
    async def update_reservation_notes(
        self,
        notes: Annotated[str, Field(description="ملاحظات أو طلبات خاصة")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر طلبات خاصة للحجز."""
        if _looks_empty_answer(notes):
            context.userdata.reservation_notes = None
            if len(self.cfg.branches) > 1 and not context.userdata.selected_branch:
                return _voice_safe_text(f"{_ack()}. أي فرع تفضل؟ {self.cfg.branch_names()}")
            return _voice_safe_text(f"{_ack()}. {_ask_name()}")
        context.userdata.reservation_notes = notes.strip()
        if len(self.cfg.branches) > 1 and not context.userdata.selected_branch:
            return _voice_safe_text(f"{_ack()}، سجلت الملاحظة. أي فرع تفضل؟ {self.cfg.branch_names()}", max_chars=180)
        return _voice_safe_text(f"{_ack()}، سجلت الملاحظة. {_ask_name()}")

    @function_tool()
    async def confirm_reservation(self, context: RunContext_T) -> str:
        """يُستدعى بعد تأكيد كل بيانات الحجز."""
        ud = context.userdata
        if ud.reservation_confirmed:
            logger.info("call=%s | reservation submit skipped | reason=already_confirmed", ud.call_id)
            return _voice_safe_text(f"الحجز مسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")
        if ud.reservation_submit_in_flight:
            logger.warning("call=%s | reservation submit skipped | reason=in_flight", ud.call_id)
            return _voice_safe_text("ثانية واحدة يا فندم، بسجل الحجز دلوقتي.")
        missing = _reservation_next_missing_slot(ud, self.cfg)
        if missing:
            return _voice_safe_text(f"لسه محتاج: {missing}.")
        if not _can_attempt_backend_write(ud):
            logger.warning("call=%s | reservation submit skipped | reason=write_unavailable", ud.call_id)
            return _backend_failure_user_message(ud)

        ud.reservation_submit_in_flight = True
        try:
            result = await submit_reservation(ud)
        finally:
            ud.reservation_submit_in_flight = False
        if not result:
            return _backend_failure_user_message(ud)
        if result.get("queued"):
            return _backend_queued_user_message("reservation")

        ud.reservation_id        = result.get("reservation_id", "")
        ud.reservation_confirmed = True
        msg = f"تمام يا {ud.customer_name}، الحجز اتأكد."
        msg += " هنبعتلك رسالة تأكيد."
        return msg

# ─────────────────────────────────────────────────────────────────────────────
# Complaint
# ─────────────────────────────────────────────────────────────────────────────

class Complaint(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self._opening = "قولي حصل إيه يا فندم؟"
        super().__init__(
            instructions=(
                f"أنت موظف خدمة عملاء في '{cfg.name}'.\n\n"
                "اتبع الخطوات دي بالترتيب:\n"
                "١. قول: 'قولي حصل إيه يا فندم؟' → استمع للشكوى\n"
                "٢. قول: 'آسفين جداً على اللي حصل، هنتابع الموضوع فوراً.' → log_complaint\n"
                "٣. لو الاسم مش متسجل: 'اسمك إيه يا فندم؟' → update_name\n"
                "٤. لو الموبايل مش متسجل: 'ورقم موبايلك علشان نتواصل معاك؟' → update_phone\n"
                "٥. قول: 'تحب حاجة تانية يا فندم؟'\n\n"
                "قواعد:\n"
                "- لا تجادل ولا تبرر\n"
                "- لو طلب أكل → to_greeter"
            ),
            tools=[update_name, update_phone, to_greeter],
        )

    @function_tool()
    async def log_complaint(
        self,
        complaint_text: Annotated[str, Field(description="ملخص الشكوى")],
        complaint_type: Annotated[str, Field(
            description="النوع: order_issue | quality | service | delivery | other"
        )],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لتسجيل الشكوى."""
        ud = context.userdata
        if ud.complaint_logged:
            logger.info("call=%s | complaint log skipped | reason=already_logged", ud.call_id)
            return _voice_safe_text("الشكوى متسجلة خلاص يا فندم.")
        cleaned_text = complaint_text.strip()
        if len(cleaned_text) < 3:
            return _voice_safe_text("قولّي الشكوى بشكل أوضح شوية يا فندم.")
        ud.complaint_text = cleaned_text
        normalized_type = _normalize_complaint_type(complaint_type)
        if not normalized_type:
            logger.info("call=%s | complaint_pending | missing=نوع الشكوى", ud.call_id)
            return _voice_safe_text("نوع الشكوى مش واضح. اختاره كطلب أو جودة أو خدمة أو توصيل.")
        ud.complaint_type = normalized_type
        note = await _maybe_submit_pending_complaint(context)
        if ud.complaint_logged:
            return _voice_safe_text(_join_user_phrases("تمام يا فندم، الشكوى اتسجلت", _complaint_followup_question(ud)), max_chars=180)
        if note:
            return _voice_safe_text(_join_user_phrases(_clean_followup_note(note), _complaint_followup_question(ud)), max_chars=180)
        return _voice_safe_text(_join_user_phrases("تمام يا فندم، سجلت الشكوى", _complaint_followup_question(ud)), max_chars=180)

# ─────────────────────────────────────────────────────────────────────────────
# Graceful shutdown — httpx cleanup via atexit (signal handling done by SDK)
# ─────────────────────────────────────────────────────────────────────────────
def _cleanup_http():
    global _http_client
    client = _http_client
    _http_client = None
    if not client or client.is_closed:
        return
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(client.aclose())
            return
        if not loop.is_closed():
            loop.run_until_complete(client.aclose())
            return
    except Exception:
        logger.debug("http client cleanup fallback triggered", exc_info=True)
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(client.aclose())
        loop.close()
    except Exception:
        logger.debug("http client cleanup failed", exc_info=True)

atexit.register(_cleanup_http)


async def _safe_aclose_session_once(
    session: AgentSession[UserData],
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
    session: AgentSession[UserData],
    close_state: dict[str, bool],
    *,
    farewell: str = "",
    timeout_seconds: float = 5.0,
) -> None:
    if close_state.get("closed"):
        return
    if farewell:
        with contextlib.suppress(Exception):
            await session.say(
                _voice_safe_text(farewell),
                allow_interruptions=False,
                add_to_chat_ctx=False,
            )
    await _safe_aclose_session_once(session, close_state, timeout_seconds=timeout_seconds)

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
server = AgentServer()

MAX_CALL_DURATION = _get_env_int("MAX_CALL_DURATION", 600, min_value=30)  # ثواني — default 10 دقايق


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    call_id = str(uuid.uuid4())[:16]
    logger.info("call=%s | started | room=%s", call_id, ctx.room.name)

    # ── استخراج restaurant_id من room metadata لدعم multi-tenant ──────────
    restaurant_id = ""
    try:
        meta = getattr(ctx.room, "metadata", None) or ""
        if meta:
            meta_dict = _json.loads(meta) if isinstance(meta, str) else meta
            restaurant_id = str(meta_dict.get("restaurant_id", ""))
    except Exception as _e:
        logger.warning("call=%s | could not parse room metadata: %s", call_id, _e)

    cfg      = await fetch_config(call_id, restaurant_id=restaurant_id)
    userdata = UserData(call_id=call_id, restaurant=cfg)
    await _ensure_backend_queue_worker_started()
    session_stt = _build_session_stt(cfg, client_reference_id=call_id)
    stt_context_terms = _stt_context_terms_for_config(cfg)
    logger.info(
        "call=%s | startup readiness | deps_ready=%s | config_available=%s | write_available=%s | config_source=%s | degraded=%s | stt_provider=%s | stt_context_terms=%d | preemptive=%s",
        call_id,
        session_dependencies_ready(),
        backend_config_available(),
        backend_write_available(userdata.write_health),
        cfg.config_source,
        cfg.degraded_mode,
        SESSION_STT_PROVIDER,
        len(stt_context_terms),
        SESSION_PREEMPTIVE_GENERATION,
    )

    agents = {
        "greeter":     Greeter(cfg),
        "takeaway":    Takeaway(cfg),
        "reservation": Reservation(cfg),
        "complaint":   Complaint(cfg),
    }
    # في degraded mode بنضيف delivery agent كمسار capture مؤقت بدل ما ننفي الخدمة غلط.
    if cfg.delivery_enabled or cfg.degraded_mode:
        agents["delivery"] = Delivery(cfg)

    userdata.agents = agents

    session = AgentSession[UserData](
        userdata       = userdata,
        stt            = session_stt,
        llm            = SESSION_LLM,
        tts            = SESSION_TTS,
        vad            = SESSION_VAD,
        allow_interruptions=True,
        min_interruption_duration=MIN_INTERRUPTION_DURATION_SECONDS,
        min_endpointing_delay=MIN_ENDPOINTING_DELAY_SECONDS,
        max_endpointing_delay=MAX_ENDPOINTING_DELAY_SECONDS,
        false_interruption_timeout=FALSE_INTERRUPTION_TIMEOUT_SECONDS,
        user_away_timeout=USER_AWAY_TIMEOUT_SECONDS,
        preemptive_generation=SESSION_PREEMPTIVE_GENERATION,
        max_tool_steps = MAX_TOOL_STEPS,
    )

    # ── Metrics: breakdown per component ──────────────────────────────────
    @session.on("metrics_collected")
    def _on_metrics(event):
        m = event.metrics
        if isinstance(m, STTMetrics):
            logger.info(
                "call=%s | METRICS STT | duration=%.0fms | audio=%.1fs",
                call_id, m.duration * 1000, m.audio_duration,
            )
        elif isinstance(m, LLMMetrics):
            logger.info(
                "call=%s | METRICS LLM | ttft=%.0fms | total=%.0fms | prompt=%d | completion=%d | tok/s=%.0f",
                call_id, m.ttft * 1000, m.duration * 1000,
                m.prompt_tokens, m.completion_tokens, m.tokens_per_second,
            )
        elif isinstance(m, TTSMetrics):
            logger.info(
                "call=%s | METRICS TTS | ttfb=%.0fms | total=%.0fms | audio=%.1fs | chars=%d",
                call_id, m.ttfb * 1000, m.duration * 1000,
                m.audio_duration, m.characters_count,
            )
        elif isinstance(m, EOUMetrics):
            logger.info(
                "call=%s | METRICS EOU | eou_delay=%.0fms | transcription=%.0fms",
                call_id, m.end_of_utterance_delay * 1000,
                m.transcription_delay * 1000,
            )

    t_start = time.monotonic()
    close_event = asyncio.Event()
    close_state = {"closed": False}
    close_reason = "normal_close"
    last_user_activity_at = t_start
    agent_state = "initializing"
    inactivity_prompt_count = 0
    last_reprompt_at = 0.0
    watchdog_task: asyncio.Task | None = None

    @session.on("close")
    def _on_close(event):
        nonlocal close_reason
        close_reason = f"session_{event.reason}"
        close_event.set()

    @session.on("agent_state_changed")
    def _on_agent_state(event):
        nonlocal agent_state, last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        agent_state = event.new_state
        if event.old_state == "speaking" and event.new_state == "listening":
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
        logger.info(
            "call=%s | agent_state=%s→%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal last_user_activity_at
        if event.new_state == "speaking":
            last_user_activity_at = time.monotonic()
        logger.info(
            "call=%s | user_state=%s→%s",
            call_id, event.old_state, event.new_state,
        )

    @session.on("user_input_transcribed")
    def _on_transcribed(event):
        nonlocal last_user_activity_at, inactivity_prompt_count, last_reprompt_at
        if event.transcript.strip():
            last_user_activity_at = time.monotonic()
            inactivity_prompt_count = 0
            last_reprompt_at = 0.0
        logger.info(
            "call=%s | transcript final=%s | text=%s",
            call_id, event.is_final, (event.transcript or "").strip(),
        )

    @session.on("agent_false_interruption")
    def _on_false_interruption(event):
        logger.info(
            "call=%s | false_interruption | resumed=%s",
            call_id, event.resumed,
        )

    @session.on("function_tools_executed")
    def _on_tools(event):
        tool_names = [getattr(call, "name", "") for call in event.function_calls]
        logger.info("call=%s | tools=%s", call_id, tool_names)

    @session.on("error")
    def _on_error(event):
        logger.error("call=%s | session error | %s", call_id, _exc_log_fields(event.error))

    async def _watch_inactivity() -> None:
        nonlocal inactivity_prompt_count, last_reprompt_at, close_reason
        while not close_event.is_set():
            await asyncio.sleep(1.0)
            if close_event.is_set():
                return
            if agent_state in {"speaking", "thinking", "initializing"}:
                continue
            if userdata.session_transitional_state:
                continue
            if (
                userdata.order_submit_in_flight
                or userdata.reservation_submit_in_flight
                or userdata.complaint_submit_in_flight
            ):
                continue

            idle_for = time.monotonic() - last_user_activity_at
            if (
                inactivity_prompt_count < NO_SPEECH_REPROMPT_LIMIT
                and idle_for >= NO_SPEECH_PROMPT_SECONDS
                and (not last_reprompt_at or (time.monotonic() - last_reprompt_at) >= NO_SPEECH_REPROMPT_GAP_SECONDS)
            ):
                inactivity_prompt_count += 1
                last_reprompt_at = time.monotonic()
                logger.warning(
                    "call=%s | inactivity reprompt | idle_for=%.1fs | count=%d",
                    call_id, idle_for, inactivity_prompt_count,
                )
                with contextlib.suppress(Exception):
                    await session.say(
                        "لسه معايا يا فندم؟",
                        allow_interruptions=True,
                        add_to_chat_ctx=False,
                    )
                continue

            if idle_for >= NO_SPEECH_CLOSE_SECONDS:
                close_reason = "inactivity_timeout"
                userdata.session_transitional_state = True
                logger.warning(
                    "call=%s | inactivity close | idle_for=%.1fs",
                    call_id, idle_for,
                )
                await _safe_close_session_once(
                    session,
                    close_state,
                    farewell="هقفل المكالمة دلوقتي، كلمنا تاني في أي وقت يا فندم.",
                )
                return

    try:
        await session.start(agent=userdata.agents["greeter"], room=ctx.room)
        watchdog_task = asyncio.create_task(_watch_inactivity(), name=f"inactivity_watchdog_{call_id}")
        await asyncio.wait_for(close_event.wait(), timeout=MAX_CALL_DURATION)
    except asyncio.TimeoutError:
        close_reason = "call_timeout"
        userdata.session_transitional_state = True
        logger.warning("call=%s | timeout after %ds — ending session", call_id, MAX_CALL_DURATION)
        await _safe_close_session_once(
            session,
            close_state,
            farewell="معلش يا فندم، وقت المكالمة خلص. كلمنا تاني في أي وقت.",
        )
    except Exception as exc:
        close_reason = "session_error"
        logger.exception("call=%s | error: %s", call_id, exc)
        userdata.session_transitional_state = True
        await _safe_aclose_session_once(session, close_state)
        raise
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task
        await _safe_aclose_session_once(session, close_state)
        duration = int(time.monotonic() - t_start)
        logger.info(
            "call=%s | ended | duration=%ds | close_reason=%s | order=%s | reservation=%s | complaint=%s | config_source=%s",
            call_id, duration, close_reason, userdata.order_confirmed, userdata.reservation_confirmed,
            userdata.complaint_logged, userdata.restaurant.config_source,
        )

if __name__ == "__main__":
    cli.run_app(server)
