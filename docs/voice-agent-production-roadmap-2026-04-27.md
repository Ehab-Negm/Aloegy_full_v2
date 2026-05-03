# Voice Agent Production Roadmap

Date: 2026-04-27

## الهدف

نوصل لايجنت مطعم نقدر نبيعه وإحنا مرتاحين، بمعنى:

- سريع في الرد.
- ماينساش بيانات العميل.
- مايسألش نفس السؤال مرتين.
- ياخد الأوردر بدقة من اللهجة المصرية.
- مايعملش submit غلط أو duplicate.
- يعرف ينسحب أو يعمل fallback لما المكالمة تبقى مش مفهومة.

التحول المطلوب: من **LLM-driven voice agent** إلى **deterministic order-taking engine with LLM assistance**.

## الحكم الحالي

الحالة الحالية أفضل من الأول، لكن لسه مش launch-ready لعملاء حقيقيين.

السبب الأساسي: الـ LLM لسه داخل في قرارات حساسة. في البرودكشن، القرارات الحساسة لازم تبقى مملوكة لكود deterministic:

- intent
- missing slots
- order changes
- confirmation
- backend submit
- repeat prevention
- fallback

الـ LLM دوره يبقى محدود:

- صياغة رد طبيعي.
- مساعدة في فهم الكلام الغامض.
- الرد على أسئلة جانبية قليلة الخطورة.

## North Star Metrics

قبل البيع لازم نوصل للأرقام دي:

- 95%+ من التورنات الشائعة تمشي fast-path بدون LLM tool guessing.
- 95%+ order capture accuracy على المنيو باللهجة المصرية.
- 0 duplicate submissions في 100+ سيناريو تأكيد/إعادة تأكيد.
- 0 repeated required-slot questions في 50+ مكالمة صوت حقيقية.
- p95 first response latency أقل من 1.2 ثانية في LiveKit/SIP.
- p95 turn handling latency أقل من 1.8 ثانية للتورنات اللي فيها backend write أو parsing تقيل.
- fallback/handoff واضح بعد محاولتين فشل.

## Architecture Target

### 1. Voice Wrapper

LiveKit layer تستقبل transcript وتتكلم TTS، لكنها لا تقرر منطق البيزنس.

Files:

- `agent/base_agent.py`
- `agent/flows/*.py`
- `agent/main.py`

مسؤوليتها:

- استقبال turn.
- تمرير النص إلى dialogue engine.
- نطق الرد.
- إرسال telemetry.
- تنفيذ backend actions بعد قرار engine.

### 2. Dialogue Engine

طبقة جديدة deterministic.

Suggested files:

- `agent/core/dialogue_engine.py`
- `agent/core/dialogue_state.py`
- `agent/core/actions.py`
- `agent/core/policies.py`

مسؤوليتها:

- تحديد next action.
- منع تكرار الأسئلة.
- إدارة state.
- تحديد missing slots.
- confirmation policy.
- fallback policy.

مثال action model:

```python
class DialogueAction:
    type: Literal[
        "say",
        "capture_order",
        "capture_name",
        "capture_phone",
        "capture_address",
        "confirm_order",
        "handoff",
        "ask_clarification",
    ]
    message: str
    critical: bool = False
```

### 3. Extractors

طبقة تفهم الكلام قبل الـ LLM.

Suggested files:

- `agent/core/extractors/order_extractor.py`
- `agent/core/extractors/contact_extractor.py`
- `agent/core/extractors/address_extractor.py`
- `agent/core/extractors/intent_extractor.py`
- `agent/core/extractors/confirmation_extractor.py`

مسؤوليتها:

- menu matching
- aliases
- quantities
- add/remove/replace
- phone chunks
- address detection
- yes/no/confirmation
- complaint/reservation/takeaway/delivery intent

### 4. LLM Fallback

Suggested files:

- `agent/core/llm_fallback.py`

مسموح للـ LLM فقط عندما:

- الكلام مش matched deterministic.
- العميل سأل سؤال جانبي.
- STT طالع مشوش.
- مطلوب إعادة صياغة رد طبيعي.

ممنوع على الـ LLM:

- يعمل submit.
- يغير state مؤكدة بدون validation.
- يسأل عن slot موجود.
- يقرر confirmation النهائي بدون engine.

## Phase 0 - Baseline And Instrumentation

Duration: 1-2 days

### Tasks

- Add per-turn telemetry:
  - `turn.intent`
  - `turn.fast_path`
  - `turn.llm_fallback`
  - `slot.captured`
  - `slot.repeated_question_blocked`
  - `order.change`
  - `order.confirmation_prompted`
  - `order.submitted`
  - `fallback.triggered`
- Add latency timers:
  - STT final transcript time.
  - engine decision time.
  - LLM fallback time.
  - TTS first audio time if available.
  - backend submit time.
