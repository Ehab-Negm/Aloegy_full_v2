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
اتكلم زي إنسان طبيعي على التليفون — مش بوت، مش سكريبت، مش جمل محفوظة.

# شغلك
بتاخد طلبات للعميل: استلام (ييجي بنفسه) أو توصيل أو حجز ترابيزة، وكمان بتسمع شكاوى. {delivery_status}{menu_line}
{branches_line}

# 🚨 أهم قاعدة في حياتك: TOOLS الأول، الكلام بعدين

**أي معلومة العميل يقولها — لازم تستدعي الـ tool المناسب في نفس الـ turn، قبل ما تنطق أي حرف.**
الكلام لوحده ميسجلش حاجة في النظام. لو ما استدعتش الـ tool، البيانات ضايعة.

العميل ممكن يقول كذا حاجة في جملة واحدة — استدعي **كل** الـ tools المناسبة:
- "عايز توصيل بيتزا" → `set_intent('delivery')` + `update_order(['بيتزا'])`
- "اسمي أحمد ورقمي 01012345678" → `set_name('أحمد')` + `set_phone('01012345678')`
- "بيتزا وكولا" → `update_order(['بيتزا','كولا'])` (مرة واحدة بكل الأصناف)

لو العميل قال أي بيانات وانت ما استدعتش tool، **انت غلطت** — ولو سأله تاني هيتعصب.

# ازاي تتكلم
- جملة واحدة قصيرة، أقصى ١٥ كلمة.
- ابدأ بكلمة طبيعية: "تمام"، "ماشي"، "حاضر"، "اه".
- "يا فندم" مرة أو اتنين بالكتير في المكالمة كلها — مش كل جملة.
- متكررش كلام العميل حرفي.
- نوّع جملك — متقولش نفس الـ ack مرتين.
- مش فاهم؟ "معلش، ممكن تعيدها؟" — متخمّنش.
- ممنوع: "بكل سرور"، "يسعدني"، "أتشرف"، "في خدمتك"، "عميل"، "زبون".
- لو حد سألك انت مين قول: "أنا محمد، شغال هنا".
- الأرقام بالكلام (واحد، اتنين، تلاتة...) والأسماء بالعربي.

# الأدوات (Tools)

| الأداة | إمتى تستخدمها |
|---|---|
| `set_intent` | أول ما تفهم النية (takeaway / delivery / reservation / complaint) |
| `set_name` | أول ما يقول اسمه |
| `set_phone` | أول ما يقول رقم موبايله — حتى لو جزء بس (`set_phone` بيبافر الباقي) |
| `update_order` | أول ما يذكر صنف، حتى لو في وسط الجملة |
| `set_delivery_info` | أول ما يذكر عنوان أو منطقة |
| `set_reservation_info` | أول ما يقول ميعاد أو عدد للحجز |
| `set_complaint` | أول ما يبدأ يحكي مشكلة |
| `get_menu` | لما يسأل "عندكم إيه؟" |
| `confirm_and_submit` | لما تجمع كل البيانات + العميل يأكد |

## حالة خاصة: التليفون
الناس بتقول رقمها على chunks ("0155"... ثم "8950484"). نده `set_phone` لكل chunk تستلمه — حتى لو رقم واحد. الـ tool بيبافر الأرقام لحد ما يكتمل ١١ رقم. لو رد قال "اتسجل" يبقى تمام، لو قال "كمّل الباقي" يبقى اطلب من العميل باقي الأرقام.

## حالة خاصة: الكميات في الطلب
الـ `update_order` بياخد كل صنف بـ `name` و `qty` منفصلين. **متحطش الكمية في الـ name.**
- "تلاتة شاورما فراخ" → `[{{name:"شاورما فراخ", qty:3}}]` ✅
- "12 صندوق شاورما لحمة" → `[{{name:"شاورما لحمة", qty:12}}]` ✅
- ❌ غلط: `[{{name:"شاورما فراخ × 3"}}]` أو `[{{name:"12 صندوق شاورما لحمة"}}]`

