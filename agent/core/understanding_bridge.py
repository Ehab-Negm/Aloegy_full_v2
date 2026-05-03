"""Bridge between ``TurnUnderstanding`` and the existing engine call-sites.

Each helper takes the cached LLM understanding plus the legacy result
and returns the one the engine should use. The rule is the same
everywhere:

- if the LLM (or a configured mock) produced an actionable result,
  validate and use it,
- otherwise fall back to the legacy cue-list extractor so tests and
  dev environments without an API key keep working.

Validation against the menu / phone format / etc. always runs on top
of the LLM output — the LLM can hallucinate, the engine can't.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.understanding import TurnUnderstanding, is_actionable

if TYPE_CHECKING:
    from backend.config import RestaurantConfig


def order_items_from_understanding(
    understanding: TurnUnderstanding | None,
    cfg: "RestaurantConfig",
) -> list[str] | None:
    """Validate + format the LLM-extracted items against the menu.

    Returns:
        - ``None`` when the understanding isn't actionable. The caller
          should fall back to the legacy extractor.
        - ``[]`` when the LLM saw no items in this turn (legitimate;
          don't retry with the legacy path).
        - ``["برجر كبير × 2", "كولا"]`` when items were captured.

    Items the LLM proposes that don't exist (or are unavailable) in
    the menu are dropped silently — the validation step keeps the
    engine immune to hallucinations.
    """
    if understanding is None or not is_actionable(understanding):
        return None
    if not understanding.order_items:
        return []

    from core.menu_index import MenuIndex

    index = MenuIndex.build(cfg.menu_items or [])
    formatted: list[str] = []
    for mention in understanding.order_items:
        entry = index.find_by_phrase(mention.item_name)
        if entry is None or not entry.available:
            continue
        qty = max(1, int(mention.quantity or 1))
        if qty <= 1:
            formatted.append(entry.name)
        else:
            formatted.append(f"{entry.name} × {qty}")
    return formatted


def intent_from_understanding(understanding: TurnUnderstanding | None) -> str | None:
    """Map the LLM intent to the legacy ``_guess_request_intent`` vocabulary.

    Returns ``None`` when the understanding isn't actionable so callers
    fall through to the legacy intent guess.
    """
    if understanding is None or not is_actionable(understanding):
        return None
    if understanding.intent_confidence == "low":
        return None

    mapping = {
        "takeaway": "takeaway",
        "delivery": "delivery",
        "reservation": "reservation",
        "complaint": "complaint",
        "menu_question": "menu",
        "delivery_zone_question": "menu",
        "total_question": "unknown",
        "post_completion_thanks": "unknown",
        "greeting": "unknown",
        "confirming": "unknown",
        "denying": "unknown",
        "clarification": "unknown",
        "unknown": "unknown",
    }
    return mapping.get(understanding.intent, "unknown")


def name_from_understanding(understanding: TurnUnderstanding | None) -> str | None:
    if understanding is None or not is_actionable(understanding):
        return None
    name = understanding.customer_name
    if not name:
        return None
    cleaned = name.strip()
    if not cleaned or len(cleaned.split()) > 3:
        return None
    return cleaned


def phone_digits_from_understanding(understanding: TurnUnderstanding | None) -> str | None:
    if understanding is None or not is_actionable(understanding):
        return None
    digits = understanding.customer_phone_digits
    if not digits:
        return None
    digits = "".join(ch for ch in digits if ch.isdigit())
    return digits or None


def address_from_understanding(
    understanding: TurnUnderstanding | None,
) -> tuple[str, str] | None:
    """Return ``(address, zone)`` from the LLM, or ``None`` to fall back."""
    if understanding is None or not is_actionable(understanding):
        return None
    addr = (understanding.delivery_address or "").strip()
    if not addr:
        return None
    zone = (understanding.delivery_zone or "").strip()
    return addr, zone


def mutation_from_understanding(understanding: TurnUnderstanding | None) -> str | None:
    if understanding is None or not is_actionable(understanding):
        return None
    return understanding.mutation


def reservation_time_from_understanding(
    understanding: TurnUnderstanding | None,
) -> str | None:
    if understanding is None or not is_actionable(understanding):
        return None
    return (understanding.reservation_time or "").strip() or None


def guests_from_understanding(understanding: TurnUnderstanding | None) -> int | None:
    if understanding is None or not is_actionable(understanding):
        return None
    return understanding.guests_count


def complaint_from_understanding(
    understanding: TurnUnderstanding | None,
) -> tuple[str, str] | None:
    if understanding is None or not is_actionable(understanding):
        return None
    text = (understanding.complaint_text or "").strip()
    if not text:
        return None
    return text, understanding.complaint_category or "other"


def is_confirming(understanding: TurnUnderstanding | None) -> bool:
    if understanding is None or not is_actionable(understanding):
        return False
    return bool(understanding.is_confirming)


def is_denying(understanding: TurnUnderstanding | None) -> bool:
    if understanding is None or not is_actionable(understanding):
        return False
    return bool(understanding.is_denying)


__all__ = [
    "address_from_understanding",
    "complaint_from_understanding",
    "guests_from_understanding",
    "intent_from_understanding",
    "is_confirming",
    "is_denying",
    "mutation_from_understanding",
    "name_from_understanding",
    "order_items_from_understanding",
    "phone_digits_from_understanding",
    "reservation_time_from_understanding",
]
