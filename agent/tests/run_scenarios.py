"""Validate the manual-QA scenario JSON files.

This isn't a full agent simulator — running a real LiveKit session in CI is
expensive and flaky. Instead, this validates that every scenario:
  * parses as JSON,
  * has the required top-level fields,
  * references only known tool names and roles,
  * has a non-empty transcript with at least one user turn.

The point is to catch typos and drift between scenarios and the live tool list
*before* a tester wastes a phone call on a broken scenario.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED_TOP_LEVEL = ("name", "transcript")
ALLOWED_ROLES = {"user", "agent_expected"}
SCENARIO_DIR = Path(__file__).parent / "scenarios"


def _known_tool_names() -> set[str]:
    """Pulled from the actual flow modules so this file stays in sync."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import re
    tools: set[str] = set()
    for src in (Path(__file__).resolve().parents[1] / "flows").glob("*.py"):
        text = src.read_text(encoding="utf-8")
        for match in re.finditer(r"@function_tool\(\)\s+async def (\w+)", text):
            tools.add(match.group(1))
    base = (Path(__file__).resolve().parents[1] / "base_agent.py").read_text(encoding="utf-8")
    for match in re.finditer(r"@function_tool\(\)\s+async def (\w+)", base):
        tools.add(match.group(1))
    # Synthetic transfer tools created by _flow_to_other_handoff_tools etc.
    tools.update({"to_greeter", "to_delivery", "to_takeaway", "to_reservation", "to_complaint"})
    return tools


def _validate_scenario(path: Path, known_tools: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name}: invalid JSON ({exc})"]

    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            errors.append(f"{path.name}: missing top-level field '{key}'")

    transcript = data.get("transcript", [])
    if not isinstance(transcript, list) or not transcript:
        errors.append(f"{path.name}: transcript must be a non-empty list")
        return errors

    user_turns = 0
    for idx, item in enumerate(transcript):
        if not isinstance(item, dict):
            errors.append(f"{path.name}: transcript[{idx}] must be an object")
            continue
        role = item.get("role")
        if role not in ALLOWED_ROLES:
            errors.append(
                f"{path.name}: transcript[{idx}] role={role!r} not in {sorted(ALLOWED_ROLES)}"
            )
        if role == "user":
            user_turns += 1
            if not (item.get("text") or "").strip():
                errors.append(f"{path.name}: transcript[{idx}] user turn missing 'text'")
        if role == "agent_expected":
            tool = item.get("tool")
            if tool and tool not in known_tools:
                errors.append(
                    f"{path.name}: transcript[{idx}] references unknown tool {tool!r}"
                )
            if not tool and not item.get("behavior"):
                errors.append(
                    f"{path.name}: transcript[{idx}] needs 'tool' or 'behavior'"
                )

    if user_turns == 0:
        errors.append(f"{path.name}: scenario has no user turns")

    return errors


def main() -> int:
    if not SCENARIO_DIR.is_dir():
        print(f"FAIL: scenarios dir not found: {SCENARIO_DIR}", file=sys.stderr)
        return 2

    files = sorted(SCENARIO_DIR.glob("*.json"))
    if not files:
        print(f"FAIL: no scenario files in {SCENARIO_DIR}", file=sys.stderr)
        return 2

    known_tools = _known_tool_names()
    all_errors: list[str] = []
    for path in files:
        errs = _validate_scenario(path, known_tools)
        all_errors.extend(errs)
        print(f"{'PASS' if not errs else 'FAIL'}: {path.name}")

    if all_errors:
        print("\nDetails:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"\n{len(files)} scenario(s) validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
