"""Delivery flow — handles delivery orders."""
from __future__ import annotations

import logging
import re
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safely, build_instructions, get_menu, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Delivery(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from utils.money import num2ar
        self.cfg = cfg
        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"

        zones_info = f" | مناطق: {cfg.delivery_zones_text()}" if cfg.delivery_zones else ""

        core = (
            f"بتاخد طلبات توصيل. {cfg.delivery_info_text()}{zones_info}\n\n"
            "الخطوات بالترتيب:\n"
            "1. اسمع الطلب → update_order (هو اللي بيتحقق من المنيو والأسعار).\n"
            "2. اقترح إضافة بسيطة مرة واحدة بس.\n"
            "3. اسأل لو في طلب خاص في التحضير.\n"
            "4. خُد العنوان → update_delivery_address فوراً.\n"
            "5. لو العنوان مش واضح، اسأل عن علامة مميزة (محل، مسجد، صيدلية…).\n"
            "6. خُد الاسم والموبايل.\n"
            "7. لخّص الطلب بإيجاز، ولو أكّد → confirm_delivery.\n\n"
            "قواعد:\n"
            "- لو قال معلومة من خطوة جاية، سجّلها فوراً وكمّل.\n"
            "- \"أيوه\" أو \"تمام\" لوحدها مش موافقة على إضافة إلا لو ذكر اسم الصنف.\n"
            "- متقولش من عندك أي صنف موجود أو لأ — update_order هو اللي بيقرر.\n"
            "- سؤال عن المنيو → get_menu | شكوى → to_complaint."
        )
        super().__init__(
            instructions=build_instructions(cfg.name, core),
            tools=[
                update_name,
                update_phone,
                to_greeter,
                get_menu,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        from agent import (
            _ask_address,
            _is_delivery_ready_for_confirmation,
            _is_positive_confirmation,
            _looks_empty_answer,
        )
        ud = self.session.userdata
        context = self._tool_context()

        if ud.pending_upsell_item:
            await self._handle_pending_upsell(
                user_text,
                flow_name="delivery",
                post_upsell_prompt=_ask_address,
            )

        if ud.order and not ud.delivery_address and _looks_empty_answer(user_text):
            logger.info("call=%s | delivery optional_empty_intercepted | step=special_requests | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_special_requests(requests=user_text, context=context))

        if ud.delivery_address and not ud.landmark_asked and _looks_empty_answer(user_text):
            logger.info("call=%s | delivery optional_empty_intercepted | step=landmark | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_delivery_landmark(landmark=user_text, context=context))

        if _is_delivery_ready_for_confirmation(ud) and _is_positive_confirmation(user_text):
            logger.info("call=%s | delivery confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_delivery(context=context), critical=True)

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
        async def _impl() -> str:
            return self._process_order_update(
                items,
                context,
                flow_name="delivery",
                min_order_total=self.cfg.min_order,
            )

        return await _run_tool_safely("update_order", context, _impl)

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر طلبات خاصة."""
        async def _impl() -> str:
            from agent import _ask_address, _clear_pending_upsell, _join_user_phrases, _looks_empty_answer
            from utils.voice import _voice_safe_text
            _clear_pending_upsell(context.userdata)
            if _looks_empty_answer(requests):
                context.userdata.special_requests = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش طلب خاص", _ask_address()),
                    max_chars=180,
                )
            context.userdata.special_requests = requests.strip()
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", _ask_address()),
                max_chars=180,
            )

        return await _run_tool_safely("update_special_requests", context, _impl)

    @function_tool()
    async def update_delivery_address(
        self,
        address: Annotated[str, Field(description="العنوان كامل: الشارع والرقم والمنطقة")],
        zone: Annotated[str, Field(description="المنطقة أو الحي — لو مش واضحة كرر اسم المنطقة من العنوان")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يقول عنوانه — استدعيه فوراً حتى لو المنطقة مش واضحة."""
        async def _impl() -> str:
            from agent import _address_seems_specific, _ask_name, _extract_zone_from_address, _join_user_phrases
            from nlp.arabic import normalize_ar as _normalize_ar
            from utils.voice import _voice_safe_text
            resolved_zone = zone
            if not resolved_zone or not resolved_zone.strip():
                resolved_zone = _extract_zone_from_address(address, self.cfg.delivery_zones)
            if self.cfg.delivery_zones:
                zone_norm = _normalize_ar(resolved_zone)
                covered = any(
                    zone_norm in _normalize_ar(z) or _normalize_ar(z) in zone_norm
                    for z in self.cfg.delivery_zones
                )
                if not covered:
                    return _voice_safe_text(
                        f"للأسف مش بنوصل {resolved_zone} دلوقتي. "
                        f"المتاح {self.cfg.delivery_zones_text()}. "
                        "تحب تيجي تاخده من عندنا؟"
                    )

            context.userdata.delivery_address = address.strip()
            context.userdata.delivery_zone = resolved_zone.strip()
            context.userdata.delivery_landmark = None
            logger.info(
                "call=%s | delivery_address=%s zone=%s",
                context.userdata.call_id,
                address,
                resolved_zone,
            )
            if _address_seems_specific(address):
                context.userdata.landmark_asked = True
                return _voice_safe_text(
                    _join_user_phrases(
                        f"تمام يا فندم، سجلت العنوان: {context.userdata.delivery_address}",
                        _ask_name(),
                    ),
                    max_chars=220,
                    critical=True,
                )
            context.userdata.landmark_asked = False
            return _voice_safe_text(
                _join_user_phrases(
                    f"تمام يا فندم، سجلت العنوان: {context.userdata.delivery_address}",
                    "في علامة مميزة قريبة منك؟",
                ),
                max_chars=220,
                critical=True,
            )

        return await _run_tool_safely("update_delivery_address", context, _impl)

    @function_tool()
    async def update_delivery_landmark(
        self,
        landmark: Annotated[str, Field(description="علامة مميزة قريبة من العنوان")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر علامة مميزة."""
        async def _impl() -> str:
            from agent import _ack, _ask_name, _ask_phone, _extract_name_candidate, _join_user_phrases, _looks_empty_answer
            from utils.voice import _voice_safe_text
            context.userdata.landmark_asked = True
            if _looks_empty_answer(landmark):
                context.userdata.delivery_landmark = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش علامة مميزة", _ask_name()),
                    max_chars=180,
                )
            explicit_name_reply = re.match(r"^\s*(?:انا|أنا|اسمي|اسمى|الاسم|اسم|معاك|معاكي)\b", landmark, flags=re.IGNORECASE)
            if explicit_name_reply and not context.userdata.customer_name:
                name_candidate = _extract_name_candidate(landmark)
                if name_candidate:
                    context.userdata.delivery_landmark = None
                    context.userdata.customer_name = name_candidate
                    return _voice_safe_text(f"{_ack()} يا {name_candidate}. {_ask_phone()}", max_chars=180)
            context.userdata.delivery_landmark = landmark.strip()
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت العلامة المميزة", _ask_name()),
                max_chars=180,
            )

        return await _run_tool_safely("update_delivery_landmark", context, _impl)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لو العميل عنده شكوى."""
        async def _impl() -> tuple[Agent, str]:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def confirm_delivery(self, context: RunContext_T) -> str:
        """يُستدعى بعد تأكيد الطلب والعنوان والاسم والرقم كاملاً."""
        async def _impl() -> str:
            from agent import (
                _backend_failure_user_message,
                _backend_queued_user_message,
                _can_attempt_backend_write,
                _delivery_next_missing_slot,
                _emit_event,
                _order_validation_user_message,
                submit_delivery,
            )
            from utils.money import money2ar, num2ar
            from utils.voice import _voice_safe_text
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

            ud.order_id = result.get("order_id", "")
            ud.order_confirmed = True
            _emit_event("order.confirmed", call_id=ud.call_id, flow="delivery", order_id=ud.order_id)
            wait = result.get("estimated_time", self.cfg.delivery_minutes)

            msg = f"تمام يا {ud.customer_name}، الطلب اتسجل للتوصيل."
            if self.cfg.delivery_fee > 0:
                msg += f" رسوم التوصيل {money2ar(self.cfg.delivery_fee)} جنيه."
            msg += f" هيوصلك خلال {num2ar(wait)} دقيقة."
            return msg

        return await _run_tool_safely("confirm_delivery", context, _impl)
