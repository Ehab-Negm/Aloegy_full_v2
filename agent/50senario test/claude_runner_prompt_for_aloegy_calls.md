# Claude Runner Prompt — AloEgy Pizza King Call QA

You are testing the AloEgy voice agent end-to-end using a JSONL scenario file.

## How to run each scenario
1. Read one JSON object from the JSONL file.
2. Start a fresh call/session.
3. Speak/send the caller turns exactly as written in `dialogue`.
4. Do not correct the agent or help it unless the caller script says so.
5. After the call ends, evaluate the agent.

## Scoring
Score each category from 0 to 5:
- routing
- entity_capture
- state_memory
- clarification_quality
- tone_and_empathy
- no_hallucination
- finalization_safety

## Required Output Per Call
Return a JSON report:

```json
{
  "scenario_id": "",
  "title": "",
  "category": "",
  "passed": true,
  "scores": {
    "routing": 0,
    "entity_capture": 0,
    "state_memory": 0,
    "clarification_quality": 0,
    "tone_and_empathy": 0,
    "no_hallucination": 0,
    "finalization_safety": 0
  },
  "critical_failures": [],
  "agent_mistakes": [],
  "missing_fields": [],
  "hallucinations": [],
  "final_state_captured_by_agent": {},
  "recommended_fix": ""
}
```

## Hard Fail Conditions
Fail immediately if:
- Agent submits order/reservation/complaint with missing required fields.
- Agent invents price, offer, availability, delivery zone, opening hours, or tracking info.
- Agent ignores the latest customer correction.
- Agent cannot recover from wrong routing.
- Agent does not summarize before final submit.
- Agent responds rudely to angry or confused callers.

Run all 50 scenarios and then produce:
1. Overall pass rate.
2. Top 10 repeated failures.
3. Worst 5 scenarios.
4. Suggested production fixes by priority: P0 / P1 / P2.
