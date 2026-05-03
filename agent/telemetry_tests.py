import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from livekit.agents import StopResponse

from call_scenario_tests import make_cfg, make_ud
from conversation_turn_tests import FakeChatContext, FakeMessage, bind_all
from core.telemetry import changed_slots, snapshot_slots


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def assert_true(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}{': ' + detail if detail else ''}")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def run_turn(session, chat, text: str) -> None:
    try:
        await session.current_agent.on_user_turn_completed(chat, FakeMessage("user", text))
    except StopResponse:
        pass


async def test_turn_trace_jsonl_for_llm_fallback() -> int:
    """In the LLM-driven architecture every turn that doesn't hit a
    quick-intercept (menu/total/zone/post-completion/upsell) falls
    through to the LLM. The trace records the fallthrough with
    ``fast_path=False`` and ``llm_fallback=True``. Slot changes
    happen *after* the LLM calls a tool — they appear in the trace
    only when the model invokes ``update_*`` during the turn, which
    requires a real LLM. Here we just verify the trace plumbing
    fires and contains the right call metadata.
    """
    cfg = make_cfg()
    ud = make_ud("telemetry-fast-path", cfg)

    with tempfile.TemporaryDirectory() as tmpdir:
        trace_path = Path(tmpdir) / "call_traces.jsonl"
        os.environ["CALL_TRACE_PATH"] = str(trace_path)
        os.environ["CALL_TRACE_ENABLED"] = "true"

        session, _said = bind_all(ud, "takeaway")
        chat = FakeChatContext()
        await run_turn(session, chat, "عايز ٢ برجر كبير")

        records = read_jsonl(trace_path)
        traces = [record for record in records if record.get("event") == "turn.trace"]
        assert_true("trace written", len(traces) == 1, str(records))
        trace = traces[0]
        assert_true("trace call id", trace["call_id"] == "scenario-telemetry-fast-path", str(trace))
        assert_true("trace flow", trace["flow"] == "takeaway", str(trace))
        assert_true("trace latency fields", "engine_decision" in trace["latency_ms"], str(trace["latency_ms"]))
        assert_true("trace decision_mode set", trace.get("decision_mode") in {"llm_fallback", "deterministic"}, str(trace))
    return 4


def test_slot_snapshot_diff() -> int:
    cfg = make_cfg()
    ud = make_ud("telemetry-slot-diff", cfg)
    before = snapshot_slots(ud)
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    ud.order = ["برجر كبير"]
    after = snapshot_slots(ud)
    diff = changed_slots(before, after)

    assert_true("name diff", "customer_name_present" in diff, str(diff))
    assert_true("phone diff", "customer_phone_present" in diff, str(diff))
    assert_true("order diff", "order_items" in diff, str(diff))
    assert_true("phone redacted", after["customer_phone_tail"] == "5678", str(after))
    return 4


async def async_main() -> int:
    failures: list[str] = []
    checks = 0
    async_tests = [test_turn_trace_jsonl_for_llm_fallback]
    sync_tests = [test_slot_snapshot_diff]

    index = 1
    for test in async_tests:
        try:
            count = await test()
            checks += count
            print(f"{index:02d}. {test.__name__}: PASS ({count} checks)")
        except Exception as exc:
            failures.append(f"{test.__name__}: {exc}")
            print(f"{index:02d}. {test.__name__}: FAIL - {exc}")
        index += 1

    for test in sync_tests:
        try:
            count = test()
            checks += count
            print(f"{index:02d}. {test.__name__}: PASS ({count} checks)")
        except Exception as exc:
            failures.append(f"{test.__name__}: {exc}")
            print(f"{index:02d}. {test.__name__}: FAIL - {exc}")
        index += 1

    total = len(async_tests) + len(sync_tests)
    print(f"TELEMETRY_TESTS_PASSED: {total - len(failures)}/{total}")
    print(f"TELEMETRY_CHECKS: {checks}")
    if failures:
        print("FAILED_TELEMETRY_TESTS:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
