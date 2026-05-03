"""Deterministic pre-LLM dialogue pipeline.

Orchestrates the existing extractors / detectors to short-circuit the LLM
on turns that can be resolved by rules alone. Designed as **additive**
infrastructure — flows opt in via ``run_pipeline(...)`` from their
``_maybe_handle_turn_deterministically`` hook. If the pipeline can't
resolve the turn it returns ``PipelineResult(action="fallback_llm")``
and the existing LLM path runs unchanged.

Resolved cases (no LLM call):

- ``cancel``         — caller speaks farewell + ends call
- ``flow_change``    — caller switches active agent
- ``handoff``        — first-turn intent routing in greeter
- ``confirm_no``     — caller replies "what would you like to change?"
- ``slot_captured``  — order / phone / name / address extracted with HIGH
                       confidence; next question comes from
                       ``DialogueEngine.next_action``. **No hallucination
                       possible: order is validated against the menu
                       index, phones validated against Egyptian carriers.**

Cases deferred to LLM (return ``fallback_llm``):

- MEDIUM-confidence captures (engine wants to clarify before saving)
- Item modifications (add / remove / replace) — extractor doesn't know
  which item to mutate without conversational context
- Open-ended chit-chat / unstructured turns
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from correction_detector import CorrectionDetection, detect_correction
from intent_router import RouteDecision, route as run_intent_router
from state.user_data import UserData


logger = logging.getLogger("restaurant.agent")


PipelineAction = Literal[
    "fallback_llm",     # caller falls through to LLM
    "say",              # caller speaks ``message`` then stops the turn
    "submit",           # caller submits the completed order/reservation
    "handoff",          # caller switches to ``target_flow`` agent
    "cancel",           # caller says farewell + ends call
    "flow_change",      # caller switches agent + speaks ``message``
]


@dataclass(frozen=True)
class PipelineResult:
    action: PipelineAction
    message: str = ""
    target_flow: str = ""
    intent: str = ""
    confidence: float = 0.0
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    decision_reason: str = ""
    captured_slots: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_fallback(self) -> bool:
        return self.action == "fallback_llm"


_FAREWELL_TEXT = "تمام يا فندم، اتلغى الطلب. لو عايز حاجة تاني كلمنا تاني."
_CONFIRM_NO_TEXT = "تمام، عايز تعدل إيه بالظبط؟"


def run_pipeline(
    *,
    text: str,
    flow: str,
    ud: UserData,
    available_flows: tuple[str, ...] = ("greeter", "delivery", "takeaway", "reservation", "complaint"),
) -> PipelineResult:
    """Resolve a user turn deterministically when possible.

    Args:
        text:              normalized or raw STT transcript (the detectors
                           normalize internally)
        flow:              currently active flow (``greeter`` / ``delivery`` / …)
        ud:                user data for the call (read-only here; caller
                           applies state mutations after acting on the result)
        available_flows:   flows the caller can route to. Used to gate
                           intent-router output against the current
                           agents dict.

    Returns a ``PipelineResult``. ``action="fallback_llm"`` means the
    caller should run the LLM path normally.
    """
    if not text or not text.strip():
        return PipelineResult(action="fallback_llm", decision_reason="empty_text")

    flow = (flow or "").strip().lower()

    memory_reply = _memory_protest_result(text=text, flow=flow, ud=ud)
    if memory_reply is not None:
        return memory_reply

    basic_reply = _basic_text_reply(text=text, flow=flow, ud=ud)
    if basic_reply is not None:
        return basic_reply

    # ── 1. Cancellation has highest priority ───────────────────────────
    correction: CorrectionDetection = detect_correction(text)
    if correction.kind == "cancel" and correction.is_actionable():
        return PipelineResult(
            action="cancel",
            message=_FAREWELL_TEXT,
            confidence=correction.confidence,
            matched_terms=(correction.cue,) if correction.cue else (),
            decision_reason="cancel",
        )

    # ── 2. Explicit flow change ("لا خليها تيكاواي") ───────────────────
    if (
        correction.kind == "flow_change"
        and correction.is_actionable()
        and correction.target
        and correction.target in available_flows
        and correction.target != flow
    ):
        return PipelineResult(
            action="flow_change",
            target_flow=correction.target,
            confidence=correction.confidence,
            matched_terms=(correction.cue,) if correction.cue else (),
            decision_reason="flow_change",
        )

    # ── 3. Greeter intent routing (first-turn handoff) ─────────────────
    if flow == "greeter":
        decision: RouteDecision = run_intent_router(text)
        if decision.is_route() and decision.intent in available_flows:
            return PipelineResult(
                action="handoff",
                target_flow=decision.intent,
                intent=decision.intent,
                confidence=decision.confidence,
                matched_terms=decision.matched_terms,
                decision_reason="intent_route",
            )
        # Medium-confidence intent: don't route deterministically; let the
        # LLM ask a clarifying question.

    # ── 4. Confirmation handling (yes / no while confirmation_pending) ──
    # Outside confirmation_pending these are ambiguous (a bare "لا" could
    # answer "في طلب خاص؟"), so we only act on them when the engine
    # has explicitly prompted for confirmation.
    confirmation_pending = bool(getattr(ud, "confirmation_pending", False))
    if confirmation_pending and correction.is_actionable():
        if correction.kind == "confirm_no":
            return PipelineResult(
                action="say",
                message=_CONFIRM_NO_TEXT,
                confidence=correction.confidence,
                matched_terms=(correction.cue,) if correction.cue else (),
                decision_reason="confirmation_rejected",
            )
        if correction.kind == "confirm_yes":
            with contextlib.suppress(Exception):
                ud.confirmation_pending = False
                ud.confirmation_received = True
            return PipelineResult(
                action="submit",
                decision_reason="confirmation_accepted",
                matched_terms=(correction.cue,) if correction.cue else (),
            )
    if confirmation_pending:
        try:
            from nlp.arabic import normalize_ar
            norm_for_reject = normalize_ar(text or "")
        except Exception:
            norm_for_reject = text or ""
        rejection_markers = ("غلط", "مش صح", "مش كده", "مش كدا", "لا مش", "لأ مش")
        if any((normalize_ar(marker) if "normalize_ar" in locals() else marker) in norm_for_reject for marker in rejection_markers):
            return PipelineResult(
                action="say",
                message=_CONFIRM_NO_TEXT,
                confidence=0.88,
                decision_reason="confirmation_rejected",
            )

    if flow in ("delivery", "takeaway") and correction.kind == "item_mod_remove" and correction.is_actionable():
        removed = _remove_order_items_against_menu(
            text=text,
            flow=flow,
            ud=ud,
            cfg=getattr(ud, "restaurant", None),
        )
        if removed is not None:
            return removed
        noop = _next_dialogue_result(flow=flow, ud=ud, reason="item_remove_noop")
        if noop is not None:
            return noop

    # Short acknowledgements outside explicit confirmation should not go
    # to the LLM. In real Egyptian calls "لا تمام" / "خلاص" often means
    # "no extra notes, continue", not "submit the order".
    ack_continue = _ack_continue_result(text=text, flow=flow, ud=ud)
    if ack_continue is not None:
        return ack_continue

    # ── 5. Small talk deflection (greetings, "how are you?") ──────────
    # Voice agents that hand "ازيك؟" / "ايه الأخبار؟" to the LLM waste 5-7s
    # of TTFT on a question the customer didn't really mean. Deflect to
    # a brief acknowledgement + steer back to the order. Cheap, zero
    # hallucination risk, and the customer doesn't notice they didn't
    # get a real answer because that's how restaurant phone calls work.
    smalltalk = _detect_smalltalk(text)
    if smalltalk is not None:
        return PipelineResult(
            action="say",
            message=smalltalk,
            decision_reason="smalltalk_deflect",
        )

    # ── 5b. Menu question ("ايه المتاح / المنيو / عندك ايه") ──────────
    # Customer asking what's on offer. We can answer deterministically
    # from the cached menu without a single LLM token.
    menu_reply = _detect_menu_question(text=text, flow=flow, ud=ud)
    if menu_reply is not None:
        return PipelineResult(
            action="say",
            message=menu_reply,
            decision_reason="menu_question",
        )

    # ── 6. Slot extraction + DialogueEngine (per-flow) ─────────────────
    # This is the production path that eliminates LLM-driven slot capture
    # for the common cases — order, phone, address, name. All extractors
    # validate against deterministic constraints (menu, carrier prefixes,
    # zone list) so the model can never invent a value.
    if flow in ("delivery", "takeaway", "reservation", "complaint"):
        slot_result = _try_slot_capture(text=text, flow=flow, ud=ud)
        if slot_result is not None:
            return slot_result

    # ── 6. Slot correction (signals intent only — actual fix in LLM) ───
    if correction.kind == "slot_correction" and correction.is_actionable():
        logger.info(
            "pipeline | slot_correction detected, deferring to LLM | "
            "call=%s | flow=%s | target=%s | cue=%s",
            getattr(ud, "call_id", "-") or "-",
            flow,
            correction.target,
            correction.cue,
        )
        # fall through

    # ── Fallback: nothing deterministic resolves this turn ─────────────
    return PipelineResult(action="fallback_llm", decision_reason="no_match")


# ────────────────────────────────────────────────────────────────────────
# Slot capture (the core production-grade payload)
# ────────────────────────────────────────────────────────────────────────


def _try_slot_capture(*, text: str, flow: str, ud: UserData) -> PipelineResult | None:
    """Run flow-specific extractors. If any HIGH-confidence capture lands,
    write to ``ud`` and let the DialogueEngine decide the next question
    (or confirmation prompt). Returns ``None`` to fall through to LLM.

    Imports are local because the extractors / engine pull in pyyaml,
    menu_index, etc. — keeping them out of the top-level import set keeps
    pipeline cold-start cheap.
    """
    try:
        from core.dialogue_engine import DialogueEngine
        from core.extractors.contact_extractor import (
            HIGH_CONFIDENCE as CONTACT_HIGH,
            extract_name,
            extract_phone,
        )
    except Exception as exc:
        logger.warning("pipeline slot capture unavailable | %s", exc)
        return None

    captured: list[str] = []
    cfg = getattr(ud, "restaurant", None)

    # ── Phone: highest precedence (digit-rich text is unambiguous) ─────
    if not ud.customer_phone:
        phone = extract_phone(text)
        if phone.value and phone.confidence >= CONTACT_HIGH:
            ud.customer_phone = phone.value
            captured.append("phone")
            logger.info(
                "pipeline | captured | call=%s | slot=phone | conf=%.2f | reason=%s",
                ud.call_id or "-", phone.confidence, phone.reason,
            )

    # ── Order (delivery / takeaway only, validated against menu) ───────
    if flow in ("delivery", "takeaway"):
        items_captured = _capture_order_against_menu(text=text, ud=ud, cfg=cfg)
        if items_captured:
            captured.append("order")

    # ── Address (delivery only) ────────────────────────────────────────
    if flow == "delivery" and not ud.delivery_address:
        if _capture_address(text=text, ud=ud, cfg=cfg):
            captured.append("address")
        elif "order" not in captured:
            # Phase 1.5 anti-hallucination: when the address has a strong
            # landmark+digit signal but no configured zone matched, ask
            # the customer to disambiguate **before** falling to the
            # "we don't deliver there" rejection. Without this, the
            # rejection fires every time the customer just says
            # "شارع التحرير ٥" without naming the district.
            zone_clarification = _address_zone_clarification_result(text=text, cfg=cfg)
            if zone_clarification is not None:
                return zone_clarification
            address_rejection = _unsupported_delivery_address_result(text=text, cfg=cfg)
            if address_rejection is not None:
                return address_rejection

    # ── Special requests (delivery / takeaway only) ───────────────────
    if flow in ("delivery", "takeaway") and ud.order and ud.special_requests is None:
        special = _capture_special_request(text)
        if special is not None:
            ud.special_requests = special or None
            captured.append("special")
            logger.info(
                "pipeline | captured | call=%s | slot=special_requests | empty=%s",
                ud.call_id or "-", not bool(special),
            )

    # ── Reservation slots (reservation flow only) ──────────────────────
    if flow == "reservation":
        if _capture_reservation_slots(text=text, ud=ud, cfg=cfg):
            captured.append("reservation")

    # ── Name (last — phone-first heuristic prevents "ألو" being captured) ─
    # Defensive guard: if the turn contains an ordering verb (عايز /
    # محتاج / هطلب) we treat the rest as a (possibly-non-menu) order
    # phrase, NOT a name. Without this guard "عايز شاورما" was capturing
    # "شاورما" as a name when شاورما isn't in the menu — the order
    # extractor correctly rejected it, but the name extractor's short-
    # token heuristic doesn't know about ordering verbs.
    if not ud.customer_name and not _looks_like_order_attempt(text):
        explicit_name = _explicit_name_before_contact(text)
        name_value = explicit_name
        name_reason = "explicit_name_before_contact" if explicit_name else ""
        name_confidence = 0.92 if explicit_name else 0.0
        name = None
        if name_value is None:
            name = extract_name(text)
            name_value = name.value if name.value and name.confidence >= CONTACT_HIGH else None
            name_reason = name.reason
            name_confidence = name.confidence
        if name_value is None:
            try:
                from agent import _extract_name_candidate
                name_value = _extract_name_candidate(text)
                name_reason = "legacy_explicit_candidate"
                name_confidence = 0.9 if name_value else 0.0
            except Exception:
                name_value = None
        if name_value:
            ud.customer_name = name_value
            captured.append("name")
            logger.info(
                "pipeline | captured | call=%s | slot=name | conf=%.2f | reason=%s",
                ud.call_id or "-", name_confidence, name_reason,
            )

    if not captured:
        return None  # nothing high-confidence; let LLM run

    if "order" in captured:
        with contextlib.suppress(Exception):
            ud.confirmation_pending = False
            ud.confirmation_received = False
        try:
            from agent import _get_upsell_suggestion
            upsell = _get_upsell_suggestion(ud, cfg) if cfg is not None else None
        except Exception:
            upsell = None
        if upsell:
            return PipelineResult(
                action="say",
                message=upsell,
                decision_reason=f"slot_captured:{','.join(captured)}",
                captured_slots=tuple(captured),
            )

    # ── Hand the engine a freshly-mutated UserData to pick the next ask ─
    try:
        action = DialogueEngine().next_action(flow, ud)
    except Exception as exc:
        logger.warning("pipeline | dialogue_engine failure | %s", exc)
        return None

    message = (action.message or "").strip()
    if not message:
        return None  # engine has no canned reply; defer to LLM for paraphrase

    if action.type == "confirm":
        with contextlib.suppress(Exception):
            ud.confirmation_pending = True
    elif captured:
        with contextlib.suppress(Exception):
            ud.confirmation_pending = False
            ud.confirmation_received = False

    return PipelineResult(
        action="say",
        message=message,
        decision_reason=f"slot_captured:{','.join(captured)}",
        captured_slots=tuple(captured),
    )


def _capture_order_against_menu(*, text: str, ud: UserData, cfg: Any) -> bool:
    """Extract menu items + quantities from ``text``. Writes ``ud.order`` /
    ``ud.order_total`` only when at least one HIGH-confidence item lands.

    Returns True iff anything was written.
    """
    if cfg is None or not getattr(cfg, "menu_items", None):
        return False
    try:
        from core.extractors.order_extractor import HIGH_CONFIDENCE, extract_order
        from core.menu_index import MenuIndex
    except Exception:
        return False

    try:
        index = MenuIndex.build(cfg.menu_items or [])
    except Exception:
        return False
    if index.is_empty():
        return False

    extraction = extract_order(text, index)
    if extraction.is_empty() or extraction.has_ambiguity():
        return False
    if extraction.overall_confidence < HIGH_CONFIDENCE:
        return False

    incoming_items = extraction.formatted_items()
    if not incoming_items:
        return False

    if ud.order:
        try:
            from agent import (
                _merge_incremental_order_items,
                _normalize_order_items,
                _order_update_is_replace,
            )
        except Exception:
            _merge_incremental_order_items = None
            _normalize_order_items = None
            _order_update_is_replace = None

        replace = False
        if callable(_order_update_is_replace):
            with contextlib.suppress(Exception):
                replace = bool(_order_update_is_replace(text))

        if not replace and callable(_merge_incremental_order_items):
            normalized, unknown, total = _merge_incremental_order_items(
                ud.order,
                incoming_items,
                cfg.menu_items or [],
            )
            if unknown:
                return False
            ud.order = normalized
            with contextlib.suppress(Exception):
                ud.order_total = float(total)
                ud.order_validated = True
        elif callable(_normalize_order_items):
            normalized, unknown, total = _normalize_order_items(
                incoming_items,
                cfg.menu_items or [],
            )
            if unknown:
                return False
            ud.order = normalized
            with contextlib.suppress(Exception):
                ud.order_total = float(total)
                ud.order_validated = True
        else:
            ud.order = incoming_items
            with contextlib.suppress(Exception):
                ud.order_total = float(extraction.total())
                ud.order_validated = True
    else:
        ud.order = incoming_items
        with contextlib.suppress(Exception):
            ud.order_total = float(extraction.total())
            ud.order_validated = True

    logger.info(
        "pipeline | captured | call=%s | slot=order | items=%s | total=%.2f | conf=%.2f",
        ud.call_id or "-",
        ud.order,
        getattr(ud, "order_total", 0.0) or 0.0,
        extraction.overall_confidence,
    )
    return True


def _remove_order_items_against_menu(*, text: str, flow: str, ud: UserData, cfg: Any) -> PipelineResult | None:
    if not ud.order or cfg is None or not getattr(cfg, "menu_items", None):
        return None
    try:
        from agent import _normalize_order_items, _parse_order_item
        from core.dialogue_engine import DialogueEngine
        from core.extractors.order_extractor import MEDIUM_CONFIDENCE, extract_order
        from core.menu_index import MenuIndex
    except Exception:
        return None

    try:
        index = MenuIndex.build(cfg.menu_items or [])
    except Exception:
        return None
    if index.is_empty():
        return None

    extraction = extract_order(text, index, min_confidence=MEDIUM_CONFIDENCE)
    if extraction.is_empty() or extraction.has_ambiguity():
        return None

    remove_names = {item.canonical_name for item in extraction.items}
    if not remove_names:
        return None

    kept: list[str] = []
    removed_any = False
    for raw_item in ud.order or []:
        name, _qty = _parse_order_item(raw_item)
        if name in remove_names:
            removed_any = True
            continue
        kept.append(raw_item)
    if not removed_any:
        return None

    normalized, unknown, total = _normalize_order_items(kept, cfg.menu_items or [])
    if unknown:
        return None
    ud.order = normalized
    ud.order_total = float(total)
    ud.order_validated = bool(normalized)
    with contextlib.suppress(Exception):
        ud.confirmation_pending = False
        ud.confirmation_received = False
        ud.pending_upsell_item = None
        ud.pending_upsell_price = None

    try:
        action = DialogueEngine().next_action(flow, ud)
    except Exception:
        return None
    message = (action.message or "").strip()
    if not message:
        return None
    if action.type == "confirm":
        with contextlib.suppress(Exception):
            ud.confirmation_pending = True

    logger.info(
        "pipeline | order item removed | call=%s | removed=%s | remaining=%s",
        ud.call_id or "-",
        sorted(remove_names),
        ud.order,
    )
    return PipelineResult(
        action="say",
        message=message,
        decision_reason="item_removed",
        captured_slots=("order",),
    )


def _next_dialogue_result(*, flow: str, ud: UserData, reason: str) -> PipelineResult | None:
    try:
        from core.dialogue_engine import DialogueEngine
    except Exception:
        return None
    try:
        action = DialogueEngine().next_action(flow, ud)
    except Exception:
        return None
    message = (action.message or "").strip()
    if not message:
        return None
    if action.type == "confirm":
        with contextlib.suppress(Exception):
            ud.confirmation_pending = True
    return PipelineResult(action="say", message=message, decision_reason=reason)


def _basic_text_reply(*, text: str, flow: str, ud: UserData) -> PipelineResult | None:
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return None
    raw = (text or "").strip()
    norm = normalize_ar(raw)
    if not norm:
        return None

    if norm in {normalize_ar("أنا"), normalize_ar("انا"), normalize_ar("ألو"), normalize_ar("الو")}:
        return PipelineResult(
            action="say",
            message="معاك، تحب تطلب إيه؟",
            decision_reason="basic_opening",
        )

    if ("؟" in raw or "?" in raw) and any(phrase in norm for phrase in (normalize_ar("اسمي"), normalize_ar("اسمي انا"), normalize_ar("الاسم"))):
        return PipelineResult(
            action="say",
            message="آه، قولّي الاسم اللي أسجل بيه الطلب.",
            decision_reason="name_question",
        )

    if flow == "delivery" and not getattr(ud, "order", None):
        delivery_only = {
            normalize_ar("عايز دليفري"),
            normalize_ar("عايز توصيل"),
            normalize_ar("دليفري"),
            normalize_ar("توصيل"),
            normalize_ar("عايز اطلب دليفري"),
            normalize_ar("عايز اطلب توصيل"),
        }
        if norm in delivery_only:
            return PipelineResult(
                action="say",
                message="تمام، تحب تطلب إيه؟",
                decision_reason="delivery_mode_ack",
            )
    return None


def _memory_protest_result(*, text: str, flow: str, ud: UserData) -> PipelineResult | None:
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return None
    norm = normalize_ar(text or "")
    if not norm:
        return None
    if not any(phrase in norm for phrase in ("ناسي", "نسيت", "قلتلك", "قلتلكش", "قولتلك")):
        return None

    next_action = _next_dialogue_result(flow=flow, ud=ud, reason="memory_protest")
    if next_action is None:
        return None
    if flow == "delivery" and not getattr(ud, "customer_name", None) and "اسم" in norm:
        next_action = PipelineResult(
            action="say",
            message="لا، الطلب والعنوان والرقم معايا. لسه محتاج الاسم بس.",
            decision_reason="memory_protest",
        )
    elif getattr(ud, "order", None) or getattr(ud, "customer_phone", None) or getattr(ud, "delivery_address", None):
        next_action = PipelineResult(
            action="say",
            message=f"لا، البيانات اللي قلتها معايا. {next_action.message}",
            decision_reason="memory_protest",
        )
    return next_action


def _capture_address(*, text: str, ud: UserData, cfg: Any) -> bool:
    try:
        from core.extractors.address_extractor import (
            HIGH_CONFIDENCE,
            extract_address,
        )
    except Exception:
        return False

    zones: tuple[str, ...] | None = None
    if cfg is not None and getattr(cfg, "delivery_zones", None):
        zones = tuple(cfg.delivery_zones)

    capture = extract_address(text, delivery_zones=zones)
    if not capture.value or capture.confidence < HIGH_CONFIDENCE:
        return False
    if zones and not capture.zone:
        return False

    ud.delivery_address = capture.value
    if capture.zone:
        with contextlib.suppress(Exception):
            ud.delivery_zone = capture.zone
    logger.info(
        "pipeline | captured | call=%s | slot=address | conf=%.2f | reason=%s",
        ud.call_id or "-", capture.confidence, capture.reason,
    )
    return True


def _address_zone_clarification_result(*, text: str, cfg: Any) -> PipelineResult | None:
    """Ask which zone when the address has a HIGH-confidence landmark+digit
    signal but no configured zone was named.

    This is the gap between ``_capture_address`` (which silently rejects
    when zones are configured but none matched) and
    ``_unsupported_delivery_address_result`` (which assumes the area is
    out-of-coverage). The reality is the customer often just says
    "شارع التحرير ٥" without naming the district — they're not telling
    us they live somewhere we don't cover, they just haven't said the
    district yet. Asking "ده في أي منطقة؟" is the right move.

    Returns ``None`` (caller falls through) when:
    - cfg has no delivery_zones (nothing to clarify against)
    - extractor didn't find an address signal
    - confidence < HIGH (let LLM disambiguate hesitant input)
    - any configured zone IS mentioned (already handled by
      ``_capture_address`` or ``_unsupported_delivery_address_result``)
    """
    if cfg is None or not getattr(cfg, "delivery_zones", None):
        return None
    try:
        from core.extractors.address_extractor import (
            HIGH_CONFIDENCE,
            extract_address,
        )
        from nlp.arabic import normalize_ar
    except Exception:
        return None

    zones = tuple(cfg.delivery_zones)
    capture = extract_address(text, delivery_zones=zones)
    if not capture.value or capture.confidence < HIGH_CONFIDENCE:
        return None
    if capture.zone:
        return None  # zone matched — _capture_address handles it

    # If ANY configured zone phrase appears in the text, this isn't a
    # "no zone given" case — it's a "wrong zone given" case which
    # _unsupported_delivery_address_result owns. Bail out.
    norm = normalize_ar(text)
    for zone in zones:
        zone_norm = normalize_ar(str(zone))
        if zone_norm and zone_norm in norm:
            return None

    try:
        zones_text = cfg.delivery_zones_text()
    except Exception:
        zones_text = "، ".join(str(z) for z in zones)

    return PipelineResult(
        action="say",
        message=f"تمام، ده في أي منطقة بالظبط؟ بنوصل في {zones_text}.",
        decision_reason="address_zone_clarification",
    )


def _unsupported_delivery_address_result(*, text: str, cfg: Any) -> PipelineResult | None:
    if cfg is None or not getattr(cfg, "delivery_zones", None):
        return None
    try:
        from agent import _looks_like_delivery_address_turn
        from nlp.arabic import normalize_ar
        from nlp.phone_extract import is_phone_like_text
    except Exception:
        return None

    raw = (text or "").strip()
    if not raw or is_phone_like_text(raw):
        return None
    if _capture_special_request(raw) is not None:
        return None
    if not _looks_like_delivery_address_turn(raw, cfg):
        return None

    norm = normalize_ar(raw)
    for zone in getattr(cfg, "delivery_zones", None) or ():
        zone_norm = normalize_ar(str(zone))
        if zone_norm and (zone_norm in norm or norm in zone_norm):
            return None

    try:
        zones_text = cfg.delivery_zones_text()
    except Exception:
        zones_text = "، ".join(str(z) for z in (getattr(cfg, "delivery_zones", None) or ()))
    return PipelineResult(
        action="say",
        message=f"للأسف مش بنوصل المنطقة دي دلوقتي. التوصيل متاح في {zones_text}. تحب تيكاواي؟",
        decision_reason="unsupported_delivery_zone",
    )


def _capture_special_request(text: str) -> str | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    if not raw:
        return None
    try:
        from agent import _extract_special_request_candidate, _looks_empty_answer
    except Exception:
        _extract_special_request_candidate = None
        _looks_empty_answer = None
    if callable(_looks_empty_answer) and _looks_empty_answer(raw):
        return ""
    if callable(_extract_special_request_candidate):
        candidate = _extract_special_request_candidate(raw)
        if candidate:
            return _clean_special_request(candidate)

    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return None
    norm = normalize_ar(raw)
    if not norm:
        return None
    empty_special_phrases = (
        "مفيش طلب خاص",
        "مفيش اي طلب خاص",
        "مفيش أي طلب خاص",
        "مفيش ملاحظات",
        "مفيش ملاحظة",
        "لا مفيش طلب خاص",
    )
    if any(normalize_ar(phrase) in norm for phrase in empty_special_phrases):
        return ""

    special_hints = (
        "من غير", "بدون", "بلاش", "كاتشب", "مايونيز", "صوص", "شطة", "شطه",
        "مخلل", "بصل", "طماطم", "جبنة", "جبنه", "زيادة", "زياده",
        "ما يكونش", "مايكونش", "ما يبقاش", "مايبقاش", "متحطش", "ما تحطش",
        "مش عايز", "على جنب",
    )
    if not any(normalize_ar(hint) in norm for hint in special_hints):
        return None

    cleaned = _strip_leading_call_fillers(raw)
    normalized_cleaned = normalize_ar(cleaned)
    for cue in ("من غير", "بدون", "بلاش", "على جنب"):
        cue_norm = normalize_ar(cue)
        if cue_norm in normalized_cleaned:
            tail = cleaned[normalized_cleaned.find(cue_norm) + len(cue_norm):].strip(" ،,.")
            return _clean_special_request(f"{cue} {tail}".strip()) if tail else cue

    negative_match = re.search(
        r"(?:ما\s*يكونش|مايكونش|ما\s*يبقاش|مايبقاش|متحطش|ما\s*تحطش|مش\s*عايز)\s+(?P<tail>.+)$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if negative_match:
        tail = re.sub(
            r"^(?:عليها|عليه|فيها|فيه|معاها|معاه|اي|أي)\s+",
            "",
            negative_match.group("tail").strip(" ،,."),
            flags=re.IGNORECASE,
        )
        return _clean_special_request(f"من غير {tail}".strip()) if tail else "من غير"

    return _clean_special_request(cleaned)


def _strip_leading_call_fillers(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip(" ،,.")
    filler_re = re.compile(
        r"^(?:اه|أه|آه|ااه|ايوه|أيوه|يعني|طب|طيب|ماشي|تمام|حاضر|بس)\b[\s،,]*",
        flags=re.IGNORECASE,
    )
    for _ in range(5):
        updated = filler_re.sub("", cleaned).strip(" ،,.")
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned


def _clean_special_request(text: str) -> str:
    cleaned = _strip_leading_call_fillers(text)
    cleaned = re.sub(r"\b(?:خالص|بس|يا\s+فندم|لو\s+سمحت)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ،,.")
    return cleaned


def _ack_continue_result(*, text: str, flow: str, ud: UserData) -> PipelineResult | None:
    if flow not in ("delivery", "takeaway", "reservation", "complaint"):
        return None
    if getattr(ud, "confirmation_pending", False):
        return None
    try:
        from core.dialogue_engine import DialogueEngine
        from nlp.arabic import normalize_ar
    except Exception:
        return None

    norm = normalize_ar(text or "").strip()
    if not norm:
        return None
    norm = _strip_fillers(norm)
    tokens = norm.split()
    if len(tokens) > 4:
        return None

    ack_phrases = {
        "تمام", "لا تمام", "لأ تمام", "اه تمام", "ايوه تمام", "ماشي",
        "حاضر", "خلاص", "خلاص كده", "ايه بقى", "كده تمام",
    }
    if norm not in {normalize_ar(phrase) for phrase in ack_phrases}:
        return None

    try:
        action = DialogueEngine().next_action(flow, ud)
    except Exception:
        return None
    message = (action.message or "").strip()
    if not message:
        return None
    if action.type == "confirm":
        with contextlib.suppress(Exception):
            ud.confirmation_pending = True
    return PipelineResult(
        action="say",
        message=message,
        decision_reason="ack_continue",
    )


_ORDER_VERBS: tuple[str, ...] = (
    "عايز", "عاوز", "عاوزه", "عاوزة", "محتاج", "محتاجه", "محتاجة",
    "هطلب", "أطلب", "اطلب", "أوردر", "اوردر", "ابعت", "هاخد",
)


import random as _random


# Multiple variants per intent — picked randomly each turn so back-to-back
# calls don't sound robotic ("الحمد لله يا فندم… الحمد لله يا فندم…").
# Each (match phrase, replies tuple) entry is checked in order; first match
# wins. The replies cycle randomly within each match.
_SMALLTALK_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ازيك", (
        "الحمد لله يا فندم، تحب تطلب إيه؟",
        "تمام والحمد لله، تحب تطلب إيه؟",
        "كويس يا فندم، أقدر أساعدك إزاي؟",
    )),
    ("اخبارك", (
        "كله تمام، تحب تطلب إيه؟",
        "الحمد لله، أقدر أساعدك إزاي؟",
        "كويس يا فندم، تحب تطلب إيه؟",
    )),
    ("الاخبار", (
        "كله تمام، تحب تطلب إيه؟",
        "الحمد لله، تحب تطلب إيه؟",
    )),
    ("عامل ايه", (
        "تمام يا فندم، تحب تطلب إيه؟",
        "الحمد لله، أقدر أساعدك إزاي؟",
    )),
    ("عامله ايه", (
        "تمام يا فندم، تحب تطلب إيه؟",
        "الحمد لله، أقدر أساعدك إزاي؟",
    )),
    ("صباح الخير", (
        "صباح النور، تحب تطلب إيه؟",
        "صباح الفل يا فندم، أقدر أساعدك إزاي؟",
        "صباح النور، تحت أمرك.",
    )),
    ("مساء الخير", (
        "مساء النور، تحب تطلب إيه؟",
        "مساء الفل يا فندم، تحت أمرك.",
        "مساء النور، أقدر أساعدك إزاي؟",
    )),
    ("السلام عليكم", (
        "وعليكم السلام، تحب تطلب إيه؟",
        "وعليكم السلام ورحمة الله، أقدر أساعدك إزاي؟",
    )),
    ("اهلا", (
        "أهلاً بيك، تحب تطلب إيه؟",
        "أهلاً وسهلاً، أقدر أساعدك إزاي؟",
        "أهلاً يا فندم، تحت أمرك.",
    )),
    ("اهلين", (
        "أهلاً بيك، تحب تطلب إيه؟",
        "أهلاً وسهلاً، تحت أمرك.",
    )),
    ("هاي", (
        "أهلاً، تحب تطلب إيه؟",
        "أهلاً يا فندم، تحت أمرك.",
    )),
    ("hello", (
        "Hello, what would you like to order?",
    )),
    ("hi", (
        "Hi, what would you like to order?",
    )),
    ("شكرا", (
        "العفو يا فندم.",
        "تحت أمرك يا فندم.",
        "العفو، نورتنا.",
    )),
    ("متشكر", (
        "العفو يا فندم.",
        "تحت أمرك.",
    )),
)


# Tiny filler words customers often say while gathering thoughts. Stripping
# them before deterministic detection prevents "اه يعني ازيك" from being
# treated as long-form because it's actually just "ازيك" with filler.
# Phase 2.3 — expanded vocabulary based on Cairo phone-call corpora
# (research §4.2 / §4.3): hesitation markers, discourse fillers, soft
# politeness words, and Egyptianized loan-fillers. Single-word only —
# ``_strip_fillers`` is whitespace-tokenised, so multi-word entries are
# dropped during a separate phrase-strip pass below.
_FILLER_TOKENS: frozenset[str] = frozenset({
    # Original set (single tokens)
    "يعني", "بس", "هيه", "اه", "اممم", "يعنى", "كده",
    "طب", "ام", "هه",
    # Hesitation / thinking
    "ممم", "هممم", "اممممم", "اوف", "اف",
    # Egyptian discourse fillers
    "بصراحة", "بصي", "بصراحه", "صراحه", "بالظبط", "بالظبت",
    "صحيح", "والله", "وحياتك", "بقا", "بقى", "اهو",
    # Politeness softeners (when standalone — strip)
    "بليز",
    # Question prefaces
    "طيب",
    # Code-switch loan fillers
    "اوكي", "okay", "ok", "yeah",
})

# Phase 2.3 — multi-word filler phrases. Replaced as substrings before
# token-stripping so "اه يعني" / "بصراحة كده" / "لو سمحت" disappear from
# the normalised text. Order matters: longer phrases first.
_FILLER_PHRASES: tuple[str, ...] = (
    "اممم لحظة", "خد بالك بقا", "خد بالك", "بصراحة كده", "بصراحه كده",
    "صحيح كده", "بالنسبة", "بالنسبه", "اه يعني", "اه طب", "اه طيب",
    "طب يعني", "ممكن لو سمحت", "لو سمحت", "من فضلك",
)


def _strip_fillers(norm_text: str) -> str:
    if not norm_text:
        return norm_text
    # First-pass: drop multi-word filler phrases as substrings so the
    # remaining single-token strip works on the residue. Guard with a
    # space-padded compare so "بقا" inside a longer word is left alone.
    cleaned_text = f" {norm_text} "
    for phrase in _FILLER_PHRASES:
        cleaned_text = cleaned_text.replace(f" {phrase} ", " ")
    tokens = [t for t in cleaned_text.split() if t]
    cleaned = [t for t in tokens if t not in _FILLER_TOKENS]
    return " ".join(cleaned) if cleaned else norm_text


_MENU_QUESTION_CUES: tuple[str, ...] = (
    "ايه المتاح", "المتاح ايه", "ايه عندك", "عندك ايه", "عندكم ايه",
    "ايه عندكم", "المنيو", "menu", "ايه الاصناف", "الاصناف", "ايه الأصناف",
    "ممكن المنيو", "ممكن المتاح", "ايه عندكوا", "ايه اللي عندك",
)


def _detect_menu_question(*, text: str, flow: str, ud: UserData) -> str | None:
    """Return a spoken menu list when the customer asks what's available.

    Reads ``ud.restaurant.menu_items`` (already loaded in the call's
    config) and produces a short comma-joined string. We don't list
    prices to keep the reply tight — customer can ask about a specific
    item if they want price info.
    """
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return None
    norm = normalize_ar(text or "")
    if not norm:
        return None
    if not any(normalize_ar(cue) in norm for cue in _MENU_QUESTION_CUES):
        return None

    cfg = getattr(ud, "restaurant", None)
    items = getattr(cfg, "menu_items", None) if cfg is not None else None
    followup = "تحبها دليفري ولا تيكاواي؟" if flow == "greeter" else "تحب تطلب إيه؟"
    if not items:
        return f"معلش يا فندم، المنيو لسه بيتحدث، {followup}"

    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("available") is False:
            continue
        name = (item.get("name") or "").strip()
        if name:
            names.append(name)
    if not names:
        return "معلش يا فندم، مفيش حاجة متاحة دلوقتي."
    if len(names) > 6:
        names = names[:6]
        more = "، وتاني"
    else:
        more = ""
    return f"عندنا {'، '.join(names)}{more}. {followup}"


def _detect_smalltalk(text: str) -> str | None:
    """Return a canned Egyptian-Arabic reply for short greeting / pleasantry
    turns; ``None`` otherwise. Picks randomly among variants for naturalness.

    Only fires when the turn is short (≤ 6 words after filler stripping)
    and contains no order verb (so "عايز ازيك" doesn't get deflected).
    """
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return None
    norm = normalize_ar(text or "").strip()
    if not norm:
        return None
    norm = _strip_fillers(norm)
    word_count = len([w for w in norm.split() if w])
    if word_count == 0 or word_count > 6:
        return None
    # Skip if the user is actually trying to order (mixed greeting+order).
    if any(normalize_ar(v) in norm for v in _ORDER_VERBS):
        return None
    for phrase, replies in _SMALLTALK_PHRASES:
        if normalize_ar(phrase) in norm:
            return _random.choice(replies)
    return None


def _looks_like_order_attempt(text: str) -> bool:
    """Did the user use an ordering verb? Used to suppress name capture
    so non-menu items ("عايز شاورما") don't leak into ``customer_name``."""
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        return False
    norm = normalize_ar(text or "")
    if not norm:
        return False
    return any(normalize_ar(v) in norm for v in _ORDER_VERBS)


_NAME_CONTACT_STOP_RE = re.compile(
    r"\s+(?:و?\s*(?:رقمي|رقم|الموبايل|موبايل|تليفوني|تليفون|التليفون|فون|نمرة|نمرتي))\b"
    r"|\s+(?:و?\s*(?:عايز|عايزة|محتاج|محتاجة|هطلب|دليفري|توصيل|تيكاواي|تيك\s*اواي))\b"
    r"|[،,.;:؟?]",
    flags=re.IGNORECASE,
)


def _explicit_name_before_contact(text: str) -> str | None:
    """Capture common Egyptian inline contact turns:
    "اسمي سارة ورقمي 010..." / "الاسم نور والموبايل ...".
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if "؟" in raw or "?" in raw:
        return None
    try:
        from nlp.arabic import normalize_ar
    except Exception:
        normalize_ar = lambda value: value  # type: ignore[assignment]

    normalized_raw = normalize_ar(raw)
    non_name_questions = (
        "الاسم ايه", "الاسم اي", "اسمك ايه", "اسمك اي", "اسمي ايه",
        "اسمي اي", "اسم ايه", "اسم اي",
    )
    if any(normalize_ar(phrase) in normalized_raw for phrase in non_name_questions):
        return None

    match = re.search(
        r"^\s*(?:(?:انا|أنا)\s+(?:اسمي|اسمى|إسمي)|اسمي|اسمى|إسمي|الاسم|الإسم)\s+(?P<tail>.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    tail = match.group("tail").strip()
    stop = _NAME_CONTACT_STOP_RE.search(tail)
    if stop:
        tail = tail[: stop.start()].strip()

    # Keep only a short human-name-looking prefix. This avoids swallowing
    # contact/order text when ASR punctuation is missing.
    tokens: list[str] = []
    for token in re.split(r"\s+", tail):
        token = token.strip(" \t\r\n،,.;:؟?!()[]{}")
        if not token or any(ch.isdigit() for ch in token):
            break
        token_norm = normalize_ar(token)
        if token_norm in {"ايه", "اي", "ابني", "ابنك", "ابن", "انا", "أناه", "أني"}:
            return None
        if token_norm == "يا" and not tokens:
            return None
        if token in {"هو", "هي", "يا", "باشا", "فندم", "حضرتك", "بعد", "اذنك", "إذنك"}:
            continue
        if not re.search(r"[\u0600-\u06FF]", token):
            break
        tokens.append(token)
        if len(tokens) >= 3:
            break

    if not tokens:
        return None
    return " ".join(tokens)


def _capture_reservation_slots(*, text: str, ud: UserData, cfg: Any) -> bool:
    try:
        from core.extractors.reservation_extractor import (
            extract_guests_count,
            extract_reservation_time,
        )
    except Exception:
        return False

    captured = False
    if not ud.reservation_time:
        time_capture = extract_reservation_time(text)
        if time_capture.raw and time_capture.confidence >= 0.85:
            ud.reservation_time = time_capture.raw
            captured = True
            logger.info(
                "pipeline | captured | call=%s | slot=reservation_time | conf=%.2f | reason=%s",
                ud.call_id or "-", time_capture.confidence, time_capture.reason,
            )
    if ud.guests_count is None:
        guests_capture = extract_guests_count(text)
        if guests_capture.count and guests_capture.confidence >= 0.85:
            ud.guests_count = guests_capture.count
            captured = True
            logger.info(
                "pipeline | captured | call=%s | slot=guests | count=%d | conf=%.2f",
                ud.call_id or "-", guests_capture.count, guests_capture.confidence,
            )
        else:
            try:
                import re as _re
                from nlp.arabic import AR_DIGITS as _AR_DIGITS

                normalized_digits_text = (text or "").translate(_AR_DIGITS)
                match = _re.search(
                    r"(?:\b(\d{1,2})\s*(?:افراد|أفراد|اشخاص|أشخاص|شخص)\b|(?:احنا|إحنا)\s*(\d{1,2})\b)",
                    normalized_digits_text,
                    flags=_re.IGNORECASE,
                )
                raw_count = next((g for g in (match.groups() if match else ()) if g), None)
                count = int(raw_count) if raw_count else None
            except Exception:
                count = None
            if count is not None and 1 <= count <= 50:
                ud.guests_count = count
                captured = True
                logger.info(
                    "pipeline | captured | call=%s | slot=guests | count=%d | conf=%.2f | reason=digit_guest_fallback",
                    ud.call_id or "-", count, 0.88,
                )
    if cfg is not None and len(getattr(cfg, "branches", None) or []) > 1 and not ud.selected_branch:
        try:
            from agent import _resolve_branch_name
            branch = _resolve_branch_name(text, cfg.branches)
        except Exception:
            branch = None
        if branch:
            ud.selected_branch = branch
            captured = True
            logger.info(
                "pipeline | captured | call=%s | slot=branch | branch=%s",
                ud.call_id or "-", branch,
            )
    return captured


__all__ = ["PipelineResult", "PipelineAction", "run_pipeline"]
