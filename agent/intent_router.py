"""Config-driven intent router.

Thin orchestrator on top of ``core.extractors.intent_extractor.detect_intent``
that:

1. Loads additional intent phrases from ``agent/config/dialogue_rules.yaml``
   so ops can tune them at runtime without code changes.
2. Returns a ``RouteDecision`` with a stable shape the dialogue pipeline
   can act on (route immediately / clarify / fall back).
3. Includes telemetry-friendly metadata (matched terms, reason).

The underlying extractor already has hand-curated Egyptian Arabic cues
and confidence scoring; this module only adds a config-overlay and a
decision shape — it doesn't reimplement classification.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - defensive
    _yaml = None

from core.extractors.intent_extractor import IntentDetection, IntentKind, detect_intent
from nlp.arabic import normalize_ar


logger = logging.getLogger("restaurant.agent")


HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6


RouteAction = Literal["route", "clarify", "fallback"]


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    intent: IntentKind = "unknown"
    confidence: float = 0.0
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def is_route(self) -> bool:
        return self.action == "route"


_OVERLAY: dict | None = None


def _load_overlay() -> dict:
    """Load extra phrases from YAML; merge over the extractor's built-in cues."""
    global _OVERLAY
    if _OVERLAY is not None:
        return _OVERLAY

    overlay: dict = {}
    if _yaml is not None:
        candidates = [
            os.getenv("DIALOGUE_RULES_PATH", "").strip(),
            str(Path(__file__).parent / "config" / "dialogue_rules.yaml"),
        ]
        for path_str in candidates:
            if not path_str:
                continue
            path = Path(path_str)
            if not path.is_file():
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    loaded = _yaml.safe_load(fh) or {}
                if isinstance(loaded, dict):
                    intents_block = loaded.get("intents") or {}
                    if isinstance(intents_block, dict):
                        overlay = intents_block
                        logger.info("intent_router overlay loaded | path=%s | intents=%d", path, len(overlay))
                        break
            except Exception as exc:
                logger.warning("intent_router overlay load failed | path=%s | %s", path, exc)

    _OVERLAY = overlay
    return overlay


def _overlay_match(text_norm: str, intent_kind: IntentKind) -> tuple[float, str | None]:
    """Check the YAML-overlay phrases for ``intent_kind`` against ``text_norm``.

    Returns ``(confidence, matched_phrase)`` or ``(0.0, None)``.
    """
    overlay = _load_overlay().get(intent_kind)
    if not isinstance(overlay, dict):
        return 0.0, None

    phrases = overlay.get("phrases") or []
    threshold = float(overlay.get("threshold", HIGH_CONFIDENCE))

    for phrase in phrases:
        if not phrase:
            continue
        n = normalize_ar(phrase)
        if n and n in text_norm:
            return threshold, phrase
    return 0.0, None


def route(text: str, *, threshold: float = HIGH_CONFIDENCE) -> RouteDecision:
    """Classify ``text`` and decide whether to route, clarify, or fall back.

    Strategy:
    1. Run the existing ``detect_intent`` extractor (tested, high-quality).
    2. If its confidence already meets ``threshold`` → ``route``.
    3. Otherwise, check the YAML overlay for additional phrases the
       hard-coded list might be missing.
    4. Medium confidence (≥ 0.6) → ``clarify`` (caller asks a follow-up).
    5. Below medium → ``fallback`` (caller defers to LLM).
    """
    if not text or not text.strip():
        return RouteDecision(action="fallback", reason="empty_text")

    detection: IntentDetection = detect_intent(text)
    matched: list[str] = []

    if detection.cue:
        matched.append(detection.cue)

    confidence = detection.confidence
    intent = detection.kind

    # Promote with overlay matches when the hard-coded extractor missed.
    if confidence < threshold:
        text_norm = normalize_ar(text)
        for candidate_intent in ("delivery", "takeaway", "reservation", "complaint"):
            ov_conf, ov_phrase = _overlay_match(text_norm, candidate_intent)  # type: ignore[arg-type]
            if ov_conf > confidence:
                confidence = ov_conf
                intent = candidate_intent  # type: ignore[assignment]
                if ov_phrase:
                    matched.append(ov_phrase)

    # Decide action.
    if intent != "unknown" and confidence >= threshold:
        return RouteDecision(
            action="route",
            intent=intent,
            confidence=confidence,
            matched_terms=tuple(matched),
            reason="high_confidence",
        )
    if intent != "unknown" and confidence >= MEDIUM_CONFIDENCE:
        return RouteDecision(
            action="clarify",
            intent=intent,
            confidence=confidence,
            matched_terms=tuple(matched),
            reason="medium_confidence",
        )
    return RouteDecision(
        action="fallback",
        intent=intent,
        confidence=confidence,
        matched_terms=tuple(matched),
        reason="low_confidence",
    )


def reset_overlay_cache() -> None:
    """Force YAML overlay reload on next ``route`` call. Used by tests."""
    global _OVERLAY
    _OVERLAY = None


__all__ = ["RouteDecision", "RouteAction", "route", "reset_overlay_cache"]
