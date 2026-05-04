"""RestaurantAgent — single LLM-driven agent that handles every flow.

Replaces the previous Greeter→Takeaway/Delivery/Reservation/Complaint
multi-agent handoff. The LLM tracks the customer's intent itself via
the `set_intent` tool and reads a live state snapshot every turn so it
never re-asks for captured information.

Design tenets:
  - ONE persona prompt (not 5 flow prompts merged with style/context/
    state/handoff/turn-guard messages).
  - ONE state snapshot system message per turn, in plain Egyptian Arabic.
  - No hardcoded flow openings, no flow-switch interceptors, no
    repetition detector wired into the response path, no corrective
    response hijacks. The LLM speaks freely with current state visible.
"""
from __future__ import annotations

import logging
from typing import Any

from livekit.agents import StopResponse, llm
from livekit.agents.voice import Agent

from backend.config import RestaurantConfig
from nlp.arabic import normalize_ar
from state.user_data import UserData
from utils.money import money2ar

import restaurant_tools

logger = logging.getLogger("restaurant.agent")

_STATE_PROMPT_MARKER = "[CALL_STATE]"


def _build_persona_prompt(cfg: RestaurantConfig) -> str:
    name = cfg.name or "المطعم"

    if cfg.degraded_mode:
        return (
            f"أنت محمد، موظف مصري بترد على تليفون مطعم \"{name}\".\n"
            "النظام عنده تحديث مؤقت دلوقتي — مش عارف المنيو ولا المواعيد.\n"
            "قول للعميل في جملة قصيرة إن في تحديث مؤقت، واطلب منه يقولّك طلبه أو يكلم المطعم تاني بعد شوية.\n"
            "متخمنش معلومات غير موجودة عندك. متذكرش أسعار ولا أصناف من نفسك."
        )

    delivery_status = (
        "بنوصّل برضه لو حد عايز توصيل."
        if cfg.delivery_enabled
        else "⚠️ التوصيل مش متاح. لو حد طلب توصيل، اعرضله الاستلام."
    )
    branches_line = (
        f"المطعم له فروع: {cfg.branch_names()}." if len(cfg.branches) > 1 else ""
    )
    menu_hint = cfg.menu_names() if cfg.menu_items else ""
    menu_line = f"\nأشهر الأصناف: {menu_hint}." if menu_hint else ""

    return f"""أنت محمد، موظف مصري حقيقي بترد على تليفون مطعم "{name}".
اتكلم زي إنسان طبيعي — مش بوت ولا سكريبت.

# شغلك
بتاخد طلب استلام/توصيل، حجز ترابيزة، وبتسمع شكاوى. {delivery_status}{menu_line}
{branches_line}

# قاعدة #١: استدعي الـ tool فوراً
أي معلومة العميل يقولها (نية، اسم، صنف، عنوان، ميعاد، شكوى) → نده الـ tool المناسب في نفس الـ turn قبل ما ترد بأي كلمة. لو ما نديتش، البيانات ضايعة. ممكن تنده أكتر من tool في turn واحد.

# طريقة الكلام
- جملة واحدة قصيرة (أقصى ١٥ كلمة).
- ابدأ بـ "تمام" / "ماشي" / "حاضر" / "اه".
- "يا فندم" مرة-مرتين في المكالمة كلها — مش كل جملة.
- متكررش كلام العميل، نوّع الـ ack.
- مش فاهم؟ "معلش، ممكن تعيدها؟" — متخمّنش.
- ممنوع: "بكل سرور" / "يسعدني" / "أتشرف" / "زبون".
- لو سألك انت مين: "أنا محمد، شغال هنا".

# الأدوات
- `set_intent` (takeaway/delivery/reservation/complaint) — أول ما تفهم النية.
- `set_name` — أول ما يقول اسمه.
- `update_order` (يضيف للطلب)، `clear_order` (لو قال هبدأ من الأول).
- `set_delivery_info`، `set_reservation_info`، `set_complaint`، `get_menu`.
- `confirm_and_submit` — بعد readback الكامل وموافقة العميل.
- `end_call(reason)` — بعد ما تقول جملة الوداع. الـ tool هيستنى كلامك يخلص ويقفل المكالمة.

# [CALL_STATE]
في أول كل turn هتلاقي رسالة سستم بادئة بـ `[CALL_STATE]` فيها `user="..."` وكل البيانات اللي اتجمعت (intent, order, name, address, time, guests…). دي بقت **المرجع الوحيد** لحالة المكالمة — اقراها قبل ما ترد.

# قواعد مهمة
1. **اللي في [CALL_STATE] ممنوع تسأله تاني.**
2. سؤال واحد في المرة.
3. الأولوية: الطلب → العنوان (للتوصيل) → الاسم.
4. **مفيش رقم تليفون خالص — متطلبش رقم موبايل من العميل ولا تسأل عنه.**
5. متخترعش أسعار ولا أصناف — الـ tools بترجّع كل ده.
6. لو العميل غيّر رأيه، نده نفس الـ tool أو الـ clear_X المناسب.
7. **قبل `confirm_and_submit`**: اقرا التفاصيل من [CALL_STATE] في جملة طبيعية وانتظر العميل يقول "أيوه/تمام". اذكر الأصناف والكميات والعنوان (للتوصيل) أو الميعاد والعدد (للحجز) والإجمالي. لو قال "لا" → صحّح بالـ tool المناسب وارجع اقرا تاني.

8. **بعد التأكيد** (✅ في STATE): قول جملة وداع قصيرة بإسم العميل + نده `end_call` فوراً. متسألش "في حاجة تانية" — الـ end_call هيقفل المكالمة. مثال: "تمام يا أحمد، الطلب اتسجل هيوصلك قريب. نورتنا!" ثم `end_call('order_completed')`."""


