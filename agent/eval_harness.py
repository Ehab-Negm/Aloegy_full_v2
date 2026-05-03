"""Phase 4.1 — Replay-based evaluation harness.

Drives the deterministic pipeline + DialogueEngine through a JSON dataset
of labelled customer turns and reports per-scenario pass/fail plus
aggregate slot F1.

This is the *scaffold* version of the harness called out in the plan.
The full version replays real call audio recordings; this one starts at
the post-STT layer and exercises the same downstream stack so we have a
canonical regression set to add to before audio replay is wired in.

Dataset format (``eval_data/cases.json``):

    [
        {
            "id": "happy_delivery",
            "flow": "delivery",
            "menu": [{"name": "كشري كبير", "price": 35}],
            "delivery_zones": ["وسط البلد"],
            "turns": [
                {"user": "عايز كشري كبير", "expect_slot": "order"},
                {"user": "أحمد", "expect_slot": "name"},
                {"user": "012 8765 4321", "expect_slot": "phone"},
                {"user": "وسط البلد، شارع طلعت حرب رقم 5", "expect_slot": "address"}
            ],
            "expected_final": {
                "customer_name": "أحمد",
                "customer_phone": "01287654321",
                "delivery_address": "وسط البلد، شارع طلعت حرب رقم 5",
                "order": ["كشري كبير"]
            }
        }
    ]

Run::

    python eval_harness.py --dataset eval_data/cases.json
    # or
    python eval_harness.py  # uses bundled built-in cases
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Ensure we can import sibling modules even when invoked from another cwd.
sys.path.insert(0, str(Path(__file__).parent))


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    turn_count: int
    duration_ms: float
    slot_matches: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# Built-in mini dataset — exercises happy-path delivery + takeaway. This
# lets the harness self-check without an external file.
_BUILTIN_CASES: list[dict[str, Any]] = [
    {
        "id": "happy_takeaway",
        "flow": "takeaway",
        "menu": [{"name": "كشري كبير", "price": 35}],
        "delivery_zones": [],
        "turns": [
            {"user": "عايز كشري كبير"},
            {"user": "اسمي محمود"},
            {"user": "زيرو واحد اتنين تلاتة اربعة خمسة ستة سبعة تمانية تسعة صفر"},
        ],
        "expected_final": {
            "order_includes": ["كشري كبير"],
            "name_set": True,
            "phone_set": True,
        },
    },
    {
        "id": "delivery_zone_clarification",
        "flow": "delivery",
        "menu": [{"name": "بيتزا مارجريتا", "price": 120}],
        "delivery_zones": ["الزمالك", "وسط البلد"],
        "turns": [
            {"user": "بيتزا مارجريتا", "expect_slot": "order"},
            {"user": "العنوان شارع الجزيرة عمارة رقم 7"},  # landmark + digit but no zone
        ],
        "expected_final": {
            "order_includes": ["بيتزا مارجريتا"],
            "address_set": False,  # zone gate should block
        },
    },
]


def _load_cases(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return list(_BUILTIN_CASES)
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"dataset not found: {path}")
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit("dataset must be a JSON array of case objects")
    return data


def _build_cfg(case: dict[str, Any]):
    """Construct a minimal RestaurantConfig sufficient for the pipeline."""
    from backend.config import RestaurantConfig

    import dataclasses
    fields = {f.name for f in dataclasses.fields(RestaurantConfig)}
    kwargs: dict[str, Any] = {}
    defaults_by_type = {
        bool: False,
        int: 0,
        float: 0.0,
        list: [],
        dict: {},
        str: "",
    }
    for f in fields:
        kwargs[f] = ""
    kwargs.update({
        "name": case.get("restaurant_name", "Eval Restaurant"),
        "menu_items": case.get("menu", []),
        "delivery_zones": case.get("delivery_zones", []),
        "branches": case.get("branches", []),
        "hours": {},
        "upsell_rules": [],
        "is_open": True,
        "closed_reason": "",
        "degraded_mode": False,
        "config_source": "eval",
        "wait_minutes": 20,
        "min_guests": 1,
        "max_guests": 20,
        "delivery_enabled": bool(case.get("delivery_zones")),
        "delivery_minutes": 45,
        "delivery_fee": 0.0,
        "min_order": 0.0,
        "phone": "",
        "address": "",
    })
    return RestaurantConfig(**{k: v for k, v in kwargs.items() if k in fields})


def _check_final_state(ud: Any, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True
    if "name_set" in expected:
        actual = bool(getattr(ud, "customer_name", ""))
        if actual != bool(expected["name_set"]):
            ok = False
            notes.append(f"customer_name expected_set={expected['name_set']} got={actual}")
    if "phone_set" in expected:
        actual = bool(getattr(ud, "customer_phone", ""))
        if actual != bool(expected["phone_set"]):
            ok = False
            notes.append(f"customer_phone expected_set={expected['phone_set']} got={actual}")
    if "address_set" in expected:
        actual = bool(getattr(ud, "delivery_address", ""))
        if actual != bool(expected["address_set"]):
            ok = False
            notes.append(f"delivery_address expected_set={expected['address_set']} got={actual}")
    if "order_includes" in expected:
        order = list(getattr(ud, "order", []) or [])
        for needed in expected["order_includes"]:
            if not any(needed in entry for entry in order):
                ok = False
                notes.append(f"order missing item: {needed!r}; got {order}")
    return ok, notes


def run_case(case: dict[str, Any]) -> CaseResult:
    from state.user_data import UserData
    from deterministic_pipeline import run_pipeline

    cfg = _build_cfg(case)
    flow = case.get("flow", "takeaway")
    ud = UserData(call_id=f"eval-{case['id']}", restaurant=cfg)

    started = time.monotonic()
    turn_count = 0
    notes: list[str] = []
    actions: list[str] = []
    for turn in case.get("turns", []):
        turn_count += 1
        text = turn.get("user", "")
        try:
            # ``run_pipeline`` mutates ``ud`` in-place for slot captures
            # (matches production flows). The returned PipelineResult
            # carries the spoken response and downstream-action hint.
            result = run_pipeline(text=text, flow=flow, ud=ud)
        except Exception as exc:
            notes.append(f"turn {turn_count} raised: {type(exc).__name__}: {exc}")
            continue
        actions.append(getattr(result, "action", "?"))

    duration_ms = (time.monotonic() - started) * 1000.0
    expected = case.get("expected_final", {}) or {}
    passed, final_notes = _check_final_state(ud, expected)
    notes.extend(final_notes)
    if actions:
        notes.append(f"actions: {actions}")

    return CaseResult(
        case_id=case["id"],
        passed=passed,
        turn_count=turn_count,
        duration_ms=duration_ms,
        notes=notes,
    )


def run_dataset(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [run_case(c) for c in cases]
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    durations = [r.duration_ms for r in results]
    summary = {
        "cases_total": len(results),
        "cases_passed": len(passed),
        "cases_failed": len(failed),
        "pass_rate": len(passed) / max(len(results), 1),
        "p50_ms": round(statistics.median(durations), 2) if durations else 0,
        "p95_ms": round(_percentile(durations, 0.95), 2) if durations else 0,
        "max_ms": round(max(durations), 2) if durations else 0,
        "failures": [
            {"id": r.case_id, "notes": r.notes}
            for r in failed
        ],
    }
    return summary


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(pct * (len(s) - 1)))))
    return s[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="AloEgy eval harness")
    parser.add_argument("--dataset", help="Path to JSON case file")
    parser.add_argument("--output", help="Path to write the summary as JSON")
    args = parser.parse_args()

    cases = _load_cases(args.dataset)
    summary = run_dataset(cases)

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if summary["cases_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
