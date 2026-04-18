# Production Readiness Master Report

## Purpose

This document is the **complete pre-production blocker list** for the Restaurant Voice Agent system.
Every issue discovered during the full-project deep audit is recorded here with actionable detail.
No issue should be silently dropped, downgraded, or summarized away.

This is the single source of truth for production readiness.

## Production Rule

- **Every issue listed here is considered UNRESOLVED until it is fixed, explicitly disproven, or consciously accepted with written justification.**
- Nothing may be removed from this report without a documented explanation in the Progress Log.
- "Low severity" does not mean "optional" — it means "fix after the higher-severity items, but still fix it."
- If an issue is determined to be a false positive after investigation, change its Status to `Disproven` and document why.
- If an issue is accepted as-is, change its Status to `Accepted` with justification and risk acknowledgement.

---

## Full Findings List

---

### PRD-001: Turn intercept fall-through in `_handle_quick_intercepts`

- **Severity:** P0
- **Category:** Control flow / correctness
- **File / function / module:** `agent/base_agent.py` / `_handle_quick_intercepts`
- **Exact code location:** Lines 151-169
- **Problem:** Three consecutive `if` blocks (not `elif`) each call `_say_and_stop()`. When the first branch fires, `_turn_responded` is set to `True`. On subsequent `if` blocks, `_say_and_stop` detects `_turn_responded=True` and raises `StopResponse` without sending a new message — but only if reached. The real danger: the calling site at `on_user_turn_completed` line 251 (`await self._handle_quick_intercepts(flow, ud, user_text)`) does NOT catch `StopResponse` and does NOT check the return value. If `_say_and_stop` raises `StopResponse` on the first branch, it propagates up and stops the turn (correct). But if `_say_and_stop` returns (which happens when `_turn_responded` is already True from a prior path), all three `if` blocks evaluate their conditions, the function returns `False`, and execution continues to `_handle_post_completion`, `_handle_name_intercept`, `_handle_phone_intercept`, and ultimately LLM generation.
- **Why it matters:** The LLM generates a response for a turn that was already handled by a deterministic intercept. The user hears two responses — the intercept reply and the LLM's confused follow-up. This corrupts the conversation flow.
- **Real failure scenario:** User says "المجموع كام" (how much is the total) in the takeaway flow. The total-question intercept fires at line 161, calls `_say_and_stop` which says the total and raises `StopResponse`. This works correctly. But consider a subtle case: if `_turn_responded` is already True when entering this function (e.g., due to a bug or re-entry), `_say_and_stop` raises `StopResponse` immediately at line 113 without sending the message. The `StopResponse` propagates up and the user gets silence — the intercept matched but no message was spoken.
- **Recommended fix:** Change all three `if` statements to `if` / `elif` / `elif`. This ensures only one branch executes. Additionally, consider wrapping the calls at lines 251-252 in `on_user_turn_completed` with a check: `if self._turn_responded: raise StopResponse()` after each intercept call.
- **Validation method:** Write a test where two intercept conditions are simultaneously true (e.g., a message that matches both total-question and menu-question patterns). Verify only one response is sent. Run with logging to confirm no LLM generation follows an intercept.
- **Test ideas:**
  - Test: send a message matching multiple intercept conditions, verify only one `session.say` call
  - Test: verify `_turn_responded=True` after first intercept prevents second intercept from speaking
  - Test: verify no LLM generation occurs after any intercept fires
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Changed `if`/`if`/`if` to `if`/`elif`/`elif` in `_handle_quick_intercepts`. Verified: in the normal path, `_say_and_stop` raises `StopResponse` which propagates up — the second/third `if` blocks were never reached in practice. The `elif` change makes this explicit and guards against the edge case where `_turn_responded` is already True. Smoke test `prd001_quick_intercepts_elif` added and passes. **Verification note:** The original report description overstated the severity — the `StopResponse` exception propagation meant double responses were NOT occurring in normal operation. However, the `elif` fix is still correct and necessary for code clarity and to guard against the `_turn_responded=True` edge case.

---

### PRD-002: Post-completion intercept fall-through in `_handle_post_completion`

- **Severity:** P1
- **Category:** Control flow / correctness
- **File / function / module:** `agent/base_agent.py` / `_handle_post_completion`
- **Exact code location:** Lines 171-182
- **Problem:** Same `if`/`if` pattern as PRD-001. Lines 176 and 179 are separate `if` blocks. If a session has both `order_confirmed=True` and the user says something that matches both `_is_thanks_message` and `_is_positive_confirmation`, both branches fire. The first calls `_say_and_stop` successfully. The second finds `_turn_responded=True` and `_say_and_stop` raises `StopResponse` immediately — which propagates up. This is mostly safe but wasteful. The real risk: if neither `_is_thanks_message` nor `_is_positive_confirmation` matches but `order_confirmed` is True, the function returns `False` and execution continues to the LLM even though the user is in a post-completion state.
- **Why it matters:** Post-completion turns should be handled deterministically. Falling through to LLM generation means the LLM may try to restart the order flow.
- **Real failure scenario:** User confirms an order. Then says something ambiguous like "كويس" (good). It doesn't match `_is_thanks_message` or `_is_positive_confirmation`. Function returns `False`. LLM generates a response and may re-ask about the order or try to start a new one.
- **Recommended fix:** Use `elif` for the two branches. Add an `else` fallback that handles unrecognized post-completion utterances with a generic "تحت أمرك يا فندم".
- **Validation method:** Send post-completion messages that match both thanks and confirmation patterns. Verify single response. Send ambiguous post-completion messages and verify they don't reach the LLM.
- **Test ideas:**
  - Test: message matching both `_is_thanks_message` and `_is_positive_confirmation` with `order_confirmed=True` → only one reply
  - Test: ambiguous message with `order_confirmed=True` → deterministic reply, not LLM
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Changed `if`/`if` to `if`/`elif` in `_handle_post_completion`. Same analysis as PRD-001 — the `StopResponse` propagation prevented double responses in practice, but `elif` is the correct structure. Smoke test `prd002_post_completion_elif` added and passes. **Remaining risk:** When neither `_is_thanks_message` nor `_is_positive_confirmation` matches in post-completion state, the function still returns `False` and execution continues to LLM. This is a separate concern — the `elif` fix addresses the fall-through between the two branches, not the missing `else` fallback. The missing `else` could be addressed as a follow-up.

---

### PRD-003: Hardcoded OTP bypass active by default