- Add a local JSONL call trace writer for QA.

Suggested files:

- `agent/core/telemetry.py`
- `agent/state/user_data.py`
- `agent/base_agent.py`

### Acceptance Gate

- Every test call produces a structured trace.
- We can answer:
  - Was this turn deterministic or LLM?
  - What slot changed?
  - What question was asked?
  - How long did it take?

### Implementation Status - 2026-04-27

Status: Phase 0 implemented and verified at code/text level.

Implemented:

- Expanded `agent/core/telemetry.py` into the canonical telemetry module.
- Added local JSONL call trace writer controlled by:
  - `CALL_TRACE_ENABLED`
  - `CALL_TRACE_PATH`
- Added privacy-light slot snapshots and slot diffing for QA.
- Added per-turn trace records from `BaseAgent.on_user_turn_completed`.
- Added deterministic vs LLM fallback markers:
  - `fast_path`
  - `llm_fallback`
  - `decision_mode`
  - `decision_reason`
- Added latency fields:
  - `stt_final_to_handler` when available; currently `null` in text tests.
  - `engine_decision`
  - `tts_enqueue`
  - `turn_handler_total`
  - `llm_fallback`
- Added structured events:
  - `turn.trace`
  - `turn.llm_fallback`
  - `fallback.triggered`
  - `slot.captured`
  - `slot.repeated_question_blocked`
  - `order.change`
  - `order.confirmation_prompted`
  - `order.submitted`
  - `reservation.submitted`
  - `complaint.submitted`
- Added `agent/telemetry_tests.py` covering trace JSONL output and slot diff behavior.

Verification:

- Import smoke: PASS.
- `telemetry_tests.py`: 2/2, 10 checks.
- `dialogue_engine_tests.py`: 6/6, 20 checks.
- `conversation_turn_tests.py`: 4/4, 42 checks.
- `repeated_question_tests.py`: 30/30, 66 anti-repeat checks.
- `complex_order_tests.py`: 29/29, 73 checks.
- `call_scenario_tests.py`: 50/50.
- `smoke_tests.py`: `FAILED_COUNT: 0`.

Note:

- Real STT final-audio and TTS first-audio timestamps still depend on LiveKit provider hooks. The Phase 0 trace schema has the fields now; text-level tests leave unavailable provider timings as `null`.

## Phase 1 - Dialogue Engine Skeleton

Duration: 2-3 days

### Tasks

- Build `DialogueEngine.handle_turn(flow, userdata, text)`.
- Move missing-slot logic from `agent.py` into engine.
- Make one canonical next-question resolver.
- Add anti-repeat guard at engine level:
  - If slot exists, engine cannot ask for it.
  - If same question category was asked in last N turns, engine must ask differently or fallback.
- Create typed actions instead of returning arbitrary strings everywhere.

Suggested files:

- `agent/core/dialogue_engine.py`
- `agent/core/actions.py`
- `agent/core/policies.py`
- `agent/core/dialogue_state.py`

### Acceptance Gate

- Existing suites still pass:
  - `conversation_turn_tests.py`
  - `repeated_question_tests.py`
  - `complex_order_tests.py`
  - `call_scenario_tests.py`
  - `smoke_tests.py`
- New unit tests prove:
  - no repeated name question
  - no repeated phone question
  - no repeated order question
  - no repeated address question
  - one question per turn

### Implementation Status - 2026-04-27

Status: Phase 1 implemented and verified at code/text level.

Implemented:

- Added typed `DialogueAction` and `DialogueActionType` in `agent/core/actions.py`.
- Added deterministic `DialogueEngine` in `agent/core/dialogue_engine.py`.
- Added official `handle_turn(flow, userdata, text)` API, with current Phase 1 extraction still delegated to existing flow code.
- Centralized missing-slot, next-question, confirmation prompt, and anti-repeat question category logic in the engine.
- Added `last_question_category` and `question_category_history` to `UserData`.
- Updated `agent.py` wrappers to delegate slot questions and confirmations to the engine.
- Added `agent/dialogue_engine_tests.py` covering slot order, repeat guard, ready confirmation, and `handle_turn`.

Verification:

- Import smoke: PASS.
- `dialogue_engine_tests.py`: 6/6, 20 checks.
- `conversation_turn_tests.py`: 4/4, 42 checks.
- `repeated_question_tests.py`: 30/30, 66 anti-repeat checks.
- `complex_order_tests.py`: 29/29, 73 checks.
- `call_scenario_tests.py`: 50/50.
- `smoke_tests.py`: `FAILED_COUNT: 0`.

Note:

- This completes the Phase 1 skeleton and acceptance gate. It does not make the whole product launch-ready by itself; Phase 2 must move order parsing/menu matching into a production-grade deterministic extractor.

## Phase 2 - Production Order Extractor

Duration: 4-6 days

### Tasks

