# Agent Production Readiness Audit - 2026-05-06

## Objective

Bring the restaurant voice agent to market readiness:

- handle common customer requests end to end;
- avoid asking for the same information twice in one call;
- keep user-to-first-audio end-to-end latency under 2.5s;
- sound natural enough for real customer calls;
- provide enough evidence to decide whether the system can ship.

## Prompt-To-Artifact Checklist

| Requirement | Artifact / evidence | Current status |
| --- | --- | --- |
| Handle takeaway requests | `agent/production_readiness_checks.py::check_customer_request_tool_scenarios` covers intent, order capture, name capture, submit, order id state, order clear/change, duplicate submit prevention, and backend failure fallback. | Offline verified |
| Handle delivery requests | Same check covers delivery intent, disabled-delivery fallback, order capture, address capture, name capture, minimum-order blocking, submit, duplicate submit prevention, and no phone requirement. | Offline verified |
| Handle reservation requests | Same check covers reservation time, guests, branch-required blocking, name, submit, duplicate submit prevention, and reservation id state. | Offline verified |
| Handle complaints | Same check covers complaint text/type capture, submit state, and duplicate submit prevention. | Offline verified |
| Handle explicit cancellation / done-without-submit | `RestaurantAgent.is_explicit_cancel_or_done` and `_deterministic_done_reply` intercept explicit cancel/done phrases, clear in-progress order/reservation/complaint state, speak a farewell, and close the session without backend submit. | Offline verified for order and reservation cancellation |
| Do not ask for phone twice | Current product rule is stronger: do not ask for phone at all. Persona prompt says no phone, and offline scenario asserts takeaway/delivery submit without phone. | Offline verified |
| Do not ask any required slot twice | `UserData.asked_slot_questions`, `RestaurantAgent.detect_slot_question_categories`, `record_asked_slot_questions`, `main.py` conversation listener, `[CALL_STATE] asked_once=...` injection, and `pending_corrective_response` for repeated already-captured slots. | Guardrail verified offline; live-call QA still required |
| Detect repeated slot questions | `main.py` emits `slot.repeated_question_detected`; `CallMetrics.record_repetition` records categories; repeated captured-slot asks get a deterministic non-question correction on the next turn. | Code verified; dashboard/alert wiring still required |
| Prevent duplicate submit | `confirm_and_submit` idempotency path is covered by offline duplicate-confirm scenarios for takeaway, delivery, reservation, and complaint. | Offline verified; live/API idempotency still needs broader QA |
| Track <2.5s E2E first audio | `main.py` emits `latency.e2e` with `flow`, `target_ms=2500`, and `breach` flag; logs `METRICS E2E SLO BREACH`. `qa_telemetry_gate.py` checks both overall p95 and per-required-flow p95. | Instrumented; live p95 evidence still missing |
| Capture live QA telemetry | `TELEMETRY_LOG_PATH` enables a raw JSONL file sink from `restaurant.telemetry`, suitable for the QA gate. | Implemented; still needs a real 50-call run |
| Enforce live QA pass/fail | `agent/qa_telemetry_gate.py` parses raw JSONL or regular logs containing `restaurant.telemetry` JSON. It fails cleanly when the telemetry file is missing, and fails on fewer than 50 completed `call.end` calls, missing required `call.end` flows (`takeaway`, `delivery`, `reservation`, `complaint`), missing successful outcome for any required flow, missing per-flow latency for any required flow, any repeated-slot question event, explicit latency breaches, overall p95 above 2500ms, or per-required-flow p95 above 2500ms. | Gate implemented and unit-checked offline; still needs live telemetry input |
| Preflight live QA setup | `agent/qa_live_preflight.py` checks required LiveKit/backend/provider credentials, placeholder secrets, telemetry directory writability, latency-sensitive toggles, and current telemetry progress without printing secret values. | Implemented; current local `.env` fails because `BACKEND_API_KEY` is still a placeholder |
| Monitor live QA alerts | `agent/qa_alerts.py` evaluates the telemetry file during a QA batch and fails on repeated slot questions, first-audio latency breaches/current p95 over target, backend circuit-open events, and backend queue drops; it warns on failed call outcomes and queued backend writes. | Implemented; current local run fails because no telemetry log exists yet |
| Review human-like transcript quality | `QA_TRANSCRIPT_EVENTS_ENABLED=true` enables opt-in redacted `qa.transcript` events. `agent/qa_transcript_review.py` fails on missing transcript evidence, repeated assistant messages, multiple questions in one assistant turn, and overlong assistant turns. | Implemented and offline-checked; still needs real transcript/audio review |
| Prove scenario-matrix coverage | `agent/qa_call_matrix.py` validates that a JSON/CSV call matrix maps completed telemetry call IDs to required scenarios such as cancellation, changed orders, no speech, interruptions, noisy STT, and backend failure. It also requires each matrix call to have `audio_reviewed=true` so human naturalness review is explicit. `docs/qa-call-matrix-template.csv` is the operator template. | Implemented and offline-checked; still needs a filled live matrix |
| Run one final market gate | `agent/qa_market_gate.py` combines `qa_telemetry_gate.py`, `qa_alerts.py`, `qa_transcript_review.py`, and required `qa_call_matrix.py` validation into one pass/fail release command for the collected QA batch. | Implemented and offline-checked; current local run fails because no telemetry log exists yet |
| Document QA env switches | `agent/.env.example` includes `TARGET_E2E_FIRST_AUDIO_MS`, `TELEMETRY_LOG_PATH`, `QA_TRANSCRIPT_EVENTS_ENABLED`, `CALL_METRICS_PATH`, `SESSION_PREEMPTIVE_GENERATION`, and `SESSION_TTS_STREAMING_ENABLED`. | Offline verified |
| Reject unsafe prod config | `agent.py::_validate_production_env()` fails startup when `APP_ENV=prod` uses placeholder secrets such as `BACKEND_API_KEY=mock_secret_key`. | Verified by prod-placeholder import failure |
| Fast provider warmup | `agent.py::warmup_llm` strips `cerebras/` and `groq/` prefixes before direct provider warmup calls. | Source and import verified |
| Human-like voice flow | Persona prompt enforces short Egyptian Arabic replies, one question at a time, low repetition, and graceful close. | Prompt-level only; needs call review |
| Market acceptance | Requires live QA, latency distribution, failure handling, and ops dashboards. | Not achieved |

