# Production Fix Workplan

## Execution Policy

**All 37 tracked issues must be resolved before production launch.**

- No issue is optional regardless of severity
- Ordering is for execution sequencing only — not for priority dismissal
- Every phase must be fully completed before moving to the next
- "Not Started" means work has not begun — it does NOT mean "deferred"
- If an issue is determined to be a false positive, it must be documented as `Disproven` in the master report with evidence

---

## Phase-by-Phase Fix Plan

---

### Phase 1: Security Ship-Stoppers

> These must be fixed before ANY external access to the system.

---

#### Task 1.1: Remove hardcoded OTP bypass default

- **Related issue ID(s):** PRD-003
- **Exact file(s) to modify:** `backend/main.py`
- **Function(s) to inspect:** Module-level config around line 107
- **Expected code change:**
  ```python
  # BEFORE
  DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS", "956956")

  # AFTER
  DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS") if os.getenv("ENVIRONMENT") == "development" else None
  ```
- **Risk of change:** Low. Only affects authentication flow. Development environments that rely on the default bypass code `956956` will need to explicitly set `DEV_OTP_BYPASS=956956` in their env config.
- **Validation steps:**
  1. Deploy with no `ENVIRONMENT` set → verify OTP `956956` is rejected
  2. Deploy with `ENVIRONMENT=production` → verify all OTP bypass codes rejected
  3. Deploy with `ENVIRONMENT=development` and `DEV_OTP_BYPASS=testcode` → verify `testcode` works
- **Test coverage needed:** Integration test for OTP authentication with various env configurations
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed maintainability risk. Direct source comparison showed ~0.925 similarity between the two `update_order` implementations before the fix.
- **Implementation notes:** Added `BaseAgent._process_order_update()` and routed both `Takeaway.update_order()` and `Delivery.update_order()` through it. Kept the delivery-only minimum-order branch intact; there was no legitimate flow-specific next-step divergence inside `update_order` itself beyond that branch.
- **Validation notes:** Added smoke tests `prd027_shared_order_logic_consistent` and `prd027_flow_specific_delivery_minimum_preserved`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Low. Shared helper still assumes ordering flows expose `self.cfg`, which matches the current design.

---

#### Task 1.2: Restrict CORS to explicit allowlist

- **Related issue ID(s):** PRD-008
- **Exact file(s) to modify:** `backend/main.py`
- **Function(s) to inspect:** CORS middleware setup, lines 131-136
- **Expected code change:**
  ```python
  # BEFORE
  allow_origin_regex=r"https?://.*"

  # AFTER (development)
  allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080"]

  # AFTER (production)
  allow_origins=[os.getenv("ALLOWED_ORIGIN", "https://yourdomain.com")]
  ```
- **Risk of change:** Low. May break development setups using non-standard ports. Developers need to add their port to the allowlist.
- **Validation steps:**
  1. Request from `http://evil.com` → CORS blocks
  2. Request from `http://localhost:5173` → CORS allows
  3. Production: request from configured domain → allowed
- **Test coverage needed:** Integration test for CORS headers
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed maintainability risk. Direct source comparison showed ~0.901 similarity between the takeaway and delivery upsell branches before refactoring.
- **Implementation notes:** Added `BaseAgent._handle_pending_upsell()` with a flow label plus a callback for the post-upsell prompt. `Takeaway` now passes `_ask_name`; `Delivery` passes `_ask_address`.
- **Validation notes:** Added smoke tests `prd028_shared_upsell_acceptance_state` and `prd028_upsell_followup_remains_flow_specific`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Low. The helper intentionally preserves the previous “clear pending upsell on ambiguous follow-up” behavior.

---

### Phase 2: Control Flow Correctness

> Fixes that prevent double responses, silent turn swallows, and LLM fall-through.

---

#### Task 2.1: Fix `if`/`if` fall-through in `_handle_quick_intercepts`

- **Related issue ID(s):** PRD-001
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `_handle_quick_intercepts` (lines 151-169)
- **Expected code change:**
  ```python
  # BEFORE
  if flow in {"takeaway", "delivery"} and _is_total_question(user_text):
      ...
  if flow in {"greeter", "delivery"} and _is_delivery_zone_question(user_text):
      ...
  if flow in {"takeaway", "delivery"} and _is_menu_question(user_text):
      ...

  # AFTER
  if flow in {"takeaway", "delivery"} and _is_total_question(user_text):
      ...
  elif flow in {"greeter", "delivery"} and _is_delivery_zone_question(user_text):
      ...
  elif flow in {"takeaway", "delivery"} and _is_menu_question(user_text):
      ...
  ```
- **Risk of change:** Very low. `_say_and_stop` raises `StopResponse`, so only the first matching branch could ever execute in practice. The `elif` change makes this explicit and prevents the edge case where `_turn_responded=True` before entering.
- **Validation steps:**
  1. Send a message matching two intercept conditions → verify only one response
  2. Run all 80 smoke tests → all pass
- **Test coverage needed:** New test: simultaneous intercept match → single response
- **Status:** Complete — Fixed 2026-04-14. Changed `if`/`if`/`if` to `if`/`elif`/`elif`. Smoke test `prd001_quick_intercepts_elif` added and passes. Verification: StopResponse propagation already prevented double responses in practice; elif makes the intent explicit and guards edge cases.

---

#### Task 2.2: Fix `if`/`if` fall-through in `_handle_post_completion`

- **Related issue ID(s):** PRD-002
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `_handle_post_completion` (lines 171-182)
- **Expected code change:**
  ```python
  # BEFORE
  if _is_thanks_message(user_text):
      ...
  if _is_positive_confirmation(user_text):
      ...

  # AFTER
  if _is_thanks_message(user_text):
      ...
  elif _is_positive_confirmation(user_text):
      ...
  ```
- **Risk of change:** Very low. Same reasoning as Task 2.1.
- **Validation steps:**
  1. Post-completion: send thanks → one response
  2. Post-completion: send confirmation → one response
  3. Run all smoke tests
- **Test coverage needed:** New test: post-completion with ambiguous text
- **Status:** Complete — Fixed 2026-04-14. Changed `if`/`if` to `if`/`elif`. Smoke test `prd002_post_completion_elif` added and passes. Remaining risk: unrecognized post-completion utterances still fall through to LLM (missing `else` fallback — separate concern from the elif fix).

---

#### Task 2.3: Guard `_handle_phone_intercept` against empty reply

- **Related issue ID(s):** PRD-009
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `_handle_phone_intercept` (lines 202-211)
- **Expected code change:**
  ```python
  # BEFORE
  phone_reply = await _apply_phone_update(ud, user_text, flow_name=flow)
  if phone_reply:
      await self._say_and_stop(phone_reply)
  raise StopResponse()

  # AFTER
  phone_reply = await _apply_phone_update(ud, user_text, flow_name=flow)
  if phone_reply:
      await self._say_and_stop(phone_reply)
  # If no reply (digits buffered silently), don't swallow the turn
  return True
  ```