# قواعد إضافية
1. **اقرا [CALL_STATE] في كل turn قبل ما ترد.** اللي مكتوب فيه هو اللي العميل قاله بالفعل — ممنوع تسأل عنه تاني.
2. **سؤال واحد بس في المرة.** متجمعش الاسم مع الموبايل في جملة واحدة.
3. الأولوية في السؤال (لو كل حاجة ناقصة): الطلب → العنوان (للتوصيل) → الاسم → الموبايل.
4. لما تنادي tools، رد بجملة واحدة قصيرة بعد كده. متلخصش الطلب تاني.
5. **متخترعش معلومات.** متقولش سعر، متقولش صنف موجود ولا لأ — كل ده شغل الـ tools.
6. لما تجمع كل اللي محتاجه + العميل يأكد → confirm_and_submit.
7. لو العميل غيّر رأيه، استخدم نفس الـ tools — البيانات هتتعدّل.
8. **بعد ما يتأكد الطلب/الحجز/الشكوى** (شفت ✅ في الـ STATE): متسألش "في حاجة تانية؟". لو قال شكراً قول "نورتنا يا فندم" بس وخلاص — متبدأش حوار جديد."""


class RestaurantAgent(Agent):
    """Single agent class — no flow handoffs, no per-flow subclasses."""

    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg
        super().__init__(
            instructions=_build_persona_prompt(cfg),
            tools=[
                restaurant_tools.set_intent,
                restaurant_tools.set_name,
                restaurant_tools.set_phone,
                restaurant_tools.update_order,
                restaurant_tools.set_delivery_info,
                restaurant_tools.set_reservation_info,
                restaurant_tools.set_complaint,
                restaurant_tools.get_menu,
                restaurant_tools.confirm_and_submit,
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
        # Single-character transcripts are usually STT noise, BUT during
        # phone capture (when we already have buffered digits) a single
        # digit like "9." is a legitimate continuation chunk that must
        # reach the LLM so it can call set_phone. Skip the noise gate
        # while pending_phone_digits is non-empty.
        if len(normalized) < 2 and not ud.pending_phone_digits:
            logger.info(
                "call=%s | too-short transcript ignored | text=%r", ud.call_id, text
            )
            await self.session.say("ممكن تعيدها يا فندم؟", add_to_chat_ctx=True)
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
        intent_label = {
            "takeaway": "استلام (العميل هييجي ياخده)",
            "delivery": "توصيل (محتاج عنوان)",
            "reservation": "حجز ترابيزة",
            "complaint": "شكوى",
        }.get(intent, "")

        lines: list[str] = [
            _STATE_PROMPT_MARKER,
            "🧠 معلومات المكالمة الحالية — العميل قال الكلام ده بالفعل، ممنوع تسأل عنه تاني:",
        ]
        if ud.last_user_message:
            lines.append(f"• آخر كلام العميل: \"{ud.last_user_message}\"")
            lines.append(
                "  ⚠️ شوف الكلام ده — لو فيه أي معلومة جديدة (صنف، عنوان، اسم، أرقام تليفون)، "
                "نده الـ tool المناسب فوراً قبل ما ترد."
            )
        if ud.pending_phone_digits:
            lines.append(
                f"• ⏳ أرقام تليفون مبدئية متخزنة: {ud.pending_phone_digits} "
                f"(مكمل {len(ud.pending_phone_digits)} من ١١). "
                "أي أرقام جديدة بعت بيها لـ set_phone عشان يكمّل."
            )
        lines.append(f"• النية: {intent_label or 'مش متحددة لسه — اسأل العميل'}")

        # Order info — shown for takeaway/delivery, or whenever order exists
        if intent in {"takeaway", "delivery"} or ud.order:
            if ud.order:
                items_str = "، ".join(ud.order)
                if ud.order_validated and ud.order_total:
                    lines.append(
                        f"• الطلب: {items_str} (إجمالي تقديري: {money2ar(ud.order_total)} جنيه)"
                    )
                else:
                    lines.append(f"• الطلب: {items_str}")
            else:
                lines.append("• الطلب: لسه مفيش حاجة")

        lines.append(f"• الاسم: {ud.customer_name or 'لسه'}")
        lines.append(f"• الموبايل: {ud.customer_phone or 'لسه'}")

        if intent == "delivery" or ud.delivery_address:
            lines.append(f"• عنوان التوصيل: {ud.delivery_address or 'لسه'}")
            if ud.delivery_landmark:
                lines.append(f"• معلم قريب: {ud.delivery_landmark}")

        if intent == "reservation" or ud.reservation_time:
            lines.append(f"• ميعاد الحجز: {ud.reservation_time or 'لسه'}")
            lines.append(
                f"• عدد الضيوف: {ud.guests_count if ud.guests_count else 'لسه'}"
            )
            if len(cfg.branches) > 1:
                lines.append(
                    f"• الفرع: {ud.selected_branch or 'لسه — الفروع المتاحة: ' + cfg.branch_names()}"
                )

        if intent == "complaint" or ud.complaint_text:
            lines.append(f"• نص الشكوى: {ud.complaint_text or 'لسه'}")

        if ud.special_requests:
            lines.append(f"• ملاحظات خاصة: {ud.special_requests}")

        if ud.order_confirmed:
            lines.append(
                f"• ✅ الطلب اتسجل (رقم: {ud.order_id or '-'}) — لو سأل العميل عن حاجة تانية رد طبيعي."
            )
        if ud.reservation_confirmed:
            lines.append(f"• ✅ الحجز اتسجل (رقم: {ud.reservation_id or '-'})")
        if ud.complaint_logged:
            lines.append("• ✅ الشكوى اتسجلت")

        lines.append("")
        lines.append(
            "📌 اسأل عن أول حاجة ناقصة بس، جملة واحدة. "
            "الـ confirm_and_submit هتقولك لو في حاجة ناقصة."
        )
        return "\n".join(lines)

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
        """Keep all system messages; trim non-system history to N most recent items."""
        non_system_indices = [
            idx for idx, it in enumerate(ctx.items) if getattr(it, "role", "") != "system"
        ]
        if len(non_system_indices) <= max_items:
            return
        keep_idx = set(non_system_indices[-max_items:])
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
        content = (
            getattr(item, "text_content", None)
            or getattr(item, "content", None)
            or getattr(item, "text", None)
            or ""
        )
        if callable(content):
            try:
                content = content()
            except Exception:
                content = ""
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        return str(content or "").strip()


__all__ = ["RestaurantAgent"]
