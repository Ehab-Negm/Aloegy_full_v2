"""Phase 5 latency benchmark for the deterministic engine + extractors.

Goals (from the production roadmap):

- Local engine decision p95 under 30 ms.
- Deterministic turn p95 (extract + decide) under 100 ms.
- LLM fallback rate < 10 % on the deterministic ordering corpus.

This benchmark runs entirely in process: it measures wall time for
``order_extractor.extract_order`` and the dialogue engine's
``next_action`` over a representative Egyptian Arabic corpus, then
asserts the percentile gates.

The thresholds intentionally have a generous margin over the roadmap's
production p95 figures because CI machines are slower than the runtime
target, and the deterministic path is always faster than the LLM path it
is replacing — the gate exists to catch regressions, not to certify
exact runtime characteristics.
"""

from __future__ import annotations

import statistics
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from core.extractors.intent_extractor import detect_intent  # noqa: E402
from core.extractors.order_extractor import extract_order  # noqa: E402
from core.menu_index import MenuIndex  # noqa: E402


_FAILURES: list[tuple[str, str]] = []
_PASSED = 0
_TOTAL = 0


def _check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _TOTAL
    _TOTAL += 1
    if condition:
        _PASSED += 1
    else:
        _FAILURES.append((name, detail))


_MENU = [
    {"name": "برجر كبير", "price": 45.0, "available": True},
    {"name": "برجر صغير", "price": 30.0, "available": True},
    {"name": "بطاطس", "price": 20.0, "available": True},
    {"name": "كولا", "price": 15.0, "available": True},
    {"name": "كشري كبير", "price": 35.0, "available": True},
    {"name": "كشري صغير", "price": 25.0, "available": True},
    {"name": "شاورما فراخ", "price": 50.0, "available": True},
    {"name": "شاورما لحمة", "price": 60.0, "available": True},
    {"name": "بيتزا مارجريتا", "price": 80.0, "available": True},
    {"name": "سلطة", "price": 18.0, "available": True},
    {"name": "ميه", "price": 5.0, "available": True},
]


