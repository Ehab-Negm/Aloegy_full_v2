# Ultra Production Review - Voice Agent

Date: 2026-04-27

Related execution roadmap: `docs/voice-agent-production-roadmap-2026-04-27.md`

## Executive verdict

النتيجة الحالية بمعيار برودكشن بيتعامل مع ناس حقيقية وفلوس: **لسه مش production-ready**.

الكود اتحسن وعدى اختبارات كتير، لكن ده يخليه **internal pilot candidate** مش launch-ready. عشان يبقى ينفع ياخد أوردرات من عملاء حقيقيين لازم نثبت جودة الصوت، سرعة الرد، دقة التقاط الأوردر، وعدم تكرار الأسئلة على مكالمات LiveKit/SIP حقيقية.

السبب: عملت فحص كود وتشغيل اختبارات محلية واسعة، وبنيت سويت 50 سيناريو مكالمة عربي مصري على مستوى state/tools، وكلها عدت. لكن لم يتم تنفيذ 20-50 مكالمة صوت فعلية عبر STT/TTS/LiveKit من هذه البيئة، لأنها لا تملك عميل صوت/هاتف/SIP فعلي.

## What was tested

- Conversation turn suite: `python conversation_turn_tests.py`
  - Result: PASS, `CONVERSATION_TURN_TESTS_PASSED: 4/4`
  - Coverage: 42 checks through `on_user_turn_completed` with full Egyptian Arabic user turns, fake session speech capture, real deterministic intercepts, and anti-repeat assertions after order/address/name/phone capture
- Anti-repeat text-call suite: `python repeated_question_tests.py`
  - Result: PASS, `TEXT_CALLS_PASSED: 30/30`
  - Coverage: 66 direct checks that the agent does not ask again for phone/order/name/address when that field is already present
- Complex order suite: `python complex_order_tests.py`
  - Result: PASS, `COMPLEX_ORDER_TESTS_PASSED: 29/29`
  - Coverage: 73 checks for quantities, Arabic digits, add/replace/remove, unknown/unavailable items, minimum delivery order, duplicate confirms, payload totals, and contact-repeat regressions
- Agent smoke suite: `python smoke_tests.py`
  - Result: PASS, `FAILED_COUNT: 0`
- Egyptian Arabic simulated call suite: `python call_scenario_tests.py`
  - Result: PASS, `SCENARIOS_PASSED: 50/50`
- Backend isolated smoke: `python backend/smoke_test.py --base-url http://127.0.0.1:8011 --api-key mock_secret_key`
  - Result: PASS, all backend smoke checks passed
  - Ran against isolated SQLite/runtime config, not production DB
- Frontend tests: `npm test`
  - Result: PASS, 1/1 test file passed
- Frontend production build: `npm run build`
  - Result: PASS
  - Warnings remain: stale Browserslist data and one large dashboard chunk

## 50 Egyptian Arabic simulated calls covered

The new suite is in `agent/call_scenario_tests.py`.

Coverage includes:

- Delivery happy path, address, zone, landmark, name, phone, confirm
- Delivery add vs replace order logic
- Delivery upsell accept/reject/ambiguous reply
- Unsupported delivery zone rejection
- Duplicate delivery confirmation idempotency
- Takeaway happy path, add, replace, special requests, no-menu fallback
- Duplicate takeaway confirmation idempotency
- Greeter routing to delivery/takeaway/reservation/complaint/menu
- Greeter prefill of name and phone before handoff
- Reservation time validation, guest limits, branch validation, confirm, duplicate confirm
- Complaint pending contact, type normalization, short complaint rejection, submit, duplicate skip
- Spoken Egyptian phone capture and chunked phone capture
- Guard cases: order quantities not phone numbers, protest not captured as name
- Handoff summary memory
- Post-completion response
- Backend failure, queued write, and write-unavailable behavior

## Confirmed issues found and fixed

1. Frontend/backend order stream mismatch
   - Problem: frontend and backend smoke were using access token in `token` query param, while backend expects a one-time stream ticket.
   - Fix:
     - `frontend/entameen-main/src/services/api.ts` now requests `/auth/stream-ticket` and opens SSE with `ticket`.
     - `backend/smoke_test.py` now tests the same production path.

2. Greeter prefill could store bad name
   - Problem: phrase like `انا اسمي احمد ورقمي...` could store `اسمي احمد`.
   - Fix:
     - `agent/flows/greeter.py` now prioritizes inline intro extraction and ignores name-intro tokens.

