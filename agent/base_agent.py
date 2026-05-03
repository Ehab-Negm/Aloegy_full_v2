"""BaseAgent — shared base class for all flow agents."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import time
from types import SimpleNamespace
from typing import Annotated, TypeVar

from livekit.agents import StopResponse, llm
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

from state.user_data import UserData

logger = logging.getLogger("restaurant.agent")

RunContext_T = RunContext[UserData]
ToolResultT = TypeVar("ToolResultT")
_TOOL_ERROR_MESSAGE = "معلش يا فندم، حصلت مشكلة عندنا. ممكن تقولي تاني؟"


_EGY_PERSONA_TMPL = (
    "أنت محمد، موظف مصري بترد على تليفون مطعم \"{name}\". "
    "اتكلم كإنسان حقيقي — جملة قصيرة، نبرة طبيعية، مش سكريبت.\n\n"
    ""
    "# طريقة الكلام\n"
    "- ابدأ بكلمة تأكيد صغيرة (اه/تمام/ماشي/حاضر).\n"
    "- جملة واحدة، أقصى 15 كلمة.\n"
    "- \"يا فندم\" مرة أو اتنين في المكالمة كلها.\n"
    "- متكررش كلام الزبون حرفي.\n"
    "- مش فاهم؟ \"معلش، ممكن تعيدها؟\" — متخمّنش.\n"
    "- Tool محتاج وقت؟ قول \"ثانية بس\" مش تسكت.\n\n"
    ""
    "# ممنوع\n"
    "- \"بكل سرور\" / \"يسعدني\" / \"أتشرف\" / \"في خدمتك\".\n"
    "- إنك AI أو بوت. لو اتسألت: \"أنا محمد، شغال هنا\".\n"
    "- \"عميل\" أو \"زبون\" — قول \"حضرتك\".\n"
    "- تعيد كلام الزبون كإنه ردك.\n\n"
    ""
    "# قواعد الـ Tools (مهم جداً)\n"
    "- لما الزبون يقول طلب أو اسم أو رقم أو عنوان، استدعي الـ tool **فوراً** "
    "في نفس الـ turn — قبل ما ترد عليه. الـ tool هو اللي بيتأكد ويرجّعلك "
    "الرد المناسب تتكلم بيه.\n"
    "- **ما تخترعش أصناف مش في المنيو.** الـ tool بيرفض اللي مش موجود ويرجع "
    "رسالة \"الصنف ده مش عندنا\". قول الرسالة دي زي ما هي.\n"
    "- لما الزبون يضيف أو يشيل أو يعدل صنف، ابعت القائمة الكاملة الجديدة لـ "
    "update_order (مش بس التغيير).\n"
    "- ما تأكدش على حاجة قبل ما الـ tool يرجع — ممكن الـ tool يرفض ويغيّر "
    "الرسالة.\n\n"
    ""
    "# أمثلة\n"
    "محادثة بسيطة:\n"
    "- زبون: ألو → أهلاً، معاك محمد من {name}، تحت أمرك.\n"
    "- زبون: عايز كشري → استدعي update_order(['كشري']) → الـ tool هيرجع "
    "  رد فيه السعر، قوله.\n"
    "- زبون: [مش واضح] → معلش، ممكن تعيدها؟\n"
    "- زبون: ازيك؟ → الحمد لله يا فندم، تحب تطلب إيه؟\n\n"
    ""
    "أوردر معقد (multi-item):\n"
    "- زبون: \"عايز برجر كبير وعصير ليمون وكشري صغير\"\n"
    "  → update_order(['برجر كبير', 'عصير ليمون', 'كشري صغير'])\n"
    "- زبون: \"خلي البرجر اتنين بدل واحد، وشيل العصير\"\n"
    "  → update_order(['برجر كبير × 2', 'كشري صغير'])  ← القائمة الكاملة الجديدة\n\n"
    ""
    "تعديلات وإلغاء:\n"
    "- زبون: \"ألغي الكشري\" → update_order(القائمة من غير الكشري)\n"
    "- زبون: \"ألغي الطلب كله\" → استدعي cancel_order (مش update_order بقائمة فاضية)\n"
    "- زبون: \"خليه دليفري بدل تيكاواي\" → استدعي to_delivery (الـ flow handoff)\n\n"
    ""
    "أصناف مش في المنيو:\n"
    "- زبون: \"عايز بيتزا\" والمنيو فيه كشري بس\n"
    "  → استدعي update_order(['بيتزا']) → الـ tool هيرجع \"البيتزا مش عندنا، "
    "  المتاح كشري\". قول الرد ده — متخترعش بيتزا.\n\n"
    ""
    "أرقام وعناوين (الـ tool هو اللي بيتحقق):\n"
    "- زبون: \"رقمي زيرو واحد اتنين\" → update_phone('012') → الـ tool هيقول "
    "  \"محتاج آخر الرقم\". متأكدش إنك سجلت رقم كامل لو الـ tool رفض.\n"
    "- زبون: \"ساكن في المعادي\" → update_delivery_address('المعادي', 'المعادي')\n"
    "  → الـ tool بيتأكد إن المنطقة مدعومة وبيرجع التأكيد.\n\n"
    ""
    "حالة المكالمة (system message):\n"
    "- بتيجيلك في الأول قائمة بالـ slots المسجلة. ما تسألش عن حاجة موجودة\n"
    "  فيها (لو الاسم متسجل ما تسألش عن الاسم تاني).\n"
)


def build_instructions(restaurant_name: str, flow_core: str) -> str:
    """Prepends the shared Egyptian-persona preamble to a flow-specific core prompt."""
    persona = _EGY_PERSONA_TMPL.format(name=restaurant_name)
    return f"{persona}\n# شغلك دلوقتي\n\n{flow_core.strip()}"


async def _run_tool_safely(
    tool_name: str,
    context: RunContext_T,
    fn: Callable[[], Awaitable[ToolResultT]],
) -> ToolResultT | str:
    """Legacy two-roundtrip path: tool returns text, framework sends it
    back to the LLM for a paraphrased response.

    Each tool call therefore costs **two** LLM round-trips (~1s each
    on gpt-4.1) before TTS can start, which puts user-perceived
    latency above 3s. New tools should use ``_run_tool_safe_speak``
    instead, which speaks the tool result directly and skips the
    second round-trip — saving ~1 second per turn that includes a
    tool call.
    """
    try:
        return await fn()
    except StopResponse:
        raise
    except Exception:
        call_id = getattr(getattr(context, "userdata", None), "call_id", "-") or "-"
        logger.exception("call=%s | tool error | tool=%s", call_id, tool_name)
        return _TOOL_ERROR_MESSAGE


async def _run_tool_safe_speak(
    tool_name: str,
    context: RunContext_T,
    fn: Callable[[], Awaitable[str]],
) -> str:
    """Run the tool, speak its result directly via TTS, and skip the
    second LLM round-trip.

    Why this helper exists
    ----------------------
    The default OpenAI tool-calling pattern is:

        user → LLM₁ (decides tool call)
              → tool execution
              → LLM₂ (paraphrases tool result into spoken text)
              → TTS

    LLM₂ is almost always redundant — our tools already return a
    natural Egyptian-Arabic sentence (e.g. "تمام يا فندم، سجلت
    العنوان…"). Sending it back through the LLM only paraphrases it
    and costs ~1 second of TTFT. With this helper we play the tool
    result directly and ``raise StopResponse`` so livekit-agents
    skips LLM₂ entirely. Total saving: ~1 second per tool turn,
    which is what gets us under the 2.5s end-to-end target.

    Chat-context integrity
    ----------------------
    When ``StopResponse`` is raised inside a tool, livekit
    (``make_function_call_output``) sets ``fnc_call_out=None`` and
    ``reply_required`` stays such that no follow-up LLM call is
    scheduled. The assistant message that contained the tool_call is
    never persisted (only the spoken text from ``session.say``
    becomes the assistant turn). So next turn the LLM sees a clean
    history — no orphaned tool_call without a matching tool result.
    """
    try:
        result = await fn()
    except StopResponse:
        raise
    except Exception:
        call_id = getattr(getattr(context, "userdata", None), "call_id", "-") or "-"
        logger.exception("call=%s | tool error | tool=%s", call_id, tool_name)
        result = _TOOL_ERROR_MESSAGE

    if not isinstance(result, str) or not result.strip():
        # Nothing to speak — let the framework fall back to LLM₂ so it
        # can choose what to say. Should not happen for normal tools.
        return result if isinstance(result, str) else ""

    session = getattr(context, "session", None)
    userdata = getattr(context, "userdata", None)

    # Only the live LiveKit ``AgentSession`` knows how to play audio
    # AND track the StopResponse signal so the framework can skip
    # LLM₂. Test harnesses wire a ``SimpleNamespace`` with a fake
    # ``say`` and would not propagate StopResponse correctly — for
    # those we fall back to the legacy "return string" contract so
    # all the scenario assertions on tool return values keep working.
    try:
        from livekit.agents.voice import AgentSession as _LiveAgentSession
    except Exception:
        _LiveAgentSession = None  # pragma: no cover
    if _LiveAgentSession is None or not isinstance(session, _LiveAgentSession):
        return result

    # In realtime mode the realtime model is the only speaker. Two paths:
    #   - 2.5 Live native-audio: ``session.say`` works (routed through
    #     ``generate_reply`` by the main.py wrapper), so the original
    #     fast-path is correct. Take it.
    #   - 3.1 Flash Live: ``generate_reply`` is rejected and ``session.say``
    #     is a hard noop. Returning a long deterministic confirmation as the
    #     tool result confuses the model (sync tool calling halts on long
    #     payloads). Trim to a short ack so the model unblocks and produces
    #     its own verbal confirmation per ``SESSION_REALTIME_INSTRUCTIONS``.
    _rt_model = getattr(session, "llm", None)
    if isinstance(_rt_model, llm.RealtimeModel):
        _model_name = getattr(_rt_model, "model", "") or ""
        if _model_name == "gemini-3.1-flash-live-preview":
            realtime_result = result.strip()
            if realtime_result.startswith("["):
                close_idx = realtime_result.find("]")
                if close_idx > 0:
                    realtime_result = realtime_result[close_idx + 1 :].strip()
            if len(realtime_result) > 240:
                realtime_result = realtime_result[:240].rstrip() + "…"
            if not realtime_result:
                realtime_result = "OK"
            if userdata is not None:
                userdata.last_agent_message = result
            logger.info(
                "call=%s | tool fast-path skipped (realtime/3.1) | tool=%s | ack_len=%d",
                getattr(userdata, "call_id", "-") or "-",
                tool_name,
                len(realtime_result),
            )
            return realtime_result
        # 2.5 native-audio falls through to the standard say() fast-path —
        # the wrapper routes it via generate_reply.

    try:
        await session.say(result, allow_interruptions=True, add_to_chat_ctx=True)
        if userdata is not None:
            userdata.last_agent_message = result
        logger.info(
            "call=%s | tool fast-path | tool=%s | skipped LLM₂",
            getattr(userdata, "call_id", "-") or "-",
            tool_name,
        )
    except Exception:
        call_id = getattr(userdata, "call_id", "-") or "-"
        logger.exception("call=%s | tool say failed | tool=%s", call_id, tool_name)
        return result
    raise StopResponse()


class BaseAgent(Agent):
    def __init__(self, **kwargs) -> None:
        opening = getattr(self, "_opening", "")
        super().__init__(**kwargs)
        self._opening = opening
        self._turn_responded = False

    def _sync_phone_capture_mode(self) -> None:
        from agent import SESSION_PREEMPTIVE_GENERATION
        ud: UserData = self.session.userdata
        desired_preemptive = SESSION_PREEMPTIVE_GENERATION and not ud.phone_capture_mode
        if self.session.options.preemptive_generation == desired_preemptive:
            return
        self.session.options.preemptive_generation = desired_preemptive
        logger.info(
            "call=%s | phone_capture_mode=%s | preemptive=%s",
            ud.call_id,
            ud.phone_capture_mode,
            desired_preemptive,
        )

    def _mark_turn_decision(self, mode: str, reason: str) -> None:
        from core.telemetry import elapsed_ms

        ud: UserData = self.session.userdata
        ud.turn_trace_decision_mode = mode
        ud.turn_trace_decision_reason = reason
        if ud.turn_trace_engine_decision_ms is None:
            ud.turn_trace_engine_decision_ms = elapsed_ms(ud.turn_trace_started_monotonic)

    def _finish_turn_trace(self, status: str) -> None:
        from agent import _emit_event
        from core.ops_metrics import METRICS
        from core.telemetry import snapshot_slots, turn_trace_record, write_call_trace

        ud: UserData = self.session.userdata
        if ud.turn_trace_finished or not ud.turn_trace_started_monotonic:
            return
        ud.turn_trace_finished = True
        flow = self.__class__.__name__.lower()
        slot_after = snapshot_slots(ud)
        decision_mode = ud.turn_trace_decision_mode or "llm_fallback"
        decision_reason = ud.turn_trace_decision_reason or "fell_through_to_llm"
        record = turn_trace_record(
            call_id=ud.call_id or "",
            flow=flow,
            turn=ud.turn_trace_current_turn,
            status=status,
            transcript=ud.last_user_message,
            decision_mode=decision_mode,
            decision_reason=decision_reason,
            slot_before=ud.turn_trace_slot_before,
            slot_after=slot_after,
            agent_message=ud.last_agent_message or "",
            question_category=ud.last_question_category or "",
            started_monotonic=ud.turn_trace_started_monotonic,
            engine_decision_ms=ud.turn_trace_engine_decision_ms,
            tts_enqueue_ms=ud.turn_trace_tts_enqueue_ms,
        )
        write_call_trace(record)
        METRICS.record_turn(
            fast_path=record["fast_path"],
            latency_ms=record["latency_ms"].get("turn_handler_total"),
        )
        if ud.turn_trace_engine_decision_ms is not None:
            METRICS.record_engine_decision(float(ud.turn_trace_engine_decision_ms))
        for slot_name in record["slot_changed"]:
            if slot_name.startswith("order_"):
                _emit_event(
                    "order.change",
                    call_id=ud.call_id or "",
                    flow=flow,
                    turn=ud.turn_trace_current_turn,
                    field=slot_name,
                )
            else:
                _emit_event(
                    "slot.captured",
                    call_id=ud.call_id or "",
                    flow=flow,
                    turn=ud.turn_trace_current_turn,
                    slot=slot_name,
                )
        _emit_event(
            "turn.trace",
            call_id=ud.call_id or "",
            flow=flow,
            turn=ud.turn_trace_current_turn,
            fast_path=record["fast_path"],
            llm_fallback=record["llm_fallback"],
            decision_reason=decision_reason,
            slot_changed=record["slot_changed"],
            question_category=record["question_category"],
            latency_ms=record["latency_ms"],
        )

    async def on_enter(self) -> None:
        """Run when the LLM hands control to this agent.

        Follows the LiveKit canonical multi-agent pattern (see the
        official restaurant example):

        - bring the previous agent's recent chat history forward so the
          new agent has continuity (truncated to 6 items),
        - inject ONE system message describing who the agent is and a
          YAML snapshot of ``UserData``,
        - let the LLM speak first.

        The chat history of past tool calls + their results IS the
        running state log. Every ``update_*`` tool returns a clear
        confirmation message; the LLM reads that history and never
        needs a separate state-guard system message. This is what made
        the agent stop "forgetting" the customer — the model now sees
        every capture as a recent assistant message.
        """
        from agent import _flow_missing_phone, _set_phone_capture_mode

        ud: UserData = self.session.userdata
        flow = self.__class__.__name__.lower()
        ud.active_flow = flow
        agent_name = self.__class__.__name__
        logger.info("call=%s | agent=%s", ud.call_id, agent_name)

        desired_phone_mode = _flow_missing_phone(flow, ud)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)
        if ud.last_guard_flow and ud.last_guard_flow != flow:
            ud.last_guard_flow = None
            ud.last_guard_signature = None

        chat_ctx = self.chat_ctx.copy()

        # Carry forward the FULL previous conversation so the new agent
        # has continuous memory of every question already asked and
        # every tool call already made. The reference example used
        # truncate(max_items=6) for short demos; for real restaurant
        # calls (which can run 30+ turns across greeter → delivery →
        # confirmation), 6 items wipes the customer's earlier answers
        # and forces re-asking. Effectively this makes the multi-agent
        # setup feel like a single agent for the duration of the call:
        # the persona/tools change on handoff, the chat memory does
        # not. ``TURN_CHAT_CTX_MAX_ITEMS`` (env-tunable, default 36)
        # still bounds the LLM prompt size per turn, so prompts cannot
        # grow unbounded.
        if isinstance(ud.prev_agent, Agent):
            carried = ud.prev_agent.chat_ctx.copy(
                exclude_instructions=True,
                exclude_function_call=False,
                exclude_handoff=True,
                exclude_config_update=True,
            )
            existing_ids = {item.id for item in chat_ctx.items}
            for item in carried.items:
                if item.id not in existing_ids:
                    chat_ctx.items.append(item)

        # ONE system message: who you are + current state in YAML. This
        # replaces the previous multi-marker setup (FLOW_STYLE +
        # FLOW_CONTEXT + TURN_GUARD). YAML is denser and OpenAI's
        # research confirms it parses better than JSON in long
        # contexts.
        # Marked with ``_FLOW_STATE_PROMPT_MARKER`` so it can be stripped and
        # re-injected with a fresh ``ud.summarize()`` at the top of every
        # ``on_user_turn_completed``. Without that refresh the LLM keeps
        # seeing this turn-zero snapshot — e.g. ``customer_phone: unknown``
        # — even after the phone has been captured, and re-asks for it.
        from agent import _FLOW_STATE_PROMPT_MARKER, SESSION_LLM_NO_THINK
        # ``/no_think`` is the directive Qwen3 looks for to skip its
        # hidden chain-of-thought. Other models ignore it as plain text.
        no_think_prefix = "/no_think\n" if SESSION_LLM_NO_THINK else ""
        chat_ctx.add_message(
            role="system",
            content=(
                f"{_FLOW_STATE_PROMPT_MARKER}\n"
                f"{no_think_prefix}"
                f"You are the {agent_name} agent at مطعم {ud.restaurant.name}.\n"
                f"Talk to the customer in natural Egyptian Arabic — you are a "
                f"human waiter, not a bot. Don't repeat captured information. "
                f"Use the tools to record what the customer says.\n\n"
                f"Current call state:\n{ud.summarize()}"
            ),
        )

        # Gemini 3.1 Live rejects ``send_client_content`` after the first model
        # turn (1007 close code), so update_chat_ctx fails on every handoff
        # except the first. Swallow it: state still flows via tool results,
        # which the realtime model accepts. Non-realtime LLMs are unaffected
        # — the call succeeds and the swallow path never runs.
        try:
            await self.update_chat_ctx(chat_ctx)
        except Exception as _ctx_err:
            logger.warning(
                "call=%s | update_chat_ctx skipped (realtime/3.1 limit) | %s",
                ud.call_id, _ctx_err,
            )
        self._sync_phone_capture_mode()

        # Register the static replies this flow uses so the TTS cache
        # can replay them instantly on the next call instead of paying
        # the ~1s ttfb every time. Only stable, customer-data-free
        # strings are registered; per-call replies (with names, totals,
        # order items) are never cached.
        try:
            from core.tts_cache import GLOBAL_CACHE as _TTS_CACHE
            _TTS_CACHE.register_cacheable(self._opening or "")
            for stable in self._stable_replies(ud):
                _TTS_CACHE.register_cacheable(stable)
        except Exception:
            pass

        # main.py wraps ``session.say`` in realtime mode: for 2.5 native-audio
        # it routes to ``generate_reply``; for 3.1 it noops. Either way calling
        # ``session.say`` from here is safe. ``generate_reply(tool_choice=...)``
        # is rejected by 3.1 specifically — skip it in that case.
        _rt_model = getattr(self.session, "llm", None)
        _is_31 = (
            isinstance(_rt_model, llm.RealtimeModel)
            and getattr(_rt_model, "model", "") == "gemini-3.1-flash-live-preview"
        )
        if self._opening:
            ud.last_agent_message = self._opening
            await self.session.say(self._opening, add_to_chat_ctx=True)
        elif not _is_31:
            self.session.generate_reply(tool_choice="none")

    async def _legacy_on_enter_unused(self) -> None:
        # Kept only as a reference to where the old multi-marker system
        # used to live. The dead-code body below preserves the strip
        # invocations and persona text we used to inject; nothing here
        # runs anymore.
        from agent import (
            PROMPT_HISTORY_ITEMS,
            _FLOW_CONTEXT_PROMPT_MARKER,
            _FLOW_STYLE_PROMPT_MARKER,
            _TURN_CAP_PROMPT_MARKER,
            _TURN_GUARD_PROMPT_MARKER,
            _flow_missing_phone,
            _limit_chat_ctx_preserving_system,
            _recent_chat_ctx_non_system_items,
            _set_phone_capture_mode,
            _strip_marked_system_messages,
        )
        ud: UserData = self.session.userdata
        flow = self.__class__.__name__.lower()
        ud.active_flow = flow
        logger.info("call=%s | agent=%s", ud.call_id, self.__class__.__name__)
        desired_phone_mode = _flow_missing_phone(flow, ud)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)
        if ud.last_guard_flow and ud.last_guard_flow != flow:
            ud.last_guard_flow = None
            ud.last_guard_signature = None

        chat_ctx = self.chat_ctx.copy()
        _strip_marked_system_messages(
            chat_ctx,
            _FLOW_STYLE_PROMPT_MARKER,
            _FLOW_CONTEXT_PROMPT_MARKER,
            _TURN_GUARD_PROMPT_MARKER,
            _TURN_CAP_PROMPT_MARKER,
        )
        _limit_chat_ctx_preserving_system(
            chat_ctx,
            max_non_system_items=PROMPT_HISTORY_ITEMS,
        )
        if isinstance(ud.prev_agent, Agent):
            prev_ctx = ud.prev_agent.chat_ctx.copy(
                exclude_instructions=True,
                exclude_function_call=False,
                exclude_handoff=True,
                exclude_config_update=True,
            )
            prev_items = _recent_chat_ctx_non_system_items(
                prev_ctx,
                max_items=PROMPT_HISTORY_ITEMS,
            )
            def _ctx_item_key(item: object) -> tuple[str, str]:
                role = str(getattr(item, "role", ""))
                text = str(getattr(item, "text_content", "") or getattr(item, "content", ""))
                return role, " ".join(text.split())

            seen = {
                getattr(item, "id", None) or _ctx_item_key(item)
                for item in chat_ctx.items
            }
            chat_ctx.items.extend(
                item
                for item in prev_items
                if (getattr(item, "id", None) or _ctx_item_key(item)) not in seen
            )

        _strip_marked_system_messages(
            chat_ctx,
            _FLOW_STYLE_PROMPT_MARKER,
            _FLOW_CONTEXT_PROMPT_MARKER,
        )

        # Pure persona/style instructions. NO state in here — the
        # ``TURN_GUARD`` marker injected per turn is the single source
        # of truth for what's captured. Embedding state here at
        # ``on_enter`` time froze a stale snapshot for the rest of the
        # flow, which made the LLM see contradicting summaries (old
        # flow-style summary vs. fresh turn-guard state) and second-
        # guess what the customer had already told it. That's exactly
        # the "بينسي" behaviour the user described.
        # (legacy body removed; the live ``on_enter`` now uses the
        # canonical LiveKit pattern above.)
        return None

    def _stable_replies(self, ud: "UserData") -> list[str]:
        """Static reply strings this flow uses verbatim. Subclasses can
        override to register additional cacheable phrases."""
        replies: list[str] = []
        try:
            from agent import (
                _delivery_zone_user_message,
                _menu_response_for_flow,
            )
            cfg = getattr(ud, "restaurant", None)
            if cfg is not None:
                # The menu read-out is the single biggest TTS cost in a
                # call (7+ seconds of audio). Caching it eliminates that
                # latency on the second and later calls per worker.
                replies.append(_menu_response_for_flow("delivery", cfg))
                replies.append(_menu_response_for_flow("takeaway", cfg))
                replies.append(_delivery_zone_user_message(cfg))
        except Exception:
            pass
        # Common fillers used verbatim by ``_handle_post_completion``.
        replies.extend(
            [
                "العفو يا فندم!",
                "ولا يهمك!",
                "بالهنا والشفا!",
                "تسلم يا فندم، نورتنا!",
                "الله يخليك، في أي وقت!",
                "العفو، نورتنا يا فندم!",
                "تحت أمرك!",
                "في أي حاجة تانية يا فندم؟",
                "لو محتاج أي حاجة تاني كلمنا!",
                "نورتنا يا فندم!",
                "تمام يا فندم، تحت أمرك.",
                "حاضر يا فندم، نورتنا.",
                "ماشي يا فندم، في أي حاجة تانية؟",
                "ممكن تعيدها يا فندم؟",
                "مش واضح، قوللي تاني؟",
                "ممكن تقولها تاني لو سمحت؟",
                "اتفضل يا فندم، قوللي تاني؟",
            ]
        )
        return [r for r in replies if r]

    async def _say_and_stop(self, text: str, *, critical: bool = False) -> None:
        from utils.voice import _voice_safe_text
        if self._turn_responded:
            logger.warning(
                "call=%s | say_and_stop skipped | reason=already_responded | agent=%s",
                self.session.userdata.call_id,
                self.__class__.__name__,
            )
            raise StopResponse()
        self._turn_responded = True
        self._sync_phone_capture_mode()
        spoken_text = text if critical else _voice_safe_text(text, max_chars=180)
        self.session.userdata.last_agent_message = spoken_text
        if not self.session.userdata.turn_trace_decision_mode:
            self._mark_turn_decision("deterministic", "say_and_stop")
        # ``session.say`` is a sync method that returns a SpeechHandle
        # immediately after the text is enqueued. Awaiting the handle
        # waits for full playout. The enqueue metric must capture only
        # the enqueue step — measuring across the playout wait was
        # making ``tts_enqueue`` look like 3-5s when it's really <50ms
        # (the rest is just audio playback time).
        tts_start = time.monotonic()
        handle = self.session.say(
            spoken_text,
            allow_interruptions=True,
            add_to_chat_ctx=True,
        )
        self.session.userdata.turn_trace_tts_enqueue_ms = max(0, int((time.monotonic() - tts_start) * 1000))
        # Now wait for playout so the call flow stays in sync with TTS.
        await handle
        self._finish_turn_trace("responded")
        raise StopResponse()

    def _tool_context(self) -> SimpleNamespace:
        return SimpleNamespace(userdata=self.session.userdata, session=self.session)

    async def _apply_pipeline_result(self, result, ud, flow: str, turn_num: int) -> None:
        """Execute a ``deterministic_pipeline.PipelineResult``.

        Speaks the message, switches the active agent, or marks the call
        for closure as appropriate. Telemetry is emitted with the
        ``decision_reason`` from the pipeline so the dashboards can
        track which paths bypass the LLM.
        """
        from agent import _emit_event

        action = result.action
        call_id = ud.call_id or "-"
        logger.info(
            "call=%s | pipeline match | flow=%s | action=%s | reason=%s | "
            "intent=%s | confidence=%.2f | matched=%s",
            call_id, flow, action, result.decision_reason,
            result.intent or "-", float(result.confidence or 0.0),
            list(result.matched_terms) or "-",
        )
        _emit_event(
            "pipeline.decision",
            call_id=call_id,
            flow=flow,
            turn=turn_num,
            action=action,
            reason=result.decision_reason,
            intent=result.intent or "",
            confidence=round(float(result.confidence or 0.0), 3),
            matched_terms=list(result.matched_terms),
        )
        self._mark_turn_decision("deterministic", f"pipeline_{result.decision_reason}")

        if action == "say":
            await self._say_and_stop(result.message)  # raises StopResponse internally
            return

        if action == "submit":
            submitter_name = {
                "takeaway": "confirm_order",
                "delivery": "confirm_delivery",
                "reservation": "confirm_reservation",
            }.get(flow)
            submitter = getattr(self, submitter_name or "", None)
            if submitter is None:
                logger.warning(
                    "call=%s | pipeline submit target missing | flow=%s",
                    call_id, flow,
                )
                self._finish_turn_trace("submit_missing_target")
                return
            reply = await submitter(context=self._tool_context())
            await self._say_and_stop(reply, critical=True)
            return

        if action == "cancel":
            ud.session_transitional_state = True
            try:
                await self.session.say(
                    result.message,
                    allow_interruptions=False,
                    add_to_chat_ctx=False,
                )
            except Exception:
                pass
            self._finish_turn_trace("cancelled")
            return

        if action in ("handoff", "flow_change"):
            target = (result.target_flow or "").strip().lower()
            if target and target in (ud.agents or {}):
                ud.prev_agent = self
                ud.handoff_target = target
                self.session.update_agent(ud.agents[target])
                _emit_event(
                    "flow.transfer",
                    call_id=call_id,
                    flow=flow,
                    mode="deterministic",
                    source=flow,
                    target=target,
                    result="success",
                )
                self._finish_turn_trace("handed_off")
                return
            logger.warning(
                "call=%s | pipeline handoff target missing | target=%s",
                call_id, target,
            )
            self._finish_turn_trace("handoff_missing_target")
            return

        # ``fallback_llm`` is handled by the caller; anything else is a
        # programming error.
        logger.error(
            "call=%s | unhandled pipeline action | action=%s",
            call_id, action,
        )
        self._finish_turn_trace("pipeline_unhandled")

    def _transfer_live(self, name: str) -> bool:
        from agent import _emit_event

        ud: UserData = self.session.userdata
        current = self.session.current_agent
        current_name = current.__class__.__name__.lower()
        if current_name == name:
            logger.warning("call=%s | live transfer skipped | reason=self | agent=%s", ud.call_id, name)
            _emit_event(
                "flow.transfer",
                call_id=ud.call_id,
                flow=current_name,
                mode="live",
                source=current_name,
                target=name,
                result="skipped_self",
            )
            return False
        target = ud.agents.get(name)
        if target is None:
            logger.error("call=%s | live transfer target missing: %s", ud.call_id, name)
            _emit_event(
                "flow.transfer",
                call_id=ud.call_id,
                flow=current_name,
                mode="live",
                source=current_name,
                target=name,
                result="missing_target",
            )
            return False
        logger.info("call=%s | live transfer | %s -> %s", ud.call_id, self.__class__.__name__, name)
        _emit_event(
            "flow.transfer",
            call_id=ud.call_id,
            flow=current_name,
            mode="live",
            source=current_name,
            target=name,
            result="success",
        )
        ud.prev_agent = current
        ud.handoff_target = name
        self.session.update_agent(target)
        return True

    def _process_order_update(
        self,
        items: list[str],
        context: RunContext_T,
        *,
        flow_name: str,
        min_order_total: float = 0.0,
    ) -> str:
        from agent import (
            _ack_got,
            _available_menu_items,
            _get_upsell_suggestion,
            _merge_incremental_order_items,
            _merge_incremental_raw_order_items,
            _menu_unavailable_user_message,
            _normalize_order_items,
            _order_update_is_incremental,
        )
        from utils.money import money2ar
        from utils.voice import _voice_safe_text

        ud = context.userdata
        if not items:
            return _voice_safe_text("الطلب فاضي.")

        if not _available_menu_items(self.cfg):
            normalized_items = [item.strip() for item in items if item.strip()]
            if not normalized_items:
                return _menu_unavailable_user_message(self.cfg)
            if _order_update_is_incremental(ud.last_user_message):
                normalized_items = _merge_incremental_raw_order_items(ud.order, normalized_items)
            ud.order = normalized_items
            ud.order_validated = False
            ud.order_total = 0.0
            logger.warning(
                "call=%s | %s order captured without menu validation",
                ud.call_id,
                flow_name,
            )
            return _voice_safe_text(
                f"تمام يا فندم، {_ack_got(', '.join(normalized_items))}.",
                max_chars=180,
            )

        normalized_items, unknown, total = _normalize_order_items(items, self.cfg.menu_items)
        if (
            not unknown
            and normalized_items
            and _order_update_is_incremental(ud.last_user_message)
        ):
            normalized_items, unknown, total = _merge_incremental_order_items(
                ud.order,
                normalized_items,
                self.cfg.menu_items,
            )
        if unknown and not normalized_items:
            return _voice_safe_text(
                f"معلش يا فندم، '{', '.join(unknown)}' مش موجود عندنا. {self.cfg.menu_text()} تحب تطلب إيه منهم؟",
                max_chars=220,
            )

        if unknown:
            ud.order = normalized_items
            ud.order_validated = True
            ud.order_total = total
            return _voice_safe_text(
                f"سجلت {', '.join(normalized_items)} بس '{', '.join(unknown)}' مش في المنيو. تحب تبدلها بحاجة تانية؟",
                max_chars=200,
            )

        if min_order_total > 0 and total < min_order_total:
            return _voice_safe_text(
                f"أقل طلب للتوصيل {money2ar(min_order_total)} جنيه. "
                f"طلبك دلوقتي {money2ar(total)} جنيه. "
                "تحب تضيف حاجة؟"
            )

        ud.order = normalized_items
        ud.order_validated = True
        ud.order_total = total
        upsell = _get_upsell_suggestion(ud, self.cfg)
        # The tool result is what the LLM sees — not what the customer
        # hears. Front-load the *new state* so the LLM cannot hallucinate
        # what's in the order on the next edit. Voice text comes after
        # so a tool-call→reply round-trip still produces a clean line.
        items_phrase = ", ".join(normalized_items)
        state_line = f"[الطلب الحالي: {items_phrase}، الإجمالي {money2ar(total)} جنيه]"
        if upsell:
            return _voice_safe_text(
                f"{state_line} تمام، {_ack_got(items_phrase)}. {upsell}",
                max_chars=240,
            )
        return _voice_safe_text(
            f"{state_line} تمام، {_ack_got(items_phrase)}.",
            max_chars=200,
        )

    async def _handle_pending_upsell(
        self,
        user_text: str,
        *,
        flow_name: str,
        post_upsell_prompt: Callable[[], str],
    ) -> None:
        from agent import (
            _accept_pending_upsell,
            _ack,
            _ask_special,
            _clear_pending_upsell,
            _emit_event,
            _extract_special_request_after_upsell_reply,
            _is_explicit_upsell_acceptance,
            _is_explicit_upsell_rejection,
            _is_positive_confirmation,
            _join_user_phrases,
            _special_request_followup_message,
            _upsell_reply_negates_special,
        )
        from correction_detector import detect_correction
        from utils.voice import _voice_safe_text

        ud = self.session.userdata
        pending_item = ud.pending_upsell_item
        if not pending_item:
            return
        correction = detect_correction(user_text)
        if correction.kind == "cancel" and correction.is_actionable():
            return

        if _is_explicit_upsell_acceptance(user_text, pending_item):
            logger.info("call=%s | %s upsell accepted | item=%s", ud.call_id, flow_name, pending_item)
            accepted_item = _accept_pending_upsell(ud, self.cfg, user_text=user_text) or "الإضافة"
            _emit_event(
                "upsell.accepted",
                call_id=ud.call_id,
                flow=flow_name,
                item=accepted_item,
            )
            special_request = _extract_special_request_after_upsell_reply(user_text, pending_item)
            if special_request:
                ud.special_requests = special_request
                await self._say_and_stop(
                    _special_request_followup_message(flow_name, ud, accepted_item=accepted_item)
                )
            if _upsell_reply_negates_special(user_text):
                ud.special_requests = None
                await self._say_and_stop(
                    _voice_safe_text(
                        _join_user_phrases(
                            f"تمام يا فندم، ضفت {accepted_item}",
                            post_upsell_prompt(),
                        ),
                        max_chars=180,
                    )
                )
            await self._say_and_stop(
                _voice_safe_text(
                    _join_user_phrases(f"تمام يا فندم، ضفت {accepted_item}", _ask_special()),
                    max_chars=180,
                )
            )

        if _is_positive_confirmation(user_text) or _is_explicit_upsell_rejection(user_text):
            logger.info(
                "call=%s | %s upsell skipped | item=%s | text=%r",
                ud.call_id,
                flow_name,
                pending_item,
                user_text,
            )
            _emit_event(
                "upsell.rejected",
                call_id=ud.call_id,
                flow=flow_name,
                item=pending_item,
            )
            _clear_pending_upsell(ud, accepted=False)
            special_request = _extract_special_request_after_upsell_reply(user_text, pending_item)
            if special_request:
                ud.special_requests = special_request
                await self._say_and_stop(_special_request_followup_message(flow_name, ud))
            if _upsell_reply_negates_special(user_text):
                ud.special_requests = None
                await self._say_and_stop(
                    _voice_safe_text(
                        _join_user_phrases(_ack(), post_upsell_prompt()),
                        max_chars=180,
                    )
                )
            await self._say_and_stop(
                _voice_safe_text(_join_user_phrases(_ack(), _ask_special()), max_chars=180)
            )

        logger.info(
            "call=%s | %s pending upsell cleared for next turn | item=%s",
            ud.call_id,
            flow_name,
            pending_item,
        )
        _clear_pending_upsell(ud, accepted=False)

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        return False

    def _turn_guard_message(self, user_text: str) -> str:
        from agent import _flow_turn_guard_message
        flow = self.__class__.__name__.lower()
        return _flow_turn_guard_message(flow, self.session.userdata, user_text)

    async def _handle_quick_intercepts(self, flow: str, ud: "UserData", user_text: str) -> bool:
        from agent import (
            _delivery_zone_user_message,
            _is_delivery_zone_question,
            _is_menu_question,
            _is_total_question,
            _menu_response_for_flow,
            _order_total_user_message,
        )
        if flow in {"takeaway", "delivery"} and _is_total_question(user_text):
            logger.info("call=%s | total turn intercepted | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_order_total_user_message(flow, ud, ud.restaurant))
        elif flow in {"greeter", "delivery"} and _is_delivery_zone_question(user_text):
            logger.info("call=%s | delivery_zones_intercepted | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_delivery_zone_user_message(ud.restaurant))
        elif flow in {"takeaway", "delivery"} and _is_menu_question(user_text):
            logger.info("call=%s | menu turn intercepted | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_menu_response_for_flow(flow, ud.restaurant))
        return False

    async def _handle_post_completion(self, flow: str, ud: "UserData", user_text: str) -> bool:
        import random as _random
        from agent import _is_positive_confirmation, _is_thanks_message
        if not (ud.order_confirmed or ud.reservation_confirmed or ud.complaint_logged):
            return False
        if _is_thanks_message(user_text):
            logger.info("call=%s | post_completion_thanks | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_random.choice([
                "العفو يا فندم!",
                "ولا يهمك!",
                "بالهنا والشفا!",
                "تسلم يا فندم، نورتنا!",
                "الله يخليك، في أي وقت!",
                "العفو، نورتنا يا فندم!",
            ]))
        elif _is_positive_confirmation(user_text):
            logger.info("call=%s | post_completion_ack | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_random.choice([
                "تحت أمرك!",
                "في أي حاجة تانية يا فندم؟",
                "لو محتاج أي حاجة تاني كلمنا!",
                "نورتنا يا فندم!",
            ]))
        else:
            logger.info("call=%s | post_completion_generic | flow=%s", ud.call_id, flow)
            await self._say_and_stop(_random.choice([
                "تمام يا فندم، تحت أمرك.",
                "حاضر يا فندم، نورتنا.",
                "ماشي يا فندم، في أي حاجة تانية؟",
            ]))
        return False

    async def _handle_name_intercept(self, flow: str, ud: "UserData", user_text: str) -> bool:
        from agent import (
            _apply_name_update,
            _flow_missing_name,
            _is_likely_non_name_response,
        )
        from core.extractors.contact_extractor import (
            MEDIUM_CONFIDENCE,
            extract_name,
        )
        from core.telemetry import emit_event
        from core.understanding import get_or_extract_for_turn
        from core.understanding_bridge import name_from_understanding
        if not _flow_missing_name(flow, ud):
            return False
        if ud.pending_upsell_item or _is_likely_non_name_response(user_text):
            return False

        # Prefer the LLM-extracted name; fall back to the deterministic
        # extractor (with confidence gating) when no provider is wired
        # up.
        name_value: str | None = None
        confidence = 0.0
        reason = "llm_understanding"
        try:
            understanding = get_or_extract_for_turn(ud, user_text, flow)
            llm_name = name_from_understanding(understanding)
            if llm_name:
                name_value = llm_name
                confidence = 0.9
        except Exception:
            llm_name = None

        if name_value is None:
            capture = extract_name(user_text)
            if capture.value is None or capture.confidence < MEDIUM_CONFIDENCE:
                return False
            name_value = capture.value
            confidence = capture.confidence
            reason = capture.reason

        emit_event(
            "slot.captured",
            call_id=ud.call_id or "",
            flow=flow,
            slot="customer_name",
            confidence=round(confidence, 3),
            reason=reason,
            source="llm" if reason == "llm_understanding" else "phase3_extractor",
        )
        logger.info(
            "call=%s | name turn intercepted | flow=%s | confidence=%.2f | reason=%s",
            ud.call_id,
            flow,
            confidence,
            reason,
        )
        await self._say_and_stop(await _apply_name_update(ud, name_value, flow_name=flow))
        return True

    async def _handle_phone_intercept(self, flow: str, ud: "UserData", user_text: str) -> bool:
        from agent import _apply_phone_update
        from core.extractors.contact_extractor import extract_phone
        from core.telemetry import emit_event
        from nlp.arabic import normalize_ar as _normalize_ar
        from nlp.phone_extract import phone_digits_only as _phone_digits_only, is_phone_like_text as _is_phone_like_text
        phone_missing = flow in {"delivery", "takeaway", "reservation", "complaint"} and not bool(
            getattr(ud, "customer_phone", None)
        )
        if not phone_missing:
            return False
        digits = _phone_digits_only(user_text)
        normalized = _normalize_ar(user_text)
        phone_cue = any(cue in normalized for cue in ("رقم", "موبايل", "تليفون", "نمرة", "نمره"))
        if not (
            _is_phone_like_text(user_text)
            or (digits and phone_cue)
            or (digits and bool(ud.pending_phone_digits))
        ):
            return False
        # Surface the confidence breakdown for QA traces; the actual digit
        # buffering and validation continues through ``_apply_phone_update``
        # so multi-turn dictation ("0101", then "234 5678") keeps working.
        capture = extract_phone(user_text)
        emit_event(
            "slot.captured",
            call_id=ud.call_id or "",
            flow=flow,
            slot="customer_phone",
            confidence=round(capture.confidence, 3),
            reason=capture.reason,
            source="phase3_extractor",
            validated=bool(capture.value),
        )
        logger.info(
            "call=%s | phone turn intercepted | flow=%s | confidence=%.2f | reason=%s",
            ud.call_id,
            flow,
            capture.confidence,
            capture.reason,
        )
        phone_reply = await _apply_phone_update(ud, user_text, flow_name=flow)
        if phone_reply:
            await self._say_and_stop(phone_reply)
        # phone_reply empty means digits were processed but no user-facing message
        # was generated (e.g. 11+ digits buffered but invalid). Don't silently
        # swallow the turn — let it continue to LLM which can ask for correction.
        return True

    def _emit_extractor_signals(
        self,
        flow: str,
        ud: "UserData",
        user_text: str,
        turn_num: int,
    ) -> None:
        """Surface what the LLM understood from this turn.

        This is the single per-turn extraction call. The result is
        cached on ``UserData`` so the order / contact / address
        intercept callers reuse it without paying a second LLM round-
        trip. When no LLM provider is configured (tests, dev without
        an API key), the event still fires with ``source=no_provider``
        and the legacy extractor path takes over downstream.
        """
        from agent import _emit_event
        from core.understanding import get_or_extract_for_turn

        try:
            understanding = get_or_extract_for_turn(ud, user_text, flow)
        except Exception:
            return

        _emit_event(
            "turn.signals",
            call_id=ud.call_id or "",
            flow=flow,
            turn=turn_num,
            source=understanding.source,
            extraction_ms=understanding.extraction_ms,
            intent=understanding.intent,
            intent_confidence=understanding.intent_confidence,
            mutation=understanding.mutation,
            order_items_count=len(understanding.order_items),
            order_items=[
                {"item": item.item_name, "qty": item.quantity}
                for item in understanding.order_items[:8]
            ],
            customer_name_present=bool(understanding.customer_name),
            customer_phone_digits_len=len(understanding.customer_phone_digits or ""),
            delivery_address_present=bool(understanding.delivery_address),
            delivery_zone=understanding.delivery_zone or "",
            reservation_time_present=bool(understanding.reservation_time),
            guests_count=understanding.guests_count,
            complaint_category=understanding.complaint_category or "",
            is_confirming=understanding.is_confirming,
            is_denying=understanding.is_denying,
            error=understanding.error,
        )

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        from agent import (
            MAX_TURNS_PER_SESSION,
            TURN_CAP_GRACE_TURNS,
            TURN_CAP_WARNING_TURNS,
            TURN_CHAT_CTX_MAX_ITEMS,
            _FLOW_CONTEXT_PROMPT_MARKER,
            _TURN_CAP_PROMPT_MARKER,
            _TURN_GUARD_PROMPT_MARKER,
            _chat_message_text,
            _emit_event,
            _flow_missing_phone,
            _flow_turn_guard_message,
            _limit_chat_ctx_preserving_system,
            _increment_turn_count,
            _is_near_turn_cap_completion,
            _set_phone_capture_mode,
            _strip_marked_system_messages,
            _turn_cap_system_message,
        )
        from core.telemetry import snapshot_slots
        from nlp.arabic import normalize_ar as _normalize_ar

        user_text = _chat_message_text(new_message)
        flow = self.__class__.__name__.lower()
        ud = self.session.userdata
        self._turn_responded = False

        turn_num = _increment_turn_count(ud.call_id)
        ud.turn_trace_started_monotonic = time.monotonic()
        ud.turn_trace_slot_before = snapshot_slots(ud)
        ud.turn_trace_current_turn = turn_num
        ud.turn_trace_decision_mode = ""
        ud.turn_trace_decision_reason = ""
        ud.turn_trace_engine_decision_ms = None
        ud.turn_trace_tts_enqueue_ms = None
        ud.turn_trace_finished = False
        _emit_event("turn.received", call_id=ud.call_id, flow=flow, turn=turn_num)

        nearing_turn_cap = turn_num >= max(1, MAX_TURNS_PER_SESSION - TURN_CAP_WARNING_TURNS)
        grace_turn_active = (
            TURN_CAP_GRACE_TURNS > 0
            and turn_num >= MAX_TURNS_PER_SESSION
            and turn_num < MAX_TURNS_PER_SESSION + TURN_CAP_GRACE_TURNS
            and _is_near_turn_cap_completion(flow, ud)
        )

        if turn_num >= MAX_TURNS_PER_SESSION and not grace_turn_active:
            logger.warning("call=%s | turn cap reached | turns=%d | max=%d", ud.call_id, turn_num, MAX_TURNS_PER_SESSION)
            await self._say_and_stop("معلش يا فندم المكالمة طولت شوية. كلمنا تاني في أي وقت، نورتنا!", critical=True)
        if grace_turn_active:
            logger.info(
                "call=%s | turn cap grace | flow=%s | turn=%d | max=%d",
                ud.call_id,
                flow,
                turn_num,
                MAX_TURNS_PER_SESSION,
            )
        elif nearing_turn_cap:
            logger.info(
                "call=%s | approaching turn cap | flow=%s | turn=%d | max=%d",
                ud.call_id,
                flow,
                turn_num,
                MAX_TURNS_PER_SESSION,
            )

        normalized_user_text = _normalize_ar(user_text)
        if not normalized_user_text:
            logger.info("call=%s | empty transcript ignored | flow=%s | text=%r", ud.call_id, flow, user_text)
            self._mark_turn_decision("deterministic", "empty_transcript")
            self._finish_turn_trace("ignored")
            raise StopResponse()
        ud.last_user_message = user_text
        # Single-char transcripts are almost always STT artifacts picked up from
        # background noise (TV, traffic, crosstalk). Re-prompt with a soft rotating
        # phrase instead of letting the LLM guess. Skip in phone-capture mode where
        # digit-by-digit fragments are legitimate input.
        if len(normalized_user_text) < 2 and not ud.phone_capture_mode:
            import random as _random
            logger.info(
                "call=%s | too-short transcript treated as noise | flow=%s | text=%r | norm=%r",
                ud.call_id, flow, user_text, normalized_user_text,
            )
            reprompt = _random.choice([
                "ممكن تعيدها يا فندم؟",
                "مش واضح، قوللي تاني؟",
                "ممكن تقولها تاني لو سمحت؟",
                "اتفضل يا فندم، قوللي تاني؟",
            ])
            await self._say_and_stop(reprompt)

        desired_phone_mode = _flow_missing_phone(flow, ud) or bool(ud.pending_phone_digits)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)
        self._sync_phone_capture_mode()

        if await self._handle_post_completion(flow, ud, user_text):
            self._finish_turn_trace("handled")
            raise StopResponse()

        if await self._handle_quick_intercepts(flow, ud, user_text):
            self._finish_turn_trace("handled")
            raise StopResponse()

        # All deterministic intercepts removed. Silent state mutation
        # (capturing name/phone via regex, routing via keyword match,
        # menu/total auto-replies) caused the LLM to see captured slots
        # in the YAML snapshot without a matching tool-call in chat
        # history → the model thought it already acknowledged them and
        # either re-asked or hallucinated. Now the only writer is the
        # LLM via @function_tool, exactly like the canonical LiveKit
        # multi-agent reference. The flow-level handlers may still
        # branch on explicit prompt-state (e.g. pending upsell), but
        # they cannot read or mutate the user's text.
        if await self._maybe_handle_turn_deterministically(user_text):
            self._mark_turn_decision("deterministic", "flow_fast_path")
            self._finish_turn_trace("handled")
            raise StopResponse()

        # Generic deterministic pipeline. Runs after the flow's own
        # ``_maybe_handle_turn_deterministically`` so per-flow logic gets
        # first crack. Resolves cancels, mid-call flow changes, greeter
        # routing the flow handler missed, and explicit confirmation
        # rejections — all without an LLM call. Returns ``fallback_llm``
        # when the turn needs the LLM, in which case execution continues
        # to the chat-context / LLM fallback block below.
        try:
            from deterministic_pipeline import run_pipeline as _run_pipeline
        except Exception:  # pragma: no cover — defensive
            _run_pipeline = None
        if _run_pipeline is not None:
            try:
                pipeline_result = _run_pipeline(
                    text=user_text,
                    flow=flow,
                    ud=ud,
                    available_flows=tuple((ud.agents or {}).keys()) or (
                        "greeter", "delivery", "takeaway", "reservation", "complaint",
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "call=%s | deterministic pipeline error | %s", ud.call_id, exc,
                )
                pipeline_result = None
            if pipeline_result is not None and pipeline_result.action != "fallback_llm":
                await self._apply_pipeline_result(pipeline_result, ud, flow, turn_num)
                raise StopResponse()

        if await self._handle_phone_intercept(flow, ud, user_text):
            self._finish_turn_trace("handled")
            raise StopResponse()
        if await self._handle_name_intercept(flow, ud, user_text):
            self._finish_turn_trace("handled")
            raise StopResponse()

        # Strip the previous flow-state snapshot and any turn-cap / context /
        # turn-guard markers, then re-inject ONE fresh state snapshot.
        #
        # Why this matters: the on_enter system message captured ``ud.summarize()``
        # at the moment the agent became active. Tool calls during the call
        # mutate ``ud`` (name, phone, order, address, …), but the system
        # message stayed frozen — so GPT-4o saw "customer_phone: unknown" while
        # chat history said "تمام، اتسجل التليفون 0123…". Models trust the
        # system block, so they re-asked for already-captured slots ("بينسي
        # هو سأل علي إيه"). Refreshing it every turn closes that gap.
        from agent import _FLOW_STATE_PROMPT_MARKER, SESSION_LLM_NO_THINK
        _strip_marked_system_messages(
            turn_ctx,
            _TURN_GUARD_PROMPT_MARKER,
            _TURN_CAP_PROMPT_MARKER,
            _FLOW_CONTEXT_PROMPT_MARKER,
            _FLOW_STATE_PROMPT_MARKER,
        )
        _limit_chat_ctx_preserving_system(turn_ctx, max_items=TURN_CHAT_CTX_MAX_ITEMS)
        agent_name = self.__class__.__name__
        no_think_prefix = "/no_think\n" if SESSION_LLM_NO_THINK else ""
        turn_ctx.add_message(
            role="system",
            content=(
                f"{_FLOW_STATE_PROMPT_MARKER}\n"
                f"{no_think_prefix}"
                f"You are the {agent_name} agent at مطعم {ud.restaurant.name}.\n"
                f"Talk to the customer in natural Egyptian Arabic — you are a "
                f"human waiter, not a bot. Don't repeat captured information. "
                f"Use the tools to record what the customer says.\n\n"
                f"Current call state:\n{ud.summarize()}"
            ),
        )
        if nearing_turn_cap:
            turn_ctx.add_message(
                role="system",
                content=(
                    f"{_TURN_CAP_PROMPT_MARKER}\n"
                    f"{_turn_cap_system_message(flow, ud, in_grace=grace_turn_active)}"
                ),
            )

        self._mark_turn_decision("llm_fallback", "fell_through_to_llm")
        _emit_event("turn.llm_fallback", call_id=ud.call_id, flow=flow, turn=turn_num)
        _emit_event("fallback.triggered", call_id=ud.call_id, flow=flow, turn=turn_num, reason="llm_fallback")
        self._finish_turn_trace("llm_fallback")

    async def _transfer(self, name: str, context: RunContext_T) -> "Agent | str":
        """Transfer to another agent.

        Returns the target ``Agent`` directly (no string tuple) so livekit
        sets ``reply_required=False`` on the tool call. That skips the
        second LLM round-trip the framework would otherwise fire after
        a tool that returns ``(agent, "...")``. Net saving: ~1 second
        per transfer (validated against real-call METRICS TURN logs).

        The target agent's ``on_enter`` already speaks its own opening
        line via ``session.say``, so the customer hears a fresh greeting
        from the new flow without a second LLM call to paraphrase.
        """
        from agent import _emit_event

        ud = context.userdata
        current = context.session.current_agent
        current_name = current.__class__.__name__.lower()
        if current_name == name:
            logger.warning("call=%s | skipped self-transfer for agent=%s", ud.call_id, name)
            _emit_event(
                "flow.transfer",
                call_id=ud.call_id,
                flow=current_name,
                mode="handoff",
                source=current_name,
                target=name,
                result="skipped_self",
            )
            # Self-transfer is a model mistake; return a string so the
            # LLM gets a reply cycle to recover. Rare path.
            return ""
        if name not in ud.agents:
            logger.error("call=%s | transfer target missing: %s", ud.call_id, name)
            _emit_event(
                "flow.transfer",
                call_id=ud.call_id,
                flow=current_name,
                mode="handoff",
                source=current_name,
                target=name,
                result="missing_target",
            )
            return "الخدمة دي مش متاحة دلوقتي."
        logger.info("call=%s | %s → %s", ud.call_id, self.__class__.__name__, name)
        _emit_event(
            "flow.transfer",
            call_id=ud.call_id,
            flow=current_name,
            mode="handoff",
            source=current_name,
            target=name,
            result="success",
        )
        ud.prev_agent = current
        ud.handoff_target = name
        # Return the target Agent ALONE (not a tuple). LiveKit's
        # ``make_tool_output`` recognises this as a handoff with
        # ``reply_required=False``, so no LLM₂ round-trip.
        return ud.agents[name]


# ─────────────────────────────────────────────────────────────────────────────
# Shared tools — used by multiple flow agents
# ─────────────────────────────────────────────────────────────────────────────

@function_tool()
async def update_name(
    name: Annotated[str, Field(description="اسم العميل واكتبه بالعربي الصوتي حتى لو اتقال بالإنجليزي")],
    context: RunContext_T,
) -> str:
    async def _impl() -> str:
        from agent import _apply_name_update, _current_flow_name
        return await _apply_name_update(
            context.userdata,
            name,
            flow_name=_current_flow_name(context),
        )

    return await _run_tool_safe_speak("update_name", context, _impl)


@function_tool()
async def update_phone(
    phone: Annotated[str, Field(description="رقم موبايل مصري بالأرقام فقط مثل 01012345678")],
    context: RunContext_T,
) -> str:
    async def _impl() -> str:
        from agent import _apply_phone_update, _current_flow_name
        return await _apply_phone_update(
            context.userdata,
            phone,
            flow_name=_current_flow_name(context),
        )

    return await _run_tool_safe_speak("update_phone", context, _impl)


@function_tool()
async def get_menu(context: RunContext_T) -> str:
    async def _impl() -> str:
        from agent import _current_flow_name, _menu_response_for_flow
        return _menu_response_for_flow(_current_flow_name(context), context.userdata.restaurant)

    return await _run_tool_safe_speak("get_menu", context, _impl)


@function_tool()
async def to_greeter(context: RunContext_T) -> str | Agent:
    async def _impl() -> Agent:
        curr: BaseAgent = context.session.current_agent
        return await curr._transfer("greeter", context)

    return await _run_tool_safely("to_greeter", context, _impl)
