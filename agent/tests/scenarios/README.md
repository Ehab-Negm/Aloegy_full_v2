# Voice-Agent QA Scenarios

Each JSON file in this directory describes one end-to-end manual-QA scenario for the
voice agent. The schema is intentionally informal — these are checklists for a human
tester, not an automated runner (yet).

## Schema

```jsonc
{
  "name": "Short title",
  "description": "What this scenario is checking.",
  "transcript": [
    {"role": "user",            "text": "What the customer says"},
    {"role": "agent_expected",  "tool": "name_of_tool",      "args": {...}},
    {"role": "agent_expected",  "behavior": "free-form expectation"}
  ],
  "forbidden_behaviors": ["what the agent must NOT do"],
  "expected_final_state": {"...UserData snapshot expectations..."}
}
```

## Running manually

1. Bring up the stack (backend + agent).
2. Place a real call (or use the LiveKit web client) and read the `transcript` lines
   into the call in order.
3. After the call ends, check the agent log for `CALL_METRICS | {...}` — verify
   `tool_calls`, `flow_transitions`, and `repetition_detected` against the scenario.
4. Compare the final UserData state (visible in `call.end` event log) against
   `expected_final_state`.

## Scenarios

- `happy_delivery.json` — golden path for a delivery order.
- `handoff_repeat_test.json` — name captured before handoff must NOT be re-asked.
- `address_change.json` — customer changes the address mid-flow.
- `repeated_confirmation.json` — `confirm_*` must run only once.
- `unknown_item.json` — item not on the menu, agent rejects gracefully.
- `multi_item_order.json` — many items at once, addition shouldn't drop anything.
- `out_of_hours.json` — reservation outside hours.
- `interrupted_order.json` — side question mid-flow, then resume.
