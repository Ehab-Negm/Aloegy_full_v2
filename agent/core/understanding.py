"""Structured turn understanding — the single source of "what did the user mean?".

The deterministic engine used to be a wall of regex / cue lists / Arabic
hint sets. That approach broke every time a customer phrased their
order naturally ("بيتزا مارجريتا محتاج منها 15 واحدة" missed by 14
units of pizza). Each new dialect or restaurant required a new patch.

The right division of labour:

- **LLM** understands natural language → returns a strict JSON
  ``TurnUnderstanding``. It can hallucinate, but the only damage it can
  do is propose items / intents that the engine will reject.
- **Engine** owns every consequential decision: validate items against
  the menu, apply mutations, gate submissions, manage anti-repeat,
  enforce idempotency.

This module defines the shared schema, the LLM caller, the per-turn
cache, and a deterministic mock provider so tests never hit the
network.
"""

from __future__ import annotations

import json as _json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


IntentKind = Literal[
    "takeaway",
    "delivery",
    "reservation",
    "complaint",
    "menu_question",
    "delivery_zone_question",
    "total_question",
    "post_completion_thanks",
    "greeting",
    "confirming",
    "denying",
    "clarification",
    "unknown",
]

MutationKind = Literal[
    "add",
    "replace",
    "remove",
    "increase",
    "decrease",
    "keep",
    "none",
]

ConfidenceTier = Literal["high", "medium", "low"]

ComplaintCategory = Literal[
    "order",
    "quality",
    "service",
    "delivery",
    "other",
]


@dataclass(frozen=True)
class OrderItemMention:
    """One menu item the customer mentioned, with the quantity they asked for."""

    item_name: str  # canonical preferred; engine resolves via MenuIndex
    quantity: int = 1
    evidence: str = ""  # the phrase that triggered the capture


@dataclass(frozen=True)
class TurnUnderstanding:
    """Structured view of a single user turn.

    Every field is optional / default-safe so a ``TurnUnderstanding()``
    is a valid "I have no idea" result. The engine treats the absence
    of a field as "do nothing", so partial extractions never trigger
    half-actions.
    """

    intent: IntentKind = "unknown"
    intent_confidence: ConfidenceTier = "low"

    order_items: tuple[OrderItemMention, ...] = field(default_factory=tuple)
    mutation: MutationKind = "none"

    customer_name: str | None = None
    customer_phone_digits: str | None = None
    delivery_address: str | None = None
    delivery_zone: str | None = None

    reservation_time: str | None = None
    guests_count: int | None = None

    complaint_text: str | None = None
    complaint_category: ComplaintCategory | None = None

    is_confirming: bool = False
    is_denying: bool = False

    raw_json: str = ""
    extraction_ms: int = 0
    source: str = "fallback"  # "llm", "mock", "fallback", "cache"
    error: str = ""


# JSON schema returned to the LLM provider. Kept declarative so swapping
# providers (Gemini, OpenAI, Anthropic) only changes the call wrapper —
# never the schema.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": list(IntentKind.__args__),  # type: ignore[attr-defined]
        },
        "intent_confidence": {
            "type": "string",
            "enum": list(ConfidenceTier.__args__),  # type: ignore[attr-defined]
        },
        "order_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_name": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "evidence": {"type": "string"},
                },
                "required": ["item_name", "quantity"],
            },
        },
        "mutation": {
            "type": "string",
            "enum": list(MutationKind.__args__),  # type: ignore[attr-defined]
        },
        "customer_name": {"type": "string", "nullable": True},
        "customer_phone_digits": {"type": "string", "nullable": True},
        "delivery_address": {"type": "string", "nullable": True},
        "delivery_zone": {"type": "string", "nullable": True},
        "reservation_time": {"type": "string", "nullable": True},
        "guests_count": {"type": "integer", "nullable": True},
        "complaint_text": {"type": "string", "nullable": True},
        "complaint_category": {
            "type": "string",
            "nullable": True,
            "enum": [None, "order", "quality", "service", "delivery", "other"],
        },
        "is_confirming": {"type": "boolean"},
        "is_denying": {"type": "boolean"},
    },
    "required": ["intent", "intent_confidence", "mutation", "is_confirming", "is_denying"],
}


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnContext:
    """Per-turn inputs the provider needs to ground the extraction.

    Kept tiny on purpose — the only mutable bit is ``user_text``;
    everything else is config-derived and can be promoted into the
    cached prefix of the prompt.
    """

    user_text: str
    flow: str
    menu_items: tuple[dict, ...]
    delivery_zones: tuple[str, ...] = ()
    last_agent_message: str = ""
    pending_upsell_item: str = ""