class RestaurantAgent(Agent):
    """Single agent class — no flow handoffs, no per-flow subclasses."""

    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg
        super().__init__(
            instructions=_build_persona_prompt(cfg),
            tools=[
                restaurant_tools.set_intent,
                restaurant_tools.set_name,
                restaurant_tools.update_order,
                restaurant_tools.clear_order,
                restaurant_tools.set_delivery_info,
                restaurant_tools.set_reservation_info,
                restaurant_tools.set_complaint,
                restaurant_tools.get_menu,
                restaurant_tools.confirm_and_submit,
                restaurant_tools.end_call,
            ],
        )

    async def on_enter(self) -> None:
        ud: UserData = self.session.userdata
        opening = self._opening_line()
        ud.last_agent_message = opening
        await self.session.say(opening, add_to_chat_ctx=True)

    def _opening_line(self) -> str:
        cfg = self.cfg
        if cfg.degraded_mode:
            from agent import _degraded_user_message
            return _degraded_user_message(cfg)
        if not cfg.is_open:
            return f"أهلاً بيك! معاك {cfg.name}، للأسف إحنا مقفولين دلوقتي."
        return f"أهلاً بيك! معاك {cfg.name}، أقدر أساعدك في إيه؟"

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        from agent import (
            MAX_TURNS_PER_SESSION,
            TURN_CHAT_CTX_MAX_ITEMS,
            _emit_event,
            _increment_turn_count,
        )

        ud: UserData = self.session.userdata
        text = self._extract_text(new_message)
        normalized = normalize_ar(text)

        if not normalized:
            logger.info("call=%s | empty transcript ignored", ud.call_id)
            raise StopResponse()

        ud.last_user_message = text[:280]

        turn_num = _increment_turn_count(ud.call_id or "")
        _emit_event("turn.received", call_id=ud.call_id, turn=turn_num)

        if turn_num >= MAX_TURNS_PER_SESSION:
            logger.warning(
                "call=%s | turn cap reached | turns=%d", ud.call_id, turn_num
            )
            await self.session.say(
                "معلش يا فندم المكالمة طولت شوية. كلمنا تاني في أي وقت، نورتنا!",
                add_to_chat_ctx=True,
            )
            raise StopResponse()

        self._strip_state_messages(turn_ctx)
        self._trim_chat_ctx(turn_ctx, max_items=TURN_CHAT_CTX_MAX_ITEMS)
        turn_ctx.add_message(role="system", content=self._build_state_snapshot())

    def _build_state_snapshot(self) -> str:
        ud: UserData = self.session.userdata
        cfg = self.cfg

        intent = (ud.active_flow or "").strip()
        parts: list[str] = [_STATE_PROMPT_MARKER]
        if ud.last_user_message:
            parts.append(f'user="{ud.last_user_message}"')
        parts.append(f"intent={intent or '?'}")
        if ud.order:
            order_str = "، ".join(ud.order)
            if ud.order_total:
                parts.append(f"order=[{order_str}] total={money2ar(ud.order_total)}ج")
            else:
                parts.append(f"order=[{order_str}]")
        if ud.customer_name:
            parts.append(f"name={ud.customer_name}")
        if ud.delivery_address:
            parts.append(f"address={ud.delivery_address}")
        if ud.delivery_landmark:
            parts.append(f"landmark={ud.delivery_landmark}")
        if ud.reservation_time:
            parts.append(f"time={ud.reservation_time}")
        if ud.guests_count:
            parts.append(f"guests={ud.guests_count}")
        if ud.selected_branch:
            parts.append(f"branch={ud.selected_branch}")
        elif intent == "reservation" and len(cfg.branches) > 1:
            parts.append(f"branch=? (متاح: {cfg.branch_names()})")
        if ud.complaint_text:
            parts.append(f'complaint="{ud.complaint_text[:80]}"')
        if ud.order_confirmed:
            parts.append(f"DONE order_id={ud.order_id or '-'}")
        if ud.reservation_confirmed:
            parts.append(f"DONE reservation_id={ud.reservation_id or '-'}")
        if ud.complaint_logged:
            parts.append("DONE complaint_logged")
        return " | ".join(parts)

    def _strip_state_messages(self, ctx: llm.ChatContext) -> None:
        """Remove all prior [CALL_STATE] system messages so the prompt doesn't bloat."""
        kept: list[Any] = []
        for item in ctx.items:
            if getattr(item, "role", "") == "system":
                content = self._item_text(item)
                if content.startswith(_STATE_PROMPT_MARKER):
                    continue
            kept.append(item)
        ctx.items.clear()
        ctx.items.extend(kept)

    def _trim_chat_ctx(self, ctx: llm.ChatContext, *, max_items: int) -> None:
        """Keep all system messages; trim non-system history to N most recent items.

        FunctionCall and FunctionCallOutput items must stay paired by call_id —
        if we drop the call but keep the output, the LLM provider warns
        "function output missing the corresponding function call, ignoring".
        After picking the keep window, expand it to include any partner items.
        """
        non_system_indices = [
            idx for idx, it in enumerate(ctx.items)
            if getattr(it, "role", "") != "system"
        ]
        if len(non_system_indices) <= max_items:
            return
        keep_idx = set(non_system_indices[-max_items:])

        # Collect call_ids appearing in the keep window, then add any
        # FunctionCall/FunctionCallOutput sharing those ids regardless of
        # position. This keeps every tool exchange whole.
        kept_call_ids: set[str] = set()
        for idx in keep_idx:
            item = ctx.items[idx]
            cid = getattr(item, "call_id", None)
            if cid:
                kept_call_ids.add(cid)
        for idx, item in enumerate(ctx.items):
            cid = getattr(item, "call_id", None)
            if cid and cid in kept_call_ids:
                keep_idx.add(idx)

        kept: list[Any] = []
        for idx, item in enumerate(ctx.items):
            if getattr(item, "role", "") == "system" or idx in keep_idx:
                kept.append(item)
        ctx.items.clear()
        ctx.items.extend(kept)

    @staticmethod
    def _extract_text(message: llm.ChatMessage) -> str:
        return RestaurantAgent._item_text(message)

    @staticmethod
    def _item_text(item: Any) -> str:
        # ChatMessage.text_content is a property that joins string parts and
        # filters out non-text content (images, audio). Prefer it when present.
        text = getattr(item, "text_content", None)
        if text is not None:
            return str(text).strip()
        content = getattr(item, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(c for c in content if isinstance(c, str)).strip()
        return str(content or "").strip()


__all__ = ["RestaurantAgent"]