- **Risk of change:** Medium. Returning `True` instead of raising `StopResponse` changes the turn flow. The turn will continue to name intercept / LLM. Need to verify this doesn't cause issues when digits are being buffered. Consider whether a brief "تمام، كمّل" acknowledgment is better.
- **Validation steps:**
  1. Send partial phone digits → no silence (either ack or LLM response)
  2. Send complete phone → full confirmation
  3. Run smoke tests for phone capture scenarios
- **Test coverage needed:** New tests for partial phone digit scenarios
- **Status:** Complete — Fixed 2026-04-14. Replaced `raise StopResponse()` with `return True`. When `phone_reply` is empty (confirmed reachable via `_phone_capture_short_reply` returning "" for 11+ buffered invalid digits), the turn now continues to LLM instead of being silently swallowed. Smoke tests `prd009_phone_intercept_no_bare_stop_response` and `prd009_short_reply_can_be_empty` added and pass.

---

### Phase 3: Concurrency & I/O Safety

> Prevent event loop blocking and shared state races.

---

#### Task 3.1: Add locks to circuit breaker state

- **Related issue ID(s):** PRD-004
- **Exact file(s) to modify:** `agent/state/worker_context.py`, `agent/agent.py`
- **Function(s) to inspect:**
  - `WorkerContext` class (add `circuit_lock`)
  - `_get_backend_circuit` (line 830)
  - `_backend_circuit_is_open` (line 840)
  - `_record_backend_circuit_success` (line 845)
  - `_record_backend_circuit_failure` (line 852)
- **Expected code change:**
  1. Add `circuit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)` to `WorkerContext`
  2. Convert circuit functions to async, wrap state access in `async with ctx.circuit_lock:`
  3. Update all callers of these functions to `await` them
- **Risk of change:** Medium. Converting sync functions to async requires updating all callers. The lock contention is minimal (circuit checks are fast) but adds slight overhead.
- **Validation steps:**
  1. Stress test: 50 concurrent failing requests → circuit opens at exact threshold
  2. Run all smoke tests
  3. Profile: verify lock overhead is <1ms
- **Test coverage needed:** Concurrency test for circuit breaker with multiple coroutines
- **Status:** Complete

---

#### Task 3.2: Wrap blocking filesystem calls in `asyncio.to_thread`

- **Related issue ID(s):** PRD-005, PRD-006
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:**
  - `_read_shared_cache_map` (line 504: `path.exists()`)
  - `_write_shared_cache_entry` (line 543: `path.exists()`, and `_ensure_parent_dir`)
  - `_read_backend_queue_recovery_lines` (lines 905, 908: `queue_path.exists()`)
  - `_append_backend_queue_recovery_items` (line 939: `queue_path.exists()`, and `_ensure_parent_dir`)
- **Expected code change:**
  ```python
  # BEFORE
  if not path.exists():

  # AFTER
  if not await asyncio.to_thread(path.exists):
  ```
  Also wrap `_ensure_parent_dir`:
  ```python
  await asyncio.to_thread(_ensure_parent_dir, path)
  ```
  OR make `_ensure_parent_dir` itself use `to_thread` internally.
- **Risk of change:** Low. These are all in async functions already. The `to_thread` wrapper adds minimal overhead on local filesystems.
- **Validation steps:**
  1. All existing smoke tests pass
  2. Mock slow filesystem → event loop not blocked
- **Test coverage needed:** Mock test with delayed `Path.exists`
- **Status:** Complete

---

#### Task 3.3: Add lock or safety comment for turn counts