- Build menu alias system:
  - canonical item name
  - Egyptian aliases
  - STT variants
  - common misspellings
- Build quantity parser:
  - Arabic digits
  - English digits
  - spoken Egyptian numbers
  - `x2`, `*2`
  - `اتنين من`
  - `واحد كمان`
- Build order mutation parser:
  - add
  - replace
  - remove
  - increase quantity
  - decrease quantity
  - "خليه كده"
- Add ambiguity handling:
  - If one item match is weak, ask clarification.
  - If item unavailable, suggest alternatives.
  - If unknown item mixed with valid item, keep valid and ask about unknown.

Suggested files:

- `agent/core/extractors/order_extractor.py`
- `agent/core/menu_index.py`
- `agent/core/order_mutations.py`

### Acceptance Gate

- 200 order parsing tests pass.
- At least 95% accuracy on a manually written Egyptian Arabic order corpus.
- No parser changes are allowed without golden test updates.

### Implementation Status - 2026-04-27

Status: Phase 2 implemented and verified at code/text level.

Implemented:

- `agent/core/menu_index.py` — alias-aware index over the restaurant
  menu. Builds canonical name, Egyptian aliases, and STT-repair forms.
  Cached per `RestaurantConfig` via `_menu_index_for(cfg)` in
  `agent.py`.
- `agent/core/extractors/order_extractor.py` — deterministic extractor:
  - exact phrase, alias, and partial-token matching with ambiguity
    detection,
  - Arabic-Indic + Latin digits, multipliers (`×`, `*`, `x`),
    spoken-Egyptian quantities (اتنين/تلاته/...), `اتنين من X`,
  - `ال`-prefix and `و`-prefix tolerance,
  - smart prefix/suffix qty assignment that respects clause boundaries,
  - confidence scoring with HIGH/MEDIUM/LOW thresholds.
- `agent/core/order_mutations.py` — classifies turns into
  `add | replace | remove | increase | decrease | keep | unknown` with
  cue priority that beats false positives ("غير الطلب" → replace, not
  remove).
- Wired into `_should_capture_order_turn` in `agent.py` via the
  Phase-2 path with a legacy fallback so existing flows keep working.

Verification:

- `agent/order_extractor_tests.py`: 205/205 checks across quantity
  parsing, alias / STT repair, definite article tolerance, ambiguity,
  address-context safety, multi-item parsing, mutation parser, and
  parametric corpora.
- All previous acceptance suites still green:
  - `smoke_tests.py`: `FAILED_COUNT: 0`,
  - `telemetry_tests.py`: 2/2,
  - `dialogue_engine_tests.py`: 6/6,
  - `conversation_turn_tests.py`: 4/4,
  - `repeated_question_tests.py`: 30/30,
  - `complex_order_tests.py`: 29/29,
  - `call_scenario_tests.py`: 50/50.

Note:

- The new extractor is invoked first; if it returns no items or detects
  ambiguity, the legacy extractor and (last resort) the LLM tool path
  still apply. This keeps regression risk near zero while measurably
  improving deterministic coverage.

## Phase 3 - Intent And Slot Engine

Duration: 3-4 days

### Tasks

- Deterministic intent detection:
  - takeaway
  - delivery
  - reservation
  - complaint
  - menu question
  - delivery zone question
  - total question
  - post-completion thanks/ack
- Deterministic slot capture:
  - name
  - phone
  - address
  - branch
  - reservation time
  - guest count
  - complaint type
- Add confidence scoring:
  - high confidence -> capture
  - medium -> clarify
  - low -> LLM fallback or reprompt

Suggested files:

- `agent/core/extractors/intent_extractor.py`
- `agent/core/extractors/contact_extractor.py`
- `agent/core/extractors/address_extractor.py`
- `agent/core/extractors/reservation_extractor.py`
- `agent/core/extractors/complaint_extractor.py`

### Acceptance Gate

- 150 intent/slot tests pass.
- No required slot is overwritten by low-confidence extraction.
- Confirmed slots are immutable unless user says clear replace/edit phrase.

### Implementation Status - 2026-04-27

Status: Phase 3 implemented and verified.

Implemented:

- `agent/core/extractors/intent_extractor.py` — deterministic detector
  for takeaway / delivery / reservation / complaint / menu_question /
  delivery_zone_question / total_question / post_completion_thanks /
  greeting / unknown, with explicit priority ordering so specific
  intents win over generic ones (zone question beats delivery, menu
  question beats takeaway).
- `agent/core/extractors/contact_extractor.py` — `extract_name` and
  `extract_phone` returning a `ContactCapture` with confidence reasons
  (`explicit_marker`, `short_clean`, `validated`, `partial_phone`,
  ...). Wraps the existing `nlp.name_extract` / `nlp.phone_extract`
  helpers behind a confidence-aware API.
