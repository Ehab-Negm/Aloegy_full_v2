"""Reservation flow — handles table bookings."""
from __future__ import annotations

import logging
from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safe_speak, build_instructions, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Reservation(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        from utils.money import num2ar
        self.cfg = cfg
        self._opening = "عايز تحجز إمتى يا فندم؟"

        branch_note = f" | فروع: {cfg.branch_names()}" if len(cfg.branches) > 1 else ""

        branch_tool_line = (
            "- update_branch: لما يحدد الفرع.\n" if len(cfg.branches) > 1 else ""
        )
        core = (
            "إنت موظف مطعم بياخد حجوزات ترابيزات. اتكلم زي موظف بشري، "
            "ودود وطبيعي، باللهجة المصرية.\n\n"
            f"المواعيد: {cfg.hours_text()}\n"
            f"الحجز يقبل من {num2ar(cfg.min_guests)} لـ{num2ar(cfg.max_guests)} "
            f"ضيف{branch_note}.\n\n"
            "كيف تشتغل:\n"
            "- في كل turn هيوصلك STATE فيه اللي اتسجل دلوقتي. اعتمد عليه.\n"
            "- لما يقول وقت → update_reservation_time (لو خارج المواعيد قوله واقترح بديل).\n"
            "- لما يقول عدد الضيوف → update_guests_count.\n"
            "- اسمه → update_name. رقمه → update_phone. مناسبة خاصة → update_reservation_notes.\n"
            f"{('- الفرع → update_branch.' + chr(10)) if len(cfg.branches) > 1 else ''}"
            "- لما كل حاجة جاهزة وأكد → confirm_reservation.\n"
            "- لو طلب حاجة خارج نطاق الحجز → to_greeter.\n\n"
            "قواعد:\n"
            "- ما تسألش عن slot موجود في الـ STATE.\n"
            "- ما تستدعي tool لمعلومة ما قالهاش العميل.\n"
            "- سؤال واحد بس في المرة."
        )
        super().__init__(
            instructions=build_instructions(cfg.name, core),
            tools=[
                update_name,
                update_phone,
                to_greeter,
            ],
        )

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        from agent import (
            _is_positive_confirmation,
            _is_reservation_ready_for_confirmation,
            _looks_empty_answer,
        )
        ud = self.session.userdata
        context = self._tool_context()

        if ud.reservation_time and ud.guests_count is not None and not ud.customer_name and _looks_empty_answer(user_text):
            logger.info("call=%s | reservation optional_empty_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.update_reservation_notes(notes=user_text, context=context))

        if _is_reservation_ready_for_confirmation(ud, self.cfg) and _is_positive_confirmation(user_text):
            logger.info("call=%s | reservation confirm_intercepted | text=%r", ud.call_id, user_text)
            await self._say_and_stop(await self.confirm_reservation(context=context), critical=True)

        return False

    @function_tool()
    async def update_reservation_time(
        self,
        time: Annotated[str, Field(description="وقت وتاريخ الحجز")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يحدد وقت الحجز."""
        async def _impl() -> str:
            from agent import _ack, _parse_reservation_time
            from utils.voice import _voice_safe_text
            cleaned_time = time.strip()
            parsed = _parse_reservation_time(cleaned_time, self.cfg)
            if parsed is None:
                if self.cfg.hours:
                    return _voice_safe_text(
                        f"الوقت مش واضح أو خارج المواعيد. مواعيدنا {self.cfg.hours_text()}. قول اليوم والساعة مع بعض.",
                        max_chars=170,
                    )
                return _voice_safe_text("الوقت مش واضح. قول اليوم والساعة مع بعض، زي بكرة الساعة 8 بالليل.")
            context.userdata.reservation_time = parsed.raw_text
            context.userdata.reservation_time_iso = parsed.normalized_text
            return _voice_safe_text(f"{_ack()}، {parsed.raw_text}. كام شخص هتكونوا؟", max_chars=180)

        return await _run_tool_safe_speak("update_reservation_time", context, _impl)

    @function_tool()
    async def update_guests_count(
        self,
        count: Annotated[int, Field(description="عدد الضيوف", ge=1)],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يقول عدد الضيوف."""
        async def _impl() -> str:
            from agent import _ack, _join_user_phrases, _next_slot_question_for_flow, spoken_phone
            from utils.money import num2ar
            from utils.voice import _voice_safe_text
            if count < self.cfg.min_guests:
                return _voice_safe_text(f"أقل عدد للحجز {num2ar(self.cfg.min_guests)} أشخاص.")
            if count > self.cfg.max_guests:
                return _voice_safe_text(f"أكتر عدد في حجز واحد {num2ar(self.cfg.max_guests)}، اتصل على {spoken_phone(self.cfg.phone)} مباشرة لو أكتر.")
            context.userdata.guests_count = count
            return _voice_safe_text(
                _join_user_phrases(f"{_ack()}، {num2ar(count)} أشخاص", _next_slot_question_for_flow("reservation", context.userdata)),
                max_chars=180,
            )

        return await _run_tool_safe_speak("update_guests_count", context, _impl)

    @function_tool()
    async def update_branch(
        self,
        branch: Annotated[str, Field(description="اسم الفرع")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يختار فرع."""
        async def _impl() -> str:
            from agent import _ack, _ask_name, _resolve_branch_name
            from utils.voice import _voice_safe_text
            resolved = _resolve_branch_name(branch, self.cfg.branches)
            if not resolved:
                return _voice_safe_text(f"الفرع ده مش واضح. الفروع المتاحة: {self.cfg.branch_names()}.", max_chars=170)
            context.userdata.selected_branch = resolved
            return _voice_safe_text(f"{_ack()}، فرع {resolved}. {_ask_name()}")

        return await _run_tool_safe_speak("update_branch", context, _impl)

    @function_tool()
    async def update_reservation_notes(
        self,
        notes: Annotated[str, Field(description="ملاحظات أو طلبات خاصة")],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لما العميل يذكر طلبات خاصة للحجز."""
        async def _impl() -> str:
            from agent import _join_user_phrases, _looks_empty_answer, _next_slot_question_for_flow
            from utils.voice import _voice_safe_text
            if _looks_empty_answer(notes):
                context.userdata.reservation_notes = None
                if len(self.cfg.branches) > 1 and not context.userdata.selected_branch:
                    return _voice_safe_text(
                        f"تمام يا فندم، مفيش ملاحظات. أي فرع تفضل؟ {self.cfg.branch_names()}",
                        max_chars=180,
                    )
                return _voice_safe_text(
                    _join_user_phrases("تمام يا فندم، مفيش ملاحظات", _next_slot_question_for_flow("reservation", context.userdata)),
                    max_chars=180,
                )
            context.userdata.reservation_notes = notes.strip()
            if len(self.cfg.branches) > 1 and not context.userdata.selected_branch:
                return _voice_safe_text(
                    f"تمام يا فندم، سجلت الملاحظة. أي فرع تفضل؟ {self.cfg.branch_names()}",
                    max_chars=180,
                )
            return _voice_safe_text(
                _join_user_phrases("تمام يا فندم، سجلت الملاحظة", _next_slot_question_for_flow("reservation", context.userdata)),
                max_chars=180,
            )

        return await _run_tool_safe_speak("update_reservation_notes", context, _impl)

    @function_tool()
    async def confirm_reservation(self, context: RunContext_T) -> str:
        """يُستدعى بعد تأكيد كل بيانات الحجز."""
        async def _impl() -> str:
            from agent import (
                _backend_failure_user_message,
                _backend_queued_user_message,
                _can_attempt_backend_write,
                _emit_event,
                _reservation_next_missing_slot,
                submit_reservation,
            )
            from core.confirmation_helpers import (
                begin_submit,
                finish_submit,
                gate_submit,
            )
            from utils.voice import _voice_safe_text
            ud = context.userdata
            payload = {
                "name": ud.customer_name or "",
                "phone": ud.customer_phone or "",
                "time": ud.reservation_time or "",
                "guests": ud.guests_count or 0,
                "branch": ud.selected_branch or "",
            }
            gate = gate_submit("reservation", ud, payload)

            if gate.view.is_terminal:
                logger.info("call=%s | reservation submit skipped | reason=already_confirmed", ud.call_id)
                return _voice_safe_text(f"الحجز مسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")
            if ud.reservation_submit_in_flight:
                logger.warning("call=%s | reservation submit skipped | reason=in_flight", ud.call_id)
                return _voice_safe_text("ثانية واحدة يا فندم، بسجل الحجز دلوقتي.")
            missing = _reservation_next_missing_slot(ud, self.cfg)
            if missing:
                return _voice_safe_text(f"لسه محتاج: {missing}.")
            if not _can_attempt_backend_write(ud):
                logger.warning("call=%s | reservation submit skipped | reason=write_unavailable", ud.call_id)
                return _backend_failure_user_message(ud)
            if not gate.allow:
                logger.warning(
                    "call=%s | reservation submit blocked by tracker | reason=%s",
                    ud.call_id,
                    gate.reason,
                )
                return _voice_safe_text(f"الحجز مسجل خلاص يا {ud.customer_name}. في حاجة تانية؟")

            begin_submit("reservation", ud, gate.idempotency_key)
            ud.reservation_submit_in_flight = True
            try:
                result = await submit_reservation(ud)
            except Exception as exc:  # pragma: no cover - defensive
                finish_submit("reservation", ud, gate.idempotency_key, succeeded=False, error=type(exc).__name__)
                raise
            finally:
                ud.reservation_submit_in_flight = False
            if not result:
                finish_submit("reservation", ud, gate.idempotency_key, succeeded=False, error="empty_result")
                return _backend_failure_user_message(ud)
            if result.get("queued"):
                finish_submit("reservation", ud, gate.idempotency_key, succeeded=False, error="queued")
                return _backend_queued_user_message("reservation")

            ud.reservation_id = result.get("reservation_id", "")
            ud.reservation_confirmed = True
            finish_submit("reservation", ud, gate.idempotency_key, succeeded=True, backend_id=ud.reservation_id or "")
            _emit_event("reservation.submitted", call_id=ud.call_id, flow="reservation", reservation_id=ud.reservation_id)
            _emit_event("reservation.confirmed", call_id=ud.call_id, flow="reservation", reservation_id=ud.reservation_id)
            msg = f"تمام يا {ud.customer_name}، الحجز اتأكد."
            msg += " هنبعتلك رسالة تأكيد."
            return msg

        return await _run_tool_safe_speak("confirm_reservation", context, _impl)
