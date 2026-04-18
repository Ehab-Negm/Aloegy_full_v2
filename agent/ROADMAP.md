# Production Roadmap — Restaurant Voice Agent

## Phase 0 — Stop the Bleeding (Week 1) ✓
*Fix issues that will lose data or crash under load*

### 0.1 Kill Global Mutable State
- [x] Create `worker_context.py` with `WorkerContext` class
- [x] Move `_config_cache`, `_runtime_health`, `_backend_circuits` into it
- [x] Add `asyncio.Lock` for shared state access
- [x] Pass context via LiveKit's worker_context into each `RestaurantAgent`

### 0.2 Replace File-Based Write Queue with Async Queue
- [x] Replace `_enqueue_write()` sync file I/O with `asyncio.Queue`
- [x] Add single background consumer task with batched writes + retry
- [x] Flush remaining queue on graceful shutdown (`SIGTERM`)
- [x] Keep file fallback only for crash recovery

### 0.3 Fix Config Cache Concurrent Writes
- [x] Single `asyncio.Lock` guards cache reads/writes
- [x] Write to temp file + atomic rename (`os.replace`)
- [x] One refresh task per worker, not per call

### 0.4 Fix `_voice_safe_text` Truncating Critical Data
- [x] Confirmation prompts bypass `_voice_safe_text` entirely
- [x] Add `critical=True` parameter that skips truncation
- [x] Only truncate LLM-generated freeform responses

---

## Phase 1 — Customer Experience (Weeks 2-3)
*Fix issues that cause bad calls*

### 1.1 Split the Monolith (4354 lines → modules)
- [x] `main.py` — entrypoint, worker setup (~300 lines)
- [x] `base_agent.py` — BaseAgent + shared tools (~330 lines)
- [x] `flows/greeter.py`
- [x] `flows/delivery.py`
- [x] `flows/takeaway.py`
- [x] `flows/reservation.py`
- [x] `flows/complaint.py`
- [x] Tools kept as `@function_tool` methods on flow classes (no separate tools/ needed)
- [x] `nlp/arabic.py` — _normalize_ar, _spoken_words_to_digits, etc.
- [x] `nlp/name_extract.py`
- [x] `nlp/phone_extract.py`
- [x] `state/user_data.py` — split into OrderState, CustomerInfo, etc.
- [x] `state/worker_context.py`
- [x] `backend/client.py` — HTTP primitives, retry, circuit helpers
- [x] `backend/config.py` — RestaurantConfig
- [x] Queue/config/circuit helpers remain in `agent.py` (shared by all modules)
- [x] `utils/voice.py` — _voice_safe_text, _say_and_stop
- [x] `utils/money.py` — money2ar
- [x] Run smoke tests after each file extraction

### 1.2 Tighten Fuzzy Matching
- [x] Replace substring matching with token-level matching
- [x] Score candidates (Jaccard-like), reject below 0.5 threshold
- [ ] Small allow-list of known abbreviations
- [x] Lean on `update_order` backend validation

### 1.3 Fix Spoken Digit Fragility
- [x] In phone context, treat spoken numbers as single digits only
- [x] "عشرة" in phone context → "1","0" not "10"
- [x] Add test cases for 10 most common Egyptian phone speaking patterns

### 1.4 Idempotent `_say_and_stop`
- [x] Add `_turn_responded` flag, checked at entry
- [x] Second call becomes no-op with warning log
- [x] Reset flag at start of each `on_user_turn_completed`

### 1.5 Fix Stale Turn Guards
- [x] Verify injected system messages don't accumulate (replace, not append)
- [x] Add `_last_guard_flow` tracker; clear on flow change

### 1.6 Early Returns in `on_user_turn_completed`
- [x] Restructure as chain of early-return helper calls
- [x] `_handle_quick_intercepts` → `_handle_post_completion` → `_handle_deterministic` → `_handle_name_intercept` → `_handle_phone_intercept` → LLM

---

## Phase 2 — Engineering Quality (Weeks 3-4)
*Fix design flaws before they compound*

### 2.1 Structured Telemetry
- [x] Add JSON event logger (`restaurant.telemetry`) with `_emit_event()`
- [x] Key events: call.start, turn.received, tool.called, order.confirmed, call.end
- [x] Each event carries: call_id, flow, turn_number

### 2.2 Config Auto-Refresh
- [x] Background task refreshes every 5 minutes (CONFIG_REFRESH_INTERVAL_SECONDS)
- [x] Iterates all cached keys, re-fetches from backend
- [x] Worker-level task via `_ensure_config_refresh_started()`

### 2.3 Split UserData God Object
- [x] `OrderState` — items, validated, total, special_request
- [x] `CustomerInfo` — name, phone, phone_buffer
- [x] `ReservationState` — date, time, guests, branch, notes
- [x] `ComplaintState` — text, category
- [x] `UserData` composes these with proxy properties for backward compat

### 2.4 Rate Limiting
- [x] Track active sessions in `WorkerContext` (MAX_CONCURRENT_SESSIONS)
- [x] Reject above threshold at entrypoint
- [x] Per-session: cap turns at MAX_TURNS_PER_SESSION (default 50)

### 2.5 Remove yaml.dump from Hot Path
- [x] Replace with `json.dumps` or `dataclasses.asdict`
- [x] Only serialize for debug logging (UserData.summarize uses json.dumps)

### 2.6 Cache `_normalize_ar` Results
- [x] Add `@functools.lru_cache(maxsize=2048)`

---

## Module Structure (Current)

```
agent/
├── agent.py              (~2800 lines — helpers, config, queue, validators, re-exports)
├── base_agent.py          (BaseAgent + shared tools: update_name, update_phone, get_menu, to_greeter)
├── main.py                (entrypoint, AgentServer, session setup, watchdog)
├── flows/
│   ├── greeter.py         (Greeter — routes to correct flow)
│   ├── takeaway.py        (Takeaway — pickup orders)
│   ├── delivery.py        (Delivery — delivery orders)
│   ├── reservation.py     (Reservation — table bookings)
│   ├── complaint.py       (Complaint — customer complaints)
│   └── __init__.py
├── backend/
│   ├── client.py          (HTTP primitives, retry, exception helpers)
│   ├── config.py          (RestaurantConfig, CachedConfigEntry)
│   └── __init__.py
├── nlp/
│   ├── arabic.py          (normalize_ar, spoken_words_to_digits, etc.)
│   ├── name_extract.py    (name candidate extraction, non-name detection)
│   ├── phone_extract.py   (phone validation, digit extraction, buffering)
│   └── __init__.py
├── state/
│   ├── user_data.py       (UserData, OrderState, CustomerInfo, etc.)
│   ├── worker_context.py  (re-exports from top-level)
│   └── __init__.py
├── utils/
│   ├── voice.py           (_voice_safe_text)
│   ├── money.py           (money2ar, num2ar, phone2ar)
│   └── __init__.py
├── worker_context.py      (WorkerContext, RuntimeHealth, etc.)
├── smoke_tests.py         (80 tests)
├── xai_tts.py
└── ROADMAP.md
```
