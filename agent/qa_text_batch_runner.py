"""Text-mode batch runner for AloEgy 50-scenario JSONL test pack.

Drives the real RestaurantAgent persona prompt through OpenAI function-calling
without touching LiveKit / STT / TTS. For each scenario it:
  1. Resets state, replays the JSONL `caller` turns one at a time.
  2. Lets the LLM call the same tool surface as production (set_intent,
     update_order, set_delivery_info, set_reservation_info, set_complaint,
     confirm_and_submit, end_call, ...).
  3. Captures the assistant transcript and final captured-field snapshot.
  4. Applies rule-based scoring against the rubric in
     `claude_runner_prompt_for_aloegy_calls.md` and writes per-scenario JSON
     reports plus an aggregate summary.

Run:
    cd "D:\\lovable livekit\\agent"
    python qa_text_batch_runner.py

Env: needs OPENAI_API_KEY (read from agent/.env). Model defaults to
SESSION_LLM_MODEL or "gpt-4.1-mini".
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai import APIError, RateLimitError, APIConnectionError

AGENT_DIR = Path(__file__).resolve().parent
load_dotenv(AGENT_DIR / ".env")

# Reuse the real persona prompt and config dataclass so the test exercises the
# same instructions production runs against.
sys.path.insert(0, str(AGENT_DIR))
from backend.config import RestaurantConfig  # noqa: E402
from restaurant_agent import _build_persona_prompt  # noqa: E402

SCENARIO_FILE = AGENT_DIR / "50senario test" / "aloegy_pizza_king_full_call_scenarios_50.jsonl"
RESULTS_DIR = AGENT_DIR / "50senario test" / "results"

DEFAULT_MODEL = os.getenv("SESSION_LLM_MODEL", "gpt-4.1-mini")
# gpt-4.1-mini direct via OpenAI; allow override.
if "/" in DEFAULT_MODEL:
    # OpenRouter slug — fall back to the default OpenAI model for the harness.
    DEFAULT_MODEL = "gpt-4.1-mini"

MAX_TOOL_STEPS = 6
MAX_TURN_RETRIES = 2
TURN_TIMEOUT_SECONDS = 45.0


# ─────────────────────────────────────────────────────────────────────────────
# Pizza King config — built from the items / zones / branches referenced in
# the scenario pack so the menu fuzzy match has something realistic to lock on.
# ─────────────────────────────────────────────────────────────────────────────

PIZZA_KING_MENU: list[dict[str, Any]] = [
    {"name": "بيتزا بيبروني لارج",      "price": 220.0, "available": True},
    {"name": "بيتزا بيبروني ميديم",     "price": 170.0, "available": True},
    {"name": "بيتزا مارجريتا لارج",     "price": 180.0, "available": True},
    {"name": "بيتزا مارجريتا ميديم",    "price": 140.0, "available": True},
    {"name": "بيتزا تشيكن رانش لارج",   "price": 240.0, "available": True},
    {"name": "بيتزا تشيكن رانش ميديم",  "price": 190.0, "available": True},
    {"name": "بيتزا تشيكن باربكيو لارج",  "price": 240.0, "available": True},
    {"name": "بيتزا تشيكن باربكيو ميديم", "price": 190.0, "available": True},
    {"name": "بيتزا خضار لارج",         "price": 190.0, "available": True},
    {"name": "بيتزا خضار ميديم",        "price": 150.0, "available": True},
    {"name": "بيتزا تونة ميديم",        "price": 160.0, "available": True},
    {"name": "بيتزا تونة لارج",         "price": 200.0, "available": True},
    {"name": "بيتزا سوبر سوبريم لارج",  "price": 250.0, "available": True},
    {"name": "بيتزا سوبر سوبريم ميديم", "price": 200.0, "available": True},
    {"name": "بيتزا جبنة ميديم",        "price": 130.0, "available": True},
    {"name": "بيتزا جبنة لارج",         "price": 170.0, "available": True},
    {"name": "تشيز رولز",               "price": 65.0,  "available": True},
    {"name": "جارليك ديب",              "price": 25.0,  "available": True},
    {"name": "بطاطس",                   "price": 45.0,  "available": True},
    {"name": "كولا واحد لتر",           "price": 35.0,  "available": True},
    {"name": "كولا اتنين لتر",          "price": 55.0,  "available": True},
    {"name": "بيبسي",                   "price": 30.0,  "available": True},
    {"name": "مياه صغيرة",              "price": 10.0,  "available": True},
]

PIZZA_KING_BRANCHES = [
    {"name": "فرع المعادي"},
    {"name": "فرع المعادي الجديدة"},
    {"name": "فرع زهراء المعادي"},
]

PIZZA_KING_DELIVERY_ZONES = [
    "المعادي",
    "المعادي الجديدة",
    "زهراء المعادي",
    "دجلة المعادي",
    "كورنيش المعادي",
    "ثكنات المعادي",
]


def build_pizza_king_config() -> RestaurantConfig:
    return RestaurantConfig(
        name="بيتزا كينج",
        phone="19719",
        address="المعادي",
        branches=PIZZA_KING_BRANCHES,
        hours={
            "saturday":  {"open": "12:00", "close": "02:00"},
            "sunday":    {"open": "12:00", "close": "02:00"},
            "monday":    {"open": "12:00", "close": "02:00"},
            "tuesday":   {"open": "12:00", "close": "02:00"},
            "wednesday": {"open": "12:00", "close": "02:00"},
            "thursday":  {"open": "12:00", "close": "03:00"},
            "friday":    {"open": "12:00", "close": "03:00"},
        },
        menu_items=PIZZA_KING_MENU,
        upsell_rules=[],
        is_open=True,
        delivery_enabled=True,
        delivery_minutes=45,
        delivery_fee=15.0,
        min_order=80.0,
        delivery_zones=PIZZA_KING_DELIVERY_ZONES,
        wait_minutes=20,
        min_guests=1,
        max_guests=20,
        config_source="backend",
    )


# ─────────────────────────────────────────────────────────────────────────────
# State container — flattens the bits of UserData the tools mutate.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CallState:
    intent: str = ""
    customer_name: str = ""
    order: list[str] = field(default_factory=list)
    order_total: float = 0.0
    # Every distinct total the order has had during the call. Used by the
    # hallucination detector so a mid-call total like '265 جنيه' isn't
    # flagged as fabricated when the order is later trimmed to 220.
    order_total_history: list[float] = field(default_factory=list)
    order_validated: bool = False
    order_confirmed: bool = False
    order_id: str = ""
    delivery_address: str = ""
    delivery_landmark: str = ""
    reservation_time: str = ""
    guests_count: int | None = None
    selected_branch: str = ""
    complaint_text: str = ""
    complaint_type: str = ""
    complaint_logged: bool = False
    reservation_confirmed: bool = False
    reservation_id: str = ""
    asked_slot_questions: dict[str, int] = field(default_factory=dict)
    end_call_requested: bool = False
    end_call_reason: str = ""
    last_user_message: str = ""

    def snapshot(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        # Drop noisy fields from the captured-state report.
        d.pop("asked_slot_questions", None)
        d.pop("last_user_message", None)
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Menu / order helpers — small enough to embed; mirror agent._normalize_order_items
# ─────────────────────────────────────────────────────────────────────────────

_AR_PUNCT = re.compile(r"[ًٌٍَُِّْـ‌‍]")
_NORM_SPACES = re.compile(r"\s+")


def _normalize_ar(text: str) -> str:
    if not text:
        return ""
    t = _AR_PUNCT.sub("", text)
    t = (
        t.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
    )
    return _NORM_SPACES.sub(" ", t).strip().lower()


def _resolve_menu_item(item_name: str, menu_items: list[dict]) -> dict | None:
    target = _normalize_ar(item_name)
    if not target:
        return None
    target_tokens = set(target.split())
    if not target_tokens:
        return None

    best: tuple[float, dict] | None = None
    for item in menu_items:
        if not item.get("available", True):
            continue
        norm_name = _normalize_ar(item.get("name", ""))
        if not norm_name:
            continue
        if norm_name == target:
            return item
        item_tokens = set(norm_name.split())
        if not item_tokens:
            continue
        overlap = len(target_tokens & item_tokens)
        if overlap == 0:
            continue
        # Score = (matched tokens / target tokens) weighted slightly by recall on the menu name.
        score = overlap / max(1, len(target_tokens)) + 0.1 * (overlap / len(item_tokens))
        threshold = 0.5 if len(target_tokens) > 1 else 0.99
        if score >= threshold and (best is None or score > best[0]):
            best = (score, item)
    return best[1] if best else None


def _match_delivery_zone(address: str, delivery_zones: list[str] | None) -> str | None:
    if not delivery_zones:
        return None
    addr_norm = _normalize_ar(address)
    for zone in delivery_zones:
        if _normalize_ar(zone) in addr_norm:
            return zone
    return None


def _format_order_item(name: str, qty: int) -> str:
    return name if qty == 1 else f"{name} x {qty}"


def _normalize_order_items(
    new_items: list[tuple[str, int]],
    menu_items: list[dict],
) -> tuple[list[str], list[str], float]:
    aggregated: dict[str, int] = {}
    unknown: list[str] = []
    total = 0.0
    for name, qty in new_items:
        menu_item = _resolve_menu_item(name, menu_items)
        if not menu_item:
            if name:
                unknown.append(name)
            continue
        canonical = str(menu_item["name"]).strip()
        aggregated[canonical] = aggregated.get(canonical, 0) + qty
        total += float(menu_item.get("price", 0) or 0) * qty
    normalized = [_format_order_item(name, qty) for name, qty in aggregated.items()]
    return normalized, unknown, total


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas — OpenAI function-calling format. Wording mirrors restaurant_tools.
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_intent",
            "description": "سجّل نوع طلب العميل بمجرد ما يتضح. ممكن تنده تاني لو غيّر رأيه.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["takeaway", "delivery", "reservation", "complaint"],
                        "description": "takeaway = استلام، delivery = توصيل، reservation = حجز ترابيزة، complaint = شكوى.",
                    }
                },
                "required": ["intent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_name",
            "description": "سجّل اسم العميل لما يقوله.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "اسم العميل بالعربي زي ما قاله"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_order",
            "description": (
                "ضيف أصناف لطلب العميل. الـ tool بيتأكد من المنيو ويحسب الإجمالي. "
                "بيتم الإضافة دايماً للطلب الموجود — لو بدّل الطلب، نده clear_order الأول."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "اسم الصنف من غير كميات"},
                                "qty": {"type": "integer", "minimum": 1, "maximum": 99, "default": 1},
                            },
                            "required": ["name"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_order",
            "description": "امسح الطلب الحالي بالكامل. استخدم لما العميل يقول 'الغي الطلب' أو 'هبدأ من الأول'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_delivery_info",
            "description": "سجّل عنوان التوصيل ومعلم قريب.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "عنوان التوصيل كامل بالشارع والمنطقة"},
                    "landmark": {"type": "string", "description": "معلم قريب — ابعت '' لو ما قالش"},
                },
                "required": ["address", "landmark"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reservation_info",
            "description": "سجّل بيانات الحجز: الميعاد، العدد، والفرع لو متعدد.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_text": {"type": "string", "description": "ميعاد الحجز كنص بكلام العميل"},
                    "guests": {"type": "integer", "minimum": 1, "maximum": 200, "description": "عدد الضيوف"},
                    "branch": {"type": "string", "description": "اسم الفرع لو متعدد، أو '' لو فرع واحد/ما قالش"},
                },
                "required": ["time_text", "guests", "branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_complaint",
            "description": "سجّل شكوى العميل بنصها كاملة.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "نص الشكوى كامل بكلام العميل"},
                    "category": {
                        "type": "string",
                        "description": "نوع الشكوى: جودة / تأخير / خدمة / توصيل / تاني",
                    },
                },
                "required": ["text", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_menu",
            "description": "رجّع المنيو المتاح للعميل بصيغة مختصرة.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_and_submit",
            "description": (
                "أكد بيانات العميل وقدّم الطلب/الحجز/الشكوى للنظام. الـ tool بيراجع البيانات الناقصة ويرجّع رسالة لو في حاجة لازم تسأل عنها."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": "قفل المكالمة بشكل لائق بعد ما تكون قلت جملة الوداع.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "order_completed",
                            "reservation_completed",
                            "complaint_logged",
                            "customer_done",
                            "other",
                        ],
                    }
                },
                "required": ["reason"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatch — mirrors restaurant_tools behavior. Returns the same kind of
# Arabic acknowledgement strings the LLM sees in production.
# ─────────────────────────────────────────────────────────────────────────────


def dispatch_tool(name: str, args: dict[str, Any], state: CallState, cfg: RestaurantConfig) -> str:
    if name == "set_intent":
        intent = args.get("intent", "")
        if intent == "delivery" and not cfg.delivery_enabled:
            return "التوصيل مش متاح في المطعم. اعرض على العميل الاستلام بدلاً منه."
        state.intent = intent
        return f"النية اتسجلت: {intent}. كمّل عادي وشوف ناقص إيه."

    if name == "set_name":
        name_val = (args.get("name") or "").strip()
        if not name_val:
            return "الاسم فاضي. اطلب من العميل يعيده."
        state.customer_name = name_val
        return f"الاسم اتسجل: {name_val}"

    if name == "update_order":
        items_in = args.get("items") or []
        prepared: list[tuple[str, int]] = []
        for spec in items_in:
            n = (spec.get("name") or "").strip()
            if not n:
                continue
            q = max(1, min(99, int(spec.get("qty") or 1)))
            prepared.append((n, q))
        if not prepared:
            return "الطلب فاضي. اطلب من العميل يقول الأصناف."
        # Append to existing order, then renormalize the combined list.
        # Re-parse existing strings back into (name, qty) so we don't double-count.
        combined: list[tuple[str, int]] = []
        for raw in state.order:
            m = re.match(r"^(.+?)\s+x\s+(\d+)$", raw)
            if m:
                combined.append((m.group(1).strip(), int(m.group(2))))
            else:
                combined.append((raw.strip(), 1))
        combined.extend(prepared)
        normalized, unknown, total = _normalize_order_items(combined, cfg.menu_items)
        if not normalized:
            return (
                f"مفيش حاجة من اللي طلبه ('{', '.join(unknown)}') في المنيو. "
                f"اعرض على العميل المتاح: {cfg.menu_text()}"
            )
        state.order = normalized
        state.order_total = total
        if total and total not in state.order_total_history:
            state.order_total_history.append(total)
        state.order_validated = not unknown
        if state.order_confirmed:
            state.order_confirmed = False
        msg = f"الطلب اتسجل: {', '.join(normalized)} (إجمالي: {total:.0f} جنيه)."
        if unknown:
            msg += f" بس '{', '.join(unknown)}' مش في المنيو."
        return msg

    if name == "clear_order":
        if not state.order:
            return "مفيش طلب اتسجل أصلاً."
        last_user = _normalize_ar(state.last_user_message or "")
        switching_to_delivery = (
            any(word in last_user for word in ("دليفري", "توصيل", "وصل", "وصلي", "وصلها"))
            and not any(word in last_user for word in ("الغي", "الغى", "إلغي", "امسح", "ابدأ من الاول", "من الأول"))
        )
        if switching_to_delivery:
            return "الطلب القديم يفضل زي ما هو؛ غير النوع لدليفري وخد العنوان بس."
        state.order = []
        state.order_total = 0.0
        state.order_validated = False
        if state.order_confirmed:
            state.order_confirmed = False
        return "الطلب اتمسح. اسأل العميل عايز يطلب إيه."

    if name == "set_delivery_info":
        addr = (args.get("address") or "").strip()
        landmark = (args.get("landmark") or "").strip()
        if not addr:
            return "العنوان فاضي. اطلب من العميل يقوله تاني."
        if cfg.delivery_zones:
            matched_zone = _match_delivery_zone(addr, cfg.delivery_zones)
            if not matched_zone:
                state.delivery_address = ""
                state.delivery_zone = ""
                return (
                    f"العنوان خارج نطاق التوصيل عندنا. التوصيل متاح في {cfg.delivery_zones_text()}. "
                    "اعتذر للعميل واعرض عليه الاستلام من الفرع."
                )
            state.delivery_zone = matched_zone
        state.delivery_address = addr
        if landmark:
            state.delivery_landmark = landmark
        if not state.intent:
            state.intent = "delivery"
        msg = f"العنوان اتسجل: {addr}"
        if landmark:
            msg += f" (معلم: {landmark})"
        return msg

    if name == "set_reservation_info":
        time_text = (args.get("time_text") or "").strip()
        guests = int(args.get("guests") or 0)
        branch = (args.get("branch") or "").strip()
        if not time_text:
            return "ميعاد الحجز فاضي. اطلب من العميل يحدد الميعاد."
        if guests < cfg.min_guests:
            return f"أقل عدد ضيوف للحجز عندنا {cfg.min_guests}. اعتذر للعميل."
        if guests > cfg.max_guests:
            return f"أكتر عدد ضيوف نقدر نحجزله {cfg.max_guests}. اعتذر للعميل."
        state.reservation_time = time_text
        state.guests_count = guests
        if branch:
            state.selected_branch = branch
        elif cfg.branches:
            state.selected_branch = cfg.branches[0].get("name", "")
        if not state.intent:
            state.intent = "reservation"
        msg = f"الحجز اتسجل: {time_text} لـ{guests} نفر"
        if state.selected_branch:
            msg += f" - {state.selected_branch}"
        return msg

    if name == "set_complaint":
        text = (args.get("text") or "").strip()
        category = (args.get("category") or "").strip() or "general"
        if not text:
            return "نص الشكوى فاضي. اطلب من العميل يحكي بالتفصيل."
        state.complaint_text = text
        state.complaint_type = category
        if not state.intent:
            state.intent = "complaint"
        return f"الشكوى اتسجلت. النوع: {category}"

    if name == "get_menu":
        return cfg.menu_text()

    if name == "confirm_and_submit":
        intent = state.intent
        if not intent:
            return "النية مش معروفة. اسأل العميل: عايز استلام، توصيل، حجز، ولا شكوى؟"
        if intent in {"takeaway", "delivery"}:
            if not state.order:
                return "ناقص الطلب. اسأل العميل عن الأصناف اللي عايزها."
            if intent == "delivery" and not state.delivery_address:
                return "ناقص العنوان. اطلبه من العميل قبل التأكيد."
            if intent == "delivery" and cfg.delivery_zones and not state.delivery_zone:
                return (
                    f"العنوان مش مؤكد داخل نطاق التوصيل. التوصيل متاح في {cfg.delivery_zones_text()}. "
                    "اطلب عنوان داخل النطاق أو اعرض الاستلام."
                )
            if not state.customer_name:
                return "ناقص الاسم. اطلبه من العميل."
            if intent == "delivery" and cfg.min_order > 0 and state.order_total < cfg.min_order:
                return (
                    f"إجمالي الطلب ({state.order_total:.0f} جنيه) أقل من الحد الأدنى للتوصيل "
                    f"({cfg.min_order:.0f} جنيه). اعرض زيادة."
                )
            if state.order_confirmed:
                return "الطلب متسجل خلاص."
            state.order_confirmed = True
            state.order_id = f"PK-TEST-{int(time.time()*1000) % 10_000_000}"
            wait = cfg.delivery_minutes if intent == "delivery" else cfg.wait_minutes
            return (
                f"تم التأكيد. order_id={state.order_id}. "
                f"أكد للعميل: 'تمام يا {state.customer_name}، الطلب اتسجل، خلال {wait} دقيقة.'"
            )
        if intent == "reservation":
            if not state.reservation_time:
                return "ناقص ميعاد الحجز."
            if not state.guests_count:
                return "ناقص عدد الضيوف."
            if len(cfg.branches) > 1 and not state.selected_branch:
                return f"ناقص الفرع. الفروع المتاحة: {cfg.branch_names()}."
            if not state.customer_name:
                return "ناقص الاسم. اطلبه من العميل."
            if state.reservation_confirmed:
                return "الحجز متسجل خلاص."
            state.reservation_confirmed = True
            state.reservation_id = f"PK-RES-{int(time.time()*1000) % 10_000_000}"
            return (
                f"تم التأكيد. reservation_id={state.reservation_id}. "
                f"أكد للعميل: 'تمام يا {state.customer_name}، حجزتلك ترابيزة لـ{state.guests_count} نفر يوم {state.reservation_time}.'"
            )
        if intent == "complaint":
            if not state.complaint_text:
                return "ناقص نص الشكوى."
            if state.complaint_logged:
                return "الشكوى متسجلة خلاص."
            state.complaint_logged = True
            return (
                f"الشكوى اتسجلت. أكد للعميل بإن فريق الإدارة هيتابع الشكوى."
            )
        return "النية غير معروفة."

    if name == "end_call":
        state.end_call_requested = True
        state.end_call_reason = args.get("reason", "")
        return "تم قول جملة الوداع للعميل، والمكالمة هتقفل بعد ما الصوت يخلص."

    return f"[unknown tool: {name}]"


# ─────────────────────────────────────────────────────────────────────────────
# Per-turn [CALL_STATE] snapshot — mirrors RestaurantAgent._build_state_snapshot
# ─────────────────────────────────────────────────────────────────────────────


def build_state_snapshot(state: CallState, cfg: RestaurantConfig) -> str:
    parts: list[str] = ["[CALL_STATE]"]
    if state.last_user_message:
        parts.append(f'user="{state.last_user_message}"')
    parts.append(f"intent={state.intent or '?'}")
    if state.order:
        order_str = "، ".join(state.order)
        if state.order_total:
            parts.append(f"order=[{order_str}] total={state.order_total:.0f}ج")
        else:
            parts.append(f"order=[{order_str}]")
    if state.customer_name:
        parts.append(f"name={state.customer_name}")
    if state.delivery_address:
        parts.append(f"address={state.delivery_address}")
    if state.delivery_landmark:
        parts.append(f"landmark={state.delivery_landmark}")
    if state.reservation_time:
        parts.append(f"time={state.reservation_time}")
    if state.guests_count:
        parts.append(f"guests={state.guests_count}")
    if state.selected_branch:
        parts.append(f"branch={state.selected_branch}")
    elif state.intent == "reservation" and len(cfg.branches) > 1:
        parts.append(f"branch=? (متاح: {cfg.branch_names()})")
    if state.complaint_text:
        parts.append(f'complaint="{state.complaint_text[:80]}"')
    asked = sorted(s for s, c in state.asked_slot_questions.items() if c > 0)
    if asked:
        parts.append(f"asked_once={','.join(asked)}")
        parts.append("rule=never_repeat_asked_slot_question")
    if state.order_confirmed:
        parts.append(f"DONE order_id={state.order_id or '-'}")
    if state.reservation_confirmed:
        parts.append(f"DONE reservation_id={state.reservation_id or '-'}")
    if state.complaint_logged:
        parts.append("DONE complaint_logged")
    return " | ".join(parts)


# Track which slot questions the assistant just asked, so we can surface
# state-memory violations later.
_SLOT_QUESTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "order":            ("تحب تطلب", "عايز تطلب", "تطلب ايه", "الأصناف", "عايز ايه"),
    "address":          ("العنوان", "عنوانك", "توصل فين", "فين التوصيل", "المنطقة"),
    "name":             ("اسمك", "الاسم", "اسم حضرتك", "اقول لمين", "أسجل باسم"),
    "reservation_time": ("ميعاد", "وقت الحجز", "امتى الحجز"),
    "guests":           ("كام فرد", "كام شخص", "عدد الأفراد", "عدد الضيوف"),
    "branch":           ("فرع", "أنهي فرع", "اي فرع"),
    "complaint":        ("الشكوى", "المشكلة", "ايه اللي حصل"),
}


def detect_slot_questions(agent_text: str) -> set[str]:
    norm = _normalize_ar(agent_text)
    if not norm:
        return set()
    out: set[str] = set()
    for cat, patterns in _SLOT_QUESTION_PATTERNS.items():
        for p in patterns:
            if _normalize_ar(p) in norm:
                out.add(cat)
                break
    return out


def slot_is_captured(category: str, state: CallState, cfg: RestaurantConfig) -> bool:
    if category == "order":            return bool(state.order)
    if category == "address":          return bool(state.delivery_address)
    if category == "name":             return bool(state.customer_name)
    if category == "reservation_time": return bool(state.reservation_time)
    if category == "guests":           return bool(state.guests_count)
    if category == "branch":           return bool(state.selected_branch)
    if category == "complaint":        return bool(state.complaint_text)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Conversation driver
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TurnRecord:
    role: str          # "caller" | "agent" | "tool"
    text: str
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallTrace:
    scenario_id: str
    title: str
    turns: list[TurnRecord] = field(default_factory=list)
    repeated_slot_questions: list[str] = field(default_factory=list)
    error: str = ""


def _opening_line(cfg: RestaurantConfig) -> str:
    if not cfg.is_open:
        return f"أهلاً بيك! معاك ليلي من {cfg.name}، للأسف إحنا مقفولين دلوقتي."
    return f"أهلاً بيك! معاك ليلي من {cfg.name}، أقدر أساعدك في إيه؟"


async def run_scenario(
    scenario: dict[str, Any],
    cfg: RestaurantConfig,
    client: AsyncOpenAI,
    model: str,
) -> tuple[CallState, CallTrace]:
    state = CallState()
    trace = CallTrace(scenario_id=scenario.get("id", ""), title=scenario.get("title", ""))

    persona = _build_persona_prompt(cfg)
    messages: list[dict[str, Any]] = [{"role": "system", "content": persona}]
    opening = _opening_line(cfg)
    messages.append({"role": "assistant", "content": opening})
    trace.turns.append(TurnRecord(role="agent", text=opening))

    for turn in scenario.get("dialogue", []):
        caller_text = turn.get("caller", "").strip()
        if not caller_text:
            continue
        # The runner ignores stage directions like "[silence 8 seconds]" — we
        # treat them as empty user input by sending the literal so the LLM can
        # see what the caller "did" and decide how to react. (Production would
        # see real silence and the inactivity reprompt would fire.)
        state.last_user_message = caller_text
        trace.turns.append(TurnRecord(role="caller", text=caller_text))
        messages.append({"role": "user", "content": caller_text})
        # Per-turn state snapshot — same pattern as on_user_turn_completed.
        messages.append({"role": "system", "content": build_state_snapshot(state, cfg)})

        # Tool loop — give the LLM up to MAX_TOOL_STEPS chained tool calls
        # before forcing a text reply.
        for step in range(MAX_TOOL_STEPS + 1):
            resp = None
            last_exc: Exception | None = None
            for attempt in range(MAX_TURN_RETRIES + 1):
                try:
                    resp = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=model,
                            messages=messages,
                            tools=TOOL_SCHEMAS,
                            tool_choice="auto" if step < MAX_TOOL_STEPS else "none",
                            temperature=0.25,
                            top_p=0.85,
                            max_completion_tokens=200,
                        ),
                        timeout=TURN_TIMEOUT_SECONDS,
                    )
                    break
                except (asyncio.TimeoutError, APIConnectionError, RateLimitError, APIError) as exc:
                    last_exc = exc
                    if attempt < MAX_TURN_RETRIES:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
            if resp is None:
                assert last_exc is not None
                trace.error = f"llm_call_failed: {type(last_exc).__name__}: {last_exc}"
                return state, trace

            choice = resp.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls or []

            if tool_calls:
                # Append the assistant turn that requested tools.
                assistant_turn: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
                messages.append(assistant_turn)
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = dispatch_tool(name, args, state, cfg)
                    trace.turns.append(TurnRecord(role="tool", text=result, tool_name=name, tool_args=args))
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                # Loop back so the LLM can either chain more tools or reply.
                continue

            # No tool calls — final assistant text for this turn.
            text = (msg.content or "").strip()
            if text:
                trace.turns.append(TurnRecord(role="agent", text=text))
                # Track slot-question repetition
                asked = detect_slot_questions(text)
                if asked:
                    for cat in asked:
                        prev = state.asked_slot_questions.get(cat, 0)
                        state.asked_slot_questions[cat] = prev + 1
                        if slot_is_captured(cat, state, cfg) and prev >= 1:
                            trace.repeated_slot_questions.append(cat)
            messages.append({"role": "assistant", "content": text})
            break

        if state.end_call_requested:
            break

    return state, trace


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────


PROMISED_PRICE_RE = re.compile(r"(\d{2,4})\s*(?:جنيه|جنيها|جنيهاً)\b")
PROMISED_HOUR_RE = re.compile(r"(?:الساعة|لحد|من)\s*(\d{1,2})")


def _agent_text(trace: CallTrace) -> str:
    return "\n".join(t.text for t in trace.turns if t.role == "agent")


def _called_tools(trace: CallTrace) -> list[str]:
    return [t.tool_name for t in trace.turns if t.role == "tool" and t.tool_name]


def detect_hallucinations(trace: CallTrace, state: CallState, cfg: RestaurantConfig, scenario: dict) -> list[str]:
    hallucinations: list[str] = []
    text = _agent_text(trace)

    # Promised prices that don't match any menu item the call actually engaged.
    menu_prices = {int(item["price"]) for item in cfg.menu_items if item.get("price")}
    # Allow any plausible total within ±5 of the captured order total
    # (covers the typical "rounded total" agent says back to the customer).
    for m in PROMISED_PRICE_RE.finditer(text):
        try:
            price = int(m.group(1))
        except ValueError:
            continue
        if price in menu_prices:
            continue
        # Accept any historical order total within ±5 (covers running totals
        # before the customer trimmed/swapped items).
        if any(abs(price - t) <= 5 for t in state.order_total_history):
            continue
        if state.order_total and abs(price - state.order_total) <= 5:
            continue
        # Ignore the stated minimum-order amount and delivery fee from cfg.
        if price in {int(cfg.min_order), int(cfg.delivery_fee)}:
            continue
        if price < 1000:
            hallucinations.append(
                f"agent stated price '{m.group(0)}' that's not in the menu and doesn't match the order total"
            )

    # Promised exact opening hours
    configured_hour_tokens = {
        str(int(str(times.get(key, "")).split(":", 1)[0]))
        for times in (cfg.hours or {}).values()
        for key in ("open", "close")
        if str(times.get(key, "")).split(":", 1)[0].isdigit()
    }
    for m in PROMISED_HOUR_RE.finditer(text):
        # Soft signal — only flag if the scenario explicitly asks about hours
        if (
            scenario.get("id") in {"PK-GREEN-010"}
            and m.group(1)
            and m.group(1) not in configured_hour_tokens
        ):
            hallucinations.append(f"agent quoted a specific hour '{m.group(0)}' in an opening-hours question")
            break

    # Promised delivery to a zone outside the configured zones
    if state.intent == "delivery" and scenario.get("id") in {"PK-STRESS-020"}:
        if state.order_confirmed:
            hallucinations.append("agent confirmed delivery to حدائق حلوان (out-of-zone)")

    # Tracking info invented for late-order complaint
    if "PK-GREEN-005" in scenario.get("id", "") or "PK-STRESS-015" in scenario.get("id", ""):
        if any(kw in text for kw in ("المندوب في الطريق", "هيوصل خلال", "تتبع الطلب", "موقع الطلب")):
            hallucinations.append("agent fabricated tracking information for a complaint scenario")

    return hallucinations


def score_scenario(
    scenario: dict,
    state: CallState,
    trace: CallTrace,
    cfg: RestaurantConfig,
) -> dict[str, Any]:
    sid = scenario.get("id", "")
    category = scenario.get("category", "")
    title = scenario.get("title", "")
    must_check = scenario.get("must_check", []) or []
    expected_intent = _expected_intent(scenario)

    critical_failures: list[str] = []
    agent_mistakes: list[str] = []
    missing_fields: list[str] = []

    called = _called_tools(trace)
    agent_text = _agent_text(trace)

    # ── routing ─────────────────────────────────────────────────────────────
    routing = 5
    if expected_intent and state.intent != expected_intent:
        if (
            scenario.get("id") == "PK-STRESS-020"
            and expected_intent == "delivery"
            and state.intent == "takeaway"
            and not state.order_confirmed
        ):
            # Correct market behavior for an out-of-zone delivery request is
            # refusing delivery and recovering to pickup, not forcing delivery.
            routing = 5
        elif (
            scenario.get("id") == "PK-STRESS-026"
            and expected_intent == "complaint"
            and state.complaint_text
            and state.order
        ):
            # This scenario explicitly combines a complaint with a new order.
            # The final active flow may be order/delivery as long as the
            # complaint itself was captured before continuing the sale.
            routing = 5
        elif state.intent == "":
            routing = 1
            agent_mistakes.append(f"intent never set (expected {expected_intent})")
        else:
            routing = 2
            agent_mistakes.append(f"wrong intent {state.intent!r} (expected {expected_intent})")
    elif "set_intent" not in called and expected_intent:
        routing = 3
        agent_mistakes.append("intent inferred only via downstream tool, set_intent never called")

    # ── entity_capture & finalization_safety ─────────────────────────────────
    finalization = 5
    entity = 5
    submitted = state.order_confirmed or state.reservation_confirmed or state.complaint_logged

    if expected_intent in {"takeaway", "delivery"}:
        for fld, label in (("order", "order"), ("customer_name", "name")):
            if not getattr(state, fld):
                missing_fields.append(label)
        if expected_intent == "delivery" and not state.delivery_address:
            missing_fields.append("delivery_address")
    elif expected_intent == "reservation":
        for fld, label in (
            ("reservation_time", "reservation_time"),
            ("guests_count", "guests_count"),
            ("customer_name", "name"),
        ):
            if not getattr(state, fld):
                missing_fields.append(label)
        if len(cfg.branches) > 1 and not state.selected_branch:
            # Optional — only flag if the scenario expects a branch decision
            if any("branch" in c for c in must_check):
                missing_fields.append("branch")
    elif expected_intent == "complaint":
        if not state.complaint_text:
            missing_fields.append("complaint_text")

    if missing_fields:
        entity = max(0, 5 - len(missing_fields))
        if submitted:
            critical_failures.append(f"submitted with missing fields: {', '.join(missing_fields)}")
            finalization = 0

    if expected_intent and not submitted and expected_intent != "complaint" and category != "stress":
        # Most green scenarios end with confirmation
        if scenario.get("id") not in {"PK-GREEN-010"}:  # opening-hours scenario doesn't submit
            finalization = min(finalization, 2)
            agent_mistakes.append("flow never reached confirm_and_submit")

    # ── state_memory ────────────────────────────────────────────────────────
    state_mem = 5
    if trace.repeated_slot_questions:
        unique_repeats = sorted(set(trace.repeated_slot_questions))
        state_mem = max(0, 5 - len(unique_repeats))
        for cat in unique_repeats:
            agent_mistakes.append(f"re-asked '{cat}' after it was captured in [CALL_STATE]")

    # ── no_hallucination ────────────────────────────────────────────────────
    hallucinations = detect_hallucinations(trace, state, cfg, scenario)
    no_hall = max(0, 5 - 2 * len(hallucinations))

    # ── tone_and_empathy (heuristic) ────────────────────────────────────────
    tone = 4
    forbidden_phrases = ("بكل سرور", "يسعدني", "أتشرف", "زبون")
    found = [p for p in forbidden_phrases if p in agent_text]
    if found:
        tone -= len(found)
        for p in found:
            agent_mistakes.append(f"used forbidden persona phrase '{p}'")
    if scenario.get("mood") in {"angry", "mildly upset"}:
        if not any(kw in agent_text for kw in ("معلش", "أسف", "آسف", "اعتذر", "اعتذار", "أعتذر")):
            tone -= 2
            agent_mistakes.append("no apology / acknowledgement on an upset/angry caller")
    tone = max(0, min(5, tone))

    # ── clarification_quality (heuristic) ───────────────────────────────────
    clar = 3
    if category == "stress":
        # Stress scenarios reward verification questions and acknowledgement of corrections
        clarifying_markers = ("ممكن تعيد", "تأكدلي", "يعني", "تقصد", "هل", "تأكد")
        if any(m in agent_text for m in clarifying_markers):
            clar += 1
        if "ignored_correction" in [a.lower() for a in agent_mistakes]:
            clar -= 2
    if expected_intent == "delivery" and sid == "PK-STRESS-013":
        # Address ambiguity scenario — needs clarification
        if state.order_confirmed and not state.delivery_address:
            clar = 0
        elif "ممكن" in agent_text or "تأكد" in agent_text:
            clar = max(clar, 4)
    clar = max(0, min(5, clar))

    # ── Hard fails ──────────────────────────────────────────────────────────
    # Defined by the rubric.
    if missing_fields and submitted:
        critical_failures.append("hard_fail: submit with missing required fields")
    if hallucinations:
        critical_failures.append("hard_fail: hallucinated price/availability/tracking/zone/hours")
    # Summary-before-submit check: confirm_and_submit must come after at least one
    # assistant text turn that mentions the captured slots (a readback). We use a
    # crude proxy: the assistant's final non-tool turn before confirm_and_submit
    # should contain at least one captured value.
    if submitted and not _saw_summary_before_submit(trace, state):
        critical_failures.append("hard_fail: no readback summary before confirm_and_submit")
        finalization = min(finalization, 2)

    passed = (
        not critical_failures
        and routing >= 3
        and entity >= 3
        and finalization >= 3
        and no_hall >= 4
    )

    scores = {
        "routing": routing,
        "entity_capture": entity,
        "state_memory": state_mem,
        "clarification_quality": clar,
        "tone_and_empathy": tone,
        "no_hallucination": no_hall,
        "finalization_safety": finalization,
    }

    recommended_fix = _suggest_fix(scenario, state, trace, scores, critical_failures, agent_mistakes)

    return {
        "scenario_id": sid,
        "title": title,
        "category": category,
        "passed": passed,
        "scores": scores,
        "critical_failures": critical_failures,
        "agent_mistakes": agent_mistakes,
        "missing_fields": missing_fields,
        "hallucinations": hallucinations,
        "final_state_captured_by_agent": state.snapshot(),
        "recommended_fix": recommended_fix,
        "harness_error": trace.error or None,
        "trace": [
            {
                "role": turn.role,
                "text": turn.text,
                "tool_name": turn.tool_name,
                "tool_args": turn.tool_args,
            }
            for turn in trace.turns
        ],
    }


def _expected_intent(scenario: dict) -> str:
    sid = scenario.get("id", "")
    must = " ".join(scenario.get("must_check", []) or []).lower()
    title = (scenario.get("title", "") + " " + scenario.get("goal", "")).lower()
    if "delivery" in title or "delivery" in must or "دليفري" in title or "توصيل" in title:
        return "delivery"
    if "takeaway" in must or "تيك" in title or "استلام" in title or "pickup" in title:
        return "takeaway"
    if "reservation" in must or "حجز" in title:
        return "reservation"
    if "complaint" in must or "شكوى" in title or "complaint" in title.lower():
        return "complaint"
    return ""


def _saw_summary_before_submit(trace: CallTrace, state: CallState) -> bool:
    # Walk turns — find the index of the first confirm_and_submit tool call.
    submit_idx = None
    for i, t in enumerate(trace.turns):
        if t.role == "tool" and t.tool_name == "confirm_and_submit":
            submit_idx = i
            break
    if submit_idx is None:
        return True  # didn't submit; not relevant
    # Look at agent text before submit. A valid readback may be followed by an
    # unrelated correction, such as rejecting a phone number, before the caller
    # confirms. Any prior assistant turn with captured values counts.
    for t in reversed(trace.turns[:submit_idx]):
        if t.role == "agent":
            txt = t.text
            # Heuristic: a real readback mentions at least one captured field.
            captured_fragments = []
            if state.customer_name: captured_fragments.append(state.customer_name)
            if state.order: captured_fragments.extend(state.order)
            if state.delivery_address: captured_fragments.append(state.delivery_address)
            if state.reservation_time: captured_fragments.append(state.reservation_time)
            if state.complaint_text: captured_fragments.append(state.complaint_text)
            if state.complaint_text:
                captured_fragments.append("الشكوى")
            if state.complaint_type:
                captured_fragments.append(state.complaint_type)
            if state.guests_count:
                captured_fragments.append(str(state.guests_count))
            if state.selected_branch:
                captured_fragments.append(state.selected_branch)
            if any(frag and frag in txt for frag in captured_fragments):
                return True
    return False


def _suggest_fix(scenario, state, trace, scores, critical_failures, agent_mistakes) -> str:
    if scores["routing"] < 3:
        return "P0 — strengthen intent routing: enforce set_intent before any flow-specific tool, or auto-call set_intent on the first qualifying utterance."
    if "hard_fail: submit with missing required fields" in " ".join(critical_failures):
        return "P0 — confirm_and_submit must hard-block missing required slots; the harness saw a submit without all of them."
    if "hard_fail: hallucinated" in " ".join(critical_failures):
        return "P0 — tighten the persona's anti-hallucination guard; consider a deterministic post-filter that strips invented prices / hours / zones."
    if scores["state_memory"] < 4:
        return "P1 — re-asked a captured slot. The [CALL_STATE] snapshot is being ignored; consider increasing its prominence or adding a hard-coded corrective response."
    if scores["finalization_safety"] < 4:
        return "P1 — finalization didn't include a clear readback before submit. Make summary-before-submit a tool-side hard check."
    if scores["tone_and_empathy"] < 4:
        return "P2 — tone/empathy gap, especially on upset callers. Add explicit 'apologize first' rule for angry mood."
    if scores["clarification_quality"] < 3:
        return "P2 — agent didn't clarify ambiguous input. Encourage an explicit verification utterance when STT confidence proxies (here: the user's hedging) appear."
    return "no fix required at this severity"


# ─────────────────────────────────────────────────────────────────────────────
# I/O
# ─────────────────────────────────────────────────────────────────────────────


def load_scenarios(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def save_scenario_artifacts(report: dict, trace: CallTrace, dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    sid = report["scenario_id"] or "unknown"
    (dir_path / f"{sid}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    transcript_lines = []
    for t in trace.turns:
        if t.role == "tool":
            transcript_lines.append(f"[tool] {t.tool_name}({json.dumps(t.tool_args, ensure_ascii=False)}) -> {t.text}")
        else:
            prefix = "CALLER" if t.role == "caller" else "AGENT"
            transcript_lines.append(f"{prefix}: {t.text}")
    (dir_path / f"{sid}.transcript.txt").write_text("\n".join(transcript_lines), encoding="utf-8")


def aggregate(reports: list[dict]) -> dict[str, Any]:
    total = len(reports)
    passed = sum(1 for r in reports if r.get("passed"))
    pass_rate = round(100.0 * passed / total, 1) if total else 0.0

    failure_counter: dict[str, int] = {}
    for r in reports:
        for f in r.get("critical_failures", []):
            failure_counter[f] = failure_counter.get(f, 0) + 1
        for m in r.get("agent_mistakes", []):
            failure_counter[m] = failure_counter.get(m, 0) + 1
    top_failures = sorted(failure_counter.items(), key=lambda kv: -kv[1])[:10]

    def _score_sum(r: dict) -> int:
        return sum(r.get("scores", {}).values())

    worst = sorted(reports, key=lambda r: _score_sum(r))[:5]
    worst_summary = [
        {
            "scenario_id": r["scenario_id"],
            "title": r["title"],
            "score_sum": _score_sum(r),
            "scores": r["scores"],
            "critical_failures": r["critical_failures"],
        }
        for r in worst
    ]

    # Priority bucket the recommended fixes.
    p0, p1, p2 = [], [], []
    for r in reports:
        fix = r.get("recommended_fix", "")
        if not fix or fix.startswith("no fix"):
            continue
        bucket = p0 if fix.startswith("P0") else p1 if fix.startswith("P1") else p2
        bucket.append({"scenario_id": r["scenario_id"], "fix": fix})

    return {
        "total": total,
        "passed": passed,
        "pass_rate_pct": pass_rate,
        "top_10_failures": [{"failure": k, "count": v} for k, v in top_failures],
        "worst_5_scenarios": worst_summary,
        "fixes_by_priority": {
            "P0": _dedup_fixes(p0),
            "P1": _dedup_fixes(p1),
            "P2": _dedup_fixes(p2),
        },
    }


def _dedup_fixes(items: list[dict]) -> list[dict]:
    seen: dict[str, list[str]] = {}
    for it in items:
        seen.setdefault(it["fix"], []).append(it["scenario_id"])
    return [{"fix": fix, "scenarios": ids} for fix, ids in seen.items()]


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


async def main_async(args: argparse.Namespace) -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY missing — set in agent/.env", file=sys.stderr)
        return 2
    client = AsyncOpenAI(api_key=api_key)
    cfg = build_pizza_king_config()
    scenarios = load_scenarios(SCENARIO_FILE)
    if args.limit:
        scenarios = scenarios[: args.limit]
    if args.only:
        only = set(args.only)
        scenarios = [s for s in scenarios if s.get("id") in only]
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    print(f"running {len(scenarios)} scenarios | model={args.model}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    reports: list[dict] = []
    sem = asyncio.Semaphore(args.concurrency)

    async def _run_one(scen: dict) -> None:
        sid = scen.get("id", "?")
        async with sem:
            t0 = time.monotonic()
            try:
                state, trace = await run_scenario(scen, cfg, client, args.model)
            except Exception as exc:
                tb = traceback.format_exc(limit=4)
                state = CallState()
                trace = CallTrace(scenario_id=sid, title=scen.get("title", ""), error=f"runner_crash: {exc}\n{tb}")
            elapsed = time.monotonic() - t0
            report = score_scenario(scen, state, trace, cfg)
            reports.append(report)
            save_scenario_artifacts(report, trace, RESULTS_DIR)
            mark = "PASS" if report["passed"] else "FAIL"
            print(f"  [{mark}] {sid} | {elapsed:5.1f}s | scores={report['scores']}")

    await asyncio.gather(*(_run_one(s) for s in scenarios))

    reports.sort(key=lambda r: r["scenario_id"])
    summary = aggregate(reports)
    (RESULTS_DIR / "_summary.json").write_text(
        json.dumps({"summary": summary, "reports": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = _summary_md(summary, reports)
    (RESULTS_DIR / "_summary.md").write_text(md, encoding="utf-8")
    print()
    print(f"DONE | pass_rate={summary['pass_rate_pct']}% ({summary['passed']}/{summary['total']})")
    print(f"results: {RESULTS_DIR}")
    return 0


def _summary_md(summary: dict, reports: list[dict]) -> str:
    lines = []
    lines.append(f"# AloEgy Pizza King — Text-Mode QA Batch")
    lines.append("")
    lines.append(f"- **Pass rate**: {summary['pass_rate_pct']}% ({summary['passed']}/{summary['total']})")
    lines.append("")
    lines.append("## Top 10 repeated failures")
    for f in summary["top_10_failures"]:
        lines.append(f"- ({f['count']}×) {f['failure']}")
    lines.append("")
    lines.append("## Worst 5 scenarios")
    for w in summary["worst_5_scenarios"]:
        lines.append(f"### {w['scenario_id']} — {w['title']} (sum={w['score_sum']})")
        lines.append(f"  - scores: `{w['scores']}`")
        for cf in w["critical_failures"]:
            lines.append(f"  - critical: {cf}")
    lines.append("")
    lines.append("## Fixes by priority")
    for prio in ("P0", "P1", "P2"):
        items = summary["fixes_by_priority"].get(prio, [])
        if not items:
            continue
        lines.append(f"### {prio}")
        for it in items:
            lines.append(f"- {it['fix']}")
            lines.append(f"  - scenarios: {', '.join(it['scenarios'])}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int, default=0, help="run only the first N scenarios")
    p.add_argument("--only", nargs="*", default=[], help="filter to specific scenario ids")
    p.add_argument("--concurrency", type=int, default=4)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))
