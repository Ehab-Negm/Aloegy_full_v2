"""BaseAgent — shared base class for all flow agents."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, TypeVar

from livekit.agents import StopResponse, llm
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field

if TYPE_CHECKING:
    from state.user_data import UserData

logger = logging.getLogger("restaurant.agent")

RunContext_T = RunContext["UserData"]
ToolResultT = TypeVar("ToolResultT")
_TOOL_ERROR_MESSAGE = "معلش يا فندم، حصلت مشكلة عندنا. ممكن تقولي تاني؟"


async def _run_tool_safely(
    tool_name: str,
    context: RunContext_T,
    fn: Callable[[], Awaitable[ToolResultT]],
) -> ToolResultT | str:
    try:
        return await fn()
    except StopResponse:
        raise
    except Exception:
        call_id = getattr(getattr(context, "userdata", None), "call_id", "-") or "-"
        logger.exception("call=%s | tool error | tool=%s", call_id, tool_name)
        return _TOOL_ERROR_MESSAGE


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

    async def on_enter(self) -> None:
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
            seen = {
                getattr(item, "id", None) or f"object:{id(item)}"
                for item in chat_ctx.items
            }
            chat_ctx.items.extend(
                item
                for item in prev_items
                if (getattr(item, "id", None) or f"object:{id(item)}") not in seen
            )

        _strip_marked_system_messages(
            chat_ctx,
            _FLOW_STYLE_PROMPT_MARKER,
            _FLOW_CONTEXT_PROMPT_MARKER,
        )

        chat_ctx.add_message(
            role="system",
            content=(
                f"{_FLOW_STYLE_PROMPT_MARKER}\n"
                "شخصيتك:\n"
                "أنت إنسان شغال في مطعم — مش بوت ولا روبوت. اتكلم كأنك بتكلم صاحبك على التليفون.\n"
                "- خليك ودود وطبيعي، استخدم كلام مصري عادي زي ما الناس بتتكلم في الحياة.\n"
                "- نوّع في ردودك — متقولش نفس الجملة مرتين. لو قلت 'تمام' المرة اللي فاتت، قول 'حاضر' أو 'ماشي' المرة دي.\n"
                "- لو حد سألك سؤال عادي زي 'أنت مين' أو 'إيه الأخبار' رد عليه طبيعي الأول وبعدين كمّل شغلك.\n"
                "- لو حد قالك حاجة مضحكة أو غريبة، تفاعل معاه — ابتسم، علّق، وبعدين ارجع للموضوع بلطف.\n"
                "- متقولش كلام رسمي أو جمل محفوظة. مفيش 'يسعدنا خدمتكم' أو 'هل تود إضافة شيء آخر'.\n"
                "- خليك مختصر بس مش جاف — كلمة حلوة هنا وهنا بتفرق.\n"
                "- الأرقام بالكلام والأسماء بالعربي.\n"
                "- متضيفش أصناف أو كميات من عندك.\n"
                "- متكررش حاجة العميل قالها قبل كده.\n\n"
                f"بيانات العميل: {ud.summarize()}"
            ),
        )
        chat_ctx.add_message(
            role="system",
            content=(
                f"{_FLOW_CONTEXT_PROMPT_MARKER}\n"
                f"أنت دلوقتي في {self.__class__.__name__}. "
                "رد طبيعي بالمصري وكمّل على اللي ناقص. "
                "لو العميل سألك سؤال جانبي جاوبه الأول وبعدين ارجع للموضوع."
            ),
        )
        await self.update_chat_ctx(chat_ctx)
        self._sync_phone_capture_mode()

        if self._opening:
            await self.session.say(self._opening, add_to_chat_ctx=True)
        else:
            self.session.generate_reply(tool_choice="none")

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
        await self.session.say(
            spoken_text,
            allow_interruptions=True,
            add_to_chat_ctx=True,
        )
        raise StopResponse()

    def _tool_context(self) -> SimpleNamespace:
        return SimpleNamespace(userdata=self.session.userdata, session=self.session)

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
            _menu_unavailable_user_message,
            _normalize_order_items,
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
        if upsell:
            return _voice_safe_text(
                f"تمام يا فندم، {_ack_got(', '.join(normalized_items))}. {upsell}",
                max_chars=180,
            )
        return _voice_safe_text(
            f"تمام يا فندم، {_ack_got(', '.join(normalized_items))}.",
            max_chars=180,
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
        from utils.voice import _voice_safe_text

        ud = self.session.userdata
        pending_item = ud.pending_upsell_item
        if not pending_item:
            return

        if _is_explicit_upsell_acceptance(user_text, pending_item):
            logger.info("call=%s | %s upsell accepted | item=%s", ud.call_id, flow_name, pending_item)
            accepted_item = _accept_pending_upsell(ud, self.cfg) or "الإضافة"
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
        return False

    async def _handle_name_intercept(self, flow: str, ud: "UserData", user_text: str) -> bool:
        from agent import (
            _apply_name_update,
            _extract_name_candidate,
            _flow_missing_name,
            _is_likely_non_name_response,
        )
        if not _flow_missing_name(flow, ud):
            return False
        if ud.pending_upsell_item or _is_likely_non_name_response(user_text):
            return False
        candidate = _extract_name_candidate(user_text)
        if not candidate:
            return False
        logger.info("call=%s | name turn intercepted | flow=%s", ud.call_id, flow)
        await self._say_and_stop(await _apply_name_update(ud, candidate, flow_name=flow))
        return True

    async def _handle_phone_intercept(self, flow: str, ud: "UserData", user_text: str) -> bool:
        from agent import _apply_phone_update, _flow_missing_phone
        from nlp.phone_extract import is_phone_like_text as _is_phone_like_text
        if not _flow_missing_phone(flow, ud) or not _is_phone_like_text(user_text):
            return False
        logger.info("call=%s | phone turn intercepted | flow=%s", ud.call_id, flow)
        phone_reply = await _apply_phone_update(ud, user_text, flow_name=flow)
        if phone_reply:
            await self._say_and_stop(phone_reply)
        # phone_reply empty means digits were processed but no user-facing message
        # was generated (e.g. 11+ digits buffered but invalid). Don't silently
        # swallow the turn — let it continue to LLM which can ask for correction.
        return True

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        from agent import (
            MAX_TURNS_PER_SESSION,
            TURN_CAP_GRACE_TURNS,
            TURN_CAP_WARNING_TURNS,
            TURN_CHAT_CTX_MAX_ITEMS,
            _TURN_CAP_PROMPT_MARKER,
            _TURN_GUARD_PROMPT_MARKER,
            _chat_message_text,
            _emit_event,
            _flow_missing_phone,
            _limit_chat_ctx_preserving_system,
            _increment_turn_count,
            _is_near_turn_cap_completion,
            _set_phone_capture_mode,
            _should_add_turn_guard,
            _strip_marked_system_messages,
            _turn_guard_signature,
            _turn_cap_system_message,
        )
        from nlp.arabic import normalize_ar as _normalize_ar

        user_text = _chat_message_text(new_message)
        flow = self.__class__.__name__.lower()
        ud = self.session.userdata
        self._turn_responded = False

        turn_num = _increment_turn_count(ud.call_id)
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

        if not _normalize_ar(user_text):
            logger.info("call=%s | empty transcript ignored | flow=%s | text=%r", ud.call_id, flow, user_text)
            raise StopResponse()

        desired_phone_mode = _flow_missing_phone(flow, ud) or bool(ud.pending_phone_digits)
        if desired_phone_mode != ud.phone_capture_mode:
            _set_phone_capture_mode(ud, desired_phone_mode)
        self._sync_phone_capture_mode()

        await self._handle_quick_intercepts(flow, ud, user_text)
        await self._handle_post_completion(flow, ud, user_text)

        if await self._maybe_handle_turn_deterministically(user_text):
            raise StopResponse()

        await self._handle_name_intercept(flow, ud, user_text)
        await self._handle_phone_intercept(flow, ud, user_text)

        _strip_marked_system_messages(turn_ctx, _TURN_GUARD_PROMPT_MARKER, _TURN_CAP_PROMPT_MARKER)
        _limit_chat_ctx_preserving_system(turn_ctx, max_items=TURN_CHAT_CTX_MAX_ITEMS)
        if nearing_turn_cap:
            turn_ctx.add_message(
                role="system",
                content=(
                    f"{_TURN_CAP_PROMPT_MARKER}\n"
                    f"{_turn_cap_system_message(flow, ud, in_grace=grace_turn_active)}"
                ),
            )
        guard = self._turn_guard_message(user_text)
        guard_signature = _turn_guard_signature(flow, guard) if guard else ""
        should_add_guard = _should_add_turn_guard(
            user_text,
            flow=flow,
            current_guard=guard,
            previous_guard_signature=ud.last_guard_signature or "",
        )
        if guard and should_add_guard:
            ud.last_guard_flow = flow
            ud.last_guard_signature = guard_signature
            turn_ctx.add_message(
                role="system",
                content=(
                    f"{_TURN_GUARD_PROMPT_MARKER}\n"
                    f"العميل قال: {user_text or '—'}\n"
                    f"{guard}\n"
                    "رد عليه طبيعي كإنسان وكمّل."
                ),
            )
            _emit_event(
                "turn.guard",
                call_id=ud.call_id,
                flow=flow,
                turn=turn_num,
                user_text=(user_text or "")[:120],
                guard=(guard or "")[:240],
            )

    async def _transfer(self, name: str, context: RunContext_T) -> tuple[Agent, str]:
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
            return current, ""
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
            return current, "الخدمة دي مش متاحة دلوقتي."
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
        return ud.agents[name], ""


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

    return await _run_tool_safely("update_name", context, _impl)


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

    return await _run_tool_safely("update_phone", context, _impl)


@function_tool()
async def get_menu(context: RunContext_T) -> str:
    async def _impl() -> str:
        from agent import _current_flow_name, _menu_response_for_flow
        return _menu_response_for_flow(_current_flow_name(context), context.userdata.restaurant)

    return await _run_tool_safely("get_menu", context, _impl)


@function_tool()
async def to_greeter(context: RunContext_T) -> str | tuple[Agent, str]:
    async def _impl() -> tuple[Agent, str]:
        curr: BaseAgent = context.session.current_agent
        return await curr._transfer("greeter", context)

    return await _run_tool_safely("to_greeter", context, _impl)