3. Name extraction accepted protest as a name
   - Problem: `انا قلتلك قبل كده` could be extracted as a customer name if the tool was wrongly called.
   - Fix:
     - `agent/nlp/name_extract.py` blocks common protest phrases.

4. Flaky upsell prompt smoke assertion
   - Problem: one randomized special-request prompt did not contain the expected test wording.
   - Fix:
     - `_NEXT_SPECIAL` prompts now consistently refer to `طلب خاص` or `ملاحظة`.

5. Earlier agent memory/tool issues addressed in this pass
   - Incremental add preserves existing order and dedupes duplicate additions.
   - Replace wording still replaces the order.
   - Handoff briefing includes customer/order facts and tells the next agent not to ask for already-known info.
   - Post-completion generic replies stop deterministically instead of falling back to the LLM.
   - Model/runtime defaults were lowered for production stability.

6. Repeated question regressions fixed
   - Problem: after phone was collected early, some flow replies could ask for the order again instead of the actual missing field.
   - Problem: after name was collected while phone already existed, follow-up could ask for the phone again.
   - Problem: delivery address, landmark, reservation notes, and special-request tools had hard-coded next questions that ignored already-known contact/order facts.
   - Fix: follow-ups now use one shared `next missing slot` resolver for takeaway, delivery, and reservation.
   - Verification: `repeated_question_tests.py` runs 30 text calls and 66 anti-repeat checks.

7. Test methodology gap fixed
   - Problem: previous suites were too tool/state-level and could miss real turn behavior.
   - Problem: `last_user_message` was not updated inside the real turn handler, while incremental add/replace logic depends on it.
   - Fix:
     - `BaseAgent.on_user_turn_completed` now saves the real user turn into `ud.last_user_message`.
     - Takeaway and delivery now have deterministic order capture for obvious menu-item turns.
     - Delivery now has deterministic address capture for obvious address turns.
     - Order extraction now handles Egyptian conjunction tokens like `وكولا` / `وبطاطس` and avoids assigning a previous item's quantity to the next item.
   - Verification: `conversation_turn_tests.py` sends full Egyptian Arabic turns through `on_user_turn_completed` and passed 4/4 transcripts with 42 checks.

## Remaining production blockers

0. Reduce LLM decision ownership
   - Current risk: the LLM still owns too much natural conversation and tool choice outside the deterministic fast paths.
   - Production target: state machine owns flow transitions, required slots, validation, confirmation, retries, and backend writes.
   - LLM should only handle wording, fuzzy extraction when deterministic parsers fail, and low-risk side questions.

1. Run real voice-call QA
   - Required before launch: 20-50 real calls through the deployed LiveKit/SIP path.
   - Must include noisy audio, interruptions, silence, repeated confirmations, long calls, wrong addresses, complaint escalation, and backend write failure.

2. Rotate secrets
   - Real secrets are present in local `.env` files. Treat them as exposed and rotate before production.

3. Deployment environment verification
   - Confirm production env uses the intended LLM model/temperature/history values.
   - Confirm backend DB, storage, API key, CORS origins, LiveKit keys, and STT/TTS providers are production-specific.

4. Observability gate
   - Keep telemetry for `flow.transfer`, `order.confirmed`, `upsell.*`, `phone.capture`, backend queue/circuit events.
   - Add alerting on backend write failures, duplicate submission attempts, fallback/degraded config, and long calls.

5. Frontend build optimization
   - Build passes, but `OwnerDashboardView` chunk is over 500 kB.
   - Browserslist data is stale by 10 months.

## Recommended launch gate

Do not launch as final production until these pass:

- 50/50 simulated scenarios remain green.
- Agent smoke remains green.
- Backend isolated smoke remains green.
- Frontend build/test remain green.
- 20-50 real Arabic Egyptian LiveKit/SIP calls pass with recordings reviewed.
- Secrets rotated and production env verified.

Current status: **not production-ready for real customers yet**.

## Production-grade target

لازم الأرقام دي تتحقق قبل أي launch حقيقي:

- 95%+ من التورنات الشائعة تتحل deterministic fast-path بدون LLM tool guessing.
- p95 first response latency أقل من 1.2s في الصوت الحقيقي.
- 0 duplicate order submissions في 100+ سيناريو تأكيد/إعادة تأكيد.
- 0 repeated required-slot questions بعد ما المعلومة تتسجل في 50+ مكالمة.
- 95%+ order capture accuracy على المنيو باللهجة المصرية.
- human handoff/fallback واضح للحالات اللي مش مفهومة بعد محاولتين.
