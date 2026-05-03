"""Complaint flow — handles customer complaints."""
from __future__ import annotations

import logging
from typing import Annotated

from livekit.agents.llm import function_tool
from pydantic import Field

from base_agent import BaseAgent, RunContext_T, _run_tool_safe_speak, build_instructions, to_greeter, update_name, update_phone
from backend.config import RestaurantConfig

logger = logging.getLogger("restaurant.agent")


class Complaint(BaseAgent):
    def __init__(self, cfg: RestaurantConfig) -> None:
        self._opening = "قولي حصل إيه يا فندم؟"
        core = (
            "إنت موظف مطعم بيسمع شكاوى العملاء وبيحتوي الموقف. اتكلم زي "
            "موظف بشري — اعتذر من قلبك، اسمعه، ما تجادلش.\n\n"
            "كيف تشتغل:\n"
            "- في كل turn هيوصلك STATE فيه اللي اتسجل. اعتمد عليه.\n"
            "- اسمع الشكوى لحد ما يخلص قبل ما تنادي log_complaint.\n"
            "- لما تفهم الشكوى ونوعها (order_issue / quality / service / "
            "delivery / other)، استدعي log_complaint.\n"
            "- اسمه → update_name. رقمه → update_phone.\n"
            "- لو طلع بقى عايز يطلب أكل بدل الشكوى → to_greeter.\n\n"
            "قواعد:\n"
            "- ما تسألش عن slot موجود في الـ STATE.\n"
            "- ما تجادلش ومتبرّرش، مهما كان.\n"
            "- اعتذر طبيعي، مش جملة محفوظة."
        )
        super().__init__(
            instructions=build_instructions(cfg.name, core),
            tools=[update_name, update_phone, to_greeter],
        )

    @function_tool()
    async def log_complaint(
        self,
        complaint_text: Annotated[str, Field(description="ملخص الشكوى")],
        complaint_type: Annotated[str, Field(
            description="النوع: order_issue | quality | service | delivery | other"
        )],
        context: RunContext_T,
    ) -> str:
        """يُستدعى لتسجيل الشكوى."""
        async def _impl() -> str:
            from agent import (
                _clean_followup_note,
                _complaint_followup_question,
                _join_user_phrases,
                _maybe_submit_pending_complaint,
                _normalize_complaint_type,
            )
            from utils.voice import _voice_safe_text
            ud = context.userdata
            if ud.complaint_logged:
                logger.info("call=%s | complaint log skipped | reason=already_logged", ud.call_id)
                return _voice_safe_text("الشكوى متسجلة خلاص يا فندم.")
            cleaned_text = complaint_text.strip()
            if len(cleaned_text) < 3:
                return _voice_safe_text("قولّي الشكوى بشكل أوضح شوية يا فندم.")
            ud.complaint_text = cleaned_text
            normalized_type = _normalize_complaint_type(complaint_type)
            if not normalized_type:
                logger.info("call=%s | complaint_pending | missing=نوع الشكوى", ud.call_id)
                return _voice_safe_text("نوع الشكوى مش واضح. اختاره كطلب أو جودة أو خدمة أو توصيل.")
            ud.complaint_type = normalized_type
            note = await _maybe_submit_pending_complaint(context)
            if ud.complaint_logged:
                return _voice_safe_text(_join_user_phrases("تمام يا فندم، الشكوى اتسجلت", _complaint_followup_question(ud)), max_chars=180)
            if note:
                return _voice_safe_text(_join_user_phrases(_clean_followup_note(note), _complaint_followup_question(ud)), max_chars=180)
            return _voice_safe_text(_join_user_phrases("تمام يا فندم، سجلت الشكوى", _complaint_followup_question(ud)), max_chars=180)

        return await _run_tool_safe_speak("log_complaint", context, _impl)