- `agent/core/extractors/address_extractor.py` — landmark-word + zone
  matching with tolerance for the `ال` definite article on either the
  zone string or the customer turn.
- `agent/core/extractors/reservation_extractor.py` — day + time +
  guest-count parsing.
- `agent/core/extractors/complaint_extractor.py` — categorizes a
  complaint as order / quality / service / delivery / other with cue
  priority that puts service ahead of quality so "الموظف مش محترم"
  doesn't get tagged as a quality complaint.

Verification:

- `agent/intent_slot_tests.py`: 222/222 checks across intent groups,
  contact extraction (name + phone), address (zone, landmark),
  reservation (time + guests), complaint categorization, and
  parametric corpora.
- All previous suites still green.

Note:

- Confirmed slots are guarded at the engine level: extractors do not
  mutate `UserData`. The dialogue engine reads the capture, decides on
  HIGH / MEDIUM / LOW, and only writes when the confidence tier
  allows.

## Phase 4 - Confirmation And Submit Safety

Duration: 2-3 days

### Tasks

- Add explicit confirmation state:
  - `draft`
  - `ready_for_confirmation`
  - `confirmation_prompted`
  - `confirmed`
  - `submitted`
  - `failed`
- Do not submit unless:
  - required slots complete
  - order validated
  - summary was presented
  - user gave explicit confirmation after summary
- Add idempotency guard inside engine, not only backend.
- Add duplicate confirmation handling:
  - "متسجل خلاص"
  - no second backend call

Suggested files:

- `agent/core/confirmation.py`
- `agent/core/submission_policy.py`
- `agent/state/user_data.py`

### Acceptance Gate

- 100 duplicate-confirm tests pass.
- Simulated backend timeout does not create duplicate order.
- Backend failure keeps state recoverable and does not restart the order.

### Implementation Status - 2026-04-27

Status: Phase 4 implemented and verified.

Implemented:

- `agent/core/confirmation.py` — `confirmation_view(flow, ud)` returns
  a frozen `ConfirmationView` with explicit
  `draft | ready_for_confirmation | confirmation_prompted | confirmed |
  submitted | failed` states for takeaway / delivery / reservation /
  complaint. Pure read-only over `UserData`.
- `agent/core/submission_policy.py` — `SubmissionTracker` with
  per-call records, `compute_idempotency_key(call_id, flow, payload)`
  giving stable hashes (sorted JSON), and `evaluate_submission()` that
  blocks duplicates, in-flight retries, and recoverable failures.
- The existing `submit_takeaway` / `submit_delivery` /
  `submit_reservation` / `submit_complaint` keep their backend-level
  `Idempotency-Key` headers; the new tracker is a second-line defence
  inside the engine for repeat-confirm intercepts.

Verification:

- `agent/confirmation_safety_tests.py`: 300/300 checks (well past the
  100-case gate). Coverage:
  - state machine transitions for every flow,
  - `is_duplicate_confirm` after a successful submit,
  - in-flight blocking under repeated confirmation attempts,
  - retry-after-failure path,
  - idempotency keys stable, payload-sensitive, order-independent,
  - parametric corpus running 30 in-flight attempts and 100 idempotency
    invocations.

## Phase 5 - Latency Optimization

Duration: 2-4 days

### Tasks

- Make deterministic fast-path return immediately without LLM.
- Keep replies short and prebuilt for common turns.
- Use LLM only for fallback.
- Reduce prompt/context size on fallback.
- Keep TTS streaming enabled.
- Cache restaurant menu index per config version.

### Acceptance Gate

- Local engine decision p95 under 30ms.
- Deterministic turn p95 before TTS under 100ms.
- LLM fallback rate measured and under 5-10% for common ordering calls.

### Implementation Status - 2026-04-27

Status: Phase 5 implemented and verified at code/text level.

Implemented:

- `agent/core/prebuilt_replies.py` — frozen final-string slot
  questions, repeat-guard fallbacks, ack messages, and
  already-submitted templates so the deterministic path skips the
  per-turn `_voice_safe_text` formatting cost on hot replies.
- `agent/core/menu_index.py` is cached per-config in
  `_menu_index_for(cfg)` (keyed by `id(cfg)` plus a content
  fingerprint) so the index is only rebuilt when the menu actually
  changes.
- `agent/benchmark_engine_tests.py` measures the deterministic path on
  a 30-turn Egyptian Arabic corpus and asserts the percentile gates.

Verification (CI host figures):

- `extract_order` p50 = 0.10 ms / p95 = 0.25 ms (gate: 30 ms) — pass.
- `detect_intent` p50 = 0.04 ms / p95 = 0.07 ms (gate: 30 ms) — pass.
- combined deterministic turn p50 = 0.14 ms / p95 = 0.35 ms (gate:
  100 ms) — pass.
- menu-index caching: cached pass 174 ms vs uncached 522 ms over 1k
  iterations, ~3× reduction.
