"""Production-grade Egyptian Arabic order extractor.

Goals:

- Replace the LLM as the source of truth for which menu items the customer
  ordered. The extractor is the deterministic spine the dialogue engine
  trusts.
- Handle the things Egyptians actually say:
    * digit quantities ("2 كشري", "كشري 2", "كشري × 2")
    * Arabic-Indic digits ("٢ كشري")
    * spoken quantities ("اتنين كشري", "تلاته كولا")
    * "اتنين من X", "X اتنين"
    * "كمان واحد" / "واحد كمان" (quantity 1 + add intent)
- Tag each captured item with a confidence score so the engine can decide
  between accept / clarify / reprompt.
- Detect ambiguity (a phrase that could match multiple menu items) without
  silently picking one and submitting it.

The extractor never mutates ``UserData`` — it only inspects text + a built
``MenuIndex`` and returns a structured ``OrderExtraction``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.menu_index import MenuEntry, MenuIndex
from nlp.arabic import SPOKEN_DIGIT_MAP, normalize_ar


# Quantity confidence thresholds — used by the dialogue engine to decide
# capture vs clarify vs reprompt.
HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.6
LOW_CONFIDENCE = 0.35


# Verbs that license a numeric quantity *before* a menu phrase. Mirrors the
# logic in the legacy extractor but kept locally so the new extractor stays
# self-contained.
_QUANTITY_PRECURSORS: frozenset[str] = frozenset(
    {
        "عايز",
        "عاوز",
        "عاوزه",
        "عاوزة",
        "اطلب",
        "أطلب",
        "طلب",
        "هات",
        "هاتلي",
        "خد",
        "ضيف",
        "ضيفلي",
        "زود",
        "كمان",
        "وعايز",
        "وعاوز",
        "وضيف",
        "وزود",
    }
)


# Tokens that can never be a quantity (street numbers, address parts).
_FORBIDDEN_QUANTITY_CONTEXT: frozenset[str] = frozenset(
    {
        "شارع",
        "طريق",
        "ميدان",
        "عماره",
        "عمارة",
        "بنايه",
        "بناية",
        "شقه",
        "شقة",
        "دور",
        "بلوك",
        "برج",
        "حي",
        "منطقه",
        "منطقة",
        "زون",
        "street",
        "road",
        "avenue",
        "building",
        "floor",
        "apt",
        "apartment",
        "block",
        "tower",
        "district",
        "zone",
        "area",
        "house",
        "address",
    }
)


@dataclass(frozen=True)
class ExtractedItem:
    canonical_name: str
    quantity: int
    price_each: float
    confidence: float
    source_phrase: str = ""

    @property
    def line_total(self) -> float:
        return self.price_each * float(self.quantity)

    def formatted(self) -> str:
        if self.quantity <= 1:
            return self.canonical_name
        return f"{self.canonical_name} × {self.quantity}"


@dataclass
class OrderExtraction:
    items: list[ExtractedItem] = field(default_factory=list)
    unknown_phrases: list[str] = field(default_factory=list)
    ambiguous_phrases: list[tuple[str, list[str]]] = field(default_factory=list)
    raw_quantity_hints: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0

    def is_empty(self) -> bool:
        return not self.items

    def has_high_confidence(self) -> bool:
        return self.overall_confidence >= HIGH_CONFIDENCE and not self.ambiguous_phrases

    def has_ambiguity(self) -> bool:
        return bool(self.ambiguous_phrases)

    def formatted_items(self) -> list[str]:
        return [item.formatted() for item in self.items]

    def total(self) -> float:
        return sum(item.line_total for item in self.items)


def extract_order(
    text: str,
    index: MenuIndex,
    *,
    min_confidence: float = MEDIUM_CONFIDENCE,
) -> OrderExtraction:
    """Extract menu items + quantities from a user turn.

    ``min_confidence`` filters items below the threshold out of the final
    list so the dialogue engine can choose to either accept the result or
    fall back to a clarification question.
    """
    if not text or index.is_empty():
        return OrderExtraction()

    normalized = normalize_ar(text)
    if not normalized:
        return OrderExtraction()

    tokens = normalized.split()
    if not tokens:
        return OrderExtraction()

    matches: list[_RawMatch] = _find_phrase_matches(tokens, index)
    if not matches:
        return OrderExtraction()

    matches = _resolve_overlaps(matches)

    match_by_start: dict[int, _RawMatch] = {m.start: m for m in matches}
    consumed_qty_idx: set[int] = set()

    items: list[ExtractedItem] = []
    ambiguous: list[tuple[str, list[str]]] = []
    quantity_hints: list[str] = []

    for match in matches:
        if len(match.candidates) > 1:
            ambiguous.append(
                (match.phrase, [c.name for c in match.candidates])
            )
            continue

        entry = match.candidates[0]
        if not entry.available:
            continue

        qty, qty_source, qty_confidence = _resolve_quantity(
            tokens,
            match,
            match_by_start=match_by_start,
            consumed=consumed_qty_idx,
        )
        if qty_source:
            quantity_hints.append(qty_source)

        confidence = match.confidence * qty_confidence
        if confidence < min_confidence:
            continue

        items.append(
            ExtractedItem(
                canonical_name=entry.name,
                quantity=qty,
                price_each=entry.price,
                confidence=confidence,
                source_phrase=match.phrase,
            )
        )

    items = _aggregate_duplicates(items)
    overall = _overall_confidence(items)
    return OrderExtraction(
        items=items,
        unknown_phrases=[],
        ambiguous_phrases=ambiguous,
        raw_quantity_hints=quantity_hints,
        overall_confidence=overall,
    )


@dataclass
class _RawMatch:
    start: int
    end: int  # exclusive
    phrase: str
    candidates: list[MenuEntry]
    confidence: float


def _find_phrase_matches(tokens: list[str], index: MenuIndex) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    unavailable_spans: list[tuple[int, int]] = []
    seen_keys: set[str] = set()

    for entry in index.entries:
        menu_tokens = entry.tokens
        if not menu_tokens:
            continue
        size = len(menu_tokens)
        for start in range(0, len(tokens) - size + 1):
            window = tokens[start: start + size]
            if not _window_matches(window, menu_tokens, first_only_prefix=True):
                continue
            if not entry.available:
                unavailable_spans.append((start, start + size))
                continue
            key = f"{entry.norm_name}@{start}:{start+size}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            matches.append(
                _RawMatch(
                    start=start,
                    end=start + size,
                    phrase=" ".join(window),
                    candidates=[entry],
                    confidence=1.0,
                )
            )

    matches.extend(_find_alias_matches(tokens, index, seen_keys))
    matches.extend(
        _find_partial_token_matches(tokens, index, matches, unavailable_spans)
    )
    return matches


def _strip_definite_article(token: str) -> str:
    """Drop a leading "ال" only when it leaves a meaningful stem.

    Egyptians casually add "ال" before a menu item ("البرجر", "الكشري")
    even when the canonical menu name has no article, so we treat the
    article as optional during matching.
    """
    if len(token) > 3 and token.startswith("ال"):
        return token[2:]
    return token


def _find_partial_token_matches(
    tokens: list[str],
    index: MenuIndex,
    existing_matches: list[_RawMatch],
    unavailable_spans: list[tuple[int, int]] | None = None,
) -> list[_RawMatch]:
    """Single-token partial matches.

    Used when the customer says "كشري" alone but the menu only carries
    "كشري كبير". Confidence is lower, and if more than one menu entry
    fits the token we surface the ambiguity instead of guessing.

    Spans already covered by an exact unavailable-item match are skipped
    so that "بيتزا فيراري" (unavailable) doesn't fall back to "بيتزا
    مارجريتا" via the partial match path.

    A partial match is also suppressed if the token only points at
    menu entries we have *already* matched via full or alias phrases —
    otherwise "هاتلي برغر كبير" yields both an alias hit on "برغر" and
    a partial hit on "كبير" that resolve to the same item.
    """
    occupied: list[tuple[int, int]] = [(m.start, m.end) for m in existing_matches]
    if unavailable_spans:
        occupied.extend(unavailable_spans)
    already_matched_norms = {
        c.norm_name
        for m in existing_matches
        for c in m.candidates
        if len(m.candidates) == 1
    }

    def _is_occupied(idx: int) -> bool:
        return any(start <= idx < end for start, end in occupied)

    partial: list[_RawMatch] = []
    for idx, raw_token in enumerate(tokens):
        if _is_occupied(idx):
            continue
        token = raw_token
        if token.startswith("و") and len(token) > 1:
            token = token[1:]
        token = _strip_definite_article(token)
        if not token:
            continue

        candidates = [
            entry
            for entry in index.candidates_for_token(token)
            if entry.available
        ]
        if not candidates:
            continue
        # Skip if the token is itself the full canonical name; that would
        # have been caught by the full-phrase match above.
        if any(entry.norm_name == token for entry in candidates):
            continue
        # Skip if every candidate has already been claimed by a full or
        # alias phrase match — the partial match would be a duplicate.
        if all(entry.norm_name in already_matched_norms for entry in candidates):
            continue
        partial.append(
            _RawMatch(
                start=idx,
                end=idx + 1,
                phrase=raw_token,
                candidates=candidates,
                confidence=0.7,
            )
        )
    return partial


def _find_alias_matches(
    tokens: list[str],
    index: MenuIndex,
    seen_keys: set[str],
) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for alias_phrase, entry in index.by_alias.items():
        if not entry.available:
            continue
        alias_tokens = alias_phrase.split()
        if not alias_tokens:
            continue
        size = len(alias_tokens)
        for start in range(0, len(tokens) - size + 1):
            window = tokens[start: start + size]
            if not _window_matches(
                window,
                tuple(alias_tokens),
                first_only_prefix=True,
            ):
                continue
            key = f"alias:{entry.norm_name}@{start}:{start+size}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            matches.append(
                _RawMatch(
                    start=start,
                    end=start + size,
                    phrase=" ".join(window),
                    candidates=[entry],
                    confidence=0.9,
                )
            )
    return matches


def _window_matches(
    window: list[str],
    menu_tokens: tuple[str, ...],
    *,
    first_only_prefix: bool,
) -> bool:
    for pos, (turn_token, menu_token) in enumerate(zip(window, menu_tokens)):
        if turn_token == menu_token:
            continue
        candidate = turn_token
        if (
            first_only_prefix
            and pos == 0
            and len(candidate) > 1
            and candidate.startswith("و")
        ):
            candidate = candidate[1:]
        # Egyptians frequently prepend "ال" to a non-articled menu name.
        # Treat it as optional whether at the head of the phrase or
        # any inner token ("اتنين من البرجر الكبير" → "برجر كبير").
        if len(candidate) > 3 and candidate.startswith("ال"):
            candidate = candidate[2:]
        if candidate == menu_token:
            continue
        return False
    return True


def _resolve_overlaps(matches: list[_RawMatch]) -> list[_RawMatch]:
    """Prefer longer phrases over shorter overlapping ones.

    "برجر كبير" should win over "برجر" alone when both fit the same span.
    Ties stay in document order so the original turn ordering is preserved.
    """
    if not matches:
        return []

    matches = sorted(
        matches,
        key=lambda m: (m.end - m.start, m.confidence),
        reverse=True,
    )
    selected: list[_RawMatch] = []
    occupied: list[tuple[int, int]] = []
    for match in matches:
        overlap = False
        for span_start, span_end in occupied:
            if match.start < span_end and match.end > span_start:
                overlap = True
                break
        if overlap:
            continue
        selected.append(match)
        occupied.append((match.start, match.end))

    selected.sort(key=lambda m: m.start)
    return selected


def _resolve_quantity(
    tokens: list[str],
    match: _RawMatch,
    *,
    match_by_start: dict[int, "_RawMatch"] | None = None,
    consumed: set[int] | None = None,
) -> tuple[int, str, float]:
    """Find the quantity that goes with a menu phrase.

    Returns ``(quantity, source_text, confidence_multiplier)``.

    Resolution order (Egyptian Arabic norms):

    1. Multiplier prefix on the immediate before-token ("×2 برجر").
    2. Multiplier symbol immediately after ("برجر × 2").
    3. Suffix quantity ("برجر اتنين", "بطاطس 2") — preferred over prefix
       because Egyptians frequently put the qty after the dish in
       dictation. The suffix digit is **rejected** when:

         - it starts with "و" (clause boundary → next phrase's prefix),
         - the next menu match begins immediately after it AND that match
           does not begin with "و" (the digit is the next phrase's
           prefix-qty, not this phrase's suffix-qty).

    4. Prefix quantity ("3 برجر كبير") — only if the digit hasn't been
       consumed by a previous match.
    5. "اتنين من X" pattern.
    6. Fallback to 1 (low confidence).
    """
    before_idx = match.start - 1
    after_idx = match.end
    match_by_start = match_by_start or {}
    consumed = consumed if consumed is not None else set()

    before_token = tokens[before_idx] if before_idx >= 0 else None
    after_token = tokens[after_idx] if after_idx < len(tokens) else None
    after_token2 = tokens[after_idx + 1] if after_idx + 1 < len(tokens) else None

    if before_token and _multiplier_token(before_token):
        qty = _parse_int_token(before_token.lstrip("×x*X"))
        if qty:
            consumed.add(before_idx)
            return qty, before_token, 1.0

    if after_token and _is_multiplier_symbol(after_token):
        qty = _parse_int_token(after_token2 or "")
        if qty:
            consumed.add(after_idx)
            consumed.add(after_idx + 1)
            return qty, f"{after_token} {after_token2}", 1.0

    if (
        after_token is not None
        and after_idx not in consumed
        and not _is_forbidden_quantity_context(tokens, after_idx)
    ):
        if not _suffix_belongs_to_next(after_token, after_idx, tokens, match_by_start):
            qty = _parse_int_token(after_token)
            if qty:
                consumed.add(after_idx)
                return qty, after_token, 1.0

    if (
        before_token is not None
        and before_idx not in consumed
        and not _is_forbidden_quantity_context(tokens, before_idx)
    ):
        qty = _parse_int_token(before_token)
        if qty and _quantity_before_is_allowed(tokens, match.start):
            consumed.add(before_idx)
            return qty, before_token, 1.0

    # "اتنين من X" pattern: tokens[before_idx-1] is qty, tokens[before_idx] is "من"
    if before_token == "من" and match.start - 2 >= 0 and (match.start - 2) not in consumed:
        qty = _parse_int_token(tokens[match.start - 2])
        if qty:
            consumed.add(match.start - 2)
            return qty, f"{tokens[match.start - 2]} من", 1.0

    # "<menu> ... منها N" / "<menu> ... منه N" partitive pronoun pattern.
    # Egyptian customers commonly say "بيتزا مارجريتا محتاج منها 15 واحدة"
    # (lit. "pizza margherita, I need from-it 15 piece"). The quantity
    # belongs to the menu phrase even though there are 1-3 filler tokens
    # ("محتاج", "عاوز", verbs of wanting) between them.
    qty_partitive = _scan_partitive_qty(tokens, after_idx, consumed)
    if qty_partitive is not None:
        qty, src, claim_idx = qty_partitive
        consumed.add(claim_idx)
        return qty, src, 0.9

    return 1, "", 0.95


_PARTITIVE_PRONOUNS: frozenset[str] = frozenset({"منها", "منه", "منهم"})
_PARTITIVE_FILLERS: frozenset[str] = frozenset(
    {
        "محتاج",
        "محتاجه",
        "محتاجة",
        "عاوز",
        "عاوزه",
        "عاوزة",
        "عايز",
        "عايزه",
        "عايزة",
        "بطلب",
        "اطلب",
        "أطلب",
        "هاتلي",
        "هاتلى",
        "خد",
        "ابعتلي",
        "ابعتلى",
        "ابعت",
    }
)


def _scan_partitive_qty(
    tokens: list[str],
    after_idx: int,
    consumed: set[int],
) -> tuple[int, str, int] | None:
    """Look for "...محتاج منها N..." after a menu phrase.

    Returns ``(qty, source_text, consumed_idx)`` when a partitive pronoun
    + numeric appears within 3 tokens of the phrase end. Returns ``None``
    otherwise so callers can fall back to default qty=1.
    """
    if after_idx >= len(tokens):
        return None
    max_lookahead = min(after_idx + 4, len(tokens))
    for idx in range(after_idx, max_lookahead):
        token = tokens[idx]
        if token in _PARTITIVE_PRONOUNS:
            digit_idx = idx + 1
            if digit_idx >= len(tokens) or digit_idx in consumed:
                continue
            qty = _parse_int_token(tokens[digit_idx])
            if qty:
                return qty, f"{token} {tokens[digit_idx]}", digit_idx
        elif token in _PARTITIVE_FILLERS:
            # Filler word — keep scanning, but bail out on anything else
            # so we don't reach across long, unrelated text.
            continue
        else:
            break
    return None


def _suffix_belongs_to_next(
    after_token: str,
    after_idx: int,
    tokens: list[str],
    match_by_start: dict[int, "_RawMatch"],
) -> bool:
    """Return True when an after-digit should be left for the next phrase.

    A digit between two menu phrases belongs to the next phrase only when
    that next phrase is *not* introduced by "و"; the conjunction marks a
    clause boundary that frees the digit to be the current phrase's
    suffix-qty ("بطاطس اتنين وكولا" → اتنين goes with بطاطس).
    """
    if after_token.startswith("و") and len(after_token) > 1:
        return True
    next_match = match_by_start.get(after_idx + 1)
    if next_match is None:
        return False
    next_first_token = tokens[next_match.start]
    if next_first_token.startswith("و") and len(next_first_token) > 1:
        # Next phrase has its own clause marker — current claims the digit.
        return False
    return True


def _is_multiplier_symbol(token: str) -> bool:
    return token in {"×", "x", "X", "*"}


def _multiplier_token(token: str) -> bool:
    return bool(re.fullmatch(r"[×xX*]\d+", token))


def _parse_int_token(token: str) -> int | None:
    if not token:
        return None
    candidate = token.strip()
    if not candidate:
        return None
    if candidate.startswith("و") and len(candidate) > 1:
        candidate = candidate[1:]
    if re.fullmatch(r"\d{1,2}", candidate):
        value = int(candidate)
        if 1 <= value <= 20:
            return value
        return None
    mapped = SPOKEN_DIGIT_MAP.get(candidate)
    if mapped and mapped.isdigit():
        value = int(mapped)
        if 1 <= value <= 20:
            return value
    return None


def _quantity_before_is_allowed(tokens: list[str], item_start: int) -> bool:
    """A digit immediately before a menu phrase is the phrase's quantity.

    The address-context forbidden token check still runs at the call site
    via ``_is_forbidden_quantity_context``, so we don't need a verb-level
    precursor whitelist here. Loosening this lets natural turns like
    "تمام، 2 كولا" or "معاهم 3 بطاطس" work without LLM help.
    """
    if item_start <= 0:
        return False
    return True


def _is_forbidden_quantity_context(tokens: list[str], idx: int) -> bool:
    """A digit next to address words is a street number, not a quantity."""
    if idx < 0 or idx >= len(tokens):
        return False
    neighbours: list[str] = []
    if idx - 1 >= 0:
        neighbours.append(tokens[idx - 1])
    if idx + 1 < len(tokens):
        neighbours.append(tokens[idx + 1])
    return any(n in _FORBIDDEN_QUANTITY_CONTEXT for n in neighbours)


def _aggregate_duplicates(items: list[ExtractedItem]) -> list[ExtractedItem]:
    by_name: dict[str, ExtractedItem] = {}
    order: list[str] = []
    for item in items:
        if item.canonical_name not in by_name:
            by_name[item.canonical_name] = item
            order.append(item.canonical_name)
            continue
        existing = by_name[item.canonical_name]
        merged_qty = existing.quantity + item.quantity
        merged_confidence = min(existing.confidence, item.confidence)
        by_name[item.canonical_name] = ExtractedItem(
            canonical_name=existing.canonical_name,
            quantity=merged_qty,
            price_each=existing.price_each,
            confidence=merged_confidence,
            source_phrase=existing.source_phrase,
        )
    return [by_name[name] for name in order]


def _overall_confidence(items: list[ExtractedItem]) -> float:
    if not items:
        return 0.0
    return min(item.confidence for item in items)


__all__ = [
    "ExtractedItem",
    "HIGH_CONFIDENCE",
    "LOW_CONFIDENCE",
    "MEDIUM_CONFIDENCE",
    "OrderExtraction",
    "extract_order",
]
