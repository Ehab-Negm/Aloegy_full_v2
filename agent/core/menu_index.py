"""Menu index for deterministic order extraction.

Builds a normalized, alias-aware index over a restaurant's menu items so the
order extractor can:

- match menu items in O(tokens) per turn instead of O(items × turn) per turn,
- accept Egyptian Arabic aliases ("بيبسي" → "كولا"),
- accept common STT mishearings ("شاورمة" → "شاورما"),
- detect ambiguous matches (more than one menu item shares a phrase),
- skip unavailable items without scanning.

The class is intentionally pure — no IO, no globals, no logging — so it can be
exercised by unit tests and cached per-config-version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from nlp.arabic import normalize_ar


@dataclass(frozen=True)
class MenuEntry:
    """Single menu item plus its alias-derived match keys."""

    name: str
    price: float
    available: bool
    tokens: tuple[str, ...]
    norm_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Manually curated Egyptian Arabic aliases keyed by normalized canonical
# name fragments. Values are normalized so the index can compare directly.
# Add to this dict when QA reveals an item being misheard or aliased.
_ALIAS_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("كولا", ("بيبسي", "كوكاكولا", "كوكا كولا", "بيبسى", "كوكا")),
    ("بيبسي", ("بيبسى", "كولا", "كوكاكولا")),
    ("سفن اب", ("سفن", "سفن آب", "7up", "سفنب")),
    ("ميرندا", ("ميراندا",)),
    ("شاورما", ("شاورمة",)),
    ("كشري", ("الكشري",)),
    ("برجر", ("برغر", "هامبرجر", "برجار")),
    ("بيتزا", ("بيتزه", "بيتسا", "بتزا")),
    ("بطاطس", ("بطاطا", "فرايز", "البطاطس")),
    ("سلطة", ("سلاطة", "سلاطه")),
    ("فراخ", ("دجاج", "الفراخ")),
    ("فول", ("الفول",)),
    ("طعميه", ("طعمية", "فلافل", "الطعميه")),
    ("سندوتش", ("ساندوتش", "ساندويتش", "ساندويش")),
    ("عصير", ("جوس", "العصير", "عصاير")),
    ("ميه", ("مياه", "ميا", "مية")),
)


_STT_REPAIR_GROUPS: tuple[tuple[str, str], ...] = (
    ("بيبسى", "بيبسي"),
    ("كوكاكولا", "كولا"),
    ("بتزا", "بيتزا"),
    ("بيتسا", "بيتزا"),
    ("شاورمة", "شاورما"),
    ("برغر", "برجر"),
    ("هامبرجر", "برجر"),
)


def _alias_set_for_token(norm_token: str) -> tuple[str, ...]:
    """Return additional alias variants for a normalized token."""
    aliases: list[str] = []
    for canonical, variants in _ALIAS_GROUPS:
        if normalize_ar(canonical) == norm_token:
            aliases.extend(normalize_ar(v) for v in variants)
        if any(normalize_ar(v) == norm_token for v in variants):
            aliases.append(normalize_ar(canonical))
            aliases.extend(
                normalize_ar(v)
                for v in variants
                if normalize_ar(v) != norm_token
            )
    return tuple(dict.fromkeys(a for a in aliases if a))


@dataclass
class MenuIndex:
    """Searchable index over a list of menu items.

    Build once per config version and reuse across turns.
    """

    entries: list[MenuEntry] = field(default_factory=list)
    by_norm_name: dict[str, MenuEntry] = field(default_factory=dict)
    by_token: dict[str, list[MenuEntry]] = field(default_factory=dict)
    by_alias: dict[str, MenuEntry] = field(default_factory=dict)
    config_version: str = ""

    @classmethod
    def build(
        cls,
        menu_items: Iterable[dict] | None,
        *,
        config_version: str = "",
    ) -> "MenuIndex":
        index = cls(config_version=config_version)
        for item in (menu_items or []):
            entry = _build_entry(item)
            if entry is None:
                continue
            index.entries.append(entry)
            index.by_norm_name[entry.norm_name] = entry
            for token in entry.tokens:
                index.by_token.setdefault(token, []).append(entry)
            for alias in entry.aliases:
                index.by_alias.setdefault(alias, entry)
        return index

    def is_empty(self) -> bool:
        return not self.entries

    def find_by_phrase(self, phrase: str) -> MenuEntry | None:
        """Look up a menu entry by normalized phrase or alias."""
        norm = normalize_ar(phrase)
        if not norm:
            return None
        if norm in self.by_norm_name:
            return self.by_norm_name[norm]
        if norm in self.by_alias:
            return self.by_alias[norm]
        return None

    def candidates_for_token(self, token: str) -> list[MenuEntry]:
        """Return menu entries whose canonical name contains the given token."""
        norm = normalize_ar(token)
        if not norm:
            return []
        return list(self.by_token.get(norm, ()))

    def is_known_token(self, token: str) -> bool:
        norm = normalize_ar(token)
        if not norm:
            return False
        return norm in self.by_token or norm in self.by_alias


def _build_entry(item: dict) -> MenuEntry | None:
    name = str(item.get("name", "") or "").strip()
    if not name:
        return None
    norm_name = normalize_ar(name)
    if not norm_name:
        return None
    tokens = tuple(t for t in norm_name.split() if t)

    aliases: list[str] = []
    for token in tokens:
        aliases.extend(_alias_set_for_token(token))
    for canonical, repaired in _STT_REPAIR_GROUPS:
        if normalize_ar(repaired) in tokens:
            aliases.append(normalize_ar(canonical))
    aliases = list(dict.fromkeys(a for a in aliases if a and a != norm_name))

    price_raw = item.get("price", 0) or 0
    try:
        price = float(price_raw)
    except (TypeError, ValueError):
        price = 0.0
    return MenuEntry(
        name=name,
        price=price,
        available=bool(item.get("available", True)),
        tokens=tokens,
        norm_name=norm_name,
        aliases=tuple(aliases),
    )


__all__ = ["MenuEntry", "MenuIndex"]
