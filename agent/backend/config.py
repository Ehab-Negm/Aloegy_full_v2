from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from utils.money import money2ar, num2ar


DAYS_AR = {
    "saturday": "السبت",
    "sunday": "الأحد",
    "monday": "الاتنين",
    "tuesday": "التلات",
    "wednesday": "الأربع",
    "thursday": "الخميس",
    "friday": "الجمعة",
}


def _voice_menu_limit() -> int:
    raw = os.getenv("VOICE_MENU_LIMIT", "10")
    try:
        return max(4, int(raw))
    except ValueError:
        return 10


def _menu_prompt_limit() -> int:
    raw = os.getenv("MENU_PROMPT_LIMIT", "20")
    try:
        return max(6, int(raw))
    except ValueError:
        return 20


@dataclass
class RestaurantConfig:
    name: str = ""
    phone: str = ""
    address: str = ""
    branches: list[dict] = field(default_factory=list)
    hours: dict = field(default_factory=dict)
    menu_items: list[dict] = field(default_factory=list)
    upsell_rules: list[dict] = field(default_factory=list)
    is_open: bool = True
    closed_reason: str = ""
    degraded_mode: bool = False
    config_source: Literal["backend", "cache_fresh", "cache_stale", "degraded_fallback"] = "backend"
    wait_minutes: int = 20
    min_guests: int = 1
    max_guests: int = 20
    delivery_enabled: bool = False
    delivery_minutes: int = 45
    delivery_fee: float = 0.0
    min_order: float = 0.0
    delivery_zones: list[str] = field(default_factory=list)

    def hours_text(self) -> str:
        if self.degraded_mode and not self.hours:
            return "مواعيد المطعم غير متاحة مؤقتًا"
        if not self.hours:
            return "المواعيد غير محددة"
        parts = []
        for day, times in self.hours.items():
            label = DAYS_AR.get(day, day)
            if times.get("closed"):
                parts.append(f"{label}: مغلق")
            else:
                parts.append(f"{label}: {times.get('open','?')} - {times.get('close','?')}")
        return " | ".join(parts)

    def branch_names(self) -> str:
        return " / ".join(b.get("name", "") for b in self.branches)

    def delivery_zones_text(self) -> str:
        if not self.delivery_zones:
            return "جميع المناطق"
        return "، ".join(self.delivery_zones)

    def delivery_info_text(self) -> str:
        if self.degraded_mode and not self.delivery_enabled:
            return "بيانات التوصيل غير متاحة مؤقتًا"
        parts = [f"وقت التوصيل: {num2ar(self.delivery_minutes)} دقيقة"]
        if self.delivery_fee > 0:
            parts.append(f"رسوم التوصيل: {money2ar(self.delivery_fee)} جنيه")
        if self.min_order > 0:
            parts.append(f"أقل طلب للتوصيل: {money2ar(self.min_order)} جنيه")
        return " | ".join(parts)

    def menu_text(self) -> str:
        available = [i for i in self.menu_items if i.get("available", True)]
        if not available:
            return "المنيو مش متاح مؤقتًا دلوقتي" if self.degraded_mode else "المنيو مش متاح دلوقتي"
        max_shown = min(_voice_menu_limit(), len(available))
        extra_suffix = "، ولو عايز حاجة تانية قولهولي." if len(available) > max_shown else "."

        for shown_count in range(max_shown, 0, -1):
            shown = available[:shown_count]
            text = "المتاح دلوقتي: " + "، ".join(
                f"{i['name']} بـ{money2ar(i['price'])}" for i in shown
            )
            text += extra_suffix
            if len(text) <= 220:
                return text

        shown = available[:min(3, len(available))]
        return "المتاح دلوقتي: " + "، ".join(str(i["name"]) for i in shown) + ". ولو عايز حاجة تانية قولهولي."

    def menu_names(self) -> str:
        available = [i["name"] for i in self.menu_items if i.get("available", True)]
        if not available:
            return "المنيو مش متاح مؤقتًا" if self.degraded_mode else "المنيو مش متاح"
        shown = available[:_menu_prompt_limit()]
        text = "، ".join(shown)
        if len(available) > len(shown):
            text += f"، وغيرهم {num2ar(len(available) - len(shown))} أصناف"
        return text


@dataclass
class CachedConfigEntry:
    fetched_at_monotonic: float
    config: RestaurantConfig
    source: str = "backend"