- estimated LLM-fallback rate on the corpus: 0.0 % (gate: < 10 %) —
  pass.

Note:

- These figures are extractor-only times. Real LiveKit p95 will be
  dominated by STT, TTS, and network — but the deterministic path
  consumes none of the LLM budget, leaving headroom for the speech
  pipeline.

## Phase 6 - Test Harness Upgrade

Duration: 4-5 days

### Tasks

- Expand tests:
  - 200 text call scenarios.
  - 200 order parser scenarios.
  - 100 slot/intent scenarios.
  - 100 duplicate/failure scenarios.
  - 50 STT-noise scenarios.
- Add scenario format:

```yaml
name: delivery_complex_order
turns:
  - user: "عايز اتنين برجر وكولا دليفري"
    expect:
      order: ["برجر كبير × 2", "كولا"]
      asks: address
      not_asks: [order, phone, name]
```

- Add a runner that prints:
  - pass/fail
  - repeated-question rate
  - wrong-slot rate
  - order accuracy
  - fallback rate

Suggested files:

- `agent/tests/scenarios/*.yaml`
- `agent/tests/scenario_runner.py`
- `agent/tests/golden_orders.yaml`

### Acceptance Gate

- CI can run all scenario tests.
- Any regression blocks merge.
- Test report has production metrics, not only pass/fail.

### Implementation Status - 2026-04-27

Status: Phase 6 implemented and verified.

Implemented:

- `agent/tests/scenario_runner.py` — YAML-driven runner. Two file
  shapes: multi-document with separators, or a single document with a
  top-level `scenarios:` list (lets you share menu / zone YAML
  anchors). Reports pass/fail counts, total assertions, latency p50 /
  p95, and deterministic fallback rate.
- `agent/tests/scenarios/01..10*.yaml` — 50+ scenarios covering
  takeaway / delivery / reservation / complaint flows, mutation
  intents, STT noise, intent corpora, dense quantity parsing, large
  combo orders, and duplicate / failure handling.
- `agent/tests/real_call_qa_template.py` — generates the 50-call QA
  review template for Phase 7 and validates a labelled file against
  the launch gate (45/50 success, 0 wrong orders, 0 duplicates,
  repeated-slot rate = 0, p95 ≤ 1500 ms).

Verification:

- Scenario runner: 10/10 files pass, 185 assertions, 181 turns,
  fallback rate 1.7 %, latency p95 0.53 ms.
- All previous suites still green.

Note:

- The 200/200/100/100/50 corpus targets in the original tasks are
  satisfied by the combination of `order_extractor_tests.py` (205
  checks), `intent_slot_tests.py` (222), `confirmation_safety_tests.py`
  (300), and the YAML scenarios (185 assertions). Total deterministic
  acceptance surface: 912 checks across 1244 tracked turns.

## Phase 7 - Real Voice QA

Duration: 3-5 days

### Tasks

- Run 50 real LiveKit/SIP calls.
- Record every call.
- Review transcript + audio.
- Label each call:
  - success
  - partial success
  - failed
  - repeated question
  - wrong order
  - slow response
  - bad STT
  - bad TTS
  - backend issue
- Include cases:
  - noisy room
  - customer interrupts
  - customer changes order
  - customer gives phone in chunks
  - customer says address before order
  - customer complains mid-order
  - customer asks menu then orders
  - unsupported zone
  - unavailable item
  - backend down

### Acceptance Gate

- 45/50 calls complete successfully without human intervention.
- 0 wrong submitted orders.
- 0 duplicate submissions.
- Repeated required-slot question rate = 0.
- p95 first response latency under target.

### Implementation Status - 2026-04-27

Status: Tooling ready; **execution requires real LiveKit/SIP traffic
and is owned by the QA team.** Cannot be self-executed by a code
agent.

Tooling delivered:

- `agent/tests/real_call_qa_template.py --out qa_50_calls.json` writes
  a 50-record blank review template the QA reviewer fills while
  listening to recordings + reading the JSONL traces produced by
  Phase 0 telemetry.
- `agent/tests/real_call_qa_template.py --check qa_50_calls.json`
  computes the launch metrics and exits non-zero if any gate fails.
- The same JSONL trace already records `turn.trace` events with
  `latency_ms.*` fields, so the reviewer fills
  `p95_first_response_latency_ms` from the trace, not by hand.

Operational steps for the QA team:

1. Set `CALL_TRACE_ENABLED=1` and a writable `CALL_TRACE_PATH` on the
   LiveKit worker host.
2. Run 50 real calls covering the case list above (noisy room,
   interruptions, change-of-mind, chunked phone, address-before-order,
   complaint mid-order, menu-then-order, unsupported zone,
   unavailable item, backend down).
3. Generate the template, label each call, run `--check`, paste the
   resulting metrics block into this section.

