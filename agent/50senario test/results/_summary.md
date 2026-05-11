# AloEgy Pizza King — Text-Mode QA Batch

- **Pass rate**: 96.0% (48/50)

## Top 10 repeated failures
- (4×) intent inferred only via downstream tool, set_intent never called
- (3×) re-asked 'branch' after it was captured in [CALL_STATE]
- (3×) re-asked 'address' after it was captured in [CALL_STATE]
- (2×) re-asked 'name' after it was captured in [CALL_STATE]
- (2×) re-asked 'complaint' after it was captured in [CALL_STATE]
- (1×) no apology / acknowledgement on an upset/angry caller
- (1×) hard_fail: no readback summary before confirm_and_submit
- (1×) intent never set (expected complaint)
- (1×) re-asked 'order' after it was captured in [CALL_STATE]

## Worst 5 scenarios
### PK-STRESS-015 — Angry complaint (sum=28)
  - scores: `{'routing': 1, 'entity_capture': 4, 'state_memory': 5, 'clarification_quality': 4, 'tone_and_empathy': 4, 'no_hallucination': 5, 'finalization_safety': 5}`
### PK-GREEN-003 — Reservation for 4 (sum=29)
  - scores: `{'routing': 3, 'entity_capture': 5, 'state_memory': 4, 'clarification_quality': 3, 'tone_and_empathy': 4, 'no_hallucination': 5, 'finalization_safety': 5}`
### PK-GREEN-008 — Reservation tomorrow (sum=29)
  - scores: `{'routing': 3, 'entity_capture': 5, 'state_memory': 4, 'clarification_quality': 3, 'tone_and_empathy': 4, 'no_hallucination': 5, 'finalization_safety': 5}`
### PK-STRESS-033 — Delivery instruction conflict (sum=29)
  - scores: `{'routing': 3, 'entity_capture': 4, 'state_memory': 4, 'clarification_quality': 4, 'tone_and_empathy': 4, 'no_hallucination': 5, 'finalization_safety': 5}`
### PK-GREEN-001 — Delivery order - simple pepperoni pizza (sum=30)
  - scores: `{'routing': 3, 'entity_capture': 5, 'state_memory': 5, 'clarification_quality': 3, 'tone_and_empathy': 4, 'no_hallucination': 5, 'finalization_safety': 5}`

## Fixes by priority
### P0
- P0 — strengthen intent routing: enforce set_intent before any flow-specific tool, or auto-call set_intent on the first qualifying utterance.
  - scenarios: PK-STRESS-015
### P1
- P1 — finalization didn't include a clear readback before submit. Make summary-before-submit a tool-side hard check.
  - scenarios: PK-STRESS-011
### P2
- P2 — tone/empathy gap, especially on upset callers. Add explicit 'apologize first' rule for angry mood.
  - scenarios: PK-GREEN-005