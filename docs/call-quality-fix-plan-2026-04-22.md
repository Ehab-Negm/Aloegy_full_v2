# Call-Quality Fix Plan — 2026-04-22

## Context

Live-call test surfaced real UX regressions: the agent feels slow, ignores user speech mid-call, and handles Arabic turns poorly. Diagnosis confirmed the root causes are environment configuration + prompt length, not architectural — so all fixes are small, targeted changes with no refactors.

**Constraints:**
- Intentional per-turn delay must stay under **2-2.5 seconds** end-to-end.
- No destructive changes, no feature regression.
- Each round independently testable.

---

## Issue Inventory

| ID | Symptom in call | Root cause | Evidence |
|----|-----------------|------------|----------|
| C1 | Slow TTS, `flush audio emitter due to slow audio generation` | `SESSION_TTS_MODEL=xai` — xAI TTS is slow and unstable | [agent/.env:40](agent/.env#L40) |
| C2 | Agent ignored user ("حضرتك معايا؟") → `skipping user input, speech scheduling is paused` | Downstream of C1 — long TTS generation pauses scheduler | LiveKit internal behavior when TTS blocks |
| C3 | `Turn detector does not support language ar` — late/cut Arabic responses | `MultilingualModel()` has no Arabic support; silently falls back incorrectly | [agent/main.py:129](agent/main.py#L129) |
| C4 | LLM TTFT 2.0-2.3s | ~80-line persona prompt in system message | [agent/base_agent.py:23-84](agent/base_agent.py#L23-L84) |
| C5 | Greeter replied socially ("الحمد لله، وانت؟") instead of pivoting to order | Persona examples model this behavior | [agent/base_agent.py:30-32](agent/base_agent.py#L30-L32) |
| C6 | Phone "012 788" rejected | `_is_plausible_partial_phone_digits` heuristic too strict on short partials | [agent/agent.py:1630-1643](agent/agent.py#L1630-L1643) |
| C7 | `stale config age 17155s` at call start | Backend unreachable during agent startup — fallback cache used | [agent/.runtime/prod/config_cache.json](agent/.runtime/prod/config_cache.json) |
| C8 | Inactivity reprompt after 12s felt abrupt | Generic reprompt text reused | `USER_AWAY_TIMEOUT_SECONDS=9` + grace |
| C9 | "أ" noise-rejected harshly with "معلش مش سامع كويس" | Guard added in session (correct behavior, overly apologetic text) | [agent/base_agent.py:598-614](agent/base_agent.py#L598-L614) |
| C10 | `config_available=False` while `config_source=backend` | Readiness state set after config-loaded log line | [agent/agent.py](agent/agent.py) startup path |

---

## Round 1 — Highest-Impact Fixes

**Goal:** eliminate the worst latency + Arabic-handling issues. Expected total perceived improvement: ~1.5s faster TTFT, stable Arabic turn-taking.

### R1.1 — Switch TTS from xAI to Hamsa

- **File:** `agent/.env`
- **Change:** `SESSION_TTS_MODEL=xai` → `SESSION_TTS_MODEL=hamsa`, `SESSION_TTS_VOICE=leo` → `SESSION_TTS_VOICE=Salma` (or another egy-dialect Hamsa voice)
- **Why:** Hamsa is purpose-built for Arabic, credentials already configured (`HAMSA_API_KEY` present). xAI TTS produces the `flush audio emitter due to slow audio generation` warnings and triggers the downstream "skipping user input" pause.
- **Risk:** Low — `hamsa_tts.py` plugin is already imported and used when `SESSION_TTS_MODEL=hamsa`.
- **Validation:** Make a test call. No `flush audio emitter` warnings. TTS TTFB under 400ms.
- **Resolves:** C1, C2

### R1.2 — Drop the broken Arabic turn detector

- **File:** `agent/main.py`
- **Change:** Remove `turn_detection=MultilingualModel()` from `AgentSession` init (line 129). Rely on Silero VAD + endpointing-delay settings for end-of-turn detection.
- **Why:** `MultilingualModel` emits `Turn detector does not support language ar` warnings and causes inconsistent endpointing. VAD-only path is stable and already tuned (`min_silence=0.35`, `max_endpointing=0.75`).
- **Risk:** Low. VAD alone is what most Arabic voice agents use today; the multilingual model adds nothing useful for this language.
- **Validation:** No more `Turn detector does not support language` log lines. End-of-turn timing feels consistent in a live call.
- **Resolves:** C3

### R1.3 — Trim persona prompt

- **File:** `agent/base_agent.py`
- **Change:** Compact `_EGY_PERSONA_TMPL` (~80 lines → ~30 lines). Keep: 3-4 conversation examples (not 7), forbidden phrases, the 5 most critical rules. Drop: redundant restatements, duplicate "don't use X" entries.
- **Why:** System-prompt length is a direct TTFT cost with every turn. Dropping ~50 lines shortens prompt tokens by ~500-700, reducing TTFT by a measurable amount at the `gpt-4.1-mini` tier.
- **Risk:** Moderate — reducing examples can drift the persona. Mitigate by keeping the strictest examples and the "forbidden phrases" block verbatim.
- **Validation:** Run `smoke_tests.py` — no regressions. Test call: LLM TTFT under 1.5s. Persona still feels natural in first 3 turns.
- **Resolves:** C4 (partial)

**Round 1 Exit Criteria:** live test call with no audio-flush warnings, no Arabic-turn warnings, and visibly snappier replies.

---

## Round 2 — UX Polish

**Goal:** remove the subtle irritations that make the bot feel robotic or stumbly.

### R2.1 — Greeter returns to purpose faster

- **File:** `agent/base_agent.py` (persona examples) or `agent/flows/greeter.py` (core prompt)
- **Change:** Add a rule in persona: "لو العميل بدأ بسؤال اجتماعي (ازيك/إزاي الأخبار)، رد في جملة واحدة قصيرة ورجع على طول للغرض. مثال: 'الحمد لله يا فندم، تحت أمرك، تحب تطلب إيه؟'"
- **Why:** Currently the bot engages in back-and-forth social chatter, which wastes call time and lowers perceived efficiency.
- **Risk:** Very low.
- **Validation:** Test call: greet with "إزيك عامل إيه" and verify reply pivots within 1 sentence.
- **Resolves:** C5

### R2.2 — Tolerate partial phone digits better

- **File:** `agent/agent.py`
- **Change:** Relax `_is_plausible_partial_phone_digits` (line 1630-1643 area) so input like "012 788" (8 digits) accepts as `partial=True` and buffers, instead of rejecting outright. Accept any 5-10 digit prefix that starts with `01` or could reasonably become a valid Egyptian mobile.
- **Why:** On phone capture, users often speak digits in chunks. Rejecting mid-stream forces re-ask and frustrates.
- **Risk:** Low. Final validation (`validate_phone`) still runs at full number; the partial state is a buffer, not a commit.
- **Validation:** Speak "012 788" → status=partial, agent asks to continue. Speak "3456789" → validates and locks.
- **Resolves:** C6

### R2.3 — Softer noise-rejection reply

- **File:** `agent/base_agent.py` (line 614 area)
- **Change:** Replace "معلش يا فندم، مش سامع كويس. ممكن تعيدها؟" with a rotating set: "ممكن تعيدها يا فندم؟" | "مش واضح، قوللي تاني؟" | "ممكن تقولها تاني لو سمحت؟". Pick randomly.
- **Why:** Repeating "مش سامع كويس" makes the bot seem faulty. Variation + softer tone reads more human.
- **Risk:** None.
- **Validation:** Guard fires → one of the new phrasings spoken.
- **Resolves:** C9

**Round 2 Exit Criteria:** a test call feels like talking to a polite human who keeps the conversation moving.

---

## Round 3 — Cleanup

### R3.1 — Make inactivity reprompt vary

- **File:** `agent/main.py` (inactivity watchdog block)
- **Change:** Replace single reprompt string with a small rotating list tied to current flow state.
- **Risk:** Trivial.
- **Resolves:** C8

### R3.2 — Tighten readiness-state logging

- **File:** `agent/agent.py` startup path
- **Change:** Set `config_available=True` BEFORE emitting the `config_source=backend` log, not after.
- **Risk:** None (log-only).
- **Resolves:** C10

### R3.3 — (Optional) Cap cache staleness

- **File:** `agent/agent.py` config-cache path
- **Change:** Reject cache entries older than a configurable max (e.g., 4 hours) instead of falling back unconditionally.
- **Why:** Prevents `stale age=17155s` (4.7h) scenarios in production where a long-unreachable backend poisons calls with very old config.
- **Risk:** Moderate — if backend is down, calls will fail outright instead of degrading. Only do this if backend availability is monitored.
- **Resolves:** C7 (partial)

---

## Execution Order

```
Round 1 (R1.1 → R1.2 → R1.3)  [20-30 min, blocking]
    │
    ▼
LIVE TEST CALL — validate TTFT, no warnings, natural flow
    │
    ▼
Round 2 (R2.1 → R2.2 → R2.3)  [15-20 min]
    │
    ▼
LIVE TEST CALL — validate UX feel
    │
    ▼
Round 3 (R3.1 → R3.2 → R3.3)  [10-15 min, low-priority]
```

**Don't skip the live test between rounds** — each round's impact is subjective and needs an ear, not just logs.

---

## Smoke-Test Gate

Before any round's changes ship, run:

```
cd agent && python smoke_tests.py
```

Expected: all 132+ checks pass. Any regression is a blocker.

---

## Out of Scope (documented, not doing here)

- Soniox per-token confidence filter — requires subclassing the LiveKit Soniox plugin. Big change, defer.
- Reservation WhatsApp confirmation — natural twin to order confirmation but separate feature request.
- The 37 items in [production-fix-workplan.md](production-fix-workplan.md) — that's the separate production-readiness track.