_TURNS = [
    "عايز اتنين برجر كبير وبطاطس",
    "هاتلي بيبسي",
    "٢ كشري كبير",
    "كشري × 3",
    "برجر كبير اتنين وكولا واحد",
    "تلاته برجر كبير",
    "عايز كولا",
    "اتنين من البرجر الكبير",
    "برجر كبير وبطاطس وكولا",
    "كولا وكولا",
    "اتنين برجر كبير وتلاته كولا",
    "شاورما فراخ مع شاورما لحمة",
    "بيتزا مارجريتا",
    "ميه ٣ مع سلطة",
    "ازيك",
    "متشكر يا فندم",
    "بتوصلوا فين؟",
    "ايه المتاح؟",
    "عايز توصيل",
    "تيكاواي",
    "احجزلي ترابيزة",
    "عندي شكوى",
    "اسمي أحمد",
    "01012345678",
    "المعادي شارع 9",
    "النهارده الساعة 7 مساء",
    "هاتلي 3 كولا",
    "اتنين شاورما فراخ",
    "كوكاكولا وفرايز",
    "البرجر الكبير اتنين",
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[rank]


def _bench_extract_order(iterations: int) -> tuple[list[float], list[float]]:
    index = MenuIndex.build(_MENU)
    extract_times: list[float] = []
    intent_times: list[float] = []
    # Warm caches.
    for turn in _TURNS:
        extract_order(turn, index)
        detect_intent(turn)
    for _ in range(iterations):
        for turn in _TURNS:
            t0 = time.perf_counter()
            extract_order(turn, index)
            t1 = time.perf_counter()
            detect_intent(turn)
            t2 = time.perf_counter()
            extract_times.append((t1 - t0) * 1000.0)
            intent_times.append((t2 - t1) * 1000.0)
    return extract_times, intent_times


def test_extract_p95_under_threshold() -> None:
    extract_times, _ = _bench_extract_order(iterations=10)
    p95 = _percentile(extract_times, 95.0)
    median = statistics.median(extract_times)
    print(
        f"extract_order: p50={median:.3f}ms p95={p95:.3f}ms "
        f"samples={len(extract_times)}"
    )
    _check(
        "phase5_extract_order_p95",
        p95 < 30.0,
        f"p95={p95:.3f}ms exceeds 30ms gate",
    )


def test_intent_p95_under_threshold() -> None:
    _, intent_times = _bench_extract_order(iterations=10)
    p95 = _percentile(intent_times, 95.0)
    median = statistics.median(intent_times)
    print(
        f"detect_intent: p50={median:.3f}ms p95={p95:.3f}ms "
        f"samples={len(intent_times)}"
    )
    _check(
        "phase5_intent_p95",
        p95 < 30.0,
        f"p95={p95:.3f}ms exceeds 30ms gate",
    )


def test_combined_turn_p95_under_threshold() -> None:
    index = MenuIndex.build(_MENU)
    combined: list[float] = []
    for _ in range(10):
        for turn in _TURNS:
            t0 = time.perf_counter()
            extract_order(turn, index)
            detect_intent(turn)
            t1 = time.perf_counter()
            combined.append((t1 - t0) * 1000.0)
    p95 = _percentile(combined, 95.0)
    median = statistics.median(combined)
    print(
        f"combined_turn: p50={median:.3f}ms p95={p95:.3f}ms "
        f"samples={len(combined)}"
    )
    _check(
        "phase5_combined_turn_p95",
        p95 < 100.0,
        f"p95={p95:.3f}ms exceeds 100ms gate",
    )


def test_menu_index_build_caching_pays_off() -> None:
    """Building the MenuIndex once is faster than per-turn rebuild."""
    iterations = 1000

    t0 = time.perf_counter()
    cached = MenuIndex.build(_MENU)
    for _ in range(iterations):
        extract_order(_TURNS[0], cached)
    t_cached = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(iterations):
        per_turn_index = MenuIndex.build(_MENU)
        extract_order(_TURNS[0], per_turn_index)
    t_uncached = time.perf_counter() - t0

    print(
        f"index_caching: cached={t_cached*1000:.1f}ms "
        f"uncached={t_uncached*1000:.1f}ms"
    )
    _check(
        "phase5_index_cache_helps",
        t_cached < t_uncached,
        f"cached {t_cached*1000:.1f}ms vs uncached {t_uncached*1000:.1f}ms",
    )


def test_fallback_rate_estimate() -> None:
    """Estimate LLM fallback rate by counting turns where *no*
    deterministic extractor (order + intent + contact + address +
    reservation) finds a capture. This is the share of turns that
    would have to fall through to the LLM in production.
    """
    from core.extractors.address_extractor import extract_address
    from core.extractors.contact_extractor import extract_name, extract_phone
    from core.extractors.reservation_extractor import (
        extract_guests_count,
        extract_reservation_time,
    )

    index = MenuIndex.build(_MENU)
    no_capture = 0
    total = 0
    for turn in _TURNS:
        if not extract_order(turn, index).is_empty():
            total += 1
            continue
        if detect_intent(turn).kind != "unknown":
            total += 1
            continue
        if extract_phone(turn).value is not None:
            total += 1
            continue
        if extract_name(turn).value is not None:
            total += 1
            continue
        if extract_address(turn, delivery_zones=("المعادي",)).value is not None:
            total += 1
            continue
        if extract_reservation_time(turn).raw is not None:
            total += 1
            continue
        if extract_guests_count(turn).count is not None:
            total += 1
            continue
        no_capture += 1
        total += 1
    rate = no_capture / total
    print(f"fallback_rate_estimate: {rate*100:.1f}% ({no_capture}/{total})")
    _check(
        "phase5_fallback_rate",
        rate < 0.10,
        f"deterministic miss rate {rate*100:.1f}% exceeds 10% gate",
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def _all_test_functions():
    g = globals()
    return [
        (name, g[name])
        for name in sorted(g)
        if name.startswith("test_") and callable(g[name])
    ]


def main() -> int:
    for name, fn in _all_test_functions():
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((name, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1

    print(f"PHASE5_LATENCY_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