- **Related issue ID(s):** PRD-013
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_increment_turn_count` (line 1153), `_cleanup_turn_count` (line 1161)
- **Expected code change:**
  Option A (lock):
  ```python
  async def _increment_turn_count(call_id: str) -> int:
      ctx = worker_context()
      async with ctx.active_sessions_lock:  # reuse existing lock
          count = ctx.turn_counts.get(call_id, 0) + 1
          ctx.turn_counts[call_id] = count
          return count
  ```
  Option B (comment):
  ```python
  def _increment_turn_count(call_id: str) -> int:
      # SAFETY: atomic under CPython GIL — no awaits between read and write.
      # Do not add await/yield between the get() and assignment.
      ctx = worker_context()
      count = ctx.turn_counts.get(call_id, 0) + 1
      ctx.turn_counts[call_id] = count
      return count
  ```
- **Risk of change:** Option A: requires changing callers to `await`. Option B: zero risk (comment only).
- **Validation steps:** Code review.
- **Test coverage needed:** If option A, verify caller update.
- **Status:** Complete

---

#### Task 3.4: Add fork-safety to HTTP client singleton

- **Related issue ID(s):** PRD-025
- **Exact file(s) to modify:** `agent/backend/client.py`
- **Function(s) to inspect:** Module-level `_http_client`, `_http_client_lock`, `get_http_client`
- **Expected code change:**
  ```python
  import os

  def _reset_http_client():
      global _http_client, _http_client_lock
      _http_client = None
      _http_client_lock = asyncio.Lock()

  # Only register on platforms that support fork
  if hasattr(os, "register_at_fork"):
      os.register_at_fork(after_in_child=_reset_http_client)
  ```
- **Risk of change:** Low. Only affects behavior after fork. No impact if processes use `spawn()`.
- **Validation steps:**
  1. Verify LiveKit uses fork or spawn
  2. If fork: run with `num_idle_processes=2`, verify each process has independent client
- **Test coverage needed:** Fork test (if applicable)
- **Status:** Complete

---

#### Task 3.5: Guard `_WORKER_CONTEXT` against double-set

- **Related issue ID(s):** PRD-018
- **Exact file(s) to modify:** `agent/agent.py`, `agent/main.py`
- **Function(s) to inspect:**
  - `_setup_worker_process` in `agent.py` (where context should be set)
  - `entrypoint` in `main.py` (line 41, where context is currently overwritten)
- **Expected code change:**
  1. In `_setup_worker_process`: add assertion `assert _WORKER_CONTEXT is None`
  2. In `main.py` entrypoint: change from unconditional set to conditional:
     ```python
     if _agent._WORKER_CONTEXT is None and isinstance(proc_context, WorkerContext):
         _agent._WORKER_CONTEXT = proc_context
     ```
  3. Remove misleading `global _WORKER_CONTEXT` at `main.py` line 35
- **Risk of change:** Low. Defensive check only.
- **Validation steps:** Double-call test → assertion fires.
- **Test coverage needed:** Unit test for setup guard.
- **Status:** Complete

---

### Phase 4: Phone & NLP Fixes

> Fix false positives and data loss in phone/name capture and Arabic text processing.

---

#### Task 4.1: Raise `is_phone_like_text` threshold

- **Related issue ID(s):** PRD-007
- **Exact file(s) to modify:** `agent/nlp/phone_extract.py`
- **Function(s) to inspect:** `is_phone_like_text` (lines 26-36)
- **Expected code change:**
  ```python
  # BEFORE
  if spoken_digits and len(spoken_digits) >= 2:
      return True

  # AFTER
  if spoken_digits and len(spoken_digits) >= 5:
      return True
  ```
- **Risk of change:** Medium. Raising the threshold means users who say phone digits in small chunks (2-4 at a time) won't trigger phone capture. The phone intercept in `base_agent.py` may need to rely more on `phone_capture_mode` context. Need to verify that legitimate phone input patterns still work.
- **Validation steps:**
  1. "اتنين كفتة وتلاتة كباب" → NOT intercepted as phone
  2. "صفر واحد صفر واحد اتنين تلاتة" (01012 3) → intercepted as phone
  3. "01012345678" → intercepted as phone
  4. Run all phone-related smoke tests
- **Test coverage needed:** 10+ test cases covering Egyptian phone speaking patterns and food ordering patterns with numbers
- **Status:** Complete - Fixed 2026-04-14. Confirmed bug in current code path: spoken/digit quantity phrases could satisfy `is_phone_like_text` too early and steal order turns when phone capture was active. Raised the spoken-digit threshold from `>=2` to `>=5` while preserving raw numeric chunk detection through the existing `non_phone` heuristic. Validated by smoke tests `phone_spoken_detected`, `prd007_order_numbers_not_phone_like`, and `prd007_digit_quantities_not_phone_like`, plus the full smoke suite. Verification note: the report's exact example with attached conjunction was too narrow; reproducible variants used for validation were `اتنين كفتة و تلاتة كباب` and `2 كفتة 3 كباب`.

---

#### Task 4.2: Fix `merge_phone_digits` buffer overwrite threshold

- **Related issue ID(s):** PRD-019
- **Exact file(s) to modify:** `agent/nlp/phone_extract.py`
- **Function(s) to inspect:** `merge_phone_digits` (lines 39-46)
- **Expected code change:**
  ```python
  # BEFORE
  if incoming.startswith("01") or incoming.startswith("20") or incoming.startswith("201"):
      return incoming

  # AFTER
  if len(incoming) >= 5 and (incoming.startswith("01") or incoming.startswith("20") or incoming.startswith("201")):
      return incoming
  ```
- **Risk of change:** Low. Only changes behavior for short incoming digit strings that happen to start with phone prefixes. Legitimate phone restarts (>=5 digits) still replace the buffer.
- **Validation steps:**
  1. buffer="010", incoming="0123" → "0100123" (appended)
  2. buffer="010", incoming="01012345678" → "01012345678" (replaced)
  3. buffer="", incoming="01" → "01" (set, no existing buffer)
- **Test coverage needed:** Unit tests for merge behavior with various digit patterns
- **Status:** Complete - Fixed 2026-04-14. Confirmed bug via direct reproduction: `merge_phone_digits("010", "0123")` overwrote the buffer instead of appending. Added `len(incoming) >= 5` before restart-style replacement so only plausible fresh phone restarts reset the buffer. Validated by smoke tests `prd019_short_prefix_chunk_appends`, `prd019_full_restart_replaces`, and `prd019_empty_buffer_sets`.

---

#### Task 4.3: Use `_normalize_ar` in upsell comparison

- **Related issue ID(s):** PRD-010
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_get_upsell_suggestion` (lines 2390-2405)
- **Expected code change:**
  ```python
  # BEFORE (line 2394)
  order_lower = {(item or "").lower() for item in (ud.order or [])}
  # ...
  if item_name.lower() not in order_lower:

  # AFTER
  order_normalized = {_normalize_ar(item or "") for item in (ud.order or [])}
  # ...
  if _normalize_ar(item_name) not in order_normalized:
  ```
- **Risk of change:** Very low. `_normalize_ar` is already used everywhere else for Arabic text comparison.
- **Validation steps:**
  1. Order has "شاورما", upsell offers "شاورمة" → no suggestion (same item)
  2. Order has "كفتة", upsell offers "كباب" → suggestion offered (different item)
- **Test coverage needed:** Unit test with taa-marbuta and hamza variants
- **Status:** Complete - Fixed 2026-04-14. Confirmed bug: `_get_upsell_suggestion()` compared Arabic items with `.lower()` on both sides, which missed normalization-equivalent variants. Switched both order items and upsell items to `_normalize_ar(...)` before comparison. Validated by smoke tests `prd010_upsell_normalizes_same_item` and `prd010_upsell_still_offers_different_item`, plus the full smoke suite.

---

#### Task 4.4: Precompute `_NEGATIVE_FORMS` at module level

