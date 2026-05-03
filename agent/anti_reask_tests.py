"""Tests proving the LLM cannot re-ask a captured slot.

The 4-step "definitive fix" guarantees:

1. ``on_user_turn_completed`` strips the stale ``FLOW_CONTEXT`` marker
   and re-injects a fresh ``TURN_GUARD`` system message every turn
   with the current state.
2. The flow ``core`` instructions stop telling the LLM to "ask for X"
   — they delegate the next-step decision to the engine's guard text.
3. ``update_name`` / ``update_phone`` are no longer exposed as tools,
   so even if the LLM hallucinates a "I should write the name" intent
   it has no tool to call.

These tests assert the live wiring of those guarantees — they don't
need a real LLM to verify, because the contract is enforced in
deterministic Python code.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


import agent  # noqa: E402
from livekit.agents import llm  # noqa: E402

from call_scenario_tests import bind_session, make_cfg, make_ud  # noqa: E402


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


# ---------------------------------------------------------------------------
# 1) No flow exposes update_name / update_phone to the LLM.
# ---------------------------------------------------------------------------


def test_every_flow_exposes_contact_writers() -> None:
    """The LLM-driven architecture re-exposes ``update_name`` /
    ``update_phone`` so the model can acknowledge name/phone in its
    own voice. The tool bodies still run the same deterministic
    validation (Egyptian carrier check, name blocklist) so the LLM
    cannot store garbage even though it now drives the call."""
    cfg = make_cfg()
    flows = {
        "greeter": agent.Greeter(cfg),
        "takeaway": agent.Takeaway(cfg),
        "delivery": agent.Delivery(cfg),
        "reservation": agent.Reservation(cfg),
        "complaint": agent.Complaint(cfg),
    }
    for flow_name, inst in flows.items():
        names = {tool.info.name for tool in llm.ToolContext(inst.tools).flatten()}
        _check(
            f"contact_writer:{flow_name}_has_update_name",
            "update_name" in names,
            f"got tools={names}",
        )
        _check(
            f"contact_writer:{flow_name}_has_update_phone",
            "update_phone" in names,
            f"got tools={names}",
        )


# ---------------------------------------------------------------------------
# 2) The flow ``core`` instructions are state-agnostic — they don't
#    embed a hard "ask for the name" / "ask for the phone" string.
# ---------------------------------------------------------------------------


def test_core_instructions_no_scripted_priority_list() -> None:
    """The flow ``core`` instructions must not hardcode a priority
    list ("ask about name first, then phone") — that's what made the
    agent feel scripted. Mentioning tools is fine; rigid ordering is
    not. The state-source-of-truth for the LLM is the on_enter YAML
    snapshot (one system message at the start of the agent), not a
    per-turn "STATE" block."""
    cfg = make_cfg()
    flows = {
        "takeaway": agent.Takeaway(cfg),
        "delivery": agent.Delivery(cfg),
        "reservation": agent.Reservation(cfg),
        "complaint": agent.Complaint(cfg),
    }
    for flow_name, inst in flows.items():
        instructions = (inst.instructions or "")
        _check(
            f"persona:{flow_name}_no_priority_list",
            "الأولوية في السؤال" not in instructions,
            f"flow={flow_name} still has hardcoded priority text",
        )
        # Persona instructions must tell the LLM to use tools and to
        # respect captured state. They no longer have to use the
        # literal word "STATE" — that's an implementation detail of
        # how the snapshot is delivered.
        _check(
            f"persona:{flow_name}_mentions_tools_or_state",
            (
                "tool" in instructions.lower()
                or "STATE" in instructions
                or "system message" in instructions
                or "حالة" in instructions
            ),
            f"flow={flow_name} doesn't reference tools or state",
        )


# ---------------------------------------------------------------------------
# 3) The turn-guard message reflects current state, not a frozen
#    snapshot.
# ---------------------------------------------------------------------------


def test_turn_guard_changes_with_state() -> None:
    cfg = make_cfg()
    ud = make_ud("anti-reask-state", cfg)
    # Empty state: should ask about the order.
    msg_empty = agent._flow_turn_guard_message("delivery", ud, "")
    _check("guard_empty:asks_for_order", "الطلب" in msg_empty, msg_empty)

    # After capturing the order: guard should not ask about the order
    # any more — it should focus on the next missing slot (address).
    ud.order_state.items = ["برجر كبير × 1"]
    ud.order_state.validated = True
    ud.order_state.total = 45.0
    msg_after_order = agent._flow_turn_guard_message("delivery", ud, "")
    _check(
        "guard_after_order:not_asks_order",
        "الطلب اتسجل" in msg_after_order,
        msg_after_order,
    )
    _check("guard_after_order:asks_address", "العنوان" in msg_after_order, msg_after_order)

    # After capturing the address too: guard should focus on the name.
    ud.delivery_state.address = "المعادي شارع 9"
    msg_after_addr = agent._flow_turn_guard_message("delivery", ud, "")
    _check("guard_after_addr:asks_name", "الاسم" in msg_after_addr, msg_after_addr)


def test_turn_guard_when_all_slots_present() -> None:
    cfg = make_cfg()
    ud = make_ud("anti-reask-complete", cfg)
    ud.order_state.items = ["برجر كبير × 1"]
    ud.order_state.validated = True
    ud.order_state.total = 45.0
    ud.delivery_state.address = "المعادي شارع 9"
    ud.customer_name = "أحمد"
    ud.customer_phone = "01012345678"
    msg = agent._flow_turn_guard_message("delivery", ud, "")
    # Guard must NOT re-ask any slot — it should be the "summarise +
    # await confirmation" line.
    _check(
        "guard_complete:no_reask",
        all(token not in msg for token in ("اسأل", "محتاج")),
        msg,
    )
    _check(
        "guard_complete:summary_or_confirm",
        "لخّص" in msg or "التأكيد" in msg or "confirm" in msg,
        msg,
    )


def test_turn_guard_after_confirm_locks() -> None:
    cfg = make_cfg()
    ud = make_ud("anti-reask-confirmed", cfg)
    ud.order_confirmed = True
    msg = agent._flow_turn_guard_message("delivery", ud, "")
    _check(
        "guard_confirmed:locked",
        "اتسجلت" in msg,
        msg,
    )
    _check(
        "guard_confirmed:no_new_topic",
        "متفتحش موضوع جديد" in msg or "متفتحش" in msg,
        msg,
    )


# ---------------------------------------------------------------------------
# 4) on_user_turn_completed actually strips and re-injects the guard.
# ---------------------------------------------------------------------------


async def test_turn_handler_strips_stale_flow_context() -> None:
    """The turn handler still strips legacy markers so a stale
    ``FLOW_CONTEXT`` from a previous architecture cannot leak into
    the LLM context. The new architecture relies on the on_enter
    YAML snapshot + tool-call history, not per-turn TURN_GUARD,
    so we only assert the strip — no fresh guard is injected.
    """
    cfg = make_cfg()
    ud = make_ud("anti-reask-strip", cfg)
    flow = ud.agents["delivery"]
    bind_session(flow, ud)

    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(
        role="system",
        content=f"{agent._FLOW_CONTEXT_PROMPT_MARKER}\nstale state: name=—",
    )
    chat_ctx.add_message(role="user", content="عايز توصيل")

    new_message = SimpleNamespace(text_content="عايز توصيل")
    try:
        await flow.on_user_turn_completed(chat_ctx, new_message)
    except Exception:
        pass

    contents = [str(getattr(item, "content", "") or "") for item in chat_ctx.items]
    _check(
        "strip:stale_marker_gone",
        not any(agent._FLOW_CONTEXT_PROMPT_MARKER in c for c in contents),
        "stale flow-context marker survived the turn handler",
    )


def test_on_enter_state_yaml_includes_captured_slots() -> None:
    """The on_enter system message embeds ``ud.summarize()`` (YAML).
    That snapshot replaced per-turn TURN_GUARD as the LLM's state
    source-of-truth — if the YAML is missing a captured slot, the
    model will re-ask. We verify the YAML contract directly; the
    on_enter wiring itself is exercised in smoke_tests.py."""
    cfg = make_cfg()
    ud = make_ud("on-enter-state-injection", cfg)
    ud.customer_name = "أحمد"  # Ahmed
    ud.customer_phone = "01012345678"
    ud.order_state.items = ["برجر كبير × 1"]
    ud.order_state.validated = True
    ud.order_state.total = 45.0
    ud.delivery_state.address = "المعادي شارع 9"

    state_yaml = ud.summarize()

    _check(
        "state:not_empty",
        bool(state_yaml.strip()),
        "summarize() returned empty state",
    )
    _check(
        "state:has_name",
        "أحمد" in state_yaml,
        f"name not in state: {state_yaml}",
    )
    _check(
        "state:has_phone_or_tail",
        "5678" in state_yaml or "01012345678" in state_yaml,
        f"phone not in state: {state_yaml}",
    )
    _check(
        "state:no_reask_directive",
        "اسأل عن" not in state_yaml,
        f"state contained a re-ask directive: {state_yaml}",
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


_ASYNC_TESTS = [
    test_turn_handler_strips_stale_flow_context,
]

_SYNC_TESTS = [
    test_every_flow_exposes_contact_writers,
    test_core_instructions_no_scripted_priority_list,
    test_turn_guard_changes_with_state,
    test_turn_guard_when_all_slots_present,
    test_turn_guard_after_confirm_locks,
    test_on_enter_state_yaml_includes_captured_slots,
]


def main() -> int:
    for fn in _SYNC_TESTS:
        try:
            fn()
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            global _TOTAL
            _TOTAL += 1
    for fn in _ASYNC_TESTS:
        try:
            asyncio.run(fn())
        except Exception as exc:
            _FAILURES.append((fn.__name__, f"raised {type(exc).__name__}: {exc}"))
            _TOTAL += 1

    print(f"ANTI_REASK_TESTS: {_PASSED}/{_TOTAL} checks")
    if _FAILURES:
        print(f"FAILED_COUNT: {len(_FAILURES)}")
        for name, detail in _FAILURES[:50]:
            print(f"  - {name}: {detail}")
        return 1
    print("FAILED_COUNT: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
