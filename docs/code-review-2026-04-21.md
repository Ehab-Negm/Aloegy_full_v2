# Code Review — Aloegy_full_v2

**Date:** 2026-04-21
**Reviewer:** Claude Opus 4.7
**Scope:** Full project (`agent/`, `backend/`, `frontend/entameen-main/`)
**Method:** Three parallel deep-dive passes (one per component) followed by synthesis.

---

## 1. Executive Summary

Aloegy_full_v2 is a three-tier voice-AI restaurant platform: a LiveKit Python agent that drives phone calls, a FastAPI backend storing orders/reservations/calls, and a React/TypeScript admin dashboard. The architecture is sound and the feature set is substantial, but the project carries several **critical-severity defects** that should block any production release until fixed.

**Overall rating:** B- / needs work before prod.

**Top concerns (headline):**

1. **Authentication is broken** — the OTP expiry check at `backend/main.py:2057` accepts expired codes, making login security effectively a no-op.
2. **Secrets leak through URLs** — TTS API key and auth token appear in query strings (`agent/hamsa_tts.py:97`, `frontend/.../services/api.ts:439`).
3. **Frontend has an unbounded memory leak** (`hooks/use-toast.ts:177`).
4. **Test coverage is thin** across all three components; no automated frontend tests at all.
5. **Shutdown/cleanup paths are racy** in the agent and can deadlock Python exit.

Estimated effort to reach production quality: **2–3 focused engineering weeks** across one backend engineer and one full-stack engineer, assuming no scope creep.

---

## 2. Scorecard

| Dimension | agent/ | backend/ | frontend/ | Overall |
|-----------|--------|----------|-----------|---------|
| Correctness | C | D (OTP bug) | C | **C-** |
| Security | C- | C | C- | **C-** |
| Code Quality | B- | B | C+ | **B-** |
| Test Coverage | C | C+ | F | **C-** |
| Performance | B | B- | C | **B-** |
| Architecture | B- | B | C+ | **B-** |
| Deployment Readiness | B- | B | B | **B-** |

Grades are relative to "ready to operate 24/7 at modest scale," not academic.

---

## 3. Severity Taxonomy

- **🔴 Critical** — Security/auth/data loss. Ship-blocker. Fix within 24 h.
- **🟠 High** — Broken or risky functionality in production paths. Fix this sprint.
- **🟡 Medium** — Code health, architecture, or performance risks. Backlog-worthy.
- **🔵 Low** — Nits, conventions, cosmetics.

---

## 4. 🔴 Critical Findings

### C-1. OTP expiry logic is inverted — authentication bypass
**File:** `backend/main.py:2057`
**What:** The verification endpoint checks `otp_row.expires_at >= utc_now()` where it should be `<=` (or the comparison operands swapped). In its current state an **expired** OTP is treated as **valid**, and a freshly-issued OTP may be treated as invalid depending on exact clock relationship. This is the single most serious finding in the review.
**Impact:** OTP-based auth is effectively circumventable by any attacker who can observe a past OTP (e.g. from a leaked SMS, log file, or database snapshot) — they can log in indefinitely.
**Fix:**
```python
# current (wrong)
if otp_row.expires_at >= utc_now():
    ...
# correct
if otp_row.expires_at <= utc_now():
    raise HTTPException(status_code=400, detail="otp_expired")
```
Add a regression test: issue OTP, fast-forward time past TTL, verify rejection.

---

### C-2. Hamsa TTS API key in WebSocket URL query string
**File:** `agent/hamsa_tts.py:97`
**What:** `f"{WS_URL}?api_key={quote(self._tts._api_key)}"` places the provider's API key in the request URL. Line 103 passes the same key in headers — so the query-string copy is redundant and only adds attack surface.
**Impact:** The key appears in proxy logs, error dashboards, Sentry breadcrumbs, and any stack trace that prints the URL. Once leaked, the attacker can impersonate the tenant with the TTS provider (cost + reputational risk).
**Fix:** Remove the `?api_key=...` segment; rely solely on the `Authorization` / custom header path.

