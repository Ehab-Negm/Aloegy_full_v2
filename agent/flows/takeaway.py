"""Takeaway flow — handles pickup orders."""
from __future__ import annotations

import logging
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safe_speak, _run_tool_safely, build_instructions, get_menu, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Takeaway(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from utils.money import num2ar
        self.cfg = cfg

        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"
        core = (
            "إنت موظف مطعم بياخد طلب استلام على التليفون. اتكلم زي موظف "
            "بشري ودود — لهجة مصرية طبيعية، مش جمل محفوظة.\n\n"
            f"وقت التحضير: {num2ar(cfg.wait_minutes)} دقيقة.\n\n"
            "اسمع العميل واستخدم الـ tools علشان تسجل اللي بيقوله:\n"
            "- update_order للأكل (ابعت القائمة كاملة كل مرة)\n"
            "- update_name للاسم، update_phone للموبايل\n"
            "- update_special_requests لطلب خاص\n"
            "- confirm_order لما كل البيانات جاهزة والعميل أكد\n"
            "- get_menu / to_delivery / to_complaint لما تحتاج\n\n"
            "قاعدة مقدسة: حالة المكالمة بتيجيلك في system message. ما "
            "تسألش عن حاجة موجودة فيها. ما تخترعش أصناف."
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
        """LLM-driven now — only the pending-upsell branch remains."""
        ud = self.session.userdata
        if ud.pending_upsell_item:
            from agent import _ask_name
            await self._handle_pending_upsell(
                user_text,
                flow_name="takeaway",
                post_upsell_prompt=_ask_name,
            )

        return False

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لو العميل عنده شكوى."""
        async def _impl() -> Agent:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def to_delivery(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لو العميل غيّر رأيه وعايز توصيل بدل الاستلام. الطلب والاسم والرقم متحفوظين."""
        async def _impl() -> str | Agent:
            from agent import _delivery_unavailable_user_message
            ud = context.userdata
            if "delivery" not in ud.agents:
                return _delivery_unavailable_user_message(ud.restaurant)
            return await self._transfer("delivery", context)

        return await _run_tool_safely("to_delivery", context, _impl)

    @function_tool()
    async def update_order(
        self,
        items: Annotated[
            list[str],
            Field(description=(
                "القائمة الكاملة للطلب مع الكميات. لما العميل يعدل، ابعت "
                "القائمة الجديدة كاملة (مش بس اللي اتغيّر). أمثلة: "
                "['كشري كبير × 2', 'بيبسي']."
            )),
        ],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يطلب أو يعدّل أو يشيل أصناف.

        Pass the FULL new list. The engine validates against the menu
        and returns a confirmation that includes the current stored
        order so you can phrase a natural acknowledgement without
        hallucinating quantities or items.
        """
        async def _impl() -> str:
            return self._process_order_update(
                items,
                context,
                flow_name="takeaway",
            )

        return await _run_tool_safe_speak("update_order", context, _impl)

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير أو مفيش")],
        context: RunContext_T,
    ) -> str:
        async def _impl() -> str:
            from agent import _clear_pending_upsell, _followup_after_special_request, _join_user_phrases, _looks_empty_answer
            from utils.voice import _voice_safe_text
            _clear_pending_upsell(context.userdata)
            if _looks_empty_answer(requests):
                context.userdata.special_requests = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش طلب خاص", _followup_after_special_request("takeaway", context.userdata)),
                    max_chars=180,
                )
            context.userdata.special_requests = requests.strip()
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", _followup_after_special_request("takeaway", context.userdata)),
                max_chars=180,
            )

        return await _run_tool_safe_speak("update_special_requests", context, _impl)

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
            from core.confirmation_helpers import (
                begin_submit,
                finish_submit,
                gate_submit,
            )
            from utils.money import num2ar
            from utils.voice import _voice_safe_text
            ud = context.userdata
            payload = {
                "items": list(ud.order or []),
                "name": ud.customer_name or "",
                "phone": ud.customer_phone or "",
            }
            gate = gate_submit("takeaway", ud, payload)

            if gate.view.is_terminal:
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
            if not gate.allow:
                # Tracker-level duplicate detected (the same payload was
                # already accepted earlier). Treat as a duplicate confirm.
                logger.warning(
                    "call=%s | takeaway submit blocked by tracker | reason=%s",
                    ud.call_id,
                    gate.reason,
                )
                return _voice_safe_text(f"الطلب متسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")

            begin_submit("takeaway", ud, gate.idempotency_key)
            ud.order_submit_in_flight = True
            try:
                result = await submit_takeaway(ud)
            except Exception as exc:  # pragma: no cover - defensive
                finish_submit("takeaway", ud, gate.idempotency_key, succeeded=False, error=type(exc).__name__)
                raise
            finally:
                ud.order_submit_in_flight = False
            if not result:
                finish_submit("takeaway", ud, gate.idempotency_key, succeeded=False, error="empty_result")
                return _backend_failure_user_message(ud)
            if result.get("queued"):
                # Queued submissions are in-flight server-side; don't mark accepted
                # until the queue worker reports back.
                finish_submit("takeaway", ud, gate.idempotency_key, succeeded=False, error="queued")
                return _backend_queued_user_message("order")

            ud.order_id = result.get("order_id", "")
            ud.order_confirmed = True
            finish_submit("takeaway", ud, gate.idempotency_key, succeeded=True, backend_id=ud.order_id or "")
            _emit_event("order.submitted", call_id=ud.call_id, flow="takeaway", order_id=ud.order_id)
            _emit_event("order.confirmed", call_id=ud.call_id, flow="takeaway", order_id=ud.order_id)
            wait = result.get("estimated_time", self.cfg.wait_minutes)
            return f"تمام يا {ud.customer_name}، الطلب اتسجل. هيبقى جاهز خلال {num2ar(wait)} دقيقة."

        return await _run_tool_safe_speak("confirm_order", context, _impl)