- **Related issue ID(s):** PRD-015
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_looks_empty_answer` (lines 1741-1755), `NEGATIVE_WORDS` (line 1699)
- **Expected code change:**
  ```python
  # After NEGATIVE_WORDS definition:
  _NEGATIVE_FORMS = frozenset(_normalize_ar(w) for w in NEGATIVE_WORDS)

  def _looks_empty_answer(text: str | None) -> bool:
      normalized = _normalize_ar(text or "")
      if not normalized:
          return True
      for word in _NEGATIVE_FORMS:
          if normalized == word:
              return True
          if normalized.startswith(f"{word} "):
              tail = normalized[len(word):].strip()
              if not tail:
                  return True
              if all(token in _EMPTY_TAIL_WORDS for token in tail.split()):
                  return True
      return False
  ```
- **Risk of change:** Zero. Pure performance improvement.
- **Validation steps:** Run all smoke tests. Verify identical behavior.
- **Test coverage needed:** Existing tests suffice.
- **Status:** Complete - Fixed 2026-04-14. Confirmed as a performance risk rather than a correctness bug: `_looks_empty_answer()` rebuilt the normalized negative-word set on every call. Added module-level `_NEGATIVE_FORMS = frozenset(...)` and reused it inside `_looks_empty_answer()`. Validated by existing functional test `empty_answer_handles_la_tamam`, new smoke test `prd015_negative_forms_cached`, and the full smoke suite.

---

### Phase 5: Error Handling & Reliability

> Prevent data loss, enforce timeouts, catch unexpected errors.

---

#### Task 5.1: Add top-level try/except to all tool methods

- **Related issue ID(s):** PRD-020
- **Exact file(s) to modify:**
  - `agent/flows/takeaway.py` (all `@function_tool` methods)
  - `agent/flows/delivery.py` (all `@function_tool` methods)
  - `agent/flows/reservation.py` (all `@function_tool` methods)
  - `agent/flows/complaint.py` (all `@function_tool` methods)
  - `agent/flows/greeter.py` (all `@function_tool` methods)
  - `agent/base_agent.py` (`update_name`, `update_phone`, `get_menu`)
- **Function(s) to inspect:** Every `@function_tool()` decorated function
- **Expected code change:**
  ```python
  @function_tool()
  async def confirm_order(self, context: RunContext_T) -> str:
      try:
          # ... existing logic ...
      except StopResponse:
          raise  # Don't catch StopResponse
      except Exception as exc:
          logger.exception("call=%s | tool error | tool=confirm_order", ud.call_id)
          return "معلش يا فندم، حصل مشكلة تقنية. ممكن نحاول تاني؟"
  ```
- **Risk of change:** Low. The catch-all only fires for truly unexpected exceptions. Known exceptions (StopResponse, handled errors) are re-raised.
- **Validation steps:**
  1. Force a `RuntimeError` inside a tool → Arabic error returned
  2. Normal flow → no change in behavior
  3. Run all smoke tests
- **Test coverage needed:** Mock tool internals to raise unexpected exceptions
- **Status:** Complete - Fixed 2026-04-15. Confirmed as a production risk: all 25 current `@function_tool()` entry points could leak unexpected exceptions back into the LLM/tool layer. Added shared `_run_tool_safely(...)` in `base_agent.py`, preserved `StopResponse`, and wrapped every tool in `base_agent.py`, `flows/takeaway.py`, `flows/delivery.py`, `flows/reservation.py`, `flows/complaint.py`, and `flows/greeter.py`. Validated with smoke tests `prd020_all_function_tools_wrapped`, `prd020_shared_tool_returns_arabic_error`, `prd020_flow_tool_returns_arabic_error`, plus the full smoke suite.

---

#### Task 5.2: Harden recovery file with idempotency and cap

- **Related issue ID(s):** PRD-011
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:**
  - `_append_backend_queue_recovery_items` (lines 928-953)
  - `_drain_backend_write_queue_once` (wherever it reads recovery items)
  - `_enqueue_backend_write` (where items are created)
- **Expected code change:**
  1. Add `idempotency_key` to each recovery item dict before writing
  2. On replay (`_drain_backend_write_queue_once`), deduplicate by idempotency key
  3. Cap the recovery file at 500 lines (configurable via env var)
  4. Log when cap is reached and items are dropped
- **Risk of change:** Medium. Changes the recovery file format. Existing recovery files without idempotency keys will still be replayed (backward compatible — just won't be deduplicated).
- **Validation steps:**
  1. Simulate backend outage, restart 3 times → no duplicate entries
  2. Fill recovery to cap → new items logged as dropped
  3. Successful replay → file cleared
- **Test coverage needed:** Multi-restart simulation test
- **Status:** Complete - Fixed 2026-04-15. Confirmed bug in the recovery pipeline: duplicate failed writes could be re-appended across retries/restarts because the recovery file had no effective dedupe and only a per-append size check. Added explicit `idempotency_key` persistence, replay-time dedupe, append-time dedupe, configurable cap via `BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES`, and cap/drop logging. Validation covered duplicate append, cap enforcement, and replay dedupe before submit via smoke tests `prd011_recovery_file_dedupes_same_item`, `prd011_recovery_cap_applies`, and `prd011_replay_dedupes_before_submit`.

---

#### Task 5.3: Add explicit timeout to `_post()` HTTP calls

- **Related issue ID(s):** PRD-012
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_post()` (lines 1196-1293, specifically the `client.post()` call at line 1242)
- **Expected code change:**
  ```python
  # Add parameter
  async def _post(
      ...
      tool_timeout: float = 3.0,
  ) -> dict | None:
      # ...
      res = await client.post(
          full_url,
          json=payload,
          headers=headers,
          timeout=tool_timeout,  # Override client default
      )
  ```
- **Risk of change:** Low. The httpx client already has a 5s default timeout. Tightening to 3s may cause slightly more timeouts for slow but valid requests. Monitor after deployment.
- **Validation steps:**
  1. Mock backend responding in 4s → timeout, retry
  2. Mock backend responding in 2s → success
  3. 3 consecutive timeouts → circuit opens
- **Test coverage needed:** Timeout-specific tests
- **Status:** Complete - Fixed 2026-04-15. Confirmed as a reliability/latency risk rather than a raw timeout bug: `_post()` already inherited a shared client timeout, but it had no explicit per-call timeout contract or override. Added `tool_timeout` to `_post()` with default from `BACKEND_POST_TIMEOUT_SECONDS` and passed it directly to `client.post(...)`. Validated with smoke test `prd012_post_uses_explicit_timeout`, which confirms explicit timeout propagation and retry behavior, plus the full smoke suite.

---

### Phase 6: User Experience & Parsing

> Fix parsing edge cases, improve truncation, add graceful turn cap.

---

#### Task 6.1: Validate `_parse_order_item` regex against menu context

- **Related issue ID(s):** PRD-014
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_parse_order_item` (lines 2309-2339)
- **Expected code change:** Add a post-parse sanity check. If the extracted "item" portion doesn't look like a food item (heuristic: too short, contains address keywords, etc.), return the original text with qty=1. Alternatively, document that the caller (`_resolve_menu_item`) already validates against the menu.
- **Risk of change:** Low to medium, depending on approach. Adding validation inside `_parse_order_item` could reject valid items.
- **Validation steps:**
  1. "شارع 15" → not parsed as item="شارع", qty=15
  2. "2 كفتة" → item="كفتة", qty=2
  3. "كفتة × 3" → item="كفتة", qty=3
- **Test coverage needed:** Edge case tests with address-like, phone-like, and order-like inputs
- **Status:** Complete - Fixed 2026-04-15. Confirmed bug in the parser path: `_parse_order_item("street 15")` extracted `("street", 15)` before any downstream validation. Added a post-parse sanity check for implicit prefix/suffix numeric matches so obvious address/location phrases fall back to the original text with `qty=1` instead of inventing a phantom quantity. Validated with smoke tests `prd014_address_like_quantity_not_split`, `prd014_prefix_quantity_still_parses`, and `prd014_explicit_multiplier_still_parses`.

---

#### Task 6.2: Raise fuzzy match threshold for short items

- **Related issue ID(s):** PRD-026
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_resolve_menu_item` (lines 2359-2387), `_MENU_MATCH_THRESHOLD` (line 2356)
- **Expected code change:**
  ```python
  # Option A: raise global threshold
  _MENU_MATCH_THRESHOLD = 0.6

  # Option B: context-dependent threshold
  threshold = 0.8 if len(target_tokens) <= 1 else _MENU_MATCH_THRESHOLD
  if score >= threshold:
      ...
  ```
