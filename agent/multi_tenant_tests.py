"""Multi-tenant isolation tests for the deterministic engine.

A worker can serve concurrent calls for different restaurants. The
shared caches must:

- never return one tenant's menu when extracting another tenant's order,
- invalidate when a tenant updates their menu (item add / remove /
  availability flip),
- keep the per-call ``SubmissionTracker`` isolated by ``call_id`` so a
  duplicate-confirm for call A can never block call B.
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


from backend.config import RestaurantConfig  # noqa: E402
from core.extractors.order_extractor import extract_order  # noqa: E402
from core.menu_index import MenuIndex  # noqa: E402
from core.submission_policy import (  # noqa: E402
    SubmissionTracker,
    compute_idempotency_key,
    get_or_create_tracker_for_call,
    release_tracker_for_call,
)


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


def _make_cfg(items: list[dict], *, name: str = "تيست") -> RestaurantConfig:
    return RestaurantConfig(name=name, menu_items=items)


# ---------------------------------------------------------------------------
# Menu-index per-tenant isolation
# ---------------------------------------------------------------------------


def test_two_tenants_get_distinct_menu_indices() -> None:
    cfg_a = _make_cfg([{"name": "برجر", "price": 40.0, "available": True}])
    cfg_b = _make_cfg([{"name": "كشري", "price": 25.0, "available": True}])
    idx_a = MenuIndex.build(cfg_a.menu_items)
    idx_b = MenuIndex.build(cfg_b.menu_items)
    _check("two_tenants:burger_only_in_a", any(e.norm_name == "برجر" for e in idx_a.entries))
    _check("two_tenants:koshary_only_in_b", any(e.norm_name == "كشري" for e in idx_b.entries))
    _check("two_tenants:no_cross_a", not any(e.norm_name == "كشري" for e in idx_a.entries))
    _check("two_tenants:no_cross_b", not any(e.norm_name == "برجر" for e in idx_b.entries))


def test_extract_order_uses_correct_tenant_menu() -> None:
    cfg_a = _make_cfg([{"name": "برجر", "price": 40.0, "available": True}])
    cfg_b = _make_cfg([{"name": "كشري", "price": 25.0, "available": True}])
    idx_a = MenuIndex.build(cfg_a.menu_items)
    idx_b = MenuIndex.build(cfg_b.menu_items)
    extraction_a = extract_order("عايز برجر", idx_a)
    extraction_b = extract_order("عايز برجر", idx_b)
    _check("extract_a:has_item", not extraction_a.is_empty())
    _check("extract_b:no_item", extraction_b.is_empty())


def test_menu_invalidates_when_item_removed() -> None:
    items = [
        {"name": "برجر", "price": 40.0, "available": True},
        {"name": "كولا", "price": 15.0, "available": True},
    ]
    idx_v1 = MenuIndex.build(items, config_version="v1")
    items_v2 = [{"name": "برجر", "price": 40.0, "available": True}]
    idx_v2 = MenuIndex.build(items_v2, config_version="v2")
    _check("invalidation:v1_has_cola", any(e.norm_name == "كولا" for e in idx_v1.entries))
    _check("invalidation:v2_no_cola", not any(e.norm_name == "كولا" for e in idx_v2.entries))
    _check("invalidation:versions_differ", idx_v1.config_version != idx_v2.config_version)


def test_menu_invalidates_when_item_unavailable() -> None:
    items_v1 = [{"name": "برجر", "price": 40.0, "available": True}]
    items_v2 = [{"name": "برجر", "price": 40.0, "available": False}]
    idx_v1 = MenuIndex.build(items_v1)
    idx_v2 = MenuIndex.build(items_v2)
    extraction_v1 = extract_order("برجر", idx_v1)
    extraction_v2 = extract_order("برجر", idx_v2)
    _check("invalidate_avail:v1_captures", not extraction_v1.is_empty())
    _check("invalidate_avail:v2_skips_unavailable", extraction_v2.is_empty())


def test_phase2_cache_invalidates_in_live_helper() -> None:
    """Live ``_menu_index_for`` rebuilds when the cfg's menu mutates."""
    import agent

    cfg = _make_cfg([{"name": "برجر", "price": 40.0, "available": True}])
    idx_first = agent._menu_index_for(cfg)
    _check("live_cache:first_has_burger", any(e.norm_name == "برجر" for e in idx_first.entries))

    cfg.menu_items.append({"name": "كولا", "price": 15.0, "available": True})
    idx_second = agent._menu_index_for(cfg)
    _check("live_cache:second_has_cola", any(e.norm_name == "كولا" for e in idx_second.entries))
    _check("live_cache:rebuilt_on_mutation", idx_first is not idx_second)