---

### C-3. Auth token in EventSource URL
**File:** `frontend/entameen-main/src/services/api.ts:439-443`
**What:** Because the browser `EventSource` API does not allow custom headers, the token is passed as `?token=...`. URLs are persisted in browser history, service-worker caches, and HTTP access logs.
**Impact:** Anyone with access to the user's local machine or the intermediate network can harvest long-lived admin tokens.
**Fix options:**
- Short-lived, single-use SSE tokens minted from a POST endpoint, then passed as query param (acceptable if TTL ≤ 30 s).
- Migrate the stream to a `fetch()` + `ReadableStream` polyfill pattern so headers work.
- Move the token into an `httpOnly` cookie and let the backend read it on the stream handshake.

---

### C-4. Async cleanup race in backend HTTP client
**File:** `agent/backend/client.py:68-85`
**What:** `cleanup_http_client()` is registered with `atexit`. When `asyncio.get_running_loop()` raises (i.e. interpreter already tearing down async machinery), it falls back to `asyncio.run(...)` which constructs a brand-new event loop. At interpreter shutdown this can deadlock or raise `RuntimeError: Event loop is closed`. Additionally, the task-creation branch at line 83 is fire-and-forget, so the HTTP client can leak sockets if the main task exits before the cleanup coroutine runs.
**Impact:** Process may hang on shutdown; connection leaks in long-running workers.
**Fix:** Run cleanup from inside the agent's own `aclose()` / shutdown hook while the loop is still alive; do **not** defer async teardown to `atexit`.

---

## 5. 🟠 High-Severity Findings

### H-1. OTP mark-consumed precedes user-lookup failure
**File:** `backend/main.py:2059, 2069-2073`
The code marks the OTP consumed and commits before it has confirmed the user exists. If `/auth/verify-otp` is called for a phone that was never provisioned, the user sees a 403 **and** the OTP is burned — a denial-of-service vector against legitimate registration flows.
**Fix:** Wrap user lookup and OTP consumption in a single transaction; on 403, rollback.

### H-2. Idempotency-Key race window
**File:** `backend/main.py:3034-3041` (and similar for reservations at 3063+)
The idempotency lookup is performed, then the handler proceeds to create the row. Two concurrent requests with the same key can both see "no prior row" and both insert. The downstream `IntegrityError` catch exists but is *reactive*: the second request still performed side effects (stock check, capacity decrement, etc.) before the constraint fired.
**Fix:** Use `INSERT … ON CONFLICT DO NOTHING RETURNING …` (Postgres) or equivalent "check-and-insert atomically" pattern. Reject duplicates **before** side-effecting work.

### H-3. Authorization silently permits when `restaurant_id` is None
**File:** `backend/main.py:2313-2315`
`if user.restaurant_id != asset.restaurant_id: forbid()` — when `user.restaurant_id` is `None` (freshly-created admin, misconfigured tenant), the comparison is `None != "abc"` which is *True*, which looks like it forbids, but elsewhere the pattern `if user.role != "admin" and user.restaurant_id != asset.restaurant_id` will short-circuit in the opposite direction. Audit every such check.
**Fix:** Make the intent explicit:
```python
if user.role != "admin":
    if user.restaurant_id is None or user.restaurant_id != asset.restaurant_id:
        raise HTTPException(status_code=403)
```

### H-4. Content-Disposition header injection
**File:** `backend/main.py:2318`
`safe_name = asset.name.replace('"', "")` strips only the quote character. An attacker who controls the stored filename (via any upstream validation gap) can inject CR/LF into the response and forge headers.
**Fix:** Use RFC 5987 encoding (`filename*=UTF-8''…`) and strip all control characters; or generate a server-side opaque filename and store the original in metadata.

### H-5. `useToast` memory leak (unbounded listener array)
**File:** `frontend/entameen-main/src/hooks/use-toast.ts:177`
The effect's dependency list is `[state]`, causing the effect body to push a new entry into the module-level `listeners` array on every state change. Nothing ever removes the stale entries. Over a long admin session this consumes heap and slows re-renders linearly.
**Fix:** Change the dependency array to `[]`, and ensure the cleanup function removes exactly the listener added at mount.

