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
            delivery_line = "نوصل لحد عند العميل، " if cfg.delivery_enabled else ""
            to_delivery_tool = "to_delivery (لو عايز توصيل) / " if cfg.delivery_enabled else ""
            core = (
                "انت أول صوت العميل بيسمعه. اتكلم معاه زي أي موظف مطعم حقيقي — "
                "اسمع منه، افهم عايز إيه، ورد عليه طبيعي.\n\n"
                f"المطعم بيقدم: أكل للطلب (استلام أو توصيل لو مطلوب)، وحجز ترابيزة. "
                f"{delivery_line}فيه أصناف زي: {cfg.menu_names()}.\n\n"
                "لو العميل سأل سؤال تعارف (انتو مين، بتعملوا إيه، ده مطعم إيه) — جاوبه في "
                "جملة قصيرة عن المطعم وعرّفه على الخدمات، واسأله يحب يعمل إيه.\n\n"
                "لو واضح إن العميل عنده طلب محدد، استخدم الأداة المناسبة:\n"
                f"- عايز يطلب أكل → {to_delivery_tool}to_takeaway.\n"
                "- عايز يحجز ترابيزة → to_reservation.\n"
                "- عنده شكوى → to_complaint.\n"
                "- سأل عن المنيو → get_menu.\n"
                "- لو قال كلام مش واضح أو الـ STT ملخبطة → resolve_request.\n\n"
                "لو طلب أكل من غير ما يقول استلام ولا توصيل، اسأله بلسانك."
                + ("\nمهم: متاخدش طلب بنفسك، أنت بس بتوجّه للـ tool الصح." if True else "")
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
        ignored_tokens = {"يا", "فندم", "باشا", "استاذ", "أستاذ", "اسمي", "اسمى", "الاسم", "اسم"}
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
        """Capture name + phone the customer volunteered before stating intent.

        Order of precedence:
        1. LLM ``TurnUnderstanding`` — most reliable when configured.
        2. Inline ``اسمي ...`` regex — handles markered self-introductions.
        3. Legacy ``_extract_name_candidate`` — fallback when no LLM.

        The LLM correctly returns ``customer_name: null`` for greeting
        turns like "ألو" or "إزيك"; using it as the primary signal stops
        those phrases from being mis-captured as names.
        """
        from agent import _extract_name_candidate
        from core.understanding import get_or_extract_for_turn
        from core.understanding_bridge import (
            name_from_understanding,
            phone_digits_from_understanding,
        )
        from nlp.phone_extract import (
            phone_digits_only as _phone_digits_only,
            validate_phone,
        )

        ud = self.session.userdata

        understanding = None
        try:
            understanding = get_or_extract_for_turn(ud, user_text, "greeter")
        except Exception:
            understanding = None

        if not ud.customer_name:
            llm_name = name_from_understanding(understanding)
            if llm_name:
                ud.customer_name = llm_name
                logger.info(
                    "call=%s | greeter prefill name | source=llm | name=%s",
                    ud.call_id,
                    llm_name,
                )
            else:
                # Only fall back to the inline / legacy extractors when the
                # LLM didn't see a name. ``self._extract_inline_intro_name``
                # still requires an explicit "اسمي / أنا" marker so it's
                # safe; the bare ``_extract_name_candidate`` is the looser
                # one that used to capture "ألو" — only run it when the
                # LLM is unavailable.
                inline = self._extract_inline_intro_name(user_text)
                if inline:
                    ud.customer_name = inline
                    logger.info(
                        "call=%s | greeter prefill name | source=inline | name=%s",
                        ud.call_id,
                        inline,
                    )
                elif understanding is None or understanding.source not in {"llm", "parsed", "cache", "mock"}:
                    candidate = _extract_name_candidate(user_text)
                    if candidate:
                        ud.customer_name = candidate
                        logger.info(
                            "call=%s | greeter prefill name | source=legacy | name=%s",
                            ud.call_id,
                            candidate,
                        )

        if not ud.customer_phone:
            llm_digits = phone_digits_from_understanding(understanding)
            cleaned_phone = validate_phone(llm_digits) if llm_digits else None
            if cleaned_phone is None:
                # Fallback path matches the legacy behaviour.
                digits = _phone_digits_only(user_text)
                cleaned_phone = validate_phone(digits) if digits else None
            if cleaned_phone:
                ud.customer_phone = cleaned_phone
                ud.pending_phone_digits = ""
                logger.info("call=%s | greeter prefill phone", ud.call_id)

    def _available_menu_names_text(self, limit: int = 5) -> str:
        names = [
            str(item.get("name", "")).strip()
            for item in (self.cfg.menu_items or [])
            if isinstance(item, dict) and item.get("available", True) and str(item.get("name", "")).strip()
        ]
        if not names:
            return "المنيو المتاح"
        return "، ".join(names[:limit])

    @staticmethod
    def _clean_order_phrase(user_text: str) -> str:
        cleaned = re.sub(r"[.!؟،,]+", " ", user_text or "")
        cleaned = re.sub(
            r"\b(?:ماشي|تمام|حاضر|طيب|طب|ممكن|لو سمحت|يا فندم|باشا|"
            r"عايز|عاوز|عاوزه|عاوزة|محتاج|محتاجة|هطلب|اطلب|أطلب|طلب|اوردر|أوردر|هات|هاتلي)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _looks_like_order_attempt(user_text: str) -> bool:
        from nlp.arabic import normalize_ar as _normalize_ar

        normalized = _normalize_ar(user_text or "")
        if not normalized:
            return False
        order_cues = {
            "عايز", "عاوز", "عاوزه", "عاوزة", "محتاج", "محتاجة",
            "هطلب", "اطلب", "أطلب", "طلب", "اوردر", "أوردر", "هات", "هاتلي",
        }
        return any(_normalize_ar(cue) in normalized for cue in order_cues)

    @staticmethod
    def _quantity_only(user_text: str) -> int | None:
        from agent import _quantity_token_to_int
        from nlp.arabic import normalize_ar as _normalize_ar

        normalized = _normalize_ar(user_text or "")
        if not normalized:
            return None
        ignored = {
            "ماشي", "تمام", "حاضر", "طيب", "طب", "هطلب", "اطلب", "أطلب",
            "عايز", "عاوز", "محتاج", "اوردر", "طلب",
        }
        quantities: list[int] = []
        other_tokens: list[str] = []
        for token in normalized.split():
            qty = _quantity_token_to_int(token)
            if qty is not None:
                quantities.append(qty)
            elif token not in ignored:
                other_tokens.append(token)
        if len(quantities) == 1 and not other_tokens:
            return quantities[0]
        return None

    def _capture_order_from_greeter(self, user_text: str) -> tuple[bool, str]:
        """Capture an order mentioned before the caller chose delivery/takeaway.

        This closes the real-call path where the customer asks for the menu,
        then says an item, but hasn't said the fulfilment mode yet. We store
        the order and only ask for the mode; no LLM needed and no repeated
        "تحب تطلب إيه؟".
        """
        from agent import (
            _format_order_item,
            _is_menu_question,
            _normalize_order_items,
            _parse_order_item,
            _phase2_extract_items,
        )

        ud = self.session.userdata
        if _is_menu_question(user_text):
            return False, ""
        qty = self._quantity_only(user_text)
        if qty is not None and not ud.order:
            from utils.money import num2ar

            return True, f"{num2ar(qty)} من إيه من المنيو؟ وتحبها دليفري ولا تيكاواي؟"
        if qty is not None and ud.order:
            from utils.money import num2ar

            last_name, _old_qty = _parse_order_item(ud.order[-1])
            updated = list(ud.order[:-1]) + [_format_order_item(last_name, qty)]
            normalized, unknown, total = _normalize_order_items(updated, self.cfg.menu_items or [])
            ud.order = normalized if not unknown else updated
            ud.order_total = total if not unknown else ud.order_total
            ud.order_validated = not unknown
            return True, f"تمام، خليتها {num2ar(qty)} {last_name}. تحبها دليفري ولا تيكاواي؟"

        if not self._looks_like_order_attempt(user_text):
            return False, ""

        items = _phase2_extract_items(user_text, self.cfg)
        if not items:
            phrase = self._clean_order_phrase(user_text)
            if phrase:
                available = self._available_menu_names_text()
                return True, f"معلش، {phrase} مش ظاهر عندي في المنيو. المتاح {available}. تحب دليفري ولا تيكاواي؟"
            return True, "تطلب إيه من المنيو؟ وتحبها دليفري ولا تيكاواي؟"

        current = list(ud.order or [])
        normalized, unknown, total = _normalize_order_items(current + items, self.cfg.menu_items or [])
        ud.order = normalized if not unknown else current + items
        ud.order_total = total if not unknown else ud.order_total
        ud.order_validated = not unknown
        order_text = "، ".join(items[:3])
        logger.info("call=%s | greeter prefilled order | items=%s", ud.call_id, items)
        return True, f"تمام، سجلت {order_text}. تحبها دليفري ولا تيكاواي؟"

    async def _maybe_handle_turn_deterministically(self, user_text: str) -> bool:
        """Deterministic contact prefill + intent routing.

        When the user clearly says "توصيل" / "تيكأواي" / "حجز" / "شكوى",
        we transfer immediately and skip the LLM₁ → tool-call → handoff
        round-trip. Saves ~1.5 s per routing turn (the dominant first
        bottleneck after STT in production logs).

        Ambiguous cases ("أوردر" alone, no channel specified) still fall
        through to the LLM so it can ask for clarification.
        """
        from agent import (
            _DELIVERY_HINTS,
            _TAKEAWAY_HINTS,
            _RESERVATION_HINTS,
            _COMPLAINT_HINTS,
            _contains_any_hint,
            _emit_event,
        )
        from nlp.arabic import normalize_ar as _normalize_ar

        normalized = _normalize_ar(user_text or "")
        if not normalized:
            return False

        ud = self.session.userdata
        self._capture_prefill_contact(user_text)
        captured_order, order_message = self._capture_order_from_greeter(user_text)
        target: str | None = None
        if _contains_any_hint(normalized, _DELIVERY_HINTS) and self._delivery_enabled:
            target = "delivery"
        elif _contains_any_hint(normalized, _TAKEAWAY_HINTS):
            target = "takeaway"
        elif _contains_any_hint(normalized, _RESERVATION_HINTS):
            target = "reservation"
        elif _contains_any_hint(normalized, _COMPLAINT_HINTS):
            target = "complaint"

        if (target is None or target not in ud.agents) and captured_order:
            await self._say_and_stop(order_message)
            return True

        if target is None or target not in ud.agents:
            return False

        logger.info(
            "call=%s | greeter deterministic route | Greeter → %s | text=%r",
            ud.call_id, target, (user_text or "")[:60],
        )
        _emit_event(
            "flow.transfer",
            call_id=ud.call_id,
            flow="greeter",
            mode="deterministic",
            source="greeter",
            target=target,
            result="success",
        )
        _emit_event(
            "fast_path.matched",
            call_id=ud.call_id,
            flow="greeter",
            kind="route",
            target=target,
        )
        ud.prev_agent = self
        ud.handoff_target = target
        self.session.update_agent(ud.agents[target])
        return True

    @function_tool()
    async def to_reservation(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لما العميل يريد حجز ترابيزة."""
        async def _impl() -> Agent:
            return await self._transfer("reservation", context)

        return await _run_tool_safely("to_reservation", context, _impl)

    @function_tool()
    async def to_takeaway(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لما العميل يريد ييجي ياخد طلبه من المطعم."""
        async def _impl() -> Agent:
            return await self._transfer("takeaway", context)

        return await _run_tool_safely("to_takeaway", context, _impl)

    @function_tool()
    async def to_delivery(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لما العميل يريد توصيل الطلب لعنوانه."""
        async def _impl() -> str | Agent:
            from agent import _delivery_unavailable_user_message
            if not self._delivery_enabled and not self.cfg.degraded_mode:
                return _delivery_unavailable_user_message(self.cfg)
            return await self._transfer("delivery", context)

        return await _run_tool_safely("to_delivery", context, _impl)

    @function_tool()
    async def to_complaint(self, context: RunContext_T) -> str | Agent:
        """يُستدعى لما العميل عنده شكوى أو مشكلة."""
        async def _impl() -> Agent:
            return await self._transfer("complaint", context)

        return await _run_tool_safely("to_complaint", context, _impl)

    @function_tool()
    async def resolve_request(
        self,
        user_text: Annotated[str, Field(description="آخر كلام واضح قاله العميل")],
        context: RunContext_T,
    ) -> str | Agent:
        async def _impl() -> str | Agent:
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