def test_phase2_cache_distinguishes_two_configs() -> None:
    import agent

    cfg_a = _make_cfg([{"name": "برجر", "price": 40.0, "available": True}], name="مطعم أ")
    cfg_b = _make_cfg([{"name": "كشري", "price": 25.0, "available": True}], name="مطعم ب")
    idx_a = agent._menu_index_for(cfg_a)
    idx_b = agent._menu_index_for(cfg_b)
    _check("live_cache:a_has_burger", any(e.norm_name == "برجر" for e in idx_a.entries))
    _check("live_cache:b_has_koshary", any(e.norm_name == "كشري" for e in idx_b.entries))
    _check("live_cache:no_cross_a", not any(e.norm_name == "كشري" for e in idx_a.entries))
    _check("live_cache:no_cross_b", not any(e.norm_name == "برجر" for e in idx_b.entries))


# ---------------------------------------------------------------------------
# Submission tracker per-call isolation
# ---------------------------------------------------------------------------


class _StubWorkerContext:
    def __init__(self) -> None:
        self.submission_trackers: dict[str, SubmissionTracker] = {}


def test_tracker_isolated_per_call() -> None:
    worker = _StubWorkerContext()
    tracker_a = get_or_create_tracker_for_call(worker, "call_A")
    tracker_b = get_or_create_tracker_for_call(worker, "call_B")
    _check("tracker_per_call:distinct", tracker_a is not tracker_b)

    payload = {"items": ["برجر"]}
    key_a = compute_idempotency_key("call_A", "takeaway", payload)
    key_b = compute_idempotency_key("call_B", "takeaway", payload)
    _check("tracker_per_call:keys_distinct", key_a != key_b)

    tracker_a.begin("takeaway", key_a)
    tracker_a.succeed(key_a, backend_id="ord_A")

    # Call B sees no record of A's submission.
    _check("tracker_per_call:b_clean", tracker_b.latest_for_flow("takeaway") is None)
    _check("tracker_per_call:b_not_duplicate", not tracker_b.is_duplicate("takeaway", key_b))


def test_tracker_returns_same_instance_across_calls() -> None:
    worker = _StubWorkerContext()
    tracker_first = get_or_create_tracker_for_call(worker, "call_1")
    tracker_second = get_or_create_tracker_for_call(worker, "call_1")
    _check("tracker_same:same_instance", tracker_first is tracker_second)


def test_tracker_release_clears_per_call() -> None:
    worker = _StubWorkerContext()
    tracker = get_or_create_tracker_for_call(worker, "call_release")
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("call_release", "takeaway", payload)
    tracker.begin("takeaway", key)
    release_tracker_for_call(worker, "call_release")
    _check("tracker_release:bucket_empty", "call_release" not in worker.submission_trackers)
    fresh = get_or_create_tracker_for_call(worker, "call_release")
    _check("tracker_release:fresh_instance", fresh is not tracker)


def test_tracker_no_worker_context_still_works() -> None:
    """When the worker context is unavailable (text-test path), the
    helper still returns a usable tracker so calls don't crash."""
    tracker = get_or_create_tracker_for_call(None, "stray_call")
    payload = {"items": ["برجر"]}
    key = compute_idempotency_key("stray_call", "takeaway", payload)
    tracker.begin("takeaway", key)
    tracker.succeed(key, backend_id="ord_x")
    _check("tracker_no_worker:works", tracker.is_duplicate("takeaway", key))


def test_concurrent_calls_independent() -> None:
    """Simulate 50 concurrent calls and verify their trackers stay isolated."""
    worker = _StubWorkerContext()
    payload = {"items": ["برجر"]}
    keys: dict[str, str] = {}
    for i in range(50):
        call_id = f"call_{i:03d}"
        tracker = get_or_create_tracker_for_call(worker, call_id)
        key = compute_idempotency_key(call_id, "takeaway", payload)
        keys[call_id] = key
        tracker.begin("takeaway", key)
        if i % 3 == 0:
            tracker.succeed(key, backend_id=f"ord_{i}")
        else:
            tracker.fail(key, "timeout")
    # Verify no call's key collides with another's record.
    for call_id, key in keys.items():
        tracker = get_or_create_tracker_for_call(worker, call_id)
        record = tracker.by_key.get(key)
        _check(
            f"concurrent[{call_id}]:has_own_record",
            record is not None,
        )
    # And nobody can read another call's record.
    misroute = 0
    for call_id, key in keys.items():
        for other_id, other_tracker in worker.submission_trackers.items():
            if other_id == call_id:
                continue
            if other_tracker.by_key.get(key) is not None:
                misroute += 1
    _check("concurrent:no_cross_leak", misroute == 0, f"misroutes={misroute}")


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
    print(f"MULTI_TENANT_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