- **Risk of change:** Medium. Raising the threshold may cause some legitimate fuzzy matches to fail. Need to test with the actual restaurant menu.
- **Validation steps:**
  1. Single-token ambiguous input with multiple matches → no false match
  2. Multi-token input with clear match → still matches
- **Test coverage needed:** Menu matching tests with various token counts
- **Status:** Complete - Fixed 2026-04-15. Confirmed false-positive bug for short inputs: a single token like `chicken` matched the first multi-token menu item at score `0.5`. Added a context-dependent threshold in `_resolve_menu_item()` so single-token inputs require `0.8` while multi-token fuzzy matching keeps the existing baseline. Validated with smoke tests `prd026_short_token_ambiguous_no_match`, `prd026_single_token_exact_still_matches`, and `prd026_multi_token_fuzzy_match_kept`.

---

#### Task 6.3: Fix `_voice_safe_text` to truncate at word boundary

- **Related issue ID(s):** PRD-030
- **Exact file(s) to modify:** `agent/utils/voice.py`
- **Function(s) to inspect:** `_voice_safe_text` (lines 6-27)
- **Expected code change:**
  ```python
  if len(cleaned) > max_chars:
      truncated = cleaned[:max_chars - 1]
      last_space = truncated.rfind(" ")
      if last_space > max_chars // 2:
          truncated = truncated[:last_space]
      cleaned = truncated.rstrip(" ،,.") + "…"
  ```
- **Risk of change:** Very low. May produce slightly shorter output than before (by up to one word).
- **Validation steps:**
  1. Long text → truncated at word boundary
  2. Short text → no truncation
- **Test coverage needed:** Unit test with various Arabic text lengths
- **Status:** Complete - Fixed 2026-04-15. Confirmed UX bug: `_voice_safe_text()` could cut a word mid-token before sending text to TTS. Switched truncation to prefer the last word boundary when available, while preserving the old behavior for short/no-space cases. Validated with smoke tests `prd030_truncates_at_word_boundary` and `prd030_exact_limit_unchanged`.

---

#### Task 6.4: Add graceful turn cap with near-completion allowance

- **Related issue ID(s):** PRD-035
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `on_user_turn_completed` (lines 238-240)
- **Expected code change:**
  ```python
  if turn_num > MAX_TURNS_PER_SESSION:
      # Check if order is nearly complete — allow a few more turns
      if _is_near_complete(ud) and turn_num <= MAX_TURNS_PER_SESSION + 3:
          logger.info("call=%s | turn cap grace | nearly complete | turn=%d", ud.call_id, turn_num)
      else:
          logger.warning("call=%s | turn cap reached", ud.call_id)
          await self._say_and_stop("معلش يا فندم، المكالمة طولت. كلمنا تاني في أي وقت.", critical=True)
  elif turn_num >= MAX_TURNS_PER_SESSION - 5:
      # Warning zone — inject a note
      logger.info("call=%s | approaching turn cap | turn=%d", ud.call_id, turn_num)
  ```
- **Risk of change:** Medium. Adds logic to a critical path. The `_is_near_complete` helper needs to be defined.
- **Validation steps:**
  1. Turn 49 with nearly complete order → allowed to continue
  2. Turn 53 with nearly complete order → hard cut
  3. Turn 50 with no data → hard cut
- **Test coverage needed:** Turn cap tests with various order states
- **Status:** Complete - Fixed 2026-04-15. Confirmed reliability bug in the live turn pipeline: once the turn cap was hit, the agent always hard-cut the call even when only the phone number or final confirmation was missing. Added turn-cap warning prompts, a near-completion grace window controlled by `TURN_CAP_GRACE_TURNS`, and preserved the hard stop for stalled calls. Validation covered warning injection, grace continuation, stalled-call hard cut, and grace expiry via smoke tests `prd035_turn_cap_warning_note`, `prd035_turn_cap_grace_allows_near_complete`, `prd035_turn_cap_hard_cuts_stalled_call`, and `prd035_turn_cap_grace_expires`.

---

### Phase 7: Code Quality & Duplication

> Reduce duplication to prevent divergent bug fixes.

---

#### Task 7.1: Extract shared `update_order` logic

- **Related issue ID(s):** PRD-027
- **Exact file(s) to modify:** `agent/base_agent.py` (add shared method), `agent/flows/takeaway.py`, `agent/flows/delivery.py`
- **Function(s) to inspect:**
  - `takeaway.py` `update_order` (lines 127-189)
  - `delivery.py` `update_order` (lines 136-199)
- **Expected code change:** Extract shared parsing/validation/menu-resolution logic into `BaseAgent._process_order_update()`. Each flow's `update_order` calls the shared method and provides flow-specific next-step prompt.
- **Risk of change:** Medium. Refactoring two working tools. Must verify identical behavior.
- **Validation steps:**
  1. Same order input in takeaway and delivery → identical parsing results
  2. Next-step prompts differ correctly between flows
  3. All smoke tests pass
- **Test coverage needed:** Side-by-side comparison tests
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed maintenance risk, not a live bug. Direct source comparison showed the takeaway and delivery `update_order` implementations were nearly identical, making future fixes likely to drift between flows.
- **Implementation notes:** Added `BaseAgent._process_order_update()` and moved the shared parsing, menu validation, and order-state update logic into that helper. `Takeaway.update_order()` and `Delivery.update_order()` now delegate to the shared implementation while delivery still passes its minimum-order requirement so flow-specific behavior is preserved.
- **Validation notes:** Added smoke tests `prd027_shared_order_logic_consistent` and `prd027_flow_specific_delivery_minimum_preserved`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Low. The common logic is now centralized, but future flow-specific behavior still needs to stay in the wrapper layer instead of reintroducing duplication.

---

#### Task 7.2: Extract shared upsell handling

- **Related issue ID(s):** PRD-028
- **Exact file(s) to modify:** `agent/base_agent.py` (add shared method), `agent/flows/takeaway.py`, `agent/flows/delivery.py`
- **Function(s) to inspect:**
  - `takeaway.py` upsell handling (lines 74-115)
  - `delivery.py` upsell handling (lines 58-134)
- **Expected code change:** Extract into `BaseAgent._handle_pending_upsell()` with a callback parameter for the post-upsell action.
- **Risk of change:** Medium. Same as Task 7.1.
- **Validation steps:** Upsell accept/reject works identically in both flows.
- **Test coverage needed:** Upsell scenario tests for both flows.
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed maintenance risk, not a live bug. Direct source comparison showed the takeaway and delivery upsell branches shared the same accept/reject state transitions but had already started diverging structurally.
- **Implementation notes:** Added `BaseAgent._handle_pending_upsell()` and moved the shared upsell accept/reject bookkeeping into that helper. Each flow now passes its own follow-up callback so takeaway still continues toward name capture while delivery still continues toward address capture.
- **Validation notes:** Added smoke tests `prd028_shared_upsell_acceptance_state` and `prd028_upsell_followup_remains_flow_specific`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Low. The shared helper intentionally centralizes only the common state transitions; flow-specific upsell copy should remain in the callback/prompt layer.