### H-6. Admin phone hardcoded in bundle
**File:** `frontend/entameen-main/src/services/api.ts:262`
The admin phone number ships in the JS bundle. Anyone who views-source on the deployed site can harvest it.
**Fix:** Read from `import.meta.env.VITE_ADMIN_PHONE` at build time, or fetch on demand from an authenticated endpoint.

### H-7. Token in `localStorage` (XSS-sensitive)
**File:** `frontend/entameen-main/src/services/api.ts:266-295`
Standard concern: any XSS escalates to full account takeover. Given there is no strict CSP or trusted-types enforcement in the app, the blast radius is substantial.
**Fix:** Move to `httpOnly` + `SameSite=Strict` cookies. If that is impractical, add a strict CSP and a defensive "short-token + refresh-token" rotation scheme.

### H-8. Overly broad `except Exception` around tool execution
**File:** `agent/base_agent.py:98`
Catching everything and re-raising `StopResponse` assumes `StopResponse` is a subclass of `Exception`. In some LiveKit versions it derives from `BaseException`, in which case this handler will swallow it and the response loop deadlocks.
**Fix:** Catch `BaseException` and re-raise everything that is `not isinstance(err, Exception)`; or explicitly list the exceptions you intend to catch.

### H-9. Temp-file naming uses thread ID only
**File:** `agent/agent.py:571`
`path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")` — thread IDs are recycled within a process *and* the agent runs across multiple forks in production. Collision under load corrupts config cache or write-queue files.
**Fix:** Use `uuid.uuid4().hex` (or `os.getpid()+uuid`) for temp names.

---

## 6. 🟡 Medium-Severity Findings

### Architecture & coupling
- **Leaky BaseAgent** — `agent/base_agent.py:112-136` imports `SESSION_PREEMPTIVE_GENERATION` from the top-level `agent` module. Flows should receive config via constructor injection, not module-level globals.
- **Global `_WORKER_CONTEXT`** — `agent/agent.py:1163` is mutable shared state; parallel tests collide.
- **Flow names as magic strings** — scattered in `agent/main.py:112-119`. Promote to an `Enum`.
- **OwnerDashboardView is 506 lines** (`frontend/.../components/admin/OwnerDashboardView.tsx`) and drills 8+ props into children. Split into child components and lift shared state into a context provider.
- **Duplicate label/badge maps** — `FAILURE_REASON_LABELS`, `outcomeBadge`, `reviewStatusBadge` are redefined in `CallsTab`, `OwnerDashboardView`, and `AnalyticsTab`. Extract to a shared `constants/` module.

### Performance
- **Stream fetch storm** — `OwnerDashboardView.tsx:189` calls `refreshDashboardMeta()` on every order event without debouncing. Wrap in `debounce(..., 500)`.
- **No data-layer cache** — No React Query / SWR. Tab switches re-fetch identical data. Adopt React Query with a 30 s stale time for dashboard data.
- **Fetch without timeout** — `src/services/api.ts:336-360`. Slow networks hang indefinitely. Add `AbortController` with 15 s budget, plus a single-attempt retry on idempotent GETs.
- **Backend analytics without cache** — `/analytics` aggregates on every call (`backend/main.py:1501-1540`). Cache per-restaurant for 60 s with a TTL key.
- **Agent chat-context full-copy per turn** — `base_agent.py:149`. For sessions >30 turns this becomes O(n²). Use slicing or maintain a cheap append-only view.
- **Analytics window slicing Python-side** — `backend/main.py:1525` applies `[-6:]` in Python after fetching all rows. Push the `LIMIT` to SQL.
- **Config refresh per session** — `agent/main.py:90` spawns `_ensure_config_refresh_started` once per session rather than once per worker process.

