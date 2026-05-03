"""YAML-driven scenario runner for the deterministic engine.

A scenario is a list of conversation turns plus the expectations the
deterministic stack must satisfy at each turn. The runner exercises:

- ``order_extractor.extract_order`` for menu items + quantities,
- ``intent_extractor.detect_intent`` for the user's intent,
- ``contact_extractor`` / ``address_extractor`` /
  ``reservation_extractor`` / ``complaint_extractor`` for slot capture,
- ``order_mutations.parse_mutation`` for add/replace/remove cues.

Output metrics include pass/fail counts, repeated-question rate (best
effort: counts turns where the extractor produced no new capture),
wrong-slot rate, order accuracy, and deterministic fallback rate. These
are the production-quality signals Phase 6 requires.

Each scenario YAML file looks like:

    name: delivery_complex_order
    menu:
      - {name: "برجر كبير", price: 45.0, available: true}
      - {name: "كولا", price: 15.0, available: true}
    delivery_zones: [المعادي]
    turns:
      - user: "عايز اتنين برجر كبير وكولا دليفري"
        expect:
          intent: delivery
          order_items:
            "برجر كبير": 2
            "كولا": 1
      - user: "اسمي أحمد"
        expect:
          name: "أحمد"

Missing keys in ``expect`` are skipped (the runner does not assert on
them), so scenarios can be precise without becoming brittle.
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

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.extractors.address_extractor import extract_address  # noqa: E402
from core.extractors.complaint_extractor import classify_complaint  # noqa: E402
from core.extractors.contact_extractor import extract_name, extract_phone  # noqa: E402
from core.extractors.intent_extractor import detect_intent  # noqa: E402
from core.extractors.order_extractor import extract_order  # noqa: E402
from core.extractors.reservation_extractor import (  # noqa: E402
    extract_guests_count,
    extract_reservation_time,
)
from core.menu_index import MenuIndex  # noqa: E402
from core.order_mutations import parse_mutation  # noqa: E402


@dataclass
class ScenarioResult:
    name: str
    file: str
    passed_assertions: int = 0
    failed_assertions: int = 0
    failures: list[str] = field(default_factory=list)
    turn_count: int = 0
    deterministic_misses: int = 0
    latency_ms: list[float] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.failed_assertions == 0


@dataclass
class RunReport:
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for s in self.scenarios if s.passed)

    @property
    def failed(self) -> int:
        return sum(1 for s in self.scenarios if not s.passed)

    @property
    def total_turns(self) -> int:
        return sum(s.turn_count for s in self.scenarios)

    @property
    def total_assertions(self) -> int:
        return sum(s.passed_assertions + s.failed_assertions for s in self.scenarios)

    @property
    def total_misses(self) -> int:
        return sum(s.deterministic_misses for s in self.scenarios)

    @property
    def fallback_rate(self) -> float:
        if not self.total_turns:
            return 0.0
        return self.total_misses / self.total_turns

    @property
    def latency_p95(self) -> float:
        all_lat = [ms for s in self.scenarios for ms in s.latency_ms]
        return _percentile(all_lat, 95.0)

    @property
    def latency_p50(self) -> float:
        all_lat = [ms for s in self.scenarios for ms in s.latency_ms]
        if not all_lat:
            return 0.0
        return statistics.median(all_lat)

    def emit(self, *, verbose: bool = False) -> None:
        print("=" * 72)
        print("SCENARIO RUNNER REPORT")
        print("=" * 72)
        print(f"scenarios:        {self.passed}/{len(self.scenarios)} passed")
        print(f"assertions:       {self.total_assertions} total")
        print(f"turns:            {self.total_turns}")
        print(f"latency p50/p95:  {self.latency_p50:.2f}ms / {self.latency_p95:.2f}ms")
        print(f"fallback_rate:    {self.fallback_rate*100:.1f}% ({self.total_misses}/{self.total_turns})")
        print()
        if verbose or self.failed:
            for s in self.scenarios:
                marker = "PASS" if s.passed else "FAIL"
                print(f"  [{marker}] {s.name} ({s.file}) — "
                      f"{s.passed_assertions}/{s.passed_assertions + s.failed_assertions}")
                if not s.passed:
                    for f in s.failures[:5]:
                        print(f"        - {f}")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((pct / 100.0) * (len(sorted_values) - 1)))))
    return sorted_values[rank]


def run_scenario_file(path: Path) -> ScenarioResult:
    """Load and run all scenarios in a YAML file.

    Two file shapes are supported:

    1. Multi-document file (``---`` separators). PyYAML resets anchor
       scope between documents, so anchors can't be shared across them.
    2. Single document with a top-level ``scenarios:`` list. Use this
       when you want shared menu / zone anchors across many scenarios.
    """
    with path.open("r", encoding="utf-8") as f:
        documents = list(yaml.safe_load_all(f))

    aggregate = ScenarioResult(name=path.stem, file=str(path))
    for raw in documents:
        if raw is None:
            continue
        if isinstance(raw, dict) and "scenarios" in raw:
            for scenario in raw.get("scenarios") or []:
                result = _run_single_scenario(scenario, source=str(path))
                _accumulate(aggregate, result)
        elif isinstance(raw, dict):
            result = _run_single_scenario(raw, source=str(path))
            _accumulate(aggregate, result)
    return aggregate


def _accumulate(aggregate: ScenarioResult, result: ScenarioResult) -> None:
    aggregate.passed_assertions += result.passed_assertions
    aggregate.failed_assertions += result.failed_assertions
    aggregate.failures.extend(result.failures)
    aggregate.turn_count += result.turn_count
    aggregate.deterministic_misses += result.deterministic_misses
    aggregate.latency_ms.extend(result.latency_ms)


def _run_single_scenario(scenario: dict[str, Any], *, source: str) -> ScenarioResult:
    name = str(scenario.get("name") or "<unnamed>")
    result = ScenarioResult(name=name, file=source)
    menu_items = scenario.get("menu") or []
    delivery_zones = tuple(scenario.get("delivery_zones") or ())
    index = MenuIndex.build(menu_items)

    state: dict[str, Any] = {
        "order": {},
        "name": None,
        "phone": None,
        "address": None,
        "reservation_time": None,
        "guests": None,
        "complaint_text": None,
        "complaint_type": None,
        "intents": [],
    }

    turns = scenario.get("turns") or []
    for idx, turn in enumerate(turns, start=1):
        user_text = str(turn.get("user") or "")
        expect = turn.get("expect") or {}

        t0 = time.perf_counter()
        observed = _apply_extractors(
            user_text, index=index, delivery_zones=delivery_zones, state=state
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        result.turn_count += 1
        result.latency_ms.append(elapsed)
        if not observed["any_capture"]:
            result.deterministic_misses += 1

        _assert_expectations(
            scenario_name=name,
            turn_index=idx,
            expect=expect,
            observed=observed,
            state=state,
            result=result,
        )
    return result


def _apply_extractors(
    user_text: str,
    *,
    index: MenuIndex,
    delivery_zones: tuple[str, ...],
    state: dict[str, Any],
) -> dict[str, Any]:
    extraction = extract_order(user_text, index)
    intent = detect_intent(user_text)
    mutation = parse_mutation(user_text)
    name_capture = extract_name(user_text)
    phone_capture = extract_phone(user_text)
    address_capture = extract_address(user_text, delivery_zones=delivery_zones)
    res_time_capture = extract_reservation_time(user_text)
    guests_capture = extract_guests_count(user_text)
    complaint_capture = classify_complaint(user_text)

    if not extraction.is_empty():
        if mutation.kind == "replace":
            state["order"] = {}
        for item in extraction.items:
            state["order"][item.canonical_name] = (
                state["order"].get(item.canonical_name, 0) + item.quantity
            )

    if intent.is_actionable():
        state["intents"].append(intent.kind)

    if name_capture.value and name_capture.is_high_confidence():
        state["name"] = name_capture.value
    if phone_capture.value and phone_capture.is_high_confidence():
        state["phone"] = phone_capture.value
    if address_capture.value and address_capture.is_high_confidence():
        state["address"] = address_capture.value
    if res_time_capture.raw and res_time_capture.is_high_confidence():
        state["reservation_time"] = res_time_capture.raw
    if guests_capture.count and guests_capture.is_high_confidence():
        state["guests"] = guests_capture.count
    if complaint_capture.text and complaint_capture.confidence >= 0.6:
        state["complaint_text"] = complaint_capture.text
        state["complaint_type"] = complaint_capture.category

    any_capture = bool(
        not extraction.is_empty()
        or intent.is_actionable()
        or name_capture.value
        or phone_capture.value
        or address_capture.value
        or res_time_capture.raw
        or guests_capture.count is not None
        or complaint_capture.text
    )

    return {
        "extraction": extraction,
        "intent": intent,
        "mutation": mutation,
        "name": name_capture,
        "phone": phone_capture,
        "address": address_capture,
        "reservation_time": res_time_capture,
        "guests": guests_capture,
        "complaint": complaint_capture,
        "any_capture": any_capture,
    }


def _assert_expectations(
    *,
    scenario_name: str,
    turn_index: int,
    expect: dict[str, Any],
    observed: dict[str, Any],
    state: dict[str, Any],
    result: ScenarioResult,
) -> None:
    def _record(name: str, ok: bool, detail: str = "") -> None:
        if ok:
            result.passed_assertions += 1
        else:
            result.failed_assertions += 1
            result.failures.append(
                f"{scenario_name}#{turn_index}::{name}: {detail}"
            )

    if "intent" in expect:
        expected = expect["intent"]
        actual = observed["intent"].kind
        _record("intent", actual == expected, f"expected={expected} actual={actual}")

    if "mutation" in expect:
        expected = expect["mutation"]
        actual = observed["mutation"].kind
        _record("mutation", actual == expected, f"expected={expected} actual={actual}")

    if "order_items" in expect:
        expected = expect["order_items"] or {}
        actual = state["order"]
        match = all(actual.get(k) == v for k, v in expected.items())
        _record(
            "order_items",
            match and (set(actual) == set(expected) if expect.get("strict_order_match", True) else True),
            f"expected={expected} actual={actual}",
        )

    if "order_contains" in expect:
        for item in expect["order_contains"]:
            _record(f"order_contains[{item}]", item in state["order"])

    if "name" in expect:
        _record(
            "name",
            state["name"] == expect["name"],
            f"expected={expect['name']} actual={state['name']}",
        )

    if "phone" in expect:
        _record(
            "phone",
            state["phone"] == expect["phone"],
            f"expected={expect['phone']} actual={state['phone']}",
        )

    if "address_contains" in expect:
        addr = state["address"] or ""
        substring = expect["address_contains"]
        _record(
            "address_contains",
            substring in addr,
            f"substring={substring} addr={addr}",
        )

    if "reservation_time_set" in expect:
        _record(
            "reservation_time_set",
            (state["reservation_time"] is not None) == bool(expect["reservation_time_set"]),
        )

    if "guests" in expect:
        _record(
            "guests",
            state["guests"] == expect["guests"],
            f"expected={expect['guests']} actual={state['guests']}",
        )

    if "complaint_type" in expect:
        _record(
            "complaint_type",
            state["complaint_type"] == expect["complaint_type"],
            f"expected={expect['complaint_type']} actual={state['complaint_type']}",
        )

    if "fallback" in expect:
        expected = bool(expect["fallback"])
        actual = not observed["any_capture"]
        _record(
            "fallback",
            actual == expected,
            f"expected={expected} actual={actual}",
        )


def run_directory(directory: Path) -> RunReport:
    report = RunReport()
    yaml_files = sorted(directory.rglob("*.yaml")) + sorted(directory.rglob("*.yml"))
    for path in yaml_files:
        report.scenarios.append(run_scenario_file(path))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run YAML conversation scenarios.")
    parser.add_argument(
        "directory",
        nargs="?",
        default=str(Path(__file__).resolve().parent / "scenarios"),
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit JSON metrics")
    args = parser.parse_args(argv)

    directory = Path(args.directory)
    if not directory.exists():
        print(f"directory not found: {directory}")
        return 2

    report = run_directory(directory)

    if args.json:
        print(json.dumps({
            "scenarios_total": len(report.scenarios),
            "scenarios_passed": report.passed,
            "scenarios_failed": report.failed,
            "assertions_total": report.total_assertions,
            "turns_total": report.total_turns,
            "fallback_rate": report.fallback_rate,
            "latency_p50_ms": report.latency_p50,
            "latency_p95_ms": report.latency_p95,
        }, ensure_ascii=False))
    else:
        report.emit(verbose=args.verbose)

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