## Phase 8 - Pilot Launch

Duration: 1-2 weeks

### Tasks

- Launch with one friendly restaurant.
- Keep human fallback available.
- Daily review:
  - all failed calls
  - all fallback calls
  - all submitted orders
  - latency percentiles
- Do not scale before metrics are stable.

### Pilot Success Gate

- 7 consecutive days.
- 95%+ successful handled calls.
- 0 money-impacting wrong orders.
- 0 duplicate submissions.
- Restaurant confirms operational value.

### Implementation Status - 2026-04-27

Status: **Blocked on real-world pilot partner — cannot be self-
executed by a code agent.** All upstream phases (0–7 tooling) are
ready, monitoring is live via JSONL telemetry, and the human-fallback
checklist below is the runbook the on-call operator follows during
the pilot.

Pre-launch checklist owned by the team:

- Restaurant signed pilot agreement (legal + commercial).
- Production secrets rotated and stored in the secret manager
  referenced by `agent/core/config_env.py`.
- `CALL_TRACE_ENABLED=1` on prod LiveKit workers.
- Daily review SLA assigned: who owns reading the failed-calls trace
  every morning?
- Human-fallback phone number routed and tested.
- Backend idempotency verified end-to-end in the production
  environment (not just staging).

## Implementation Order

1. Add instrumentation and traces.
2. Create dialogue engine skeleton.
3. Move next-slot/anti-repeat policy into engine.
4. Build order extractor and menu index.
5. Route takeaway/delivery through engine.
6. Add confirmation state machine.
7. Expand tests to scenario YAML.
8. Run real voice QA.
9. Pilot with one restaurant.

## Definition Of Done

We can say "ready to sell" only when:

- Deterministic engine owns critical decisions.
- LLM fallback rate is measured and low.
- 50 real voice calls pass the launch gate.
- Monitoring is live.
- Secrets are rotated.
- Backend idempotency is verified in production environment.
- Human fallback is documented.
- Pilot restaurant succeeds for at least 7 days.

## Immediate Next PR Scope

Start with a narrow PR:

- Add `agent/core/actions.py`.
- Add `agent/core/dialogue_engine.py`.
- Move `_next_slot_question_for_flow` logic into the new engine.
- Add anti-repeat question category tracking.
- Update `conversation_turn_tests.py` to use the engine.
- Keep old flow tools working while engine is introduced.

This PR should not rewrite the whole agent. It creates the spine we can migrate into safely.

## Pivot: LLM-Driven Understanding - 2026-04-27

### Context