# A provider receives a TurnContext and returns a JSON string matching
# RESPONSE_SCHEMA. It can fail; the caller wraps any exception into a
# fallback ``TurnUnderstanding``.
Provider = Callable[[TurnContext], str]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_understanding(raw_json: str) -> TurnUnderstanding:
    """Turn a JSON string from a provider into a typed ``TurnUnderstanding``.

    Robust to malformed input: returns a low-confidence fallback rather
    than raising. The engine treats fallback the same as "I'm not sure"
    so the LLM tool path can still take over.
    """
    if not raw_json:
        return TurnUnderstanding(raw_json="", error="empty")
    try:
        data = _json.loads(raw_json)
    except (TypeError, ValueError) as exc:
        return TurnUnderstanding(raw_json=raw_json, error=f"parse:{exc.__class__.__name__}")
    if not isinstance(data, dict):
        return TurnUnderstanding(raw_json=raw_json, error="not_object")

    items = []
    for raw_item in data.get("order_items") or []:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("item_name") or "").strip()
        if not name:
            continue
        qty_raw = raw_item.get("quantity")
        try:
            qty = int(qty_raw) if qty_raw is not None else 1
        except (TypeError, ValueError):
            qty = 1
        if qty < 1:
            qty = 1
        items.append(OrderItemMention(
            item_name=name,
            quantity=qty,
            evidence=str(raw_item.get("evidence") or "").strip(),
        ))

    intent = data.get("intent")
    if intent not in IntentKind.__args__:  # type: ignore[attr-defined]
        intent = "unknown"
    confidence = data.get("intent_confidence")
    if confidence not in ConfidenceTier.__args__:  # type: ignore[attr-defined]
        confidence = "low"
    mutation = data.get("mutation")
    if mutation not in MutationKind.__args__:  # type: ignore[attr-defined]
        mutation = "none"
    cat = data.get("complaint_category")
    if cat is not None and cat not in {"order", "quality", "service", "delivery", "other"}:
        cat = None

    guests_count: int | None = None
    raw_guests = data.get("guests_count")
    if isinstance(raw_guests, int) and 1 <= raw_guests <= 50:
        guests_count = raw_guests
    elif isinstance(raw_guests, str):
        try:
            value = int(raw_guests)
            if 1 <= value <= 50:
                guests_count = value
        except ValueError:
            guests_count = None

    return TurnUnderstanding(
        intent=intent,  # type: ignore[arg-type]
        intent_confidence=confidence,  # type: ignore[arg-type]
        order_items=tuple(items),
        mutation=mutation,  # type: ignore[arg-type]
        customer_name=_clean_str(data.get("customer_name")),
        customer_phone_digits=_clean_str(data.get("customer_phone_digits")),
        delivery_address=_clean_str(data.get("delivery_address")),
        delivery_zone=_clean_str(data.get("delivery_zone")),
        reservation_time=_clean_str(data.get("reservation_time")),
        guests_count=guests_count,
        complaint_text=_clean_str(data.get("complaint_text")),
        complaint_category=cat,  # type: ignore[arg-type]
        is_confirming=bool(data.get("is_confirming", False)),
        is_denying=bool(data.get("is_denying", False)),
        raw_json=raw_json,
        source="parsed",
    )


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


# ---------------------------------------------------------------------------
# Caller with per-turn cache + fallback
# ---------------------------------------------------------------------------


_DEFAULT_CACHE_SIZE = 256


