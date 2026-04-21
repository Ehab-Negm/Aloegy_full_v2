"""Greeter flow — routes customer to the right agent."""
from __future__ import annotations

import logging
import re
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safely, build_instructions, get_menu, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Greeter(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from agent import (
            _degraded_user_message,
            _delivery_unavailable_user_message,
        )
        self.cfg = cfg

        if cfg.degraded_mode:
            core = (
                "النظام فيه تحديث بسيط دلوقتي. قول للعميل كده بشكل طبيعي "
                "واطلب منه يقولك طلبه أو يتصل على المطعم.\n"
                "متتكلمش عن المنيو ولا المواعيد — مش عارفهم دلوقتي."
            )
        elif not cfg.is_open:
            reason = cfg.closed_reason or "خارج المواعيد"
            core = (
                f"المطعم مقفول حالياً ({reason}).\n"
                f"قول للعميل إحنا مقفولين، والمواعيد: {cfg.hours_text()}.\n"
                f"لو سأل عن أي حاجة تانية قوله يتصل على {cfg.phone}."
            )
        else:
            delivery_line = "- طلب توصيل → to_delivery\n" if cfg.delivery_enabled else ""
            core = (
                "شغلك إنك تفهم العميل عايز إيه وتحوّله على طول للمكان الصح.\n"
                "لو سألك \"أنت مين؟\" أو \"إيه الأخبار؟\"، رد قصير طبيعي وبعدين اسأله يقدر يساعده في إيه.\n"
                "لو الكلام مش واضح أو الـ STT ملخبطة → resolve_request.\n\n"
                "التحويلات:\n"
                "- طلب أكل / استلام → to_takeaway\n"
                f"{delivery_line}"
                "- حجز ترابيزة → to_reservation\n"
                "- شكوى أو مشكلة → to_complaint\n"
                "- سؤال عن المنيو → get_menu\n\n"
                f"أصناف متاحة: {cfg.menu_names()}\n\n"
                "مهم: متاخدش طلبات ولا تحسب حاجة — أنت بس بتوجّه."
            )

        super().__init__(
            instructions=build_instructions(cfg.name, core),
            tools=[get_menu, update_name, update_phone],
        )
        self._delivery_enabled = cfg.delivery_enabled
        self._opening = (
            _degraded_user_message(cfg)
            if cfg.degraded_mode else
            f"أهلاً بيك! معاك {cfg.name}، أقدر أساعدك في إيه؟"
            if cfg.is_open else
            f"أهلاً بيك! معاك {cfg.name}، للأسف إحنا مقفولين دلوقتي."
        )

    @staticmethod
    def _extract_inline_intro_name(user_text: str) -> str | None:
        from nlp.arabic import AR_DIGITS, normalize_ar

        cleaned = re.sub(r"[.!؟،,]+", " ", (user_text or "").translate(AR_DIGITS)).strip()
        if not cleaned:
            return None

        match = re.search(
            r"(?:^|\s)(?:انا|أنا|اسمي|اسمى|الاسم|معاك|معاكي)\s+(.+)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if not match:
            return None

        stop_words = {
            "عايز", "عاوزه", "عاوز", "عاوزة", "محتاج", "هطلب", "اطلب", "أطلب",
            "طلب", "اوردر", "أوردر", "توصيل", "تيك", "تيكأواي", "تيكواي", "استلام",
            "احجز", "أحجز", "حجز", "شكوى", "مشكلة", "محتاج", "عايزين", "لو", "بس",
            "رقمي", "ورقمي", "رقم", "الموبايل", "موبايلي", "موبايل", "التليفون",
        }
        ignored_tokens = {"يا", "فندم", "باشا", "استاذ", "أستاذ"}
        candidate_tokens: list[str] = []

        for raw_token in match.group(1).split():
            token = raw_token.strip(" .،,؟!")
            if not token:
                continue

            normalized_token = normalize_ar(token)
            stripped_token = normalize_ar(token.lstrip("و"))
            if re.search(r"\d", token):
                break
            if normalized_token in ignored_tokens:
                if candidate_tokens:
                    break
                continue
            if normalized_token in stop_words or stripped_token in stop_words:
                break

            candidate_tokens.append(token)
            if len(candidate_tokens) >= 3:
                break

        if not candidate_tokens:
            return None
        return " ".join(candidate_tokens).strip() or None

    def _capture_prefill_contact(self, user_text: str) -> None:
        from agent import _extract_name_candidate
        from nlp.phone_extract import phone_digits_only as _phone_digits_only, validate_phone

        ud = self.session.userdata
        if not ud.customer_name:
            candidate = _extract_name_candidate(user_text) or self._extract_inline_intro_name(user_text)
            if candidate:
                ud.customer_name = candidate
                logger.info("call=%s | greeter prefill name=%s", ud.call_id, candidate)

        if not ud.customer_phone:
            digits = _phone_digits_only(user_text)
            cleaned_phone = validate_phone(digits) if digits else None
            if cleaned_phone:
                ud.customer_phone = cleaned_phone
                ud.pending_phone_digits = ""
                logger.info("call=%s | greeter prefill phone", ud.call_id)

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        from agent import _greeter_turn_decision
        self._capture_prefill_contact(user_text)
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
    async def to_reservation(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لما العميل يريد حجز ترابيزة."""
        async def _impl() -> tuple[Agent, str]:
            return await self._transfer("reservation", context)

        return await _run_tool_safely("to_reservation", context, _impl)

    @function_tool()
    async def to_takeaway(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لما العميل يريد ييجي ياخد طلبه من المطعم."""
        async def _impl() -> tuple[Agent, str]:
            return await self._transfer("takeaway", context)

        return await _run_tool_safely("to_takeaway", context, _impl)

    @function_tool()
    async def to_delivery(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لما العميل يريد توصيل الطلب لعنوانه."""
        async def _impl() -> str | tuple[Agent, str]:
            from agent import _delivery_unavailable_user_message
            if not self._delivery_enabled and not self.cfg.degraded_mode:
                return _delivery_unavailable_user_message(self.cfg)
            return await self._transfer("delivery", context)

        return await _run_tool_safely("to_delivery", context, _impl)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لما العميل عنده شكوى أو مشكلة."""
        async def _impl() -> tuple[Agent, str]:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def resolve_request(
        self,
        user_text: Annotated[str, Field(description="آخر كلام واضح قاله العميل")],
        context: RunContext_T,
    ) -> str | tuple[Agent, str]:
        async def _impl() -> str | tuple[Agent, str]:
            from agent import _delivery_unavailable_user_message, _guess_request_intent
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
                return "هتيجي تاخده ولا نوصّلهولك؟"
            return "مش متأكد فهمتك — تحب تطلب أكل، تحجز، ولا حاجة تانية؟"

        return await _run_tool_safely("resolve_request", context, _impl)