### API design
- **Idempotency semantics inconsistent** — orders return the prior resource (`backend/main.py:2995`), reservations do too (3070), but the documented response contract is silent. Publish a single spec for Idempotency-Key behavior.
- **Restaurant routing precedence undocumented** — accepts `?restaurant_id` query, `X-Restaurant-ID` header, and `DEFAULT_RESTAURANT_PUBLIC_ID` env fallback without documented precedence (`backend/main.py:2939,2987,3063,3122`).
- **Auth response shape drift** — `/auth/verify-otp` returns `{success, token, role}` while `/auth/send-otp` returns `{success}` only. Add `role` to both.
- **Health check is shallow** — `/health` returns `{status: ok}` without touching the DB (`backend/main.py:1993`). Add a round-trip DB ping.
- **Silent status aliasing** — `backend/main.py:545-551` rewrites `completed`→`delivered`. Either reject unknown statuses explicitly or document the mapping.

### TypeScript rigor
- **Strict mode off** — `tsconfig.json:4` sets `strictNullChecks: false` and `noImplicitAny: false`. This defeats most of TypeScript's safety. Enable incrementally (start with `strictNullChecks: true`), fix fallout file-by-file.
- **`any` / missing return types** in `smoke_tests.py` (agent-side) and several React hooks.

### Accessibility
- Inputs at `CallsTab.tsx:307, 325, 342` lack `<label htmlFor>` associations.
- Motion buttons use `onClick` only — no keyboard handlers; not reachable via Tab order.
- Tables at `OwnerDashboardView.tsx:390-469` lack `<caption>` / semantic `<thead>` structure for screen readers.

### Misc code quality
- **Dead `contextlib.suppress(Exception)`** — `agent/main.py:279` silences inactivity-reprompt failures without logging. Catch specifically and log.
- **`getattr(otp_row, "attempts", 0)`** — `backend/main.py:2052,2065` suggests schema uncertainty about a field that is actually a mapped column (line 320). Remove the defensive fallback.
- **Bare `except Exception: pass` in cleanup** — `agent/backend/client.py:79-84` swallows cleanup failures with no log. Add `logger.exception`.
- **Magic constants scattered** — 180 char cap (`base_agent.py:235`), 2000 text cap (`hamsa_tts.py:16`), magic Arabic strings (`base_agent.py:20`). Collect into a constants module.
- **`UserData` `InitVar` + dynamic `setattr`** — `agent/state/user_data.py:95-131` dynamically attaches properties. Type-checker and IDE lose visibility. Consider a nested dataclass + `__getattr__` forwarder, or drop the indirection.
- **`ALTER TABLE` with f-strings** — `backend/main.py:1931`. Table/column names come from a hardcoded allowlist, so no direct SQLi, but the pattern is fragile. Move to SQLAlchemy migrations (Alembic).

---

## 7. 🔵 Low-Severity Findings

- Style inconsistency between f-strings and `.format()` in a handful of places (agent side).
- `Literal` types used selectively (`agent/agent.py:23`) but not consistently for other enum-like strings.
- README files in sub-packages are out of sync with current filenames.
- Log lines use `%s` lazy formatting in some places and f-strings in others — pick one.
- Chart component uses `dangerouslySetInnerHTML` for a CSS-variable block (`src/components/ui/chart.tsx:70-85`). Safe today because input is controlled, but a linter exception + comment would be appropriate.
- Multiple NULL values in the `idempotency_key` uniquely-constrained columns — semantically misleading. Prefer `NULL`-excluded partial indexes (Postgres) or an explicit sentinel.

---

## 8. Test Coverage Analysis

### `agent/smoke_tests.py`
**Covered:** order confirmation, delivery address validation, reservation time parsing, mocked phone extraction, menu item normalization.
**Missing:**
- Concurrent-session capacity (`MAX_CONCURRENT_SESSIONS`).
- Turn-cap enforcement (`MAX_TURNS_PER_SESSION`).
- Inactivity timeout (`NO_SPEECH_CLOSE_SECONDS`).
- Backend circuit-breaker recovery.
- TTS/STT failover paths.
- Hamsa TTS WebSocket streaming (beyond a single unit-style test).
- Session shutdown race (watchdog cancel + exception).
- Config cache miss recovery.
- Backend write-queue persistence and replay.