@dataclass
class UnderstandingService:
    """Thin orchestration around a provider.

    - Caches results for ``(text, flow, menu fingerprint)`` so duplicate
      ack/noise turns don't trigger a second LLM call.
    - Wraps every provider exception into a low-confidence fallback so
      the dialogue engine can keep working.
    - Exposes ``provider`` so tests can swap in a deterministic mock.
    """

    provider: Provider | None = None
    cache: dict[str, TurnUnderstanding] = field(default_factory=dict)
    cache_size: int = _DEFAULT_CACHE_SIZE
    timeout_seconds: float = 2.5

    def extract(self, ctx: TurnContext) -> TurnUnderstanding:
        if not ctx.user_text or not ctx.user_text.strip():
            return TurnUnderstanding(intent="unknown", source="empty")

        key = self._cache_key(ctx)
        cached = self.cache.get(key)
        if cached is not None:
            return TurnUnderstanding(
                **{**cached.__dict__, "source": "cache"}
            )

        if self.provider is None:
            return TurnUnderstanding(
                intent="unknown",
                source="no_provider",
                error="no_provider_configured",
            )

        start = time.monotonic()
        try:
            raw = self.provider(ctx)
        except Exception as exc:  # noqa: BLE001 - provider errors must not crash the turn
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return TurnUnderstanding(
                intent="unknown",
                source="provider_error",
                error=f"{type(exc).__name__}:{exc}",
                extraction_ms=elapsed_ms,
            )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        result = parse_understanding(raw)
        result = TurnUnderstanding(
            **{
                **result.__dict__,
                "source": "llm",
                "extraction_ms": elapsed_ms,
            }
        )
        self._cache_put(key, result)
        return result

    def _cache_key(self, ctx: TurnContext) -> str:
        menu_fp = "|".join(
            f"{item.get('name','')}:{item.get('available', True)}"
            for item in ctx.menu_items
        )
        return f"{ctx.flow}::{menu_fp}::{ctx.user_text.strip()}"

    def _cache_put(self, key: str, value: TurnUnderstanding) -> None:
        if len(self.cache) >= self.cache_size:
            # Drop oldest by insertion order (Python dicts preserve it).
            try:
                first_key = next(iter(self.cache))
                self.cache.pop(first_key, None)
            except StopIteration:
                pass
        self.cache[key] = value


# ---------------------------------------------------------------------------
# Singleton hook (live process) — wires the real Gemini provider lazily.
# ---------------------------------------------------------------------------


_DEFAULT_SERVICE: UnderstandingService | None = None


def get_default_service() -> UnderstandingService:
    """Return the process-wide service, lazily wiring the live provider.

    The live provider is built on first use so tests that import this
    module never accidentally instantiate a real Gemini client.
    """
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is not None:
        return _DEFAULT_SERVICE

    if os.getenv("LLM_UNDERSTANDING_ENABLED", "1") == "0":
        _DEFAULT_SERVICE = UnderstandingService(provider=None)
        return _DEFAULT_SERVICE

    from core.understanding_provider import build_default_provider

    provider = build_default_provider()
    _DEFAULT_SERVICE = UnderstandingService(provider=provider)
    return _DEFAULT_SERVICE


def set_default_service(service: UnderstandingService) -> None:
    """Used by tests to inject a deterministic provider."""
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = service


def reset_default_service() -> None:
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = None


