"""Delivery flow — handles delivery orders."""
from __future__ import annotations

import logging
import re
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safe_speak, _run_tool_safely, build_instructions, get_menu, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Delivery(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from utils.money import num2ar
        self.cfg = cfg
        self._opening = "اتفضل يا فندم، تحب تطلب إيه؟"

        zones_info = f" | مناطق: {cfg.delivery_zones_text()}" if cfg.delivery_zones else ""

        core = (
            "إنت موظف مطعم بياخد طلب توصيل على التليفون. اتكلم زي موظف "
            "بشري ودود — لهجة مصرية طبيعية، مش جمل محفوظة.\n\n"
            f"معلومات التوصيل: {cfg.delivery_info_text()}{zones_info}\n\n"
            "اسمع العميل واستخدم الـ tools دايماً علشان تسجل اللي بيقوله:\n"
            "- update_order للأكل (ابعت القائمة كاملة كل مرة)\n"
            "- update_delivery_address للعنوان\n"
            "- update_name للاسم، update_phone للموبايل\n"
            "- update_special_requests لطلب خاص\n"
            "- confirm_delivery لما كل البيانات جاهزة والعميل أكد\n"
            "- get_menu / to_takeaway / to_complaint لما تحتاج\n\n"
            "قاعدة مقدسة: حالة المكالمة بتيجيلك في system message في "
            "الأول. ما تسألش عن حاجة موجودة فيها. ما تخترعش أصناف ولا "
            "أسعار — الـ tool هو اللي بيتحقق."
        )
        super().__init__(
            instructions=build_instructions(cfg.name, core),
            # ``update_name`` / ``update_phone`` are LLM tools again. The
            # earlier removal made the agent feel forgetful: the engine
            # captured these slots silently and the LLM, never having
            # spoken about them, would ask the same question on the next
            # turn. With the tools back, the LLM asks → calls the tool
            # → acknowledges in its own voice. The tool itself still
            # runs the same deterministic validation (Egyptian phone
            # carrier check, name blocklist) so the LLM cannot store
            # garbage.
            tools=[
                update_name,
                update_phone,
                to_greeter,
                get_menu,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        """No deterministic intercepts — the LLM owns the conversation.

        Order, address, name, phone, and confirmation all flow through
        the LLM's tool calls. The deterministic engine still validates
        every write inside the tool implementations (menu lookup,
        Egyptian phone format, idempotency on submit), so the LLM
        cannot store hallucinated values even though it drives the
        dialogue. The pending-upsell handler stays — that's an
        explicit prompt-state branch, not an extraction shortcut.
        """
        ud = self.session.userdata
        if ud.pending_upsell_item:
            from agent import _ask_address
            await self._handle_pending_upsell(
                user_text,
                flow_name="delivery",
                post_upsell_prompt=_ask_address,
            )
        return False

    @function_tool()
    async def update_order(
        self,
        items: Annotated[
            list[str],
            Field(description=(
                "القائمة الكاملة للطلب مع الكميات. لما العميل يعدل، ابعت "
                "القائمة الجديدة كاملة (مش بس اللي اتغيّر). أمثلة: "
                "['كوشري كبير × 2', 'عصير ليمون'] أو ['برجر كبير × 1'] "
                "لو العميل قرر يخلي برجر واحد بس بدل اتنين."
            )),
        ],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يطلب أو يعدّل أو يشيل أصناف.

        Pass the FULL new list. The engine validates against the menu,
        rejects unknown items, and returns a confirmation that includes
        the current stored order so you (the LLM) can phrase a natural
        acknowledgement without hallucinating quantities or items.
        """
        async def _impl() -> str:
            return self._process_order_update(
                items,
                context,
                flow_name="delivery",
                min_order_total=self.cfg.min_order,
            )

        return await _run_tool_safe_speak("update_order", context, _impl)

    @function_tool()
    async def update_special_requests(
        self,
        requests: Annotated[str, Field(description="طلبات خاصة في التحضير")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر طلبات خاصة."""
        async def _impl() -> str:
            from agent import _clear_pending_upsell, _followup_after_special_request, _join_user_phrases, _looks_empty_answer
            from utils.voice import _voice_safe_text
            _clear_pending_upsell(context.userdata)
            if _looks_empty_answer(requests):
                context.userdata.special_requests = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش طلب خاص", _followup_after_special_request("delivery", context.userdata)),
                    max_chars=180,
                )
            context.userdata.special_requests = requests.strip()
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت الملاحظة على الطلب", _followup_after_special_request("delivery", context.userdata)),
                max_chars=180,
            )

        return await _run_tool_safe_speak("update_special_requests", context, _impl)

    @function_tool()
    async def update_delivery_address(
        self,
        address: Annotated[str, Field(description="العنوان كامل: الشارع والرقم والمنطقة")],
        zone: Annotated[str, Field(description="المنطقة أو الحي — لو مش واضحة كرر اسم المنطقة من العنوان")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يقول عنوانه — استدعيه فوراً حتى لو المنطقة مش واضحة."""
        async def _impl() -> str:
            from agent import _address_seems_specific, _extract_zone_from_address, _join_user_phrases, _next_slot_question_for_flow
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
                        _next_slot_question_for_flow("delivery", context.userdata),
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

        return await _run_tool_safe_speak("update_delivery_address", context, _impl)

    @function_tool()
    async def update_delivery_landmark(
        self,
        landmark: Annotated[str, Field(description="علامة مميزة قريبة من العنوان")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر علامة مميزة."""
        async def _impl() -> str:
            from agent import _ack, _ask_phone, _extract_name_candidate, _join_user_phrases, _looks_empty_answer, _next_slot_question_for_flow
            from utils.voice import _voice_safe_text
            context.userdata.landmark_asked = True
            if _looks_empty_answer(landmark):
                context.userdata.delivery_landmark = None
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش علامة مميزة", _next_slot_question_for_flow("delivery", context.userdata)),
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
                _join_user_phrases("تمام يا فندم، سجلت العلامة المميزة", _next_slot_question_for_flow("delivery", context.userdata)),
                max_chars=180,
            )

        return await _run_tool_safe_speak("update_delivery_landmark", context, _impl)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لو العميل عنده شكوى."""
        async def _impl() -> Agent:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def to_takeaway(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لو العميل غيّر رأيه وقرر ييجي يستلم الطلب بنفسه. الطلب والاسم والرقم متحفوظين."""
        async def _impl() -> Agent:
            return await self._transfer("takeaway", context)

        return await _run_tool_safely("to_takeaway", context, _impl)

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
            from core.confirmation_helpers import (
                begin_submit,
                finish_submit,
                gate_submit,
            )
            from utils.money import money2ar, num2ar
            from utils.voice import _voice_safe_text
            ud = context.userdata
            payload = {
                "items": list(ud.order or []),
                "name": ud.customer_name or "",
                "phone": ud.customer_phone or "",
                "address": ud.delivery_address or "",
                "zone": ud.delivery_zone or "",
            }
            gate = gate_submit("delivery", ud, payload)

            if gate.view.is_terminal:
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
            if not gate.allow:
                logger.warning(
                    "call=%s | delivery submit blocked by tracker | reason=%s",
                    ud.call_id,
                    gate.reason,
                )
                return _voice_safe_text(f"الطلب مسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")

            begin_submit("delivery", ud, gate.idempotency_key)
            ud.order_submit_in_flight = True
            try:
                result = await submit_delivery(ud)
            except Exception as exc:  # pragma: no cover - defensive
                finish_submit("delivery", ud, gate.idempotency_key, succeeded=False, error=type(exc).__name__)
                raise
            finally:
                ud.order_submit_in_flight = False
            if not result:
                finish_submit("delivery", ud, gate.idempotency_key, succeeded=False, error="empty_result")
                return _backend_failure_user_message(ud)
            if result.get("queued"):
                finish_submit("delivery", ud, gate.idempotency_key, succeeded=False, error="queued")
                return _backend_queued_user_message("order")

            ud.order_id = result.get("order_id", "")
            ud.order_confirmed = True
            finish_submit("delivery", ud, gate.idempotency_key, succeeded=True, backend_id=ud.order_id or "")
            _emit_event("order.submitted", call_id=ud.call_id, flow="delivery", order_id=ud.order_id)
            _emit_event("order.confirmed", call_id=ud.call_id, flow="delivery", order_id=ud.order_id)
            wait = result.get("estimated_time", self.cfg.delivery_minutes)

            msg = f"تمام يا {ud.customer_name}، الطلب اتسجل للتوصيل."
            if self.cfg.delivery_fee > 0:
                msg += f" رسوم التوصيل {money2ar(self.cfg.delivery_fee)} جنيه."
            msg += f" هيوصلك خلال {num2ar(wait)} دقيقة."
            return msg

        return await _run_tool_safe_speak("confirm_delivery", context, _impl)
