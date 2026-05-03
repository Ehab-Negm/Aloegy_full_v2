"""Live LLM providers for ``core.understanding``.

Kept in their own module so:

- the schema/orchestration code (``core.understanding``) is provider-
  agnostic and trivially unit-testable,
- importing ``core.understanding`` does not pull in vendor SDKs at
  module load — tests that don't need a real client never pay that
  cost, never hit auth, never need network access,
- adding providers (Anthropic, …) only requires a new
  ``build_<vendor>_provider`` function plus a branch in ``build_default_provider``.

Each provider builds a strict-JSON request with:

- a cached system prefix containing the engine-level rules and the
  menu, so the per-turn delta stays tiny,
- a strict JSON schema so the model is *forced* to return parseable
  output (Gemini ``response_schema``, OpenAI ``response_format``),
- a low temperature (the model should not hallucinate dishes).
"""

from __future__ import annotations

import json as _json
import logging
import os
from typing import Any

from core.understanding import RESPONSE_SCHEMA, Provider, TurnContext


logger = logging.getLogger("restaurant.agent")


_SYSTEM_INSTRUCTION = """\
You are an information extraction layer for an Egyptian Arabic restaurant
voice agent. Your only job is to read a single user turn and emit one
JSON object describing what the customer said.

Rules:
- Output strict JSON matching the provided schema. Never include prose,
  never include markdown fences.
- ``intent`` must be the single best label from the enum.
- ``order_items`` lists every menu item the user mentioned in this turn,
  with the quantity they asked for. Use the canonical name from the
  provided menu when possible. If the user said "بيتزا مارجريتا محتاج
  منها 15 واحدة" → quantity 15. If the user just said "كولا" → quantity 1.
- ``mutation``:
    add       → user wants to append items.
    replace   → user wants to wipe the order and start over.
    remove    → user wants to drop an item from the order.
    increase  → user wants more of an existing item.
    decrease  → user wants less of an existing item.
    keep      → user wants to leave the order as it is.
    none      → no change to the order.
- ``customer_phone_digits`` must be only digits (Egyptian carriers
  010/011/012/015 with 11 total digits or +20 form). Don't invent
  digits the user didn't say.
- ``is_confirming`` is true when the user explicitly says yes / أيوه /
  تمام كده to a pending confirmation. Be conservative.
- ``is_denying`` is true when the user explicitly says no / لا / غيّر.
- Never propose dishes that aren't in the menu. If the user asks for
  something unavailable, list it under ``order_items`` so the engine
  can reject it explicitly.
- Never invent slot values. If the user didn't say a name, leave
  ``customer_name`` null.
"""


def build_gemini_provider() -> Provider | None:
    """Return a provider closure or ``None`` if the SDK isn't available."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google.genai not installed — LLM understanding disabled")
        return None

    api_key = (
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        logger.warning(
            "GOOGLE_API_KEY / GEMINI_API_KEY not set — LLM understanding disabled"
        )
        return None

    model_name = os.getenv("UNDERSTANDING_MODEL", "gemini-2.5-flash").strip()
    temperature = float(os.getenv("UNDERSTANDING_TEMPERATURE", "0.05"))
    max_output = int(os.getenv("UNDERSTANDING_MAX_TOKENS", "1024"))

    client = genai.Client(api_key=api_key)

    response_schema = _to_genai_schema(RESPONSE_SCHEMA, types)

    def call(ctx: TurnContext) -> str:
        contents = _build_user_content(ctx)
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION + _menu_block(ctx),
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output,
            thinking_config=types.ThinkingConfig(thinking_budget=0)
            if model_name.startswith("gemini-2.5")
            else None,
        )
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("empty_llm_response")
        return text

    return call


def _menu_block(ctx: TurnContext) -> str:
    """Inline menu summary so the model uses canonical names.

    Kept short on purpose so the per-turn prompt stays small. The
    provider can later be upgraded to Gemini's context caching if menu
    size grows.
    """
    lines: list[str] = ["", "Menu (canonical names + availability):"]
    for item in ctx.menu_items:
        name = item.get("name") or ""
        if not name:
            continue
        avail = "available" if item.get("available", True) else "unavailable"
        price = item.get("price")
        if price is not None:
            lines.append(f"- {name} ({avail}, price: {price})")
        else:
            lines.append(f"- {name} ({avail})")
    if ctx.delivery_zones:
        lines.append("")
        lines.append("Delivery zones: " + ", ".join(ctx.delivery_zones))
    if ctx.pending_upsell_item:
        lines.append("")
        lines.append(f"Pending upsell offer: {ctx.pending_upsell_item}")
    if ctx.last_agent_message:
        lines.append("")
        lines.append(f"Agent's previous message: {ctx.last_agent_message[:280]}")
    return "\n".join(lines)


def _build_user_content(ctx: TurnContext) -> list[Any]:
    """Per-turn payload — only the user transcript and the flow tag."""
    return [
        f"Current flow: {ctx.flow}",
        f"User said: {ctx.user_text}",
    ]


def _to_genai_schema(schema: dict, types_mod: Any) -> Any:
    """Translate the dict schema into ``google.genai.types.Schema``.

    Gemini's SDK accepts a Schema object; recursively walk the dict
    and build the equivalent. Falls back to returning the dict if the
    SDK version supports raw dict schemas (newer versions do).
    """
    Schema = getattr(types_mod, "Schema", None)
    Type = getattr(types_mod, "Type", None)
    if Schema is None or Type is None:
        return schema  # SDK accepts dict schemas directly in this version.

    def _node(node: dict) -> Any:
        node_type = node.get("type")
        kwargs: dict[str, Any] = {}
        if node_type == "object":
            kwargs["type"] = Type.OBJECT
            props = node.get("properties") or {}
            kwargs["properties"] = {k: _node(v) for k, v in props.items()}
            if node.get("required"):
                kwargs["required"] = list(node["required"])
        elif node_type == "array":
            kwargs["type"] = Type.ARRAY
            if "items" in node:
                kwargs["items"] = _node(node["items"])
        elif node_type == "string":
            kwargs["type"] = Type.STRING
            if "enum" in node:
                kwargs["enum"] = [e for e in node["enum"] if e is not None]
        elif node_type == "integer":
            kwargs["type"] = Type.INTEGER
        elif node_type == "boolean":
            kwargs["type"] = Type.BOOLEAN
        else:
            kwargs["type"] = Type.OBJECT
        if node.get("nullable"):
            kwargs["nullable"] = True
        return Schema(**kwargs)

    return _node(schema)


def build_default_provider() -> Provider | None:
    """Build the Gemini understanding provider.

    OpenAI was previously selectable via ``UNDERSTANDING_PROVIDER=openai``
    but has been removed. ``UNDERSTANDING_PROVIDER`` is now ignored.
    """
    provider = build_gemini_provider()
    if provider is not None:
        logger.info(
            "understanding provider | gemini | model=%s",
            os.getenv("UNDERSTANDING_MODEL", "gemini-2.5-flash"),
        )
    return provider


__all__ = [
    "build_default_provider",
    "build_gemini_provider",
]
