"""Deterministic correction-phrase detector.

Detects when a user is correcting a previous turn and classifies the
correction so the dialogue pipeline can route it to the right handler:

- ``flow_change``      — "لا خليها تيكاواي"   → switch flow, keep slots
- ``item_mod``         — "شيل الكولا" / "زود واحد" → mutate order
- ``slot_correction``  — "الرقم غلط" / "العنوان مش ده"
- ``cancel``           — "الغي الطلب"
- ``confirm_yes``      — "آه / تمام / أكد / ماشي"
- ``confirm_no``       — "لا / مش كده"

Confidence tiers mirror the existing extractors (HIGH ≥ 0.85). The
detector is rule-based and pure (no side effects, no I/O), so it can be
unit-tested freely. Phrases are loaded from
``agent/config/dialogue_rules.yaml`` at first use; falls back to a
hard-coded mirror so the module works in environments where the YAML
file isn't shipped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - defensive
    _yaml = None

from nlp.arabic import normalize_ar


logger = logging.getLogger("restaurant.agent")

CorrectionKind = Literal[
    "flow_change",
    "item_mod_add",
    "item_mod_remove",
    "item_mod_replace",
    "item_mod_quantity",
    "slot_correction",
    "cancel",
    "confirm_yes",
    "confirm_no",
    "none",
]


@dataclass(frozen=True)
class CorrectionDetection:
    kind: CorrectionKind
    confidence: float = 0.0
    cue: str = ""
    target: str = ""  # for flow_change: target flow; for slot_correction: slot name

    def is_actionable(self, threshold: float = 0.6) -> bool:
        return self.kind != "none" and self.confidence >= threshold


# ─── Hard-coded fallback phrases (kept in sync with dialogue_rules.yaml) ────
_DEFAULT_RULES: dict = {
    "confirm_yes": [
        "اه", "آه", "ايوه", "أيوه", "تمام", "ماشي", "اوكي", "أوكي",
        "اكد", "أكد", "اكدت", "موافق", "حلو", "ok", "yes",
    ],
    "confirm_no": [
        "لا", "لأ", "مش كده", "مش كدا", "خطأ", "غلط", "no",
    ],
    "cancel": [
        "الغي", "الغى", "الغ الطلب", "الغي الطلب", "كنسل", "مش عايز",
        "بطل الطلب", "اوقف الطلب", "cancel",
    ],
    "flow_change_markers": [
        "خليها", "خلي", "بدل ما", "غير", "بدل", "اعمله", "خله",
    ],
    "flow_targets": {
        "delivery":    ["توصيل", "دليفري", "delivery", "يوصل"],
        "takeaway":    ["تيكاواي", "استلام", "هاخده", "takeaway", "اجي اخده"],
        "reservation": ["حجز", "ترابيزه", "ترابيزة", "احجز"],
        "complaint":   ["شكوى", "مشكله", "مشكلة"],
    },
    "item_remove": [
        "شيل", "احذف", "امسح", "الغ", "الغى", "ميجبش", "بدون",
        "مش عايز", "اشلح",
    ],
    "item_add": [
        "زود", "كمان", "ضيف", "اضف", "اضيف", "ابعت كمان",
    ],
    "item_replace": [
        "بدل", "غير", "خلي بدالها", "خلي بدالة", "خليها بدل",
    ],
    "slot_correction_markers": [
        "غلط", "مش صح", "مش ده", "مش دي", "خطأ", "مش الرقم", "مش العنوان",
        "عدل", "غير", "صحح",
    ],
    "slot_targets": {
        "phone":   ["رقم", "موبايل", "تليفون", "نمرة", "نمره"],
        "address": ["عنوان", "مكان", "بيت", "شقه", "شقة", "العمارة"],
        "name":    ["اسم", "اسمي"],
    },
}


_RULES: dict | None = None


def _load_rules() -> dict:
    """Load dialogue-rule phrases from YAML, fall back to defaults.

    Cached after first load. Set the ``DIALOGUE_RULES_PATH`` env var to
    override the default location (``agent/config/dialogue_rules.yaml``).
    """
    global _RULES
    if _RULES is not None:
        return _RULES

    rules: dict = {}
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
                    rules = loaded
                    logger.info("dialogue_rules loaded | path=%s", path)
                    break
            except Exception as exc:
                logger.warning("dialogue_rules load failed | path=%s | %s", path, exc)

    # Merge with defaults (yaml takes precedence; defaults fill gaps).
    merged = {**_DEFAULT_RULES, **rules}
    _RULES = merged
    return merged


def _norm_set(values) -> set[str]:
    return {normalize_ar(v) for v in (values or []) if v}


def _phrase_present(needle, haystack: str) -> bool:
    if not isinstance(needle, str) or not needle or not haystack:
        return False
    n = normalize_ar(needle)
    if not n:
        return False
    needle_tokens = n.split()
    if len(needle_tokens) == 1:
        return n in haystack.split()
    return n in haystack


def _is_explicit_cancel(cue: str, norm: str) -> bool:
    """Keep item rejections ("مش عايز كولا") away from whole-order cancel."""
    cue_norm = normalize_ar(cue)
    if cue_norm not in {normalize_ar("مش عايز"), normalize_ar("مش عاوز")}:
        return True
    cancel_targets = {
        "الطلب", "الاوردر", "الأوردر", "اوردر", "أوردر", "اكمل", "أكمل", "المكالمة",
    }
    norm_tokens = set(norm.split())
    return any(normalize_ar(target) in norm_tokens for target in cancel_targets)


def _any_present(haystack: str, candidates) -> str | None:
    for c in candidates or ():
        if isinstance(c, str) and _phrase_present(c, haystack):
            return c
    return None


def detect_correction(text: str) -> CorrectionDetection:
    """Classify the correction type expressed in ``text``.

    Returns ``CorrectionDetection(kind="none", confidence=0.0)`` when
    no correction phrase is detected. Caller is responsible for
    deciding what to do with each kind (see module docstring).
    """
    if not text or not text.strip():
        return CorrectionDetection(kind="none")

    rules = _load_rules()
    norm = normalize_ar(text)
    if not norm:
        return CorrectionDetection(kind="none")

    # Cancel takes precedence — if the user said "cancel the order",
    # nothing else matters.
    if (cue := _any_present(norm, rules.get("cancel", ()))) and _is_explicit_cancel(cue, norm):
        return CorrectionDetection(kind="cancel", confidence=0.95, cue=cue)

    # Flow change: needs both a marker (خليها / بدل / غير) AND a flow
    # target keyword. Just saying "خليها" by itself is ambiguous;
    # "خليها تيكاواي" is unambiguous.
    flow_marker = _any_present(norm, rules.get("flow_change_markers", ()))
    if flow_marker:
        for flow_name, cues in (rules.get("flow_targets") or {}).items():
            cue = _any_present(norm, cues)
            if cue:
                return CorrectionDetection(
                    kind="flow_change",
                    confidence=0.92,
                    cue=f"{flow_marker}+{cue}",
                    target=flow_name,
                )

    # Item modifications. ``replace`` is checked before ``remove``/``add``
    # because "بدل المارجريتا بفراخ" contains "بدل" which would otherwise
    # also match a remove cue.
    if (cue := _any_present(norm, rules.get("item_replace", ()))):
        return CorrectionDetection(kind="item_mod_replace", confidence=0.85, cue=cue)
    if (cue := _any_present(norm, rules.get("item_remove", ()))):
        return CorrectionDetection(kind="item_mod_remove", confidence=0.85, cue=cue)
    if (cue := _any_present(norm, rules.get("item_add", ()))):
        return CorrectionDetection(kind="item_mod_add", confidence=0.85, cue=cue)

    # Slot correction: marker + slot target keyword.
    slot_marker = _any_present(norm, rules.get("slot_correction_markers", ()))
    if slot_marker:
        for slot_name, cues in (rules.get("slot_targets") or {}).items():
            cue = _any_present(norm, cues)
            if cue:
                return CorrectionDetection(
                    kind="slot_correction",
                    confidence=0.88,
                    cue=f"{slot_marker}+{cue}",
                    target=slot_name,
                )

    # Bare yes/no — only treat as confirmation when text is very short.
    # A long sentence that happens to start with "اه" is just the user
    # speaking, not confirming.
    word_count = len([w for w in norm.split() if w])
    if word_count <= 3:
        if (cue := _any_present(norm, rules.get("confirm_yes", ()))):
            return CorrectionDetection(kind="confirm_yes", confidence=0.9, cue=cue)
        if (cue := _any_present(norm, rules.get("confirm_no", ()))):
            return CorrectionDetection(kind="confirm_no", confidence=0.9, cue=cue)

    return CorrectionDetection(kind="none")


def reset_rules_cache() -> None:
    """Force re-load of dialogue_rules.yaml on next ``detect_correction``
    call. Useful for tests and hot-reload scenarios."""
    global _RULES
    _RULES = None


__all__ = [
    "CorrectionDetection",
    "CorrectionKind",
    "detect_correction",
    "reset_rules_cache",
]