def get_or_extract_for_turn(ud: Any, user_text: str, flow: str) -> TurnUnderstanding:
    """Return a per-turn cached ``TurnUnderstanding`` for the live agent.

    The first call in a turn invokes the configured provider; subsequent
    call-sites within the same turn read from the cache on ``UserData``.
    This guarantees a single LLM call per turn even when the order
    intercept, contact intercept, and signals emit each consult the
    understanding.

    Trivial turns (pure digits, single-token confirmations, empty
    strings) skip the LLM call entirely — there is nothing the model
    can usefully extract that the deterministic engine can't handle on
    its own, and a 1.2s extraction call on every "أيوه" or phone-digit
    chunk makes the conversation feel sluggish.
    """
    cached = getattr(ud, "turn_understanding", None)
    cached_text = getattr(ud, "turn_understanding_text", "")
    if cached is not None and cached_text == user_text:
        return cached

    fast = _fast_understanding_for_trivial(user_text, flow)
    if fast is not None:
        ud.turn_understanding = fast
        ud.turn_understanding_text = user_text or ""
        return fast

    cfg = getattr(ud, "restaurant", None)
    menu_items = tuple(getattr(cfg, "menu_items", None) or [])
    delivery_zones = tuple(getattr(cfg, "delivery_zones", None) or [])
    ctx = TurnContext(
        user_text=user_text or "",
        flow=flow or "",
        menu_items=menu_items,
        delivery_zones=delivery_zones,
        last_agent_message=getattr(ud, "last_agent_message", "") or "",
        pending_upsell_item=getattr(ud, "pending_upsell_item", "") or "",
    )
    understanding = get_default_service().extract(ctx)
    ud.turn_understanding = understanding
    ud.turn_understanding_text = user_text or ""
    return understanding


def _fast_understanding_for_trivial(text: str, flow: str) -> TurnUnderstanding | None:
    """Bypass the LLM for turns whose meaning is unambiguous from the
    raw transcript alone.

    Returning ``None`` means "let the LLM look at this turn"; returning
    a ``TurnUnderstanding`` means "the engine already knows what this
    is — skip the 1.2s round-trip".
    """
    import re as _re

    raw = (text or "").strip()
    if not raw:
        return TurnUnderstanding(intent="unknown", source="fast_path")

    from nlp.arabic import normalize_ar

    norm = normalize_ar(raw)

    # Digit-only chunks → phone fragments. The phone-capture flow handles
    # buffering / validation deterministically.
    digit_only = _re.fullmatch(r"[\d\s\-+()]+", raw.replace("‏", ""))
    if digit_only and any(ch.isdigit() for ch in raw):
        return TurnUnderstanding(
            intent="unknown",
            source="fast_path",
            customer_phone_digits=_re.sub(r"\D", "", raw),
        )

    if not norm:
        return TurnUnderstanding(intent="unknown", source="fast_path")

    tokens = norm.split()

    # Single-token explicit confirmations / denials.
    confirmations = {
        "ايوه", "أيوه", "اه", "آه", "تمام", "اوكي", "أوكي",
        "ماشي", "حاضر", "اكد", "أكد", "ok", "okay", "yes",
    }
    denials = {"لا", "لأ", "لاا", "لاء", "no"}
    if len(tokens) == 1:
        if norm in {normalize_ar(c) for c in confirmations}:
            return TurnUnderstanding(
                intent="confirming",
                intent_confidence="high",
                source="fast_path",
                is_confirming=True,
            )
        if norm in {normalize_ar(d) for d in denials}:
            return TurnUnderstanding(
                intent="denying",
                intent_confidence="high",
                source="fast_path",
                is_denying=True,
            )

    # "بس كده" / "خلاص" / "متشكر" — short post-completion thanks.
    short_thanks = {"خلاص", "متشكر", "بس كده", "تمام كده", "thanks"}
    if any(norm == normalize_ar(t) for t in short_thanks):
        return TurnUnderstanding(
            intent="post_completion_thanks",
            intent_confidence="high",
            source="fast_path",
        )

    return None


def is_actionable(u: TurnUnderstanding) -> bool:
    """Return True when the LLM (or mock) actually produced a usable view.

    The engine should fall back to its legacy cue-list extractors when
    no provider was configured, the provider failed, or the source was
    pure-empty so we don't silently ignore the customer's words.
    """
    return u.source in {"llm", "parsed", "cache", "mock", "fast_path"}


__all__ = [
    "ComplaintCategory",
    "ConfidenceTier",
    "IntentKind",
    "MutationKind",
    "OrderItemMention",
    "Provider",
    "RESPONSE_SCHEMA",
    "TurnContext",
    "TurnUnderstanding",
    "UnderstandingService",
    "get_default_service",
    "parse_understanding",
    "reset_default_service",
    "set_default_service",
]