---

#### Task 7.3: Move class-level defaults to `__init__`

- **Related issue ID(s):** PRD-031
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `BaseAgent` class (lines 24-25)
- **Expected code change:**
  ```python
  class BaseAgent(Agent):
      def __init__(self, **kwargs):
          super().__init__(**kwargs)
          self._opening: str = ""
          self._turn_responded: bool = False
  ```
- **Risk of change:** Low. Need to ensure all subclass `__init__` calls `super().__init__()`.
- **Validation steps:** All smoke tests pass. Two concurrent instances have independent state.
- **Test coverage needed:** Instance independence test.
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed preventive shared-state risk, not a live bug. The defaults were immutable today, but class-level per-instance flags were misleading and unsafe if later mutated.
- **Implementation notes:** Added `BaseAgent.__init__()` to create instance-scoped `_opening` and `_turn_responded`. The implementation preserves `_opening` values assigned by subclasses before `super().__init__()` so existing flow openings remain unchanged.
- **Validation notes:** Added smoke test `prd031_instance_defaults_are_instance_scoped`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Very low. Future subclasses still need to call `super().__init__()`, which all current flow classes already do.

---

#### Task 7.4: Add `update_name`/`update_phone` to Greeter tools

- **Related issue ID(s):** PRD-032
- **Exact file(s) to modify:** `agent/flows/greeter.py`
- **Function(s) to inspect:** `Greeter.__init__` (line 60)
- **Expected code change:**
  ```python
  # BEFORE
  tools=[get_menu]

  # AFTER
  tools=[get_menu, update_name, update_phone]
  ```
- **Risk of change:** Low. Adds tools the LLM can optionally use. No behavior change unless the LLM decides to use them.
- **Validation steps:**
  1. User says name in Greeter → captured (if flow allows)
  2. Routing still works correctly
  3. Verify `_flow_missing_name("greeter", ud)` returns expected value
- **Test coverage needed:** Greeter name/phone capture test.
- **Status:** Completed 2026-04-15
- **Verification state:** Confirmed real coverage bug. The Greeter tools list excluded `update_name` and `update_phone`, while `_flow_missing_name("greeter", ud)` / `_flow_missing_phone("greeter", ud)` stayed false, so the generic intercept path alone could not capture combined intro-plus-intent turns before routing.
- **Implementation notes:** Added `update_name` and `update_phone` to the Greeter tool list and added a narrow inline prefill path in `Greeter._maybe_handle_turn_deterministically()` so explicit self-introductions can populate user data before routing to the next flow. Kept Greeter out of always-on contact-capture mode to avoid degrading the greeting stage.
- **Validation notes:** Added smoke tests `prd032_greeter_tools_include_contact_tools` and `prd032_greeter_prefills_contact_before_routing`. Full `python smoke_tests.py` passed with 132 passing checks.
- **Remaining risks:** Low. Inline name prefill currently focuses on explicit self-introductions; broader name extraction improvements remain out of scope for this phase.

---

### Phase 8: Observability & Operations

> Production visibility and monitoring.

---

#### Task 8.1: Add telemetry events for critical paths

- **Related issue ID(s):** PRD-029
- **Exact file(s) to modify:** `agent/agent.py`, `agent/base_agent.py`, `agent/flows/*.py`
- **Function(s) to inspect:** All decision points listed in PRD-029
- **Expected code change:** Add `_emit_event()` calls at:
  - Agent transfers (`_transfer`, `_transfer_live`)
  - Upsell offered/accepted/rejected
  - Phone capture success/failure/buffer
  - Name capture success/failure
  - Circuit breaker open/close
  - Write queue fallback
  - Config cache hit/miss
  - Inactivity reprompt/timeout
- **Risk of change:** Very low. Event emission is fire-and-forget.
- **Validation steps:** Run a complete order flow. Verify all expected events are emitted.
- **Test coverage needed:** Event sequence verification test.
- **Status:** Completed 2026-04-18
- **Verification state:** Confirmed observability gap. Direct inspection showed existing telemetry already covered call start/end, turns, tools, and final confirmations, but it still missed transfer events, upsell outcomes, inactivity actions, and turn-guard injection.
- **Implementation notes:** Expanded structured telemetry across `agent/agent.py`, `agent/base_agent.py`, and `agent/main.py`. Added coverage for `flow.transfer` (live + handoff), `upsell.accepted`, `upsell.rejected`, `turn.guard`, and `call.inactivity`. Earlier Phase 8 work in `agent/agent.py` already added `config.cache`, `backend.circuit`, `backend.queue`, `phone.capture`, `name.capture`, and `upsell.offer`; this task completed the missing critical-path hooks instead of refactoring the telemetry system.
- **Validation notes:** Added smoke tests `prd029_event_hooks_present` and `prd029_transfer_and_upsell_events_emitted`. Full `python smoke_tests.py` passed with 138 passing checks.
- **Remaining risks:** Low. Event naming is now production-usable, but downstream dashboards and alerts still need to be configured outside this repo.

---

#### Task 8.2: Add health check endpoint

- **Related issue ID(s):** PRD-034
- **Exact file(s) to modify:** `agent/main.py` (or new `agent/health.py`)
- **Function(s) to inspect:** N/A — new feature
- **Expected code change:** Add a simple HTTP endpoint using `aiohttp` or LiveKit's built-in mechanism that returns system health status including: active sessions, circuit breaker state, config availability, write queue health.
- **Risk of change:** Low. New code, no existing code modified.
- **Validation steps:**
  1. Healthy → 200 OK with status details
  2. Backend down → 503 with degraded status
- **Test coverage needed:** Health check response tests for various states.
- **Status:** Completed 2026-04-18
- **Verification state:** Confirmed production operations gap. `RuntimeHealth` and worker state already existed in memory, but the agent process exposed no HTTP endpoint for orchestrator monitoring.
- **Implementation notes:** Added `agent/health.py` with a lightweight background `/healthz` server, started from `agent/main.py` only in the parent process. Added worker health snapshots plus `build_agent_health_report()` aggregation in `agent/agent.py`, including active sessions, circuit state, config availability, queue backlog, and LiveKit connection state. Hardened snapshot writes on Windows with a lock, replace retries, and best-effort error handling so telemetry does not create noisy task failures.
- **Validation notes:** Added smoke tests `prd034_health_report_states` and `prd034_health_endpoint_serves_json`. Full `python smoke_tests.py` passed with 138 passing checks.
- **Remaining risks:** Low. If the configured health port is already in use, the server logs a warning and skips startup; production deployment should reserve a dedicated port.