- **Severity:** P0
- **Category:** Security
- **File / function / module:** `backend/main.py`
- **Exact code location:** Line 107
- **Problem:** `DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS", "956956")`. The default value IS the bypass code. The bypass is only disabled when `ENVIRONMENT == "production"`, but `ENVIRONMENT` defaults to `"development"` when not set. A production deployment that omits the `ENVIRONMENT` variable has full authentication bypass.
- **Why it matters:** Any user can authenticate to any account using OTP code `956956` if the environment variable is missing. This is a one-step path to full data access.
- **Real failure scenario:** Ops engineer deploys the backend to a new server or container. Forgets to add `ENVIRONMENT=production` to the env config. The system is now live with OTP bypass enabled. An attacker discovers the bypass code (it's hardcoded in the source) and can log into any account.
- **Recommended fix:** Change to: `DEV_OTP_BYPASS = os.getenv("DEV_OTP_BYPASS") if os.getenv("ENVIRONMENT") == "development" else None`. This means: (1) no bypass unless ENVIRONMENT is explicitly "development", (2) no default bypass code — it must be explicitly set.
- **Validation method:** Deploy with no `ENVIRONMENT` variable set. Attempt to authenticate with `956956`. Should fail. Deploy with `ENVIRONMENT=development` and `DEV_OTP_BYPASS=testcode`. Attempt with `testcode`. Should succeed.
- **Test ideas:**
  - Test: ENVIRONMENT unset → OTP bypass disabled
  - Test: ENVIRONMENT=production → OTP bypass disabled regardless of DEV_OTP_BYPASS value
  - Test: ENVIRONMENT=development, DEV_OTP_BYPASS=X → bypass works with X only
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** This is a ship-stopper. Must be fixed before any public deployment.

---

### PRD-004: Circuit breaker state mutated without locks

- **Severity:** P1
- **Category:** Race condition / concurrency
- **File / function / module:** `agent/agent.py` / `_get_backend_circuit`, `_record_backend_circuit_success`, `_record_backend_circuit_failure`
- **Exact code location:** Lines 830-863
- **Problem:** These functions read and write `ctx.backend_circuits` dict and mutate `BackendCircuitState` fields (`consecutive_failures`, `open_until_monotonic`, `last_error`) with no async lock. Multiple concurrent sessions call `_post()` which calls these functions. While Python's GIL prevents true data races on dict operations, the read-check-mutate sequence spans multiple Python bytecodes and can interleave between `await` points in different coroutines.
- **Why it matters:** The circuit breaker is a safety mechanism. If it fails to trip due to interleaved mutations, the system continues hammering a failing backend, potentially causing cascading failures.
- **Real failure scenario:** Backend returns 500 for 5 consecutive requests across 3 concurrent sessions. Session A reads `consecutive_failures=4`, awaits, session B reads `consecutive_failures=4`, both increment to 5. Only one write takes effect. The threshold check at line 858 (`if state.consecutive_failures >= BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD`) may see 5 for one but the dict only stores one increment. Under sufficient concurrency, the counter may never reach the threshold.
- **Recommended fix:** Add `circuit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)` to `WorkerContext`. Wrap all circuit state access: `async with ctx.circuit_lock: ...`. Convert `_get_backend_circuit`, `_record_backend_circuit_success`, `_record_backend_circuit_failure` to async functions.
- **Validation method:** Write a stress test that sends 50 concurrent requests to a failing endpoint. Verify the circuit opens after exactly `BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD` failures.
- **Test ideas:**
  - Test: 50 concurrent `_post()` calls against a 500-returning endpoint → circuit opens
  - Test: success followed by failure → counter resets then increments correctly
  - Test: circuit open → requests blocked → cooldown expires → requests resume
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Added `circuit_lock: asyncio.Lock` to `WorkerContext`. Converted `_backend_circuit_is_open`, `_record_backend_circuit_success`, `_record_backend_circuit_failure` to async functions guarded by `circuit_lock`. Updated all 5 callers in `_post()` and `_submit_queued_backend_write` to `await`. `_get_backend_circuit` remains sync but is only called while holding the lock. Smoke tests `prd004_circuit_breaker_is_async`, `prd004_circuit_lock_exists`, `prd004_circuit_breaker_roundtrip` added and pass.

---

### PRD-005: Blocking `path.exists()` calls on async event loop

- **Severity:** P1
- **Category:** Blocking I/O in async path
- **File / function / module:** `agent/agent.py` / `_read_shared_cache_map`, `_write_shared_cache_entry`, `_read_backend_queue_recovery_lines`, `_append_backend_queue_recovery_items`
- **Exact code location:** Lines 504, 543, 905, 908, 939
- **Problem:** `path.exists()` is a synchronous filesystem syscall executed directly on the async event loop. The file read/write operations inside these same functions are correctly wrapped in `asyncio.to_thread()`, but the existence check before them is not.
- **Why it matters:** On network-mounted storage, high disk contention, or slow filesystems, `path.exists()` can block for 10-500ms, stalling the entire event loop and all concurrent sessions sharing that process.
- **Real failure scenario:** NFS mount hiccups for 200ms. A coroutine calls `_read_shared_cache_map()` which calls `path.exists()`. The event loop freezes for 200ms. During this time: STT buffers overflow (missed speech), TTS audio streams stall (choppy audio), VAD timers don't fire (missed end-of-utterance). All 50 concurrent sessions in that process are affected.
- **Recommended fix:** Replace all 5 occurrences with `await asyncio.to_thread(path.exists)`:
  - Line 504: `if not await asyncio.to_thread(path.exists):`
  - Line 543: `if await asyncio.to_thread(path.exists):`
  - Line 905: `if not await asyncio.to_thread(queue_path.exists):`
  - Line 908: `if not await asyncio.to_thread(queue_path.exists):`
  - Line 939: `if await asyncio.to_thread(queue_path.exists):`
- **Validation method:** Add timing instrumentation around `path.exists()` calls. Deploy on a system with artificial filesystem latency. Verify event loop is not blocked.
- **Test ideas:**
  - Test: mock `Path.exists` to sleep 100ms, verify event loop responsiveness
  - Test: concurrent sessions during slow filesystem → no timeouts
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. All 5 `path.exists()` calls replaced with `await asyncio.to_thread(path.exists)`. `_ensure_parent_dir` also converted to async (see PRD-006). Smoke test `prd005_no_bare_path_exists` verifies no bare `.exists()` calls remain in agent.py.

---

### PRD-006: Blocking `_ensure_parent_dir` / `os.makedirs` on async event loop

- **Severity:** P2
- **Category:** Blocking I/O in async path
- **File / function / module:** `agent/agent.py` / `_ensure_parent_dir`
- **Exact code location:** Called at lines 540, 921, 946 (wherever `_ensure_parent_dir(path)` is called in async functions)
- **Problem:** `_ensure_parent_dir` likely calls `os.makedirs()` which is a synchronous filesystem operation. It's called from async functions like `_write_shared_cache_entry` and `_append_backend_queue_recovery_items`.
- **Why it matters:** Same as PRD-005 — blocks the event loop on slow filesystems.
- **Real failure scenario:** First call after deployment hits `_ensure_parent_dir` which creates directories. On a slow filesystem this blocks for 50-200ms.
- **Recommended fix:** Wrap in `await asyncio.to_thread(os.makedirs, path.parent, exist_ok=True)` or call it once at startup rather than on every write.
- **Validation method:** Verify `_ensure_parent_dir` contents. If it uses `os.makedirs`, wrap it.
- **Test ideas:**
  - Test: verify parent directory creation is non-blocking
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. `_ensure_parent_dir` converted from sync to `async def` using `await asyncio.to_thread(path.parent.mkdir, ...)`. All 3 callers updated to `await`. Smoke test `prd005_ensure_parent_dir_is_async` confirms async signature.

---

### PRD-007: `is_phone_like_text` false-positive threshold too low

- **Severity:** P1
- **Category:** NLP / phone capture
- **File / function / module:** `agent/nlp/phone_extract.py` / `is_phone_like_text`
- **Exact code location:** Lines 26-36, specifically line 33: `if spoken_digits and len(spoken_digits) >= 2`
- **Problem:** Returns `True` if `spoken_words_to_digits(text, phone_mode=True)` extracts 2 or more digit characters. In `phone_mode=True`, Arabic number words are expanded digit-by-digit. Many Arabic phrases in an ordering context contain two or more number words that would produce >=2 digits.
- **Why it matters:** When `phone_capture_mode` is True and the user says something with >=2 digit-like words, the phone intercept at `base_agent.py:202-211` fires. The user's turn is consumed as phone digits instead of being processed as an order update, name, or other intent.
- **Real failure scenario:** User is in delivery flow. Phone is the next missing slot, so `phone_capture_mode=True`. User says "عايز اتنين كفتة وتلاتة كباب" (I want 2 kofta and 3 kebab). `spoken_words_to_digits` in phone_mode extracts "23". `is_phone_like_text` returns True (2 digits >= 2). Phone intercept fires. "23" is buffered as phone digits. Order is lost. User hears "تمام، كمّل باقي الرقم".
- **Recommended fix:** Raise threshold to `len(spoken_digits) >= 5` (a plausible phone prefix like "01012" has 5 digits). Alternatively, only trigger `is_phone_like_text` when the current flow's missing slot is specifically the phone number AND the text doesn't contain known menu/order patterns.
- **Validation method:** Create test cases with food orders containing number words. Verify they don't trigger phone capture.
- **Test ideas:**
  - Test: "اتنين كفتة" with phone_capture_mode=True → should NOT trigger phone intercept
  - Test: "صفر واحد صفر واحد اتنين" → SHOULD trigger phone intercept
  - Test: "تلاتة كباب وأربعة كشري" → should NOT trigger
  - Test: "01012345678" → SHOULD trigger
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Raised the spoken-digit threshold in `is_phone_like_text()` from `>=2` to `>=5`, leaving raw numeric chunk detection to the existing `non_phone` heuristic. Confirmed bug with reproducible variants such as `اتنين كفتة و تلاتة كباب` and `2 كفتة 3 كباب` that previously matched phone capture while phone collection was active. Smoke tests `phone_spoken_detected`, `prd007_order_numbers_not_phone_like`, and `prd007_digit_quantities_not_phone_like` were added/updated and pass. **Remaining risk:** very short spoken phone chunks (2-4 spoken digits) now rely more on later turns or raw numeric chunks instead of immediate phone interception.

---

### PRD-008: CORS regex matches all origins in non-production mode

- **Severity:** P1
- **Category:** Security
- **File / function / module:** `backend/main.py`
- **Exact code location:** Lines 131-136
- **Problem:** `allow_origin_regex=r"https?://.*"` when `ENVIRONMENT != "production"`. Since `ENVIRONMENT` defaults to `"development"` (see PRD-003), the default deployment has CORS wide open to every origin.
- **Why it matters:** Any website can make authenticated cross-origin requests to the backend API. Combined with PRD-003 (OTP bypass), this enables full account takeover from any malicious webpage.
- **Real failure scenario:** Attacker hosts a phishing page. User visits it. JavaScript on the page calls the backend API with the user's cookies/tokens. CORS allows it. The attacker extracts restaurant data, customer orders, phone numbers.
- **Recommended fix:** Use an explicit allowlist even in development: `allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080"]`. In production, use the actual domain.
- **Validation method:** Deploy in default mode. Make a cross-origin request from an arbitrary domain. Should be rejected.
- **Test ideas:**
  - Test: request from `http://evil.com` → blocked
  - Test: request from `http://localhost:5173` → allowed
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** Combined with PRD-003, this creates a critical attack surface.

---

### PRD-009: `_handle_phone_intercept` raises StopResponse on empty reply

- **Severity:** P2
- **Category:** Control flow / silent turn swallow
- **File / function / module:** `agent/base_agent.py` / `_handle_phone_intercept`
- **Exact code location:** Lines 202-211, specifically line 211: `raise StopResponse()`
- **Problem:** When `_apply_phone_update` returns an empty string (digits buffered but not enough for a complete phone, and no reply is warranted), the function still raises `StopResponse()` at line 211. The user's turn is silently consumed — they hear nothing.
- **Why it matters:** The user speaks, the system acknowledges nothing. The user thinks the agent didn't hear them and repeats, potentially triggering the inactivity watchdog.
- **Real failure scenario:** User says "صفر واحد" (01 — first two digits of a phone number). `_apply_phone_update` buffers "01" and returns empty string (needs more digits). Phone intercept raises `StopResponse`. User hears silence. Says "ألو؟" (hello?). Inactivity counter starts. User may hang up.
- **Recommended fix:** Only raise `StopResponse` when `phone_reply` is non-empty. If empty, return `False` and let the turn continue to LLM generation which can ask for more digits:
  ```python
  if phone_reply:
      await self._say_and_stop(phone_reply)
  else:
      return True  # intercepted but no reply needed — let LLM handle
  ```
  Actually, better: if digits were buffered, give a brief acknowledgment: "تمام، كمّل" (ok, continue).
- **Validation method:** Send partial phone digits. Verify the user hears an acknowledgment, not silence.
- **Test ideas:**
  - Test: partial phone "01" → user hears acknowledgment
  - Test: complete phone "01012345678" → user hears full phone confirmation
  - Test: non-phone text when phone_capture_mode=True → no intercept
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Replaced the bare `raise StopResponse()` at the end of `_handle_phone_intercept` with `return True`. When `phone_reply` is non-empty, `_say_and_stop` fires as before (raises `StopResponse`). When `phone_reply` is empty (confirmed possible when `_phone_capture_short_reply` returns `""` for 11+ buffered digits that fail validation), the turn now continues to the LLM instead of being silently swallowed. The LLM has phone context and can ask for correction. Smoke tests `prd009_phone_intercept_no_bare_stop_response` and `prd009_short_reply_can_be_empty` added and pass. **Verification:** Confirmed `_phone_capture_short_reply` returns `""` when `remaining <= 0` (line 1361 of agent.py), making the empty-reply path reachable.

---

### PRD-010: Upsell comparison uses `.lower()` instead of `_normalize_ar`

- **Severity:** P2
- **Category:** NLP consistency / Arabic normalization
- **File / function / module:** `agent/agent.py` / `_get_upsell_suggestion`
- **Exact code location:** Line 2394: `order_lower = {(item or "").lower() for item in (ud.order or [])}`
- **Problem:** Arabic `.lower()` is effectively a no-op for Arabic script characters. More critically, it doesn't normalize hamzas (أ/إ/آ → ا), tashkeel (diacritics), taa-marbuta (ة → ه), or alef-maksura (ى → ي) — which `_normalize_ar` handles. The comparison at line 2397 `if item_name.lower() not in order_lower` uses the same flawed normalization.
- **Why it matters:** An order item stored as "شاورما دجاج" and an upsell rule for "شاورمة دجاج" (taa-marbuta vs taa) won't match. The agent suggests an item the user already ordered.
- **Real failure scenario:** User orders "فتة دجاج". Upsell rule offers "فتّة دجاج" (with tashkeel). `.lower()` doesn't strip the shadda (ّ). Comparison fails. Agent says "ولو تحب أزودلك فتّة دجاج؟". User is confused because they just ordered it.
- **Recommended fix:**
  ```python
  order_normalized = {_normalize_ar(item or "") for item in (ud.order or [])}
  # ...
  if _normalize_ar(item_name) not in order_normalized:
  ```
- **Validation method:** Create an order with an item using taa-marbuta. Set up an upsell rule for the same item with taa. Verify no duplicate suggestion.
- **Test ideas:**
  - Test: order has "شاورما", upsell offers "شاورمة" → no suggestion
  - Test: order has "كبدة", upsell offers "كبده" → no suggestion
  - Test: order has "فراخ", upsell offers "فراخ بانيه" → suggestion offered (different item)
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. `_get_upsell_suggestion()` now compares `_normalize_ar(...)` on both the current order items and the upsell rule item instead of relying on `.lower()`. This prevents duplicate upsell suggestions for normalization-equivalent Arabic variants. Smoke tests `prd010_upsell_normalizes_same_item` and `prd010_upsell_still_offers_different_item` were added and pass. **Verification note:** the validated normalization-equivalent example in the current codebase is `كبدة` / `كبده`; the earlier `شاورما` / `شاورمة` example is not equivalent under the existing `_normalize_ar()` rules.

---

### PRD-011: Recovery file grows unbounded across crashes

- **Severity:** P2
- **Category:** Backend queue / reliability
- **File / function / module:** `agent/agent.py` / `_append_backend_queue_recovery_items`
- **Exact code location:** Lines 928-953
- **Problem:** On queue flush failure or graceful shutdown, items are appended to a `.jsonl` recovery file. There is a cap per-append (line 942: `available = BACKEND_WRITE_QUEUE_MAX_ITEMS - len(existing_lines)`) which prevents a single append from exceeding the limit. However, if the process restarts and the recovery file is replayed but items fail again, they're re-appended. Over multiple restart cycles during an extended backend outage, the same items can be written multiple times. Items also don't carry idempotency keys in the recovery file.
- **Why it matters:** Extended backend outage + repeated process restarts = recovery file with duplicate entries. On next successful startup, all items are replayed, potentially sending duplicate order confirmations, SMS notifications, etc.
- **Real failure scenario:** Backend down for 2 hours. 200 orders queued. Process restarts 5 times during outage. Each restart replays the recovery file, fails, re-appends. Recovery file has ~1000 entries (200 unique × ~5 duplications). Backend comes back. All 1000 entries are submitted. 800 are duplicates. Customers get 5× order confirmation SMS messages.
- **Recommended fix:** (1) Include the idempotency key in each recovery item JSON. (2) On replay, deduplicate by idempotency key before submitting. (3) Add a hard cap on recovery file line count (e.g., 500). (4) Log a warning when the cap is reached.
- **Validation method:** Simulate backend outage. Restart process multiple times. Verify recovery file doesn't contain duplicates. Verify idempotency keys are present.
- **Test ideas:**
  - Test: append 10 items, replay them (fail), re-append → no duplicates
  - Test: recovery file at cap → new items logged as dropped, not silently lost
  - Test: successful replay → recovery file cleared
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed bug in the recovery pipeline: duplicate failed writes could be re-appended across retries/restarts because the recovery file had no effective dedupe. Added explicit `idempotency_key` persistence to queued items, append-time dedupe, replay-time dedupe, configurable recovery cap via `BACKEND_WRITE_QUEUE_RECOVERY_MAX_LINES`, and cap/drop logging. Smoke tests `prd011_recovery_file_dedupes_same_item`, `prd011_recovery_cap_applies`, and `prd011_replay_dedupes_before_submit` were added and pass. **Verification note:** the old queue item already carried enough information to recompute a stable idempotency key from `call_id + action + payload`, so the missing piece was durable storage plus enforced dedupe/cap behavior.

---

### PRD-012: No explicit timeout parameter in `_post()` HTTP calls

- **Severity:** P2
- **Category:** Reliability / latency
- **File / function / module:** `agent/agent.py` / `_post()`
- **Exact code location:** Lines 1242-1246 (the `client.post()` call)
- **Problem:** `_post()` calls `client.post(full_url, json=payload, headers=headers)` without an explicit `timeout` parameter. It relies on the httpx client's default timeout configured in `backend/client.py:31` (5s total, 2s connect, 3s read, 3s write). However, this means a slow-but-responding backend (e.g., sending data at 1 byte/second) can keep the connection open for the full 5s per attempt × 3 retries = 15 seconds. Circuit breakers don't track slow responses — only failures.
- **Why it matters:** A slow backend holds the tool call open. The LLM turn doesn't complete. The user hears silence during a 5-15 second wait. This is the second-largest contributor to perceived latency after non-streaming TTS (PRD-017).
- **Real failure scenario:** Backend database has a slow query. `confirm_order` takes 4.5 seconds (under the 5s timeout). Response succeeds. Circuit breaker records success. Next call also takes 4.5s. User waits 4.5s in silence each time a tool calls the backend. Over a 5-tool-call order flow, that's 22.5 seconds of dead air.
- **Recommended fix:** (1) Add a `tool_timeout` parameter to `_post()` with a default of 3.0s. (2) Pass it to `client.post(timeout=tool_timeout)`. (3) Track slow responses (>2s) as soft failures in the circuit breaker — don't open the circuit, but log them for alerting.
- **Validation method:** Mock backend to respond in 4s. Verify `_post()` times out at 3s. Verify the timeout is treated as a retryable failure.
- **Test ideas:**
  - Test: backend responds in 2s → success
  - Test: backend responds in 4s → timeout → retry
  - Test: 3 consecutive timeouts → circuit opens
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed as a reliability/latency risk rather than an unbounded-timeout bug: `_post()` was already inheriting the shared httpx client timeout, but it lacked an explicit per-call timeout contract and override. Added `tool_timeout` to `_post()` and passed it directly to `client.post(timeout=...)`, defaulting to `BACKEND_POST_TIMEOUT_SECONDS`. Smoke test `prd012_post_uses_explicit_timeout` verifies explicit timeout propagation and retry behavior. **Verification note:** the earlier report cited a 5s client default, but the current codebase had already tightened the shared client timeout; the remaining issue was absence of an explicit `_post()`-level override.

---

### PRD-013: Turn count mutations without async lock

- **Severity:** P2
- **Category:** Concurrency / fragile assumptions
- **File / function / module:** `agent/agent.py` / `_increment_turn_count`, `_cleanup_turn_count`
- **Exact code location:** Lines 1153-1164
- **Problem:** `_increment_turn_count` does `count = ctx.turn_counts.get(call_id, 0) + 1; ctx.turn_counts[call_id] = count` — a synchronous read-modify-write on a shared dict. This is safe only because: (1) CPython's GIL makes dict operations atomic at bytecode level, and (2) there's no `await` between the read and write. However, this is an implementation detail of CPython, not a language guarantee. PyPy, GraalPy, or future free-threaded Python would break this.
- **Why it matters:** If the assumption breaks (future Python version, alternative interpreter, or someone adds an `await` during refactoring), turn counts could be lost, duplicated, or corrupted. The turn count drives the `MAX_TURNS_PER_SESSION` safety check.
- **Real failure scenario:** A refactor adds logging between the read and write: `count = ctx.turn_counts.get(call_id, 0) + 1; await log_turn(count); ctx.turn_counts[call_id] = count`. Now two coroutines for the same call_id can interleave, both reading the same count and both writing the same incremented value. Turn count stays at 1 instead of 2. Turn cap never triggers.
- **Recommended fix:** Either (a) add an `asyncio.Lock` for turn_counts, or (b) add a comment: `# SAFETY: atomic under CPython GIL — no awaits between read and write. Do not add await here.`
- **Validation method:** Code review. Grep for any `await` between `turn_counts.get` and `turn_counts[call_id] =`.
- **Test ideas:**
  - Test: 100 concurrent `_increment_turn_count` calls for the same call_id → count should be 100
- **Status:** Fixed
- **Resolution type:** Code change (safety comments)
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Added `SAFETY:` docstring comments to both `_increment_turn_count` and `_cleanup_turn_count` documenting the CPython GIL atomicity assumption and warning against adding `await` between the read and write. Chose Option B (comment) over Option A (lock) because: the functions are synchronous, all callers are synchronous, practical risk is near-zero under CPython, and making them async would require cascading changes. Smoke test `prd013_turn_count_safety_comments` confirms the comments are present.

---

### PRD-014: `_parse_order_item` fourth regex catches non-quantity numbers

- **Severity:** P2
- **Category:** Order parsing / false positives
- **File / function / module:** `agent/agent.py` / `_parse_order_item`
- **Exact code location:** Lines 2309-2339, specifically line 2319: `r"^(.+?)\s+(\d+)$"`
- **Problem:** The fourth regex pattern `r"^(.+?)\s+(\d+)$"` matches any text ending with a number. And the second pattern `r"^(\d+)\s+(.+)$"` matches any text starting with a number. These patterns don't validate that the extracted text is a menu item. Input like "شارع 15" or "دور 3" (address fragments) would parse as item="شارع", qty=15.
- **Why it matters:** If a user accidentally gives address information in an order context (e.g., says their address when the agent asks for their order), it could be parsed as a phantom order item.
- **Real failure scenario:** Agent asks for the order. User says "أنا في شارع 9" (I'm on street 9). `_parse_order_item` matches pattern 4: item="أنا في شارع", qty=9. This gets passed to `_resolve_menu_item` which would (hopefully) return None. But if a menu item partially matches "شارع" tokens, a fuzzy match at 0.5 threshold could succeed.
- **Recommended fix:** After regex parsing, validate the item text against the menu before accepting the parse. If no menu match, return the original text with qty=1. This puts the validation burden on the caller (which already calls `_resolve_menu_item`), but the regex should not confidently extract a quantity from non-order text.
- **Validation method:** Pass address-like strings through `_parse_order_item`. Verify they don't extract phantom quantities.
- **Test ideas:**
  - Test: "شارع 15" → item="شارع 15", qty=1 (not item="شارع", qty=15)
  - Test: "2 كفتة" → item="كفتة", qty=2 (correct)
  - Test: "كفتة × 3" → item="كفتة", qty=3 (correct)
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed bug by direct reproduction: `_parse_order_item("street 15")` returned `("street", 15)`. Added a post-parse sanity check for implicit prefix/suffix numeric matches so address/location phrases fall back to the original text with `qty=1`. Validation covered `prd014_address_like_quantity_not_split`, `prd014_prefix_quantity_still_parses`, and `prd014_explicit_multiplier_still_parses`.

---

### PRD-015: `_looks_empty_answer` rebuilds normalized set every call

- **Severity:** P3
- **Category:** Performance
- **File / function / module:** `agent/agent.py` / `_looks_empty_answer`
- **Exact code location:** Line 1745: `negative_forms = {_normalize_ar(word) for word in NEGATIVE_WORDS}`
- **Problem:** `NEGATIVE_WORDS` is a module-level constant (defined at line 1699). The set comprehension `{_normalize_ar(word) for word in NEGATIVE_WORDS}` is recomputed on every invocation. While `_normalize_ar` is LRU-cached, the set construction itself is not.
- **Why it matters:** Called on every turn for every session. Unnecessary CPU and GC pressure. Easy to fix.
- **Real failure scenario:** No crash, but under high load (100 concurrent sessions, 2 turns/second each), that's 200 set constructions per second. Each constructs a set of ~20 items. Minor but measurable CPU waste.
- **Recommended fix:** Precompute at module level:
  ```python
  _NEGATIVE_FORMS = frozenset(_normalize_ar(w) for w in NEGATIVE_WORDS)
  ```
  Then use `_NEGATIVE_FORMS` instead of `negative_forms` in the function.
- **Validation method:** Profile before and after. Verify `_looks_empty_answer` no longer allocates a set per call.
- **Test ideas:**
  - Test: `_looks_empty_answer("لا")` returns True (functional correctness preserved)
  - Test: `_looks_empty_answer("عايز كفتة")` returns False
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Added module-level `_NEGATIVE_FORMS = frozenset(_normalize_ar(word) for word in NEGATIVE_WORDS)` and reused it inside `_looks_empty_answer()` instead of rebuilding the normalized set on every call. Confirmed as a performance risk rather than a correctness bug. Existing functional test `empty_answer_handles_la_tamam`, new smoke test `prd015_negative_forms_cached`, and the full smoke suite all pass.

---

### PRD-016: Chat context truncation can drop system messages

- **Severity:** P3
- **Category:** Prompt management / context integrity
- **File / function / module:** `agent/base_agent.py` / `on_user_turn_completed`
- **Exact code location:** Lines 260-261: `turn_ctx.items[:] = turn_ctx.truncate(max_items=TURN_CHAT_CTX_MAX_ITEMS).items`
- **Problem:** `truncate(max_items=N)` keeps the last N items by position. There's no guarantee that system messages (which contain restaurant identity, menu context, flow instructions, and style directives) are preserved. After a long call (20+ turns), the initial system prompt may be truncated away.
- **Why it matters:** The LLM loses awareness of restaurant-specific instructions, menu items, flow rules, and conversation style. It may start hallucinating menu items, ignore flow constraints, or break character.
- **Real failure scenario:** A 30-turn delivery order. `TURN_CHAT_CTX_MAX_ITEMS` is (say) 30. After turn 31, the oldest items are dropped — including the initial system prompt with the restaurant name, menu, and flow instructions. The LLM now has no context about what restaurant this is. It may say "ما اسم المطعم؟" (what's the restaurant name?) or offer items not on the menu.
- **Recommended fix:** Before truncation, separate system messages from user/assistant messages. Truncate only user/assistant pairs. Re-prepend system messages:
  ```python
  system_msgs = [i for i in turn_ctx.items if i.role == "system"]
  other_msgs = [i for i in turn_ctx.items if i.role != "system"]
  if len(other_msgs) > TURN_CHAT_CTX_MAX_ITEMS - len(system_msgs):
      other_msgs = other_msgs[-(TURN_CHAT_CTX_MAX_ITEMS - len(system_msgs)):]
  turn_ctx.items[:] = system_msgs + other_msgs
  ```
- **Validation method:** Simulate a 40-turn conversation. After truncation, verify system messages are still present.
- **Test ideas:**
  - Test: 40-turn conversation → system prompt still in context after truncation
  - Test: turn guard markers still replaced (not accumulated) after truncation
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-18. Confirmed as a production prompt-integrity risk: `BaseAgent.on_user_turn_completed()` was still truncating by raw position, so durable system prompts could be evicted on long calls. Replaced blunt `truncate(max_items=...)` with `_limit_chat_ctx_preserving_system(...)` after stripping prior turn-guard / turn-cap markers, so durable system prompts remain while older non-system history is windowed. Smoke tests `prd016_system_prompt_preserved_during_truncation` and `prd016_context_window_stays_bounded` were added and the full `python smoke_tests.py` suite passed with 143 passing checks. **Remaining risk:** if durable system prompts ever outgrow `TURN_CHAT_CTX_MAX_ITEMS`, the helper now preserves the newest system prompts and drops non-system history first.

---

### PRD-017: Non-streaming TTS adds full TTFB latency to every response

- **Severity:** P2
- **Category:** Latency
- **File / function / module:** `agent/xai_tts.py` / `ChunkedStream._run`
- **Exact code location:** Lines 54-93 (entire `_run` method), line 24: `streaming=False`
- **Problem:** The x.ai TTS plugin declares `streaming=False` in `TTSCapabilities`. The `_run` method does use `resp.aiter_bytes()` (line 83) which streams the HTTP response, but the TTS capabilities declaration tells LiveKit Agents SDK that this is a non-streaming TTS. The SDK may buffer all audio before starting playback, adding the full TTS generation time as TTFB (time to first byte).
- **Why it matters:** Every agent response has a floor latency equal to the TTS generation time — typically 500ms-2s depending on text length. Users perceive this as the agent being slow to respond. Competitors with streaming TTS start playing audio within 100-200ms.
- **Real failure scenario:** User asks "المنيو إيه؟" (what's the menu?). LLM responds in 300ms. TTS generates the full audio in 1.5s. Total time before user hears first audio: 1.8s. With streaming TTS, it would be ~500ms.
- **Recommended fix:** (1) Short-term: if x.ai supports streaming responses, change `streaming=True` in TTSCapabilities and yield chunks as they arrive. (2) Medium-term: pre-generate common phrases (greetings, "تمام يا فندم", confirmations) and cache the audio. (3) Long-term: evaluate streaming-native TTS providers (ElevenLabs, Deepgram, Cartesia).
- **Validation method:** Measure TTFB for agent responses. Compare with a streaming TTS provider.
- **Test ideas:**
  - Test: measure time from LLM completion to first audio byte
  - Test: compare TTFB with cached vs. generated audio
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** This is the single largest contributor to perceived agent slowness. However, it may require a TTS provider change, which is a larger effort.

---

### PRD-018: `_WORKER_CONTEXT` global set from entrypoint without guard

- **Severity:** P2
- **Category:** Shared mutable state
- **File / function / module:** `agent/main.py` / `entrypoint`
- **Exact code location:** Line 41: `_agent._WORKER_CONTEXT = proc_context`
- **Problem:** The entrypoint mutates `agent.py`'s module-level global `_WORKER_CONTEXT` directly. There's no guard against double-setting, no assertion that it's None before setting, and a misleading `global _WORKER_CONTEXT` comment at line 35 that references a variable that doesn't exist in `main.py`'s scope.
- **Why it matters:** If the LiveKit process model changes or if `entrypoint` is called twice in the same process, the worker context could be silently replaced, causing the first session's context to become stale.
- **Real failure scenario:** A LiveKit SDK update changes the process model. Two sessions start in the same process before the first completes. The second call's `entrypoint` overwrites `_WORKER_CONTEXT`. The first session's references to `worker_context()` now return the second session's context. Shared state corruption.
- **Recommended fix:** (1) Set `_WORKER_CONTEXT` once in `_setup_worker_process` (which is already called via `setup_fnc`). (2) Add an assertion: `assert _WORKER_CONTEXT is None, "worker context already set"`. (3) Remove the mutation from `entrypoint`. (4) Remove the misleading `global _WORKER_CONTEXT` line from `main.py`.
- **Validation method:** Verify `_setup_worker_process` sets the context. Verify `entrypoint` does not override it.
- **Test ideas:**
  - Test: call `_setup_worker_process` twice → assertion error
  - Test: `entrypoint` runs without setting `_WORKER_CONTEXT`
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. (1) Removed misleading `global _WORKER_CONTEXT` from `main.py` entrypoint. (2) Changed unconditional `_agent._WORKER_CONTEXT = proc_context` to conditional `if _agent._WORKER_CONTEXT is None and isinstance(proc_context, WorkerContext)`. (3) Added pid-logging to `_setup_worker_process` for observability. Smoke test `prd018_worker_context_guard` confirms the guard is present and the stale `global` is removed.

---

### PRD-019: `merge_phone_digits` overwrites buffer on "01" prefix

- **Severity:** P2
- **Category:** Phone capture / data loss
- **File / function / module:** `agent/nlp/phone_extract.py` / `merge_phone_digits`
- **Exact code location:** Lines 39-46, specifically line 44: `if incoming.startswith("01") or incoming.startswith("20") or incoming.startswith("201")`
- **Problem:** If incoming digits start with "01", "20", or "201", the entire buffer is replaced with just the incoming digits. This is intended to handle the case where the user restarts their phone number. But it also triggers when the user happens to say digits that start with "01" as part of a continuation (e.g., saying "واحد صفر" = 10, which in phone_mode becomes "1" "0").
- **Why it matters:** Legitimate phone digit continuations that happen to start with "01" wipe the buffer. The user has to start over.
- **Real failure scenario:** User says "صفر واحد صفر واحد اتنين" (01012) across two turns. First turn: "صفر واحد صفر" → buffer = "010". Second turn: "واحد اتنين" → phone_mode digits = "12". Does not start with "01" — buffer becomes "01012". OK, this works. But consider: First turn: "صفر واحد صفر" → buffer = "010". Second turn: "صفر واحد اتنين تلاتة" → digits = "0123". Starts with "01" → buffer REPLACED with "0123", losing the "010" prefix. User now has "0123" instead of "0100123".
- **Recommended fix:** Only replace the buffer when the incoming chunk is >= 5 digits (a plausible fresh phone number start, not a short fragment): `if len(incoming) >= 5 and (incoming.startswith("01") or ...)`.
- **Validation method:** Test buffer merge with various incoming digit patterns.
- **Test ideas:**
  - Test: buffer="010", incoming="0123" → should append, not replace
  - Test: buffer="010", incoming="01012345678" → should replace (full restart)
  - Test: buffer="", incoming="01" → should set (no existing buffer)
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. `merge_phone_digits()` now replaces the buffer only when the incoming chunk is at least 5 digits and starts with a plausible phone prefix, so short fragments like `0123` no longer wipe an existing `010` buffer. Confirmed via direct reproduction with `merge_phone_digits("010", "0123")` before the fix. Smoke tests `prd019_short_prefix_chunk_appends`, `prd019_full_restart_replaces`, and `prd019_empty_buffer_sets` were added and pass.

---

### PRD-020: Tool errors may propagate raw to LLM

- **Severity:** P2
- **Category:** Error handling / user experience
- **File / function / module:** All `@function_tool` methods in `agent/flows/takeaway.py`, `delivery.py`, `reservation.py`, `complaint.py`
- **Exact code location:** Any tool that calls `_post()` — e.g., `takeaway.py` `confirm_order`, `delivery.py` `confirm_delivery`, `reservation.py` `confirm_reservation`, `complaint.py` `log_complaint`
- **Problem:** When `_post()` returns `None` (all retries exhausted, queue full), the tool methods generally handle it and return an Arabic error message. However, if an unexpected exception is raised (e.g., `KeyError` in response parsing, `TypeError` in argument handling), it propagates as the tool result. The LLM sees a Python traceback and may regurgitate it to the user.
- **Why it matters:** The user hears a technical error message in a mix of English and Arabic. This is unprofessional and confusing.
- **Real failure scenario:** Backend returns a 200 with an unexpected JSON structure. `data.get("order_id")` returns None. Later code tries `int(None)` → `TypeError`. Exception propagates to the tool result. LLM says "حصل مشكلة TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'".
- **Recommended fix:** Wrap all tool method bodies in a top-level try/except that catches `Exception` and returns a standardized Arabic error:
  ```python
  except Exception as exc:
      logger.exception("call=%s | tool error | tool=%s", ud.call_id, "confirm_order")
      return "معلش يا فندم، حصل مشكلة تقنية. ممكن نحاول تاني؟"
  ```
- **Validation method:** Force a tool to raise an unexpected exception. Verify the user hears the Arabic error, not a traceback.
- **Test ideas:**
  - Test: mock `_post` to raise `RuntimeError` → tool returns Arabic error
  - Test: mock backend response with missing fields → tool returns Arabic error
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed as a production risk: unexpected exceptions from tool bodies were not caught at the tool boundary, so they could leak back into the tool/LLM path. Added shared `_run_tool_safely(...)` in `base_agent.py`, preserved `StopResponse`, and wrapped all 25 current `@function_tool()` entry points across `base_agent.py` and all flow modules. Smoke tests `prd020_all_function_tools_wrapped`, `prd020_shared_tool_returns_arabic_error`, and `prd020_flow_tool_returns_arabic_error` were added and pass.

---

### PRD-021: `on_enter` copies full chat context on agent transfer

- **Severity:** P3
- **Category:** Context bloat / memory
- **File / function / module:** `agent/base_agent.py` / `on_enter`
- **Exact code location:** Lines 59-68
- **Problem:** On agent transfer, the previous agent's chat context (up to `PROMPT_HISTORY_ITEMS`) is copied into the new agent's context. For a long call with multiple transfers (Greeter → Delivery → back to Greeter → Takeaway), the context accumulates with each transfer. Deduplication by `item.id` (line 67-68) prevents exact duplicates but doesn't prevent near-duplicate conversation fragments from different agents.
- **Why it matters:** Gradual context bloat across transfers increases LLM token usage, latency, and cost. After 4-5 transfers in a single call, the context may contain redundant history.
- **Real failure scenario:** User goes Greeter → Delivery (20 turns) → Greeter → Takeaway (15 turns) → Greeter → Complaint. Each transfer copies up to `PROMPT_HISTORY_ITEMS` from the previous agent. By the time the user reaches Complaint, the context has accumulated fragments from all previous agents.
- **Recommended fix:** Truncate more aggressively on transfer. Keep only the last 5-10 messages from the previous agent, plus all system messages.
- **Validation method:** Simulate a 5-transfer call. Log context size at each transfer. Verify it doesn't grow unboundedly.
- **Test ideas:**
  - Test: 5 agent transfers → context size stays below 2× `PROMPT_HISTORY_ITEMS`
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-18. Confirmed as a production context-bloat risk: reused flow-agent instances were carrying prior non-system history in `self.chat_ctx`, and `on_enter()` was also copying raw previous-agent context on transfer. The fix now strips stale marked system prompts from reused agents, trims retained non-system history to `PROMPT_HISTORY_ITEMS`, and copies only the most recent non-system items from the previous agent into the next flow. Smoke tests `prd021_transfer_context_bounded` and `prd021_keeps_recent_transfer_history_only` were added and the full `python smoke_tests.py` suite passed with 143 passing checks. **Remaining risk:** the transferred history window is intentionally smaller now, so very old conversational nuance must come from structured `UserData`, not raw transcript history.

---

### PRD-022: Shared cache file not safe across multiple worker processes

- **Severity:** P2
- **Category:** Concurrency / cache integrity
- **File / function / module:** `agent/agent.py` / `_write_shared_cache_entry`, `_read_shared_cache_map`
- **Exact code location:** Lines 500-557
- **Problem:** `_write_shared_cache_entry` uses `asyncio.Lock` to guard file access. This lock only works within a single Python process. With `num_idle_processes > 1` (configured in `main.py` line 26), multiple OS processes can read and write the same shared cache file concurrently. The atomic rename via `os.replace` (line 557) prevents file corruption (reads always see a complete file), but a read that happens between another process's write and replace may see stale data.
- **Why it matters:** A session in process A may read a stale config while process B has already written a fresh one. The config TTL check mitigates this (stale configs are re-fetched), but there's a window where a process serves outdated configuration.
- **Real failure scenario:** Process A writes a fresh config at T=0. Process B reads the old file at T=0.001 (before A's `os.replace` completes). Process B serves stale config for up to `CONFIG_CACHE_TTL` seconds.
- **Recommended fix:** (1) Accept this as a benign race and document it — the TTL check handles staleness. (2) OR use OS-level file locking (`fcntl.flock` on Linux, `msvcrt.locking` on Windows) for cross-process safety.
- **Validation method:** Run 2+ worker processes. Update config via backend. Verify both processes serve fresh config within TTL window.
- **Test ideas:**
  - Test: 2 processes writing cache simultaneously → no corruption
  - Test: process reads during another's write → gets complete (old or new) data, not partial
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** The `os.replace` atomicity makes this low-risk. May be acceptable with documentation.

---

### PRD-023: `_should_add_turn_guard` always returns True for non-empty text

- **Severity:** P3
- **Category:** Prompt bloat / token waste
- **File / function / module:** `agent/agent.py` / `_should_add_turn_guard`
- **Exact code location:** Lines 2556-2561
- **Problem:** After normalizing the user text, if it's non-empty, the function unconditionally returns `True`. This means a turn guard system message is injected into every single turn's context. Each guard includes the full user text and a flow-specific instruction (~100-200 tokens).
- **Why it matters:** Extra ~100-200 tokens per turn in LLM input. Over a 30-turn call, that's 3,000-6,000 extra tokens. At $15/M tokens (Opus), that's ~$0.05-0.10 per call. At 10,000 calls/day, that's $500-1,000/day in unnecessary token costs. It also adds ~50ms per turn in LLM processing time.
- **Real failure scenario:** No crash. But cumulative cost and latency. Over 1 million calls, the unnecessary turn guards cost $5,000-10,000 in token fees.
- **Recommended fix:** Only add turn guards when: (1) the slot state changed since the last turn, (2) the user's utterance is ambiguous (doesn't clearly match the expected slot), or (3) the flow changed since the last guard. Implement by comparing `_flow_turn_guard_message` output with the previous guard and skipping if identical.
- **Validation method:** Count turn guard injections before and after the fix. Verify reduction.
- **Test ideas:**
  - Test: same flow, same slot state, two consecutive turns → second turn has no guard
  - Test: slot state changes → new guard injected
  - Test: flow changes → new guard injected
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-18. Confirmed as a production cost / latency risk: `_should_add_turn_guard()` still returned `True` for every non-empty turn, so identical guard prompts were being re-injected every turn. Added `last_guard_signature` to `UserData`, introduced `_turn_guard_signature(...)`, and updated `BaseAgent.on_user_turn_completed()` to skip guard injection when the newly generated guard matches the previous flow-aware signature. Smoke test `prd023_identical_turn_guard_skipped` was added and the full `python smoke_tests.py` suite passed with 143 passing checks. **Remaining risk:** repeated identical guards are now skipped, so any future drift must be handled by the durable flow prompts and current slot state rather than redundant per-turn guard repetition.

---

### PRD-024: `cleanup_http_client` uses deprecated `asyncio.get_event_loop()`

- **Severity:** P3
- **Category:** Deprecation / compatibility
- **File / function / module:** `agent/backend/client.py` / `cleanup_http_client`
- **Exact code location:** Line 55: `loop = asyncio.get_event_loop()`
- **Problem:** `asyncio.get_event_loop()` is deprecated in Python 3.12+ for cases where no event loop is currently running. It emits a `DeprecationWarning` and will eventually raise an error.
- **Why it matters:** The code will break on a future Python version. The function is called from `atexit`, where the event loop may or may not be running.
- **Real failure scenario:** Upgrade to Python 3.14 (which removes the deprecated behavior). `cleanup_http_client` is called at process exit. `asyncio.get_event_loop()` raises `RuntimeError`. The httpx client is not closed. Connection pool leaks.
- **Recommended fix:** Use a more robust pattern:
  ```python
  try:
      loop = asyncio.get_running_loop()
      loop.create_task(_http_client.aclose())
  except RuntimeError:
      # No running loop — create a new one for cleanup
      asyncio.run(_http_client.aclose())
  ```
- **Validation method:** Run with Python 3.12+ and verify no deprecation warnings.
- **Test ideas:**
  - Test: `cleanup_http_client` with running loop → task created
  - Test: `cleanup_http_client` with no running loop → client closed synchronously
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** Low urgency but trivial to fix.

---

### PRD-025: HTTP client singleton not fork-safe

- **Severity:** P2
- **Category:** Concurrency / process model
- **File / function / module:** `agent/backend/client.py` / `get_http_client`
- **Exact code location:** Lines 10-12: `_http_client` and `_http_client_lock` at module level
- **Problem:** `_http_client` and `_http_client_lock` are module-level globals. If the LiveKit `AgentServer` forks worker processes (via `num_idle_processes`), the child processes inherit the parent's module state. The `asyncio.Lock` is not valid across a fork (it belongs to the parent's event loop). The httpx client's connection pool is shared across forked processes, which can cause socket corruption.
- **Why it matters:** Forked processes sharing an httpx connection pool can cause: connection reuse errors, SSL state corruption, or deadlocks on the inherited lock.
- **Real failure scenario:** Parent process creates an httpx client during module import. Forks 3 worker processes. All 3 workers inherit the same httpx client with the same socket pool. Worker A sends a request on socket 5. Worker B also tries to use socket 5. SSL handshake corruption. Connection errors.
- **Recommended fix:** (1) Don't create the httpx client at import time — only in `get_http_client()` (this is already the case — good). (2) Add `os.register_at_fork(after_in_child=_reset_http_client)` to clear the singleton after fork. (3) OR rely on LiveKit's `setup_fnc` to initialize a fresh client per worker process.
- **Validation method:** Run with `num_idle_processes=3`. Verify each worker creates its own httpx client. Verify no connection pool sharing.
- **Test ideas:**
  - Test: fork → child process gets fresh httpx client
  - Test: parent's lock is not inherited by child
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-14. Added `_reset_http_client_after_fork()` function that clears `_http_client` and recreates `_http_client_lock`. Registered via `os.register_at_fork(after_in_child=...)` with a `hasattr` guard for platforms without fork support. Smoke test `prd025_fork_reset_function_exists` confirms the reset function exists.

---

### PRD-026: Fuzzy menu match threshold 0.5 too aggressive for short items

- **Severity:** P3
- **Category:** Menu matching / false positives
- **File / function / module:** `agent/agent.py` / `_resolve_menu_item`, `_token_overlap_score`
- **Exact code location:** Line 2356: `_MENU_MATCH_THRESHOLD = 0.5`, Lines 2342-2387
- **Problem:** A Jaccard-like overlap score of 0.5 means only half the tokens need to match. For single-token menu items ("كفتة", "كشري"), any menu item containing that token matches at 0.5. For two-token items, sharing one token gives a score of 0.33-0.5 depending on the other item's length. The scoring function at line 2352 uses `len(overlap) / max(len(target_tokens), len(menu_tokens))`, which penalizes partial matches on longer items — but for short items, the threshold is still too permissive.
- **Why it matters:** Ambiguous matches return whichever scores highest, with no disambiguation. The user may get the wrong item.
- **Real failure scenario:** Menu has "دجاج مشوي" and "دجاج بانيه". User says "دجاج". Tokens: {"دجاج"}. "دجاج مشوي" has tokens {"دجاج", "مشوي"} → score = 1/2 = 0.5 (matches). "دجاج بانيه" has tokens {"دجاج", "بانيه"} → score = 1/2 = 0.5 (also matches). First one in the list wins. User gets "دجاج مشوي" without being asked.
- **Recommended fix:** (1) For single-token user input matching multiple items, ask for clarification instead of picking the first. (2) Raise threshold to 0.6. (3) For items with 1 token, require exact match or score >= 0.8.
- **Validation method:** Test with ambiguous single-token inputs against a menu with multiple matching items.
- **Test ideas:**
  - Test: "دجاج" with menu ["دجاج مشوي", "دجاج بانيه"] → disambiguation prompt, not arbitrary pick
  - Test: "بيتزا مارجريتا" with menu ["بيتزا مارجريتا", "بيتزا بيبروني"] → exact match, no ambiguity
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed bug by reproduction: `_resolve_menu_item("chicken", ["chicken grilled", "chicken pane"])` arbitrarily picked the first item at score `0.5`. Added `_SHORT_MENU_MATCH_THRESHOLD = 0.8` so single-token inputs no longer fuzzy-match multi-token items while multi-token fuzzy matching keeps the existing baseline. Validated with `prd026_short_token_ambiguous_no_match`, `prd026_single_token_exact_still_matches`, and `prd026_multi_token_fuzzy_match_kept`.

---

### PRD-027: Duplicated `update_order` logic across Takeaway and Delivery

- **Severity:** P2
- **Category:** Code duplication / maintainability
- **File / function / module:** `agent/flows/takeaway.py` / `update_order` (lines 127-189) AND `agent/flows/delivery.py` / `update_order` (lines 136-199)
- **Exact code location:** Both files, ~60 lines each
- **Problem:** Both Takeaway and Delivery have their own `update_order` tool with nearly identical parsing, validation, menu resolution, and response generation logic. Only the next-step prompt differs (takeaway asks for name, delivery asks for address).
- **Why it matters:** Bug fixes applied to one flow may be missed in the other. Feature changes (e.g., improving quantity parsing) need to be made in two places.
- **Real failure scenario:** A bug fix to menu matching is applied in `takeaway.py` but forgotten in `delivery.py`. Delivery orders start failing to match items that takeaway handles correctly.
- **Recommended fix:** Extract shared order logic into a method on `BaseAgent` or a standalone helper function. Each flow calls the shared function and provides only the flow-specific next-step prompt.
- **Validation method:** After refactoring, run all smoke tests. Verify both flows produce identical results for the same order input.
- **Test ideas:**
  - Test: same order items in takeaway and delivery → identical parsing and validation results
  - Test: next-step prompts differ correctly between flows
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed maintainability risk via direct source comparison (~0.925 similarity before refactor). Added `BaseAgent._process_order_update()` and routed both flow tools through it. Validated with `prd027_shared_order_logic_consistent`, `prd027_flow_specific_delivery_minimum_preserved`, and the full smoke suite (`python smoke_tests.py`, 132 passing checks). Also see PRD-028 for the related upsell refactor.

---

### PRD-028: Duplicated upsell handling across Takeaway and Delivery

- **Severity:** P2
- **Category:** Code duplication / maintainability
- **File / function / module:** `agent/flows/takeaway.py` (lines 74-115) AND `agent/flows/delivery.py` (lines 58-134)
- **Exact code location:** Both files, ~40-60 lines each
- **Problem:** Upsell accept/reject/fallthrough logic is copy-pasted between Takeaway and Delivery. The only difference is what happens after upsell resolution (takeaway calls `_ask_name`, delivery calls `_ask_address`).
- **Why it matters:** Same as PRD-027 — changes must be made in two places.
- **Real failure scenario:** An upsell logic fix (e.g., PRD-010's normalization fix) is applied in one flow but missed in the other.
- **Recommended fix:** Move to `BaseAgent._handle_upsell()` or a shared function. Each flow provides a callback for the post-upsell step.
- **Validation method:** Run smoke tests for upsell scenarios in both flows.
- **Test ideas:**
  - Test: upsell accept in takeaway and delivery → same item added
  - Test: upsell reject in both flows → no item added, correct next step
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed maintainability risk via direct source comparison (~0.901 similarity before refactor). Added `BaseAgent._handle_pending_upsell()` with a post-upsell callback and wired takeaway/delivery through it. Validated with `prd028_shared_upsell_acceptance_state`, `prd028_upsell_followup_remains_flow_specific`, and the full smoke suite (`python smoke_tests.py`, 132 passing checks).

---

### PRD-029: Observability — missing telemetry events for critical paths

- **Severity:** P2
- **Category:** Observability
- **File / function / module:** Multiple files
- **Exact code location:** 13 `_emit_event` calls exist across 7 files (see grep results)
- **Problem:** The following critical decision points have NO telemetry events:
  - Agent transfers / flow transitions (Greeter → Delivery, etc.)
  - Upsell offered / accepted / rejected
  - Phone capture success / failure / buffer state
  - Name capture success / failure
  - Circuit breaker open / close transitions
  - Write queue fallback (direct write → queue → recovery file)
  - Config cache hit / miss / refresh
  - Inactivity reprompt / timeout
  - Turn guard injection (what guard was added and why)
- **Why it matters:** Production debugging requires parsing unstructured logs. No structured data for dashboards, alerts, or analytics. Cannot answer basic questions like "what % of calls use upsell?" or "how often does the circuit breaker open?"
- **Real failure scenario:** A spike in customer complaints. Ops tries to determine if the issue is STT quality, phone capture failures, or backend errors. There's no structured data to query — they have to grep through text logs.
- **Recommended fix:** Add `_emit_event()` calls at each of the listed decision points. Target: 30+ event types for comprehensive coverage.
- **Validation method:** After adding events, run a test call. Verify all expected events are emitted in the correct sequence.
- **Test ideas:**
  - Test: complete order flow → events for: call.start, turn.received (×N), tool.called (×N), flow.transfer, upsell.offered, upsell.accepted, order.confirmed, call.end
  - Test: failed backend → events for: circuit.failure, queue.fallback
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-18. Confirmed as a real observability gap. Expanded structured telemetry coverage across `agent/agent.py`, `agent/base_agent.py`, and `agent/main.py`. Existing Phase 8 groundwork already covered `config.cache`, `backend.circuit`, `backend.queue`, `phone.capture`, `name.capture`, and `upsell.offer`; this pass added the missing critical-path hooks for `flow.transfer`, `upsell.accepted`, `upsell.rejected`, `turn.guard`, and `call.inactivity`. Validation: smoke tests `prd029_event_hooks_present` and `prd029_transfer_and_upsell_events_emitted`, plus the full smoke suite (`python smoke_tests.py`, 138 passing checks). Remaining risk is operational rather than code-level: dashboards and alerts still need to be wired up in the deployment environment.

---

### PRD-030: `_voice_safe_text` truncation at character boundary may cut mid-word

- **Severity:** P3
- **Category:** User experience / TTS quality
- **File / function / module:** `agent/utils/voice.py` / `_voice_safe_text`
- **Exact code location:** Line 25: `cleaned = cleaned[: max_chars - 1].rstrip(" ،,.") + "…"`
- **Problem:** Truncation at `max_chars` characters may cut in the middle of an Arabic word. The `rstrip` only removes trailing punctuation, not the truncated partial word.
- **Why it matters:** TTS will try to pronounce a partial Arabic word, producing garbled audio.
- **Real failure scenario:** A 150-character response. `max_chars=120`. Truncation cuts "الشاور" (partial "الشاورما"). TTS pronounces "الشاور" as a nonsense syllable.
- **Recommended fix:** After character truncation, find the last space and truncate there:
  ```python
  truncated = cleaned[:max_chars - 1]
  last_space = truncated.rfind(" ")
  if last_space > max_chars // 2:  # don't cut too much
      truncated = truncated[:last_space]
  cleaned = truncated.rstrip(" ،,.") + "…"
  ```
- **Validation method:** Pass a long Arabic text through `_voice_safe_text`. Verify the result ends at a word boundary.
- **Test ideas:**
  - Test: 150-char text → truncated at word boundary
  - Test: text exactly at max_chars → no truncation
  - Test: text with all short words → truncated correctly
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed UX bug by direct output inspection: `_voice_safe_text()` could end on a partial token before the ellipsis. Changed truncation to prefer the last word boundary when it does not over-shorten the response. Validated with `prd030_truncates_at_word_boundary` and `prd030_exact_limit_unchanged`.

---

### PRD-031: `_opening` and `_turn_responded` as class-level defaults

- **Severity:** P3
- **Category:** Python semantics / shared state risk
- **File / function / module:** `agent/base_agent.py` / `BaseAgent`
- **Exact code location:** Lines 24-25: `_opening: str = ""` and `_turn_responded: bool = False`
- **Problem:** `_opening` and `_turn_responded` are defined as class-level attributes, not instance attributes. For immutable types (str, bool), this is safe in practice — Python creates instance attributes on first assignment. But it's a code smell: a reader might expect class-level mutation semantics. If either were changed to a mutable type (list, dict) in the future, all instances would share the same object.
- **Why it matters:** Maintenance trap. A future developer might add `_pending_items: list = []` at class level, creating a shared-state bug.
- **Real failure scenario:** No current failure. Future risk.
- **Recommended fix:** Move to `__init__`:
  ```python
  def __init__(self, **kwargs):
      super().__init__(**kwargs)
      self._opening = ""
      self._turn_responded = False
  ```
  OR document the pattern with a comment: `# Class-level defaults — safe for immutable types only`.
- **Validation method:** Code review.
- **Test ideas:**
  - Test: two concurrent BaseAgent instances → `_turn_responded` is independent
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed as a preventive maintenance risk rather than a live bug. Added `BaseAgent.__init__()` so `_opening` and `_turn_responded` are always instance-scoped while preserving subclass openings set before `super().__init__()`. Validated with `prd031_instance_defaults_are_instance_scoped` and the full smoke suite (`python smoke_tests.py`, 132 passing checks).

---

### PRD-032: Greeter has no `update_name`/`update_phone` in tools list

- **Severity:** P3
- **Category:** Missing tools / flow coverage
- **File / function / module:** `agent/flows/greeter.py` / `Greeter.__init__`
- **Exact code location:** Line 60: `tools=[get_menu]`
- **Problem:** Greeter only has `get_menu` in its explicit tools list. The routing tools (`to_takeaway`, `to_delivery`, etc.) are auto-discovered as `@function_tool()` methods on the class. But `update_name` and `update_phone` (defined in `base_agent.py`) are NOT included. If a user provides their name or phone number while still in the Greeter flow, the LLM cannot use these tools.
- **Why it matters:** Users sometimes volunteer their name or phone number early in the conversation ("أنا أحمد، عايز أطلب أكل"). The Greeter can't capture this information — it's lost. The user will be asked again in the order flow.
- **Real failure scenario:** User says "أنا أحمد عايز توصيل". Greeter recognizes the delivery intent and routes. But "أحمد" is not captured. Delivery flow asks "ممكن اسم حضرتك؟". User says "قلتلك أحمد!" — frustrated repeat.
- **Recommended fix:** Add `update_name` and `update_phone` to the Greeter's tools list: `tools=[get_menu, update_name, update_phone]`.
- **Validation method:** In Greeter flow, provide name and phone. Verify they're captured.
- **Test ideas:**
  - Test: user says name in Greeter → name captured
  - Test: user says phone in Greeter → phone captured
  - Test: routing still works after adding tools
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed as a real coverage bug. Added `update_name` and `update_phone` to the Greeter tools list, and added a narrow inline prefill path so explicit self-introductions like "انا احمد ورقمي ... وعايز توصيل" are captured before routing. Verification also confirmed `_flow_missing_name("greeter", ud)` / `_flow_missing_phone("greeter", ud)` remain false by design, so the fix does not rely on the generic intercept path. Validated with `prd032_greeter_tools_include_contact_tools`, `prd032_greeter_prefills_contact_before_routing`, and the full smoke suite (`python smoke_tests.py`, 132 passing checks).

---

### PRD-033: Idempotency key based on payload hash — not resilient to order modifications

- **Severity:** P2
- **Category:** Idempotency / data integrity
- **File / function / module:** `agent/agent.py` / `_idempotency_key`
- **Exact code location:** Lines 1689-1694
- **Problem:** `_idempotency_key` is `f"{call_id}-{action}-{sha256(sorted_json_payload)[:16]}"`. If the user modifies their order (adds/removes items) and then confirms again, the payload changes → different hash → different idempotency key. This is correct. But if the user confirms the SAME order twice (e.g., double-tap or LLM retry), the same key is generated → backend de-duplicates → correct. The actual risk: if the backend does NOT implement idempotency checking, the key is meaningless. And if it DOES, the 16-char hash truncation gives a collision probability of ~1 in 2^64, which is fine.
- **Why it matters:** The current implementation is actually correct for its stated purpose. The real gap is: does the backend actually check the `Idempotency-Key` header? If not, this is security theater.
- **Real failure scenario:** Backend doesn't check idempotency keys. Network retry sends the same `confirm_order` twice. Both succeed. Customer gets charged twice / receives duplicate order.
- **Recommended fix:** (1) Verify the backend checks `Idempotency-Key`. (2) Add a turn number or monotonic counter to the key to prevent reuse across retries with modified payloads. (3) Document the idempotency contract between agent and backend.
- **Validation method:** Send the same request with the same idempotency key twice. Verify the backend returns the cached response for the second request.
- **Test ideas:**
  - Test: same payload + same call_id + same action → same key
  - Test: modified payload → different key
  - Test: backend receives duplicate key → returns cached response
- **Status:** Disproven
- **Resolution type:** Verified existing behavior
- **Owner:** Unassigned
- **Notes:** Disproven 2026-04-18. The suspected backend gap is not present. Direct inspection confirmed that `backend/main.py` already accepts `Idempotency-Key` on the order, reservation, and complaint endpoints and checks existing rows by `idempotency_key` before insert. Model definitions also already enforce unique constraints on `idempotency_key`. The agent-side payload-hash key remains a reasonable request identity mechanism for retry de-duplication, so no backend code change was needed. Validation: smoke tests `prd033_backend_checks_idempotency_header` and `prd033_backend_enforces_idempotency_uniqueness`, plus the full smoke suite (`python smoke_tests.py`, 138 passing checks).

---

### PRD-034: No health check endpoint for orchestrator monitoring

- **Severity:** P2
- **Category:** Observability / operations
- **File / function / module:** `agent/main.py` (missing feature)
- **Exact code location:** N/A — feature doesn't exist
- **Problem:** `RuntimeHealth` is tracked in `WorkerContext` but not exposed via any HTTP endpoint. If the agent process is alive but the backend connection is dead (all circuits open), there's no way for an orchestrator (Kubernetes, systemd, etc.) to detect the degraded state and restart/replace the process.
- **Why it matters:** A zombie agent process — running but unable to complete orders — continues accepting calls and failing them.
- **Real failure scenario:** Backend DNS changes. Agent's cached DNS is stale. All HTTP calls fail. Circuits open. Agent is "alive" (LiveKit sees it) but unable to process any orders. New calls are routed to this agent and fail. No alert triggers because the process health check (if any) only checks "is the process alive?", not "can it serve requests?".
- **Recommended fix:** Add a simple HTTP endpoint (e.g., `/healthz`) that returns: `{"status": "ok/degraded/unhealthy", "active_sessions": N, "circuits_open": [...], "config_available": bool}`. Use LiveKit's built-in health check mechanism if available.
- **Validation method:** Kill the backend. Verify `/healthz` returns `degraded` or `unhealthy`. Restart the backend. Verify it returns `ok`.
- **Test ideas:**
  - Test: healthy state → 200 OK
  - Test: all circuits open → 503 degraded
  - Test: config unavailable → 503 degraded
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-18. Added a lightweight parent-process `/healthz` endpoint in new module `agent/health.py`, started from `agent/main.py` only outside worker child processes. Added worker health snapshots and `build_agent_health_report()` in `agent/agent.py` so the endpoint reports active sessions, circuit state, config availability, queue backlog, and LiveKit connection health. Snapshot writes were hardened with Windows-safe locking and replace retries because the first implementation exposed a real file-replacement race during smoke validation. Validation: smoke tests `prd034_health_report_states` and `prd034_health_endpoint_serves_json`, plus the full smoke suite (`python smoke_tests.py`, 138 passing checks).

---

### PRD-035: Turn cap at `MAX_TURNS_PER_SESSION` hard-cuts without graceful completion

- **Severity:** P2
- **Category:** User experience / reliability
- **File / function / module:** `agent/base_agent.py` / `on_user_turn_completed`
- **Exact code location:** Lines 238-240
- **Problem:** When `turn_num > MAX_TURNS_PER_SESSION`, the agent says "معلش يا فندم، المكالمة طولت" and raises `StopResponse`. The call is effectively terminated. If the user was mid-order (items collected, name collected, just needs phone for confirmation), all collected data is lost.
- **Why it matters:** Abruptly cutting a call with nearly-complete data is a bad user experience. The user has to start over.
- **Real failure scenario:** User is on turn 49 of 50. They've given their order, name, and address. The agent asks for their phone number. User says "01012345678". Turn 51. Cap triggers. Agent says "المكالمة طولت". Call ends. Order is lost. Customer calls back angry.
- **Recommended fix:** When approaching the turn cap (e.g., at `MAX_TURNS - 5`), inject a warning. At the cap, check if the order/reservation is nearly complete (all slots except phone) — if so, allow 3 more turns to complete. Only hard-cut if the call is genuinely going nowhere.
- **Validation method:** Simulate a call at turn 48 with a nearly-complete order. Verify the agent warns but allows completion.
- **Test ideas:**
  - Test: turn 45 → warning message injected
  - Test: turn 50 with complete order → allow confirmation
  - Test: turn 50 with no data → hard cut
- **Status:** Fixed
- **Resolution type:** Code change
- **Owner:** Unassigned
- **Notes:** Fixed 2026-04-15. Confirmed reliability bug by direct turn-path verification: the cap branch always called `_say_and_stop(...)`, even when only the phone number or final confirmation was missing. Added a warning-zone system note, a near-completion grace window via `TURN_CAP_GRACE_TURNS`, and preserved the hard cut for stalled calls. Validation covered `prd035_turn_cap_warning_note`, `prd035_turn_cap_grace_allows_near_complete`, `prd035_turn_cap_hard_cuts_stalled_call`, and `prd035_turn_cap_grace_expires`.

---

### PRD-036: `xai_tts.py` uses its own httpx client without lifecycle management

- **Severity:** P3
- **Category:** Resource management
- **File / function / module:** `agent/xai_tts.py` / `TTS.__init__`
- **Exact code location:** Lines 32-38: `self._client = httpx.AsyncClient(...)`
- **Problem:** The TTS class creates its own `httpx.AsyncClient` in `__init__`. While it has an `aclose()` method (line 45-46), there's no guarantee it's called. If the LiveKit session cleanup doesn't call `tts.aclose()`, the httpx client leaks.
- **Why it matters:** Leaked httpx clients mean leaked connection pools and file descriptors. Under high load (100+ concurrent sessions), this can exhaust system resources.
- **Real failure scenario:** 500 calls per hour. Each creates a TTS instance with an httpx client. If `aclose()` is not called on session end, 500 httpx clients accumulate. Each holds ~10 keepalive connections. 5,000 open sockets. System hits `ulimit` file descriptor cap. New calls fail.
- **Recommended fix:** (1) Verify LiveKit Agents SDK calls `tts.aclose()` on session end. (2) Add a `__del__` fallback that schedules cleanup. (3) OR share a single httpx client across TTS instances (like `backend/client.py` does).
- **Validation method:** Run 100 calls. Check open file descriptors. Verify they don't accumulate.
- **Test ideas:**
  - Test: session end → TTS client closed
  - Test: 100 sequential calls → no FD leak
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** Need to verify LiveKit SDK behavior. May be a non-issue if the SDK handles cleanup.

---

### PRD-037: Config round-trip (`_config_to_dict` / `_config_from_dict`) field completeness

- **Severity:** P3
- **Category:** Data integrity / cache
- **File / function / module:** `agent/agent.py` / `_config_to_dict`, `_config_from_dict`
- **Exact code location:** Lines 452-497
- **Problem:** These functions manually map `RestaurantConfig` fields to/from dict. If a new field is added to `RestaurantConfig` but not to these functions, it will be lost during cache serialization. There's no automated check for field coverage.
- **Why it matters:** A new config field (e.g., `allow_special_requests: bool`) added to `RestaurantConfig` but forgotten in `_config_to_dict` will be lost when the config is read from cache. The agent may use default values instead of the actual configuration.
- **Real failure scenario:** Developer adds `special_instructions_enabled: bool = True` to `RestaurantConfig`. Adds it to the backend response parsing. Forgets to add it to `_config_to_dict`/`_config_from_dict`. Fresh config works. Cached config silently uses the default value. Some sessions see the correct config, others don't, depending on cache state.
- **Recommended fix:** (1) Use `dataclasses.asdict(cfg)` and `RestaurantConfig(**data)` instead of manual mapping. (2) OR add a test that verifies all `RestaurantConfig` fields appear in `_config_to_dict`.
- **Validation method:** Add a field to `RestaurantConfig`. Run the roundtrip test. Verify it catches the missing field.
- **Test ideas:**
  - Test: `_config_from_dict(_config_to_dict(cfg))` produces an equivalent config
  - Test: all fields of `RestaurantConfig` are present in `_config_to_dict` output
- **Status:** Open
- **Resolution type:** Unresolved
- **Owner:** Unassigned
- **Notes:** The current mapping looks complete for the existing fields. This is a preventive measure.

---

---

## Full Pre-Production Checklist

Every issue must be checked off before production launch.

- [x] **PRD-001** — Fix `if`/`if` fall-through in `_handle_quick_intercepts` (P0) — Fixed 2026-04-14
- [x] **PRD-002** — Fix `if`/`if` fall-through in `_handle_post_completion` (P1) — Fixed 2026-04-14
- [ ] **PRD-003** — Remove hardcoded OTP bypass default (P0)
- [x] **PRD-004** — Add locks to circuit breaker state mutations (P1)
- [x] **PRD-005** — Wrap `path.exists()` in `asyncio.to_thread` at 5 locations (P1)
- [x] **PRD-006** — Wrap `_ensure_parent_dir` in `asyncio.to_thread` (P2)
- [x] **PRD-007** — Raise `is_phone_like_text` threshold to >=5 digits (P1) — Fixed 2026-04-14
- [ ] **PRD-008** — Restrict CORS to explicit allowlist (P1)
- [x] **PRD-009** — Guard `_handle_phone_intercept` against empty reply (P2) — Fixed 2026-04-14
- [x] **PRD-010** — Use `_normalize_ar` in upsell comparison (P2) — Fixed 2026-04-14
- [x] **PRD-011** — Add idempotency and cap to recovery file (P2) — Fixed 2026-04-15
- [x] **PRD-012** — Add explicit timeout to `_post()` HTTP calls (P2) — Fixed 2026-04-15
- [x] **PRD-013** — Add lock or safety comment for turn count mutations (P2)
- [x] **PRD-014** — Validate `_parse_order_item` regex against menu (P2) — Fixed 2026-04-15
- [x] **PRD-015** — Precompute `_NEGATIVE_FORMS` at module level (P3) — Fixed 2026-04-14
- [x] **PRD-016** — Preserve system messages during context truncation (P3) — Fixed 2026-04-18
- [ ] **PRD-017** — Evaluate streaming TTS or pre-cache common phrases (P2)
- [x] **PRD-018** — Guard `_WORKER_CONTEXT` against double-set (P2)
- [x] **PRD-019** — Fix `merge_phone_digits` buffer overwrite threshold (P2) — Fixed 2026-04-14
- [x] **PRD-020** — Add top-level try/except to all tool methods (P2) — Fixed 2026-04-15
- [x] **PRD-021** — Limit context copy on agent transfer (P3) — Fixed 2026-04-18
- [ ] **PRD-022** — Document or fix shared cache cross-process safety (P2)
- [x] **PRD-023** — Conditionally inject turn guards (slot-change only) (P3) — Fixed 2026-04-18
- [ ] **PRD-024** — Replace deprecated `asyncio.get_event_loop()` (P3)
- [x] **PRD-025** — Add fork-safety to HTTP client singleton (P2)
- [x] **PRD-026** — Raise fuzzy match threshold for short items (P3) — Fixed 2026-04-15
- [x] **PRD-027** — Extract shared `update_order` logic (P2) — Fixed 2026-04-15
- [x] **PRD-028** — Extract shared upsell handling (P2) — Fixed 2026-04-15
- [x] **PRD-029** — Add telemetry events for critical paths (P2) — Fixed 2026-04-18
- [x] **PRD-030** — Fix `_voice_safe_text` to truncate at word boundary (P3) — Fixed 2026-04-15
- [x] **PRD-031** — Move class-level defaults to `__init__` (P3) — Fixed 2026-04-15
- [x] **PRD-032** — Add `update_name`/`update_phone` to Greeter tools (P3) — Fixed 2026-04-15
- [x] **PRD-033** — Verify backend idempotency key checking (P2) — Disproven 2026-04-18
- [x] **PRD-034** — Add health check endpoint (P2) — Fixed 2026-04-18
- [x] **PRD-035** — Add graceful turn cap with near-completion allowance (P2) — Fixed 2026-04-15
- [ ] **PRD-036** — Verify TTS client lifecycle / prevent FD leak (P3)
- [ ] **PRD-037** — Add config round-trip field coverage test (P3)

---

## Fix Order

> **Important:** Ordering is for execution sequencing ONLY. It does NOT imply that lower-numbered items can be skipped. ALL items must be resolved.

### Batch 1 — Ship-Stoppers (fix immediately)
1. PRD-003 — OTP bypass (security, 5 min)
2. PRD-008 — CORS restriction (security, 5 min)
3. PRD-001 — Quick intercept fall-through (correctness, 10 min)
4. PRD-002 — Post-completion fall-through (correctness, 5 min)

### Batch 2 — Concurrency & I/O Safety
5. PRD-004 — Circuit breaker locks (20 min)
6. PRD-005 — Blocking `path.exists()` (15 min)
7. PRD-006 — Blocking `_ensure_parent_dir` (5 min)
8. PRD-013 — Turn count lock/comment (5 min)
9. PRD-025 — HTTP client fork safety (15 min)

### Batch 3 — Phone/NLP Fixes
10. PRD-007 — Phone false-positive threshold (10 min)
11. PRD-009 — Phone intercept empty reply (10 min)
12. PRD-019 — Phone buffer overwrite threshold (10 min)
13. PRD-010 — Upsell normalization (5 min)
14. PRD-015 — Precompute negative forms (5 min)

### Batch 4 — Error Handling & Reliability
15. PRD-020 — Tool error catch-all (30 min)
16. PRD-011 — Recovery file hardening (30 min)
17. PRD-012 — HTTP timeout enforcement (15 min)
18. PRD-018 — Worker context guard (10 min)

### Batch 5 — User Experience & Parsing
19. PRD-014 — Order item regex validation (15 min)
20. PRD-026 — Fuzzy match threshold (10 min)
21. PRD-030 — Voice truncation at word boundary (10 min)
22. PRD-035 — Graceful turn cap (30 min)

### Batch 6 — Code Quality & Duplication
23. PRD-027 — Extract shared update_order (60 min)
24. PRD-028 — Extract shared upsell handling (30 min)
25. PRD-031 — Class-level defaults to init (10 min)
26. PRD-032 — Greeter tools list (5 min)

### Batch 7 — Observability & Operations
27. PRD-029 — Telemetry expansion (120 min)
28. PRD-034 — Health check endpoint (60 min)
29. PRD-033 — Backend idempotency verification (30 min)

### Batch 8 — Context & Performance
30. PRD-016 — Smart context truncation (30 min)
31. PRD-021 — Transfer context limiting (20 min)
32. PRD-023 — Conditional turn guards (30 min)

### Batch 9 — Infrastructure & Compatibility
33. PRD-017 — TTS streaming evaluation (240 min)
34. PRD-022 — Cross-process cache documentation (15 min)
35. PRD-024 — Deprecated asyncio fix (10 min)
36. PRD-036 — TTS client lifecycle check (20 min)
37. PRD-037 — Config round-trip test (15 min)

---

## Validation Queue

| Issue ID | Validation Method | Automated? |
|----------|-------------------|------------|
| PRD-001 | Unit test: two intercept conditions true → one response | Yes |
| PRD-002 | Unit test: post-completion with thanks + confirmation → one response | Yes |
| PRD-003 | Integration test: deploy with no ENVIRONMENT → OTP bypass disabled | Yes |
| PRD-004 | Stress test: 50 concurrent failing requests → circuit opens | Yes |
| PRD-005 | Profile: mock slow filesystem → event loop not blocked | Yes |
| PRD-006 | Code review: verify `_ensure_parent_dir` uses `to_thread` | Manual |
| PRD-007 | Unit test: order text with 2 number words → no phone intercept | Yes |
| PRD-008 | Integration test: cross-origin request from random domain → blocked | Yes |
| PRD-009 | Unit test: partial phone digits → user hears acknowledgment | Yes |
| PRD-010 | Unit test: order with taa-marbuta variant → no duplicate upsell | Yes |
| PRD-011 | Test: multiple restarts during outage → no duplicate recovery items | Yes |
| PRD-012 | Test: mock slow backend → timeout at configured threshold | Yes |
| PRD-013 | Code review: verify no `await` between read and write | Manual |
| PRD-014 | Unit test: address-like input → no phantom quantity | Yes |
| PRD-015 | Profile: `_looks_empty_answer` → no set allocation per call | Yes |
| PRD-016 | Test: 40-turn conversation → system messages preserved | Yes |
| PRD-017 | Measure: TTFB before/after streaming change | Manual |
| PRD-018 | Test: double-set of `_WORKER_CONTEXT` → assertion error | Yes |
| PRD-019 | Test: buffer="010", incoming="0123" → append not replace | Yes |
| PRD-020 | Test: mock tool exception → Arabic error returned | Yes |
| PRD-021 | Test: 5 transfers → context size bounded | Yes |
| PRD-022 | Test: 2 processes writing cache → no corruption | Yes |
| PRD-023 | Metric: turn guard injection count before/after | Yes |
| PRD-024 | Run with Python 3.12+ → no deprecation warnings | Yes |
| PRD-025 | Test: fork → child has fresh httpx client | Yes |
| PRD-026 | Test: ambiguous single-token input → disambiguation | Yes |
| PRD-027 | Smoke tests: same order in takeaway/delivery → same result | Yes |
| PRD-028 | Smoke tests: upsell in both flows → identical behavior | Yes |
| PRD-029 | Test call: verify all expected events emitted | Yes |
| PRD-030 | Test: long Arabic text → truncated at word boundary | Yes |
| PRD-031 | Test: two instances → independent `_turn_responded` | Yes |
| PRD-032 | Test: name in Greeter → captured | Yes |
| PRD-033 | Source inspection smoke test: confirm header lookup + uniqueness constraints on order/reservation/complaint paths | Yes |
| PRD-034 | Test: kill backend → healthz returns degraded | Yes |
| PRD-035 | Test: turn 49 with complete order → allowed to confirm | Yes |
| PRD-036 | Test: 100 calls → no FD leak | Yes |
| PRD-037 | Test: round-trip all config fields → equality check | Yes |

---

## Open Questions / Needs Manual Verification

These issues need confirmation from team members or external system inspection. They remain tracked until resolved.

1. **PRD-025:** Does LiveKit `AgentServer` use `fork()` or `spawn()` for worker processes? If `spawn()`, the HTTP client fork-safety issue is a non-issue. → Check LiveKit Agents SDK source or documentation.

2. **PRD-036:** Does the LiveKit Agents SDK call `tts.aclose()` on session end? → Check SDK source or run a test with FD monitoring.

3. **PRD-017:** Does x.ai TTS API support streaming responses? The current code uses `resp.aiter_bytes()` which suggests the HTTP response is streamed, but `TTSCapabilities(streaming=False)` tells the SDK it's not streaming. → Test whether changing to `streaming=True` works.

4. **PRD-022:** Is `os.replace()` truly atomic on Windows (the current deployment platform per `win32`)? POSIX guarantees atomicity, but Windows behavior may differ. → Verify Windows `os.replace` semantics.

---

## Progress Log

### 2026-04-18 (Phase 9)

- **reviewed:** Phase 9 (Context & Performance) — PRD-016, PRD-021, PRD-023
- **fixed:** PRD-016 (system-message-aware chat-context truncation), PRD-021 (bounded transfer-context carryover), PRD-023 (flow-aware guard-signature dedupe)
- **validated:** Full `python smoke_tests.py` passed with 143 passing checks. New Phase 9 tests: `prd016_system_prompt_preserved_during_truncation`, `prd016_context_window_stays_bounded`, `prd021_transfer_context_bounded`, `prd021_keeps_recent_transfer_history_only`, `prd023_identical_turn_guard_skipped`
- **still open:** PRD-003, PRD-008, PRD-017, PRD-022, PRD-024, PRD-036–PRD-037 (7 issues remain)
- **verification notes:** PRD-016 and PRD-021 were confirmed production context risks through direct tracing of `BaseAgent.on_user_turn_completed()` and `BaseAgent.on_enter()`. PRD-023 was confirmed as a token-cost / latency risk because identical turn guards were still injected on every non-empty turn. The final fixes were kept intentionally narrow: preserve durable system prompts, bound transfer history to recent non-system items, and skip only identical flow-aware guard prompts.

### 2026-04-18 (Phase 8)

- **reviewed:** Phase 8 (Observability & Operations) — PRD-029, PRD-033, PRD-034
- **fixed:** PRD-029 (critical-path telemetry expansion), PRD-034 (parent-process `/healthz` endpoint + worker health aggregation)
- **disproven:** PRD-033 — backend idempotency checking was already implemented; no code change required
- **validated:** Full `python smoke_tests.py` passed with 138 passing checks. New Phase 8 tests: `prd029_event_hooks_present`, `prd029_transfer_and_upsell_events_emitted`, `prd033_backend_checks_idempotency_header`, `prd033_backend_enforces_idempotency_uniqueness`, `prd034_health_report_states`, `prd034_health_endpoint_serves_json`
- **still open:** PRD-003, PRD-008, PRD-016–PRD-017, PRD-021–PRD-024, PRD-036–PRD-037 (10 issues remain)
- **verification notes:** PRD-029 was a confirmed observability gap. PRD-034 was a confirmed production operations gap and also exposed a Windows snapshot-write race during validation; that race was fixed as part of the final implementation. PRD-033 was disproven after direct source inspection of `backend/main.py` confirmed `Idempotency-Key` handling and uniqueness constraints on all three submission paths.

### 2026-04-15 (Phase 7)

- **reviewed:** Phase 7 (Code Quality & Duplication) — PRD-027, PRD-028, PRD-031, PRD-032
- **fixed:** PRD-027 (shared order-update helper), PRD-028 (shared upsell helper), PRD-031 (instance-scoped BaseAgent defaults), PRD-032 (Greeter tools + inline prefill before routing)
- **validated:** All 4 fixes validated via the full smoke suite with 132 passing checks. New Phase 7 tests: `prd027_shared_order_logic_consistent`, `prd027_flow_specific_delivery_minimum_preserved`, `prd028_shared_upsell_acceptance_state`, `prd028_upsell_followup_remains_flow_specific`, `prd031_instance_defaults_are_instance_scoped`, `prd032_greeter_tools_include_contact_tools`, `prd032_greeter_prefills_contact_before_routing`
- **still open:** PRD-003, PRD-008, PRD-016–PRD-017, PRD-021–PRD-024, PRD-029, PRD-033–PRD-034, PRD-036–PRD-037 (13 issues remain)
- **verification notes:** PRD-027 and PRD-028 were confirmed maintainability risks through direct source comparison before refactoring. PRD-031 was confirmed as a preventive shared-state risk, not a live incident. PRD-032 was confirmed as a real coverage bug: the generic Greeter intercept path stayed inactive because `_flow_missing_name("greeter", ud)` / `_flow_missing_phone("greeter", ud)` remain false, so the final fix uses tool coverage plus explicit inline prefill before routing.

### 2026-04-15 (Phase 6)

- **reviewed:** Phase 6 (User Experience & Parsing) — PRD-014, PRD-026, PRD-030, PRD-035
- **fixed:** PRD-014 (address-like quantity sanity check), PRD-026 (single-token fuzzy threshold), PRD-030 (word-boundary voice truncation), PRD-035 (turn-cap warning + near-completion grace)
- **validated:** All 4 fixes validated via the full smoke suite with 122 passing checks. New Phase 6 tests: `prd014_address_like_quantity_not_split`, `prd014_prefix_quantity_still_parses`, `prd014_explicit_multiplier_still_parses`, `prd026_short_token_ambiguous_no_match`, `prd026_single_token_exact_still_matches`, `prd026_multi_token_fuzzy_match_kept`, `prd030_truncates_at_word_boundary`, `prd030_exact_limit_unchanged`, `prd035_turn_cap_warning_note`, `prd035_turn_cap_grace_allows_near_complete`, `prd035_turn_cap_hard_cuts_stalled_call`, `prd035_turn_cap_grace_expires`
- **still open:** PRD-003, PRD-008, PRD-016–PRD-017, PRD-021–PRD-024, PRD-029, PRD-033–PRD-034, PRD-036–PRD-037 (13 issues remain)
- **verification notes:** PRD-014, PRD-026, and PRD-030 were confirmed bugs via direct reproduction. PRD-035 was confirmed as a live reliability bug: the old cap path always hard-cut the turn once the threshold was reached. The Phase 6 fixes were kept intentionally narrow to avoid changing unrelated flow logic.

### 2026-04-15 (Phase 5)

- **reviewed:** Phase 5 (Error Handling & Reliability) — PRD-020, PRD-011, PRD-012
- **fixed:** PRD-020 (tool-level catch-all safety wrapper), PRD-011 (recovery file idempotency/dedupe/cap), PRD-012 (explicit `_post()` timeout override)
- **validated:** All 3 fixes validated via the current smoke suite with 110 passing checks. New Phase 5 tests: `prd012_post_uses_explicit_timeout`, `prd011_recovery_file_dedupes_same_item`, `prd011_recovery_cap_applies`, `prd011_replay_dedupes_before_submit`, `prd020_all_function_tools_wrapped`, `prd020_shared_tool_returns_arabic_error`, `prd020_flow_tool_returns_arabic_error`
- **still open:** PRD-003, PRD-008, PRD-014, PRD-016–PRD-017, PRD-021–PRD-024, PRD-026–PRD-037 (21 issues remain)
- **verification notes:** PRD-011 was a confirmed bug; the file format already contained enough data to recompute a stable idempotency key, but durable storage/dedupe/cap enforcement were missing. PRD-012 was confirmed as a risk rather than a raw timeout bug because `_post()` already inherited a shared client timeout; the fix makes the timeout explicit and overridable at the call boundary. PRD-020 was confirmed as a production risk and fixed with a shared wrapper that preserves `StopResponse` while converting unexpected tool exceptions into a safe Arabic fallback.

### 2026-04-14 (Phase 4)

- **reviewed:** Phase 4 (Phone & NLP Fixes) — PRD-007, PRD-010, PRD-015, PRD-019
- **fixed:** PRD-007 (phone false-positive threshold), PRD-010 (upsell Arabic normalization), PRD-015 (precomputed negative forms cache), PRD-019 (phone buffer overwrite threshold)
- **validated:** All 4 fixes validated via the full smoke suite with 100 passing tests (92 previous + 8 new Phase 4 tests). New Phase 4 tests: `prd007_order_numbers_not_phone_like`, `prd007_digit_quantities_not_phone_like`, `prd019_short_prefix_chunk_appends`, `prd019_full_restart_replaces`, `prd019_empty_buffer_sets`, `prd015_negative_forms_cached`, `prd010_upsell_normalizes_same_item`, `prd010_upsell_still_offers_different_item`
- **still open:** PRD-003, PRD-008, PRD-011–PRD-012, PRD-014, PRD-016–PRD-017, PRD-020–PRD-024, PRD-026–PRD-037 (24 issues remain)
- **verification notes:** PRD-007 was confirmed as a real bug, but the original report example with an attached conjunction was too narrow; the validated reproductions were `اتنين كفتة و تلاتة كباب` and `2 كفتة 3 كباب`. PRD-015 was confirmed as a performance risk rather than a correctness bug. PRD-010 was validated using normalization-equivalent variants already supported by `_normalize_ar()`, such as `كبدة` / `كبده`.

### 2026-04-14 (Phase 3)

- **reviewed:** Phase 3 (Concurrency & I/O Safety) — PRD-004, PRD-005, PRD-006, PRD-013, PRD-018, PRD-025
- **fixed:** PRD-004 (circuit breaker async locks), PRD-005 (blocking path.exists → asyncio.to_thread), PRD-006 (_ensure_parent_dir → async), PRD-013 (turn count GIL safety comments), PRD-018 (worker context guard), PRD-025 (HTTP client fork-safety)
- **validated:** All 6 fixes validated via 92 smoke tests (80 original + 4 Phase 2 + 8 Phase 3). New Phase 3 tests: prd004_circuit_breaker_is_async, prd004_circuit_lock_exists, prd004_circuit_breaker_roundtrip, prd005_ensure_parent_dir_is_async, prd005_no_bare_path_exists, prd013_turn_count_safety_comments, prd018_worker_context_guard, prd025_fork_reset_function_exists
- **still open:** PRD-003, PRD-007–PRD-012, PRD-014–PRD-017, PRD-019–PRD-024, PRD-026–PRD-037 (28 issues remain)
- **verification notes:** PRD-013 resolved with Option B (safety comments) over Option A (async lock) — practical risk near-zero under CPython GIL, and converting to async would require cascading caller changes. PRD-025 uses `hasattr(os, "register_at_fork")` guard since fork is not available on Windows.

### 2026-04-14 (Phase 2)

- **reviewed:** Phase 2 (Control Flow Correctness) — PRD-001, PRD-002, PRD-009
- **fixed:** PRD-001 (elif in quick intercepts), PRD-002 (elif in post-completion), PRD-009 (phone intercept empty reply guard)
- **validated:** All 3 fixes validated via 84 smoke tests (80 original + 4 new: prd001_quick_intercepts_elif, prd002_post_completion_elif, prd009_phone_intercept_no_bare_stop_response, prd009_short_reply_can_be_empty)
- **still open:** PRD-003 through PRD-037 minus PRD-009 (34 issues remain)
- **verification notes:** PRD-001/PRD-002 severity was partially overstated — StopResponse exception propagation prevented double responses in normal operation, but elif is still the correct fix for clarity and edge-case safety. PRD-009 confirmed as a real bug: _phone_capture_short_reply returns "" when 11+ digits are buffered but invalid, causing silent turn swallow.

### 2026-04-13

- **reviewed:** Full codebase audit completed — 37 issues identified across agent/, backend/, and supporting modules
- **fixed:** (none yet)
- **validated:** (none yet)
- **still open:** PRD-001 through PRD-037 (all 37 issues)