### `backend/smoke_test.py`
**Covered:** auth flows, CRUD for orders/menu/employees, file upload/download.
**Missing:**
- Rate-limit 429 behavior.
- Concurrent idempotency-key conflict resolution.
- Permission boundaries (owner A reading owner B's data).
- Negative / zero / extreme-precision prices.
- Unicode + RTL text in Arabic fields (search, display, export).
- File uploads at and slightly above the size limit.
- OTP expiry regression (will catch C-1).

### Frontend
**No automated tests detected.** This is the biggest coverage gap in the project. Recommendation:
- Vitest + React Testing Library for components.
- Playwright for one golden-path E2E per role (admin / owner).
- MSW for API mocking.
Target: smoke-level coverage on auth flow, MenuTab CRUD, CallsTab rendering, SettingsTab save round-trip within two iterations.

---

## 9. Deployment & Operational Readiness

**Good**
- Env-based config is consistently used.
- `BACKEND_LOG_LEVEL` and structured logging are in place (`backend/main.py:43-47`).
- Prod enforcement for `JWT_SECRET` and `BACKEND_API_KEY` (`backend/main.py:111-115`).
- Graceful DB fallback from Postgres DSN to SQLite (`backend/main.py:1959-1969`).

**Needs work**
- Shallow `/health` (no DB check).
- No readiness vs. liveness split.
- No Alembic migrations visible — schema evolves by ad-hoc `ALTER TABLE`.
- No documented runbook for agent-worker restarts.
- Secrets (API keys, JWT secret) lack rotation guidance.
- No observability spec: metrics endpoint, tracing integration, error budgets.

---

## 10. Prioritized Remediation Plan

### Week 1 — Ship-blockers
1. **Fix OTP expiry logic** (C-1) + regression test.
2. **Remove TTS key from WebSocket URL** (C-2).
3. **SSE auth redesign** (C-3) — short-lived token or cookie-based.
4. **Shutdown cleanup refactor** (C-4).
5. **`useToast` leak** (H-5) — trivial one-liner.
6. **ADMIN_PHONE env-ification** (H-6).

### Week 2 — High-severity hardening
7. **OTP transactional consumption** (H-1).
8. **Idempotency pre-insert guard** (H-2).
9. **Authz None-safe checks + Content-Disposition RFC 5987** (H-3, H-4).
10. **Fetch timeout + AbortController** across `api.ts`.
11. **Thread-ID → UUID for temp files** (H-9).
12. **Tool-execution exception scope** (H-8).

### Week 3 — Architectural cleanup + tests
13. Split `OwnerDashboardView.tsx`; extract shared constants.
14. Debounce order-stream meta refresh.
15. Introduce React Query for dashboard data.
16. `BaseAgent` dependency injection cleanup.
17. Backlog of medium items depending on team capacity.
18. Establish frontend test baseline (Vitest + Playwright smoke).

### Ongoing
- Turn on `strictNullChecks` and fix one module at a time.
- Add Alembic for backend schema migrations.
- Add `/health` DB ping and expose Prometheus metrics.

---

## 11. Positive Observations

It's easy to read a review as a pile of negatives. Worth calling out what's working well:

- Clear three-tier separation; no cross-layer leaks of business logic.
- Consistent use of async in agent and backend.
- Good fallback ergonomics (Postgres → SQLite for local dev; optional Soniox STT).
- Idempotency keys are at least attempted on all mutating endpoints — the gap is implementation detail, not absence.
- Auth scopes are granular (`admin`, `owner`, restaurant-scoped assets).
- The flow abstraction (`greeter` / `reservation` / `delivery` / `takeaway` / `complaint`) is a clean carve-up of the conversational domain.

The foundations are solid; the findings above are mostly about tightening the implementation of a good design.

---

*Generated 2026-04-21 by Claude Code review pass on commit `2723fdc` ("improve agent").*