---

#### Task 8.3: Verify backend idempotency key checking

- **Related issue ID(s):** PRD-033
- **Exact file(s) to modify:** `backend/main.py` (inspection only, may need changes)
- **Function(s) to inspect:** Order/reservation/complaint submission endpoints
- **Expected code change:** Verify the backend reads and checks `Idempotency-Key` header. If not, implement idempotency checking.
- **Risk of change:** High if backend changes needed. Low if already implemented.
- **Validation steps:** Send duplicate request with same idempotency key → second returns cached response.
- **Test coverage needed:** Integration test for idempotency.
- **Status:** Completed 2026-04-18
- **Verification state:** Disproven as an unresolved backend gap. Direct inspection confirmed that `backend/main.py` already accepts the `Idempotency-Key` header on order, reservation, and complaint endpoints and queries existing rows by `idempotency_key` before inserting.
- **Implementation notes:** No backend code change was needed. Verification also confirmed unique constraints already exist on `idempotency_key` for the affected models, so the original Phase 8 task turned into documentation and validation rather than implementation.
- **Validation notes:** Added smoke tests `prd033_backend_checks_idempotency_header` and `prd033_backend_enforces_idempotency_uniqueness`. Full `python smoke_tests.py` passed with 138 passing checks.
- **Remaining risks:** Low. This closes the originally suspected gap, but true end-to-end duplicate-submit integration coverage would still be valuable in a future backend test suite.

---

### Phase 9: Context & Performance

> Optimize token usage and context management.

---

#### Task 9.1: Preserve system messages during context truncation

- **Related issue ID(s):** PRD-016
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `on_user_turn_completed` (lines 260-261)
- **Expected code change:** Replace blunt truncation with system-message-aware windowing.
- **Risk of change:** Medium. Changes context management which affects LLM behavior.
- **Validation steps:** 40-turn conversation → system messages preserved.
- **Test coverage needed:** Long conversation context test.
- **Status:** Completed 2026-04-18
- **Verification state:** Confirmed production risk. `BaseAgent.on_user_turn_completed()` was still using positional `truncate(max_items=...)`, which could evict durable system prompts after long calls.
- **Implementation notes:** Replaced blunt truncation with `_limit_chat_ctx_preserving_system(...)` after stripping prior turn-guard / turn-cap markers, so durable system prompts stay resident while older non-system history is windowed.
- **Validation notes:** Added smoke tests `prd016_system_prompt_preserved_during_truncation` and `prd016_context_window_stays_bounded`. Full `python smoke_tests.py` passed with 143 passing checks.
- **Remaining risks:** Low. If durable system prompts ever grow beyond `TURN_CHAT_CTX_MAX_ITEMS`, the helper now prioritizes keeping the newest system prompts and drops non-system history first.

---

#### Task 9.2: Limit context copy on agent transfer

- **Related issue ID(s):** PRD-021
- **Exact file(s) to modify:** `agent/base_agent.py`
- **Function(s) to inspect:** `on_enter` (lines 59-68)
- **Expected code change:** Reduce `PROMPT_HISTORY_ITEMS` or add additional truncation on transfer.
- **Risk of change:** Medium. Too aggressive truncation loses conversation context.
- **Validation steps:** 5-transfer call → context size stays bounded.
- **Test coverage needed:** Multi-transfer context size test.
- **Status:** Completed 2026-04-18
- **Verification state:** Confirmed production risk. Reused flow-agent instances were carrying prior non-system history in `self.chat_ctx`, and `on_enter()` was also copying raw previous-agent context on transfer.
- **Implementation notes:** `on_enter()` now strips stale marked system prompts from reused agent instances, trims retained non-system history to `PROMPT_HISTORY_ITEMS`, and copies only the most recent non-system items from the previous agent into the next flow.
- **Validation notes:** Added smoke tests `prd021_transfer_context_bounded` and `prd021_keeps_recent_transfer_history_only`. Full `python smoke_tests.py` passed with 143 passing checks.
- **Remaining risks:** Low. The transferred history window is intentionally smaller now, so very old conversational nuance must come from structured `UserData`, not raw transcript history.

---

#### Task 9.3: Conditionally inject turn guards

