"""Deterministic parser for order mutation intents.

Given a raw user turn, classify it into one of:

- ``add``       — add new items to the existing order ("ضيف معاه كولا").
- ``replace``   — wipe the order and replace it with the items in this turn
                  ("لأ غير الطلب، اعمله شاورما").
- ``remove``    — drop a previously ordered item ("شيل الكولا").
- ``increase``  — bump quantity of an existing item ("زود الكشري واحد").
- ``decrease``  — drop quantity of an existing item ("نقص واحد كشري").
- ``keep``      — explicit "leave it as it is" ("خليه كده").
- ``unknown``   — no mutation cue detected.

The parser only inspects user text and returns a structured intent. Applying
the mutation against ``UserData`` is the dialogue engine's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nlp.arabic import normalize_ar


MutationKind = Literal[
    "add",
    "replace",
    "remove",
    "increase",
    "decrease",
    "keep",
    "unknown",
]


@dataclass(frozen=True)
class MutationIntent:
    kind: MutationKind
    confidence: float = 0.0  # 0.0 .. 1.0
    cue: str = ""

    def is_known(self) -> bool:
        return self.kind != "unknown"


# Cue phrases keyed by intent kind. Stored normalized to keep matching
# cheap. Order within each tuple matches priority — longer/more specific
# phrases should appear first so they win the first-match contest.
_REPLACE_CUES: tuple[str, ...] = (
    "غير الطلب",
    "غيرلي الطلب",
    "بدل الطلب",
    "الغي الطلب",
    "الغى الطلب",
    "اعمل بدله",
    "خليه بدل",
    "بدل ده",
    "بدل دا",
    "لأ غير",
    "لا غير",
    "امسح الطلب",
    "شيل الطلب كله",
    "شيل كل حاجه",
    "شيل كل حاجة",
)


_REMOVE_CUES: tuple[str, ...] = (
    "شيل",
    "امسح",
    "الغي",
    "الغى",
    "احذف",
    "متجبش",
    "ما تجبش",
    "بلاش",
    "شيلي",
    "شيله",
    "اشيل",
)


_INCREASE_CUES: tuple[str, ...] = (
    "زود",
    "زوّد",
    "زيد",
    "كمّل",
    "اضافه واحد",
    "اضافة واحد",
    "اضافة واحدة",
)


_DECREASE_CUES: tuple[str, ...] = (
    "نقص",
    "خفف",
    "قلل",
    "اقل",
    "بدل ما يكون",
    "خليهم اقل",
)


_KEEP_CUES: tuple[str, ...] = (
    "خليه كده",
    "خليها كده",
    "كده تمام",
    "كده زي ما هو",
    "زي ما هو",
    "ماشي كده",
    "كده كفايه",
    "كده كفاية",
)


_ADD_CUES: tuple[str, ...] = (
    "ضيف",
    "ضيفلي",
    "زود معاه",
    "كمان",
    "هاتلي كمان",
    "وكمان",
    "وضيف",
    "وزود",
    "معاه",
    "معاها",
    "هاتلي",
    "وعايز كمان",
    "عايز كمان",
)


_NORMALIZED_GROUPS: tuple[tuple[MutationKind, tuple[str, ...]], ...] = (
    ("replace", _REPLACE_CUES),
    ("keep", _KEEP_CUES),
    ("decrease", _DECREASE_CUES),
    ("increase", _INCREASE_CUES),
    ("remove", _REMOVE_CUES),
    ("add", _ADD_CUES),
)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    norm_phrase = normalize_ar(phrase)
    if not norm_phrase:
        return False
    return f" {norm_phrase} " in f" {haystack} "


def parse_mutation(text: str) -> MutationIntent:
    """Classify a user turn into a MutationIntent.

    Replace beats keep beats decrease beats increase beats remove beats add.
    Keep wins over add/remove because "خليه كده" otherwise matches "خليه" in
    the loose remove list and "كده" tokens that look like quantity hints.
    """
    norm = normalize_ar(text)
    if not norm:
        return MutationIntent(kind="unknown")

    for kind, cues in _NORMALIZED_GROUPS:
        for cue in cues:
            if _contains_phrase(norm, cue):
                return MutationIntent(kind=kind, confidence=0.9, cue=cue)

    return MutationIntent(kind="unknown")


def is_explicit_replace(text: str) -> bool:
    return parse_mutation(text).kind == "replace"


def is_explicit_keep(text: str) -> bool:
    return parse_mutation(text).kind == "keep"


__all__ = [
    "MutationIntent",
    "MutationKind",
    "is_explicit_keep",
    "is_explicit_replace",
    "parse_mutation",
]
