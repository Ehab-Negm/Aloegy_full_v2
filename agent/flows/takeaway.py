"""Takeaway flow — handles pickup orders."""
from __future__ import annotations

import logging
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safely, get_menu, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Takeaway(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from utils.money import num2ar
        self.cfg = cfg

        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"
        super().__init__(
            instructions=(
                f"أنت شغال في '{cfg.name}' — بتاخد طلبات الاستلام على التليفون.\n"
                f"وقت الاستلام: {num2ar(cfg.wait_minutes)} دقيقة.\n\n"
                "اتكلم زي بني آدم طبيعي، مش بتقرأ من ورقة. نوّع في كلامك.\n\n"
                "المطلوب منك بالترتيب:\n"
                "- اسمع الطلب واستدعي update_order (هو اللي بيتحقق من المنيو — أنت متعرفش الأسعار).\n"
                "- اقترح إضافة بسيطة مرة واحدة بس.\n"
                "- اسأل لو في طلب خاص.\n"
                "- خُد الاسم والموبايل.\n"
                "- لخّص الطلب ولو أكّد استدعي confirm_order.\n\n"
                "لو العميل قالك معلومة من خطوة جاية سجّلها وكمّل عادي.\n"
                "لو سألك سؤال عادي رد عليه طبيعي وبعدين ارجع.\n"
                "لو سأل عن المنيو → get_menu | لو عنده شكوى → to_complaint\n"
                "متقولش للعميل صنف موجود أو لأ من عندك — ده شغل update_order.\n"
                "'أيوه' أو 'تمام' لوحدها مش موافقة على إضافة إلا لو ذكر الصنف.\n"
                "متكررش التأكيد أكتر من مرة."
            ),
            tools=[
                update_name,
                update_phone,
                to_greeter,
                get_menu,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        from agent import (
            _ask_name,
            _is_positive_confirmation,
            _is_takeaway_ready_for_confirmation,
            _looks_empty_answer,
        )
        ud = self.session.userdata
        context = self._tool_context()

        if ud.pending_upsell_item:
            await self._handle_pending_upsell(
                user_text,
                flow_name="takeaway",
                post_upsell_prompt=_ask_name,
            )

        if ud.order and not ud.customer_name and _looks_empty_answer(user_text):
            logger.info("call=%s | takeaway optional_empty_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_special_requests(requests=user_text, context=context))

        if _is_takeaway_ready_for_confirmation(ud) and _is_positive_confirmation(user_text):
            logger.info("call=%s | takeaway confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_order(context=context), critical=True)

        return False

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | tuple[Agent, str]:
        """يُستدعى لو العميل عنده شكوى."""
        async def _impl() -> tuple[Agent, str]:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def update_order(
        self,
        items: Annotated[
            list[str],
            Field(description="القايمة الكاملة للطلب مع الكميات مثل ['كشري كبير × 2', 'بيبسي']"),
        ],
        context: RunContext_T,
    ) -> str:
        async def _impl() -> str:
            return self._process_order_update(
                items,
                context,
                flow_name="takeaway",
            )

        return await _run_tool_safely("update_order", context, _impl)

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير أو مفيش")],
        context: RunContext_T,
    ) -> str:
        async def _impl() -> str:
            from agent import _ask_name, _clear_pending_upsell, _join_user_phrases, _looks_empty_answer
            from utils.voice import _voice_safe_text
            _clear_pending_upsell(context.userdata)
            if _looks_empty_answer(requests):
                context.userdata.special_requests = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش طلب خاص", _ask_name()),
                    max_chars=180,
                )
            context.userdata.special_requests = requests.strip()
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", _ask_name()),
                max_chars=180,
            )

        return await _run_tool_safely("update_special_requests", context, _impl)

    @function_tool()
    async def confirm_order(self, context: RunContext_T) -> str:
        async def _impl() -> str:
            from agent import (
                _backend_failure_user_message,
                _backend_queued_user_message,
                _can_attempt_backend_write,
                _emit_event,
                _order_validation_user_message,
                _takeaway_next_missing_slot,
                submit_takeaway,
            )
            from utils.money import num2ar
            from utils.voice import _voice_safe_text
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
            _emit_event("order.confirmed", call_id=ud.call_id, flow="takeaway", order_id=ud.order_id)
            wait = result.get("estimated_time", self.cfg.wait_minutes)
            return f"تمام يا {ud.customer_name}، الطلب اتسجل. هيبقى جاهز خلال {num2ar(wait)} دقيقة."

        return await _run_tool_safely("confirm_order", context, _impl)