## Commands Run

```powershell
$env:PYTHONPYCACHEPREFIX='..\.runtime\pycache'; python production_readiness_checks.py
```

Result:

```text
PASS check_slot_question_classifier
PASS check_repeated_question_tracking
PASS check_explicit_cancel_or_done_handling
PASS check_state_snapshot_includes_asked_once
PASS check_latency_slo_wiring
PASS check_env_example_has_qa_settings
PASS check_provider_warmup_prefix_stripping
PASS check_qa_telemetry_gate
PASS check_live_qa_preflight
PASS check_live_qa_alerts
PASS check_qa_transcript_review
PASS check_market_readiness_gate
PASS check_customer_request_tool_scenarios
PASS 13 production readiness checks
```

Set this before the 50-call QA batch so the agent writes raw telemetry JSONL:

```powershell
$env:TELEMETRY_LOG_PATH='.runtime\prod\telemetry.jsonl'
$env:QA_TRANSCRIPT_EVENTS_ENABLED='true'
```

Fill a live matrix from the template while running calls. Do not use the blank
template as final evidence. Set `audio_reviewed=true` only after a human has
listened to the call audio and accepted the naturalness/dialect quality:

```powershell
Copy-Item docs\qa-call-matrix-template.csv docs\qa-call-matrix-live.csv
```

Preflight command before collecting the QA batch:

```powershell
python agent\qa_live_preflight.py --telemetry agent\.runtime\prod\telemetry.jsonl --min-calls 50 --target-ms 2500
```

Current local preflight result: failed as expected because `BACKEND_API_KEY` in the local environment still looks like a placeholder, `TELEMETRY_LOG_PATH` is not set in the current shell, and no telemetry log has been collected yet.

Live alert command during the QA batch:

```powershell
python agent\qa_alerts.py --telemetry agent\.runtime\prod\telemetry.jsonl --target-ms 2500 --watch
```

Current local alert result: failed as expected because `agent\.runtime\prod\telemetry.jsonl` does not exist yet.

Transcript review command after or during the QA batch:

```powershell
python agent\qa_transcript_review.py --telemetry agent\.runtime\prod\telemetry.jsonl
```

Current local transcript review result: failed as expected because `agent\.runtime\prod\telemetry.jsonl` does not exist yet.

Live QA gate command after collecting the 50-call telemetry log:

```powershell
python agent\qa_telemetry_gate.py --telemetry agent\.runtime\prod\telemetry.jsonl --min-calls 50 --target-ms 2500 --require-flows takeaway,delivery,reservation,complaint
```

Final market gate command after collecting the QA batch:

```powershell
python agent\qa_market_gate.py --telemetry agent\.runtime\prod\telemetry.jsonl --matrix docs\qa-call-matrix-live.csv --min-calls 50 --target-ms 2500 --require-flows takeaway,delivery,reservation,complaint
```

Current local market gate result: failed as expected because `agent\.runtime\prod\telemetry.jsonl` and `docs\qa-call-matrix-live.csv` do not exist yet.

```powershell
$env:PYTHONPYCACHEPREFIX='.runtime\pycache'; python -m py_compile agent\agent.py agent\main.py agent\restaurant_agent.py agent\state\user_data.py agent\production_readiness_checks.py agent\qa_telemetry_gate.py agent\qa_live_preflight.py agent\qa_alerts.py agent\qa_transcript_review.py agent\qa_call_matrix.py agent\qa_market_gate.py
```

Result: pass.

Production placeholder guard:

```powershell
$env:APP_ENV='prod'; $env:BACKEND_API_KEY='mock_secret_key'; python -c "import agent"
```

Result: failed as expected with `FATAL: placeholder production secrets are not allowed: BACKEND_API_KEY`.

## Remaining Gates

The goal is not complete yet. The repo now has better offline evidence, but production acceptance still needs:

- 50+ completed real or recorded LiveKit/SIP calls (`call.end`) covering takeaway, delivery, reservation, and complaint, with at least one successful outcome per required flow and zero repeated required-slot questions.
- Overall and per-required-flow p95 `latency.e2e.user_to_first_audio_ms <= 2500` across realistic traffic, verified by `agent/qa_market_gate.py`.
- Filled live customer-request matrix covering no speech, interruptions, noisy STT, cancellation, changed orders, and real backend/provider failures, validated by `agent/qa_market_gate.py --matrix ...`. Offline checks now cover unavailable items, disabled delivery, minimum order, changed orders, duplicate submit prevention, branch-required reservations, and backend failure fallback.
- Dashboard integration for `latency.e2e`, `slot.repeated_question_detected`, backend queue/circuit events, and failed call outcomes. A CLI alert evaluator now exists for QA batches, but production dashboards still need deployment wiring.
- Human call review for naturalness and Egyptian dialect quality. The transcript reviewer catches obvious text issues, and the matrix gate now requires `audio_reviewed=true`, but real market acceptance still requires listening to call audio.
- Replace any deployment placeholder credentials with real production secrets before starting `APP_ENV=prod`.