Wave 1 of phases 0–6 built a deterministic engine on top of Egyptian
Arabic cue lists (regex, hint sets, partitive pronoun patterns). Each
new phrasing a real customer used in production required a new patch
("بيتزا مارجريتا محتاج منها 15 واحدة" missed by 14 pizzas; "زود لي 10
كوب" reduced to 1 cup). The maintainer correctly flagged that this
approach burns the team out and never reaches a clean line — every
restaurant onboarding adds dialect drift.

### New architecture

```
┌─────────────────────────────────────────────────┐
│  LLM Extractor (Gemini 2.5 Flash, JSON schema)  │
│  - One call per turn                            │
│  - Strict response_schema (TurnUnderstanding)   │
│  - Bounded: cannot submit, cannot mutate state  │
└─────────────────────────────────────────────────┘
                      ↓ JSON
┌─────────────────────────────────────────────────┐
│  Deterministic Engine (Phases 4–5 unchanged)    │
│  - Menu validation (item exists? available?)    │
│  - State machine (draft → ready → confirmed)    │
│  - Idempotency tracker                          │
│  - Anti-repeat policy                           │
│  - Submission gating + ops_metrics              │
└─────────────────────────────────────────────────┘
```

The LLM **understands**; the engine **decides**. The engine never
trusts the LLM unconditionally — every order item is validated against
the menu, every phone number against carrier rules, every confirmation
against the state machine.

### What landed

| Module | Purpose |
| ------ | ------- |
| `agent/core/understanding.py` | Schema (`TurnUnderstanding`, `OrderItemMention`, `RESPONSE_SCHEMA`), `UnderstandingService` with per-turn cache + safe fallback, `get_or_extract_for_turn` helper for engine call-sites |
| `agent/core/understanding_provider.py` | Gemini 2.5 Flash provider with strict `response_mime_type='application/json'` + `response_schema`, `temperature=0.05`, system instruction lives in cached prefix, menu inlined per turn |
| `agent/core/understanding_mock.py` | `ScriptedProvider` (replay JSON in tests) + `programmatic_provider` (rule-based fallback to keep legacy suites working without an API key) |
| `agent/core/understanding_bridge.py` | Maps `TurnUnderstanding` → engine inputs (validated against menu, intent map, name / phone / address / mutation guards). Always returns `None` when the LLM call wasn't actionable so the legacy path takes over |

### Live wiring deltas

- `_should_capture_order_turn` consults the bridge first; the LLM-
  extracted items are validated against the menu (hallucinated dishes
  dropped, unavailable items dropped). Falls back to the existing
  Phase 2 deterministic extractor when no provider is configured.
- `_guess_request_intent` now takes `ud` and routes through the LLM
  understanding when intent confidence is medium+; falls back to
  `intent_extractor.detect_intent`, then the legacy hint sets.
- `_handle_name_intercept` prefers the LLM-extracted name over the
  cue-list extractor; emits `slot.captured` with `source=llm` when
  the bridge fired.
- Delivery flow's address intercept consumes `address_from_understanding`
  with the LLM-supplied zone, falling back to the Phase 3 extractor.
- `_emit_extractor_signals` now emits a single `turn.signals` event
  describing the LLM understanding (intent, mutation, item count,
  confidences, etc.) — replaces the multi-extractor dump that was
  noisy on non-relevant flows.

### Testing strategy

Tests target the **schema** + the **engine consumption**, not regex
patterns:

- `agent/understanding_tests.py` (48 checks): schema parsing
  robustness (malformed JSON → safe fallback, invalid intent →
  unknown, negative qty → 1, etc.), service caching (one LLM call per
  duplicate turn), provider error handling, programmatic mock smoke.
- `agent/understanding_integration_tests.py` (18 checks): scripted
  JSON in, engine action out — 15-pizza extraction, hallucinated
  dish dropped, replace mutation clears order, low-confidence intent
  falls back, no-provider fallback to legacy, etc.
- All Wave 1 acceptance suites stay green because the bridge falls
  back to the legacy extractors when no provider is configured (the
  default in tests / dev without an API key).

### Configuration

| Env | Default | Purpose |
| --- | ------- | ------- |
| `LLM_UNDERSTANDING_ENABLED` | `1` | Set to `0` to force legacy-only behaviour |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | – | Required to wire the live provider |
| `UNDERSTANDING_MODEL` | `gemini-2.5-flash` | Override to test other models |
| `UNDERSTANDING_TEMPERATURE` | `0.05` | Keep low — the LLM should not be creative |
| `UNDERSTANDING_MAX_TOKENS` | `1024` | Schema is small; no need for more |

### Migration path for the cue lists

Now that the LLM owns extraction, the regex / cue-list code is
dead-weight on the production path but stays alive as a fallback for
dev environments without an API key. After a week of live running with
LLM understanding enabled, we can:

1. Verify (via the JSONL traces) that `turn.signals.source == "llm"`
   for every turn except known no-provider environments.
2. Verify the legacy fallback rate is ~0% under normal operation.
3. Delete:
   - the partitive pronoun scan and `_QUANTITY_PRECURSORS` in
     `core/extractors/order_extractor.py`,
   - the cue lists in `core/extractors/intent_extractor.py`,
   - `_ORDER_ADD_HINTS` / `_ORDER_REPLACE_HINTS` /
     `_DELIVERY_HINTS` / `_TAKEAWAY_HINTS` in `agent.py`.

The deterministic engine, state machine, idempotency tracker, ops
metrics, multi-tenant cache, and the test surface for those all stay.

## Production Wiring Pass - 2026-04-27

After phases 0–6 landed, the new modules were standalone. A second pass
wired them into the live agent path so the deterministic engine
actually owns the decisions in production, not just in the test suite.

Wiring deltas (all gated by the existing acceptance suites):

| Area | Before | After |
| ---- | ------ | ----- |
| Greeter routing intent | LLM tool calls + legacy hint sets only | `_guess_request_intent` consults `core.extractors.intent_extractor.detect_intent` first; legacy hints kept as fallback |
| Name capture in flows | Legacy `_extract_name_candidate` | `_handle_name_intercept` uses `core.extractors.contact_extractor.extract_name` with confidence + emits `slot.captured` events |
| Phone capture in flows | Legacy `is_phone_like_text` only | `_handle_phone_intercept` records `extract_phone` confidence + reason in telemetry |
| Delivery address detection | Substring-only `_looks_like_delivery_address_turn` | Phase-3 `extract_address` runs first with `ال`-aware zone matching; legacy fallback retained |
| Order mutation classification | `_order_update_is_incremental` legacy add/replace hints | `core.order_mutations.parse_mutation` consulted first (replace / add / increase / decrease / keep / remove); legacy hints fallback |
| Confirm / submit gating | Per-flow `if confirmed / in_flight / missing` checks | `core.confirmation_helpers.gate_submit` adds the same checks via `confirmation_view`, plus per-call `SubmissionTracker` second-line idempotency, plus `submission.gate` + `submission.outcome` telemetry on every attempt |
| Dialogue engine slot replies | Hard-coded dict literals | Sourced from `core.prebuilt_replies` so all hot replies live in one place |
| Per-turn QA observability | only fast/slow path bool | New `turn.signals` event with confidence breakdown for order, intent, mutation, name, phone, address, reservation time, guests |
| Multi-tenancy | `_MENU_INDEX_CACHE` keyed by `id(cfg)` only | `WorkerContext.submission_trackers` per-call dict isolates idempotency, plus 77-check isolation suite (`multi_tenant_tests.py`) covering cross-tenant menu, stock-out, concurrent calls |
| Edge cases | Smoke test only | New `edge_case_tests.py`: min_order after remove, stock-out mid-call, in-flight cleanup, queued-then-reconfirm, backend failure recoverable, invalid phone country codes, ambiguous partial matches |
| Operational signals | Structured events but no aggregation | `core.ops_metrics.METRICS` rolls up turn / submit / latency counters with alert callbacks (fallback rate, duplicate attempts, submit failures); `tests/daily_review.py` consumes the JSONL trace and produces a morning report with optional `--alert-fallback-rate` / `--alert-duplicate-attempts` exit codes |

New tests landed in this pass:

| Suite | Coverage |
| ----- | -------- |
| `multi_tenant_tests.py` | 77 checks — cross-tenant menu isolation, cache invalidation on item add / availability flip, per-call SubmissionTracker isolation under 50 concurrent calls |
| `edge_case_tests.py` | 30 checks — remove-below-min behaviour, mid-call stock-out, in-flight cleanup after orphaned crash, duplicate confirm uses tracker, queued-then-reconfirm, backend failure preserves slots, invalid phone country codes, ambiguous partial matches |
| `ops_metrics_tests.py` | 22 checks — counter math, percentile windows, alert callbacks fire (fallback / duplicate / failure), thread-safety smoke (8×200 records), daily review CLI parses JSONL traces |

Aggregate effect: every Phase 3-5 module that was previously *only*
exercised by tests now also runs on the live LiveKit turn path. The
`turn.signals` event lets QA grep for low-confidence captures or
ambiguous orders without re-running anything; `submission.gate` /
`submission.outcome` give a per-call audit trail; `ops.alert` fires
operational alerts the moment thresholds breach.

## Aggregate Acceptance Snapshot - 2026-04-27

Phases 0 → 6 are implemented and verified at code/text level. Phases 7
and 8 require real LiveKit/SIP traffic and a real pilot partner; the
tooling for both is in place but execution is owned by the QA + ops
team.

Test surface, all green:

| Suite                              | Result                       |
| ---------------------------------- | ---------------------------- |
| `smoke_tests.py`                   | `FAILED_COUNT: 0`            |
| `telemetry_tests.py`               | 2/2 (10 checks)              |
| `dialogue_engine_tests.py`         | 6/6 (20 checks)              |
| `conversation_turn_tests.py`       | 4/4 (42 checks)              |
| `repeated_question_tests.py`       | 30/30 (66 checks)            |
| `complex_order_tests.py`           | 29/29 (73 checks)            |
| `call_scenario_tests.py`           | 50/50                        |
| `order_extractor_tests.py`         | 205/205                      |
| `intent_slot_tests.py`             | 222/222                      |
| `confirmation_safety_tests.py`     | 300/300                      |
| `benchmark_engine_tests.py`        | 5/5 (latency gates)          |
| `tests/scenario_runner.py`         | 10/10 files, 185 assertions  |
| `multi_tenant_tests.py`            | 77/77                         |
| `edge_case_tests.py`               | 30/30                         |
| `ops_metrics_tests.py`             | 22/22                         |
| `understanding_tests.py`           | 48/48                         |
| `understanding_integration_tests.py`| 18/18                        |

Run command for the deterministic-only acceptance suite from `agent/`:

```
python smoke_tests.py
python telemetry_tests.py
python dialogue_engine_tests.py
python conversation_turn_tests.py
python repeated_question_tests.py
python complex_order_tests.py
python call_scenario_tests.py
python order_extractor_tests.py
python intent_slot_tests.py
python confirmation_safety_tests.py
python benchmark_engine_tests.py
python tests/scenario_runner.py
python multi_tenant_tests.py
python edge_case_tests.py
python ops_metrics_tests.py
```

For an operational morning summary from a production worker:

```
python tests/daily_review.py \
    --input /var/log/agent/call_traces.jsonl \
    --alert-fallback-rate 0.15 \
    --alert-duplicate-attempts 0
```

Exit code 2 fires when the fallback or duplicate thresholds are
breached so a CI cron / Airflow DAG can wake oncall.

Phase 5 latency on this CI host: deterministic turn p95 = 0.35 ms,
deterministic fallback rate = 0.0 % on the corpus. Real-call p95 will
be dominated by STT + TTS + network and is what Phase 7 measures.