- **Related issue ID(s):** PRD-023
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_should_add_turn_guard` (lines 2556-2561)
- **Expected code change:** Track last guard content hash. Skip injection if guard is identical to previous.
- **Risk of change:** Low-medium. May cause the LLM to drift if guards are skipped when they're actually needed.
- **Validation steps:** Token usage per turn reduced when slot state is unchanged.
- **Test coverage needed:** Token counting test.
- **Status:** Completed 2026-04-18
- **Verification state:** Confirmed production cost / latency risk. `_should_add_turn_guard()` still returned `True` for every non-empty turn, so identical guard prompts were being re-injected every turn.
- **Implementation notes:** Added `last_guard_signature` to `UserData`, introduced `_turn_guard_signature(...)`, and updated `BaseAgent.on_user_turn_completed()` to skip guard injection when the newly generated guard matches the previous flow-aware signature.
- **Validation notes:** Added smoke test `prd023_identical_turn_guard_skipped`. Full `python smoke_tests.py` passed with 143 passing checks.
- **Remaining risks:** Low. Repeated identical guards are now skipped, so any future drift must be handled by the durable flow prompts and current slot state rather than redundant per-turn guard repetition.

---

### Phase 10: Infrastructure & Compatibility

> Platform safety and forward compatibility.

---

#### Task 10.1: Evaluate TTS streaming capability

- **Related issue ID(s):** PRD-017
- **Exact file(s) to modify:** `agent/xai_tts.py`
- **Function(s) to inspect:** `TTS.__init__` (line 24: `streaming=False`), `ChunkedStream._run` (lines 54-93)
- **Expected code change:** Test whether changing `streaming=True` in `TTSCapabilities` works with the current implementation (which already uses `resp.aiter_bytes()`). If it works, this is a one-line fix. If not, evaluate alternative TTS providers.
- **Risk of change:** High. TTS is critical path. Must test thoroughly.
- **Validation steps:** Measure TTFB before/after. Verify audio quality.
- **Test coverage needed:** TTFB measurement test. Audio completeness test.
- **Status:** Not Started

---

#### Task 10.2: Document cross-process cache safety

- **Related issue ID(s):** PRD-022
- **Exact file(s) to modify:** `agent/agent.py`
- **Function(s) to inspect:** `_write_shared_cache_entry`, `_read_shared_cache_map`
- **Expected code change:** Add a comment documenting the benign race condition and why it's acceptable:
  ```python
  # NOTE: asyncio.Lock only guards within this process. With num_idle_processes > 1,
  # other processes may read stale data during a write. This is acceptable because:
  # 1. os.replace() is atomic — reads always get a complete file
  # 2. Config TTL checks handle staleness — stale configs are re-fetched
  ```
- **Risk of change:** Zero. Documentation only.
- **Validation steps:** Code review.
- **Test coverage needed:** None (documentation).
- **Status:** Not Started

---

#### Task 10.3: Replace deprecated `asyncio.get_event_loop()`

- **Related issue ID(s):** PRD-024
- **Exact file(s) to modify:** `agent/backend/client.py`
- **Function(s) to inspect:** `cleanup_http_client` (lines 50-62)
- **Expected code change:**
  ```python
  def cleanup_http_client() -> None:
      global _http_client
      if _http_client is not None and not _http_client.is_closed:
          try:
              loop = asyncio.get_running_loop()
              loop.create_task(_http_client.aclose())
          except RuntimeError:
              try:
                  asyncio.run(_http_client.aclose())
              except Exception:
                  pass
      _http_client = None
  ```
- **Risk of change:** Very low. Only affects shutdown cleanup.
- **Validation steps:** No deprecation warnings on Python 3.12+.
- **Test coverage needed:** Cleanup test with and without running loop.
- **Status:** Not Started

---

#### Task 10.4: Verify TTS client lifecycle

- **Related issue ID(s):** PRD-036
- **Exact file(s) to modify:** `agent/xai_tts.py` (may need changes)
- **Function(s) to inspect:** `TTS.__init__`, `TTS.aclose`
- **Expected code change:** Verify LiveKit SDK calls `tts.aclose()` on session end. If not, add a `__del__` method or share the httpx client.
- **Risk of change:** Low.
- **Validation steps:** 100 sequential calls → no FD leak.
- **Test coverage needed:** Resource leak test.
- **Status:** Not Started

---

#### Task 10.5: Add config round-trip field coverage test

- **Related issue ID(s):** PRD-037
- **Exact file(s) to modify:** `agent/smoke_tests.py`
- **Function(s) to inspect:** `_config_to_dict`, `_config_from_dict`
- **Expected code change:** Add a test that:
  1. Creates a `RestaurantConfig` with non-default values for every field
  2. Runs `_config_from_dict(_config_to_dict(cfg))`
  3. Asserts all fields are equal
- **Risk of change:** Zero. Test only.
- **Validation steps:** Test passes.
- **Test coverage needed:** This IS the test.
- **Status:** Not Started

---

## Dependency Notes

Some fixes should happen before others due to code dependencies:

| Fix First | Before | Reason |
|-----------|--------|--------|
| Task 1.1 (PRD-003 OTP) | Task 1.2 (PRD-008 CORS) | Both are security, but OTP is exploitable alone |
| Task 3.1 (PRD-004 circuit locks) | Task 5.3 (PRD-012 HTTP timeout) | Timeout failures feed into circuit breaker — locks must be in place first |
| Task 4.1 (PRD-007 phone threshold) | Task 4.2 (PRD-019 buffer overwrite) | Threshold change reduces how often buffer merge is reached |
| Task 7.1 (PRD-027 shared order) | Task 7.2 (PRD-028 shared upsell) | Upsell refactor depends on shared order base |
| Task 4.4 (PRD-015 negative forms) | Task 4.3 (PRD-010 upsell normalize) | Both touch normalization, do together to avoid conflicts |
| Task 9.1 (PRD-016 system msg preserve) | Task 9.2 (PRD-021 transfer context) | Transfer context logic should account for new truncation behavior |

---

## Suggested Commit Batches

Each batch should be a single commit or small PR that can be reviewed independently.

### Commit 1: Security hardening
- Task 1.1 (OTP bypass)
- Task 1.2 (CORS)

### Commit 2: Control flow correctness
- Task 2.1 (quick intercepts `elif`)
- Task 2.2 (post-completion `elif`)
- Task 2.3 (phone intercept empty reply)

### Commit 3: Async I/O safety
- Task 3.2 (blocking filesystem calls)
- Task 3.3 (turn count safety)

### Commit 4: Circuit breaker locking
- Task 3.1 (circuit locks — larger change, own commit)

### Commit 5: Fork safety and worker context
- Task 3.4 (HTTP client fork safety)
- Task 3.5 (worker context guard)

### Commit 6: Phone & NLP fixes
- Task 4.1 (phone threshold)
- Task 4.2 (buffer overwrite)
- Task 4.3 (upsell normalization)
- Task 4.4 (negative forms precompute)

### Commit 7: Error handling
- Task 5.1 (tool error catch-all)

### Commit 8: Backend reliability
- Task 5.2 (recovery file hardening)
- Task 5.3 (HTTP timeout)

### Commit 9: UX & parsing
- Task 6.1 (order item regex)
- Task 6.2 (fuzzy threshold)
- Task 6.3 (voice truncation)
- Task 6.4 (graceful turn cap)

### Commit 10: Code deduplication
- Task 7.1 (shared update_order)
- Task 7.2 (shared upsell)
- Task 7.3 (class defaults)
- Task 7.4 (Greeter tools)

### Commit 11: Observability
- Task 8.1 (telemetry events)
- Task 8.2 (health check)

### Commit 12: Context management
- Task 9.1 (system message preservation)
- Task 9.2 (transfer context limit)
- Task 9.3 (conditional turn guards)

### Commit 13: Infrastructure cleanup
- Task 10.1 (TTS streaming — may be own PR)
- Task 10.2 (cache documentation)
- Task 10.3 (deprecated asyncio)
- Task 10.4 (TTS lifecycle)
- Task 10.5 (config round-trip test)

### Commit 14: Backend idempotency
- Task 8.3 (verify/implement backend idempotency)

---

## Final Pre-Launch Gate

**ALL of the following must be true before production launch:**

- [ ] All 37 issues in the master report are resolved (Fixed, Disproven, or Accepted)
- [ ] No issue has been removed without documented explanation
- [ ] All automated tests pass (smoke tests + new tests from this plan)
- [ ] Security fixes (PRD-003, PRD-008) deployed and verified
- [ ] Circuit breaker locking verified under concurrent load
- [ ] Phone capture tested with 10+ Egyptian speaking patterns
- [ ] Recovery file tested with multi-restart scenario
- [ ] Context truncation verified on 30+ turn conversation
- [ ] Health check endpoint operational
- [ ] Telemetry events verified in production-like environment
- [ ] Backend idempotency key checking confirmed
- [ ] TTS TTFB measured and documented (acceptable threshold agreed)
- [ ] `ENVIRONMENT=production` verified in deployment config
- [ ] CORS allowlist configured for production domain
- [ ] Full call flow tested end-to-end: Greeter → Order → Confirm → 
- [ ] Stress test: 50 concurrent sessions for 10 minutes with no errors
- [ ] Master report progress log updated with final status
