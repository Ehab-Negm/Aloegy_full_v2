# Egyptian Restaurant Voice Agent

Single-LLM voice agent for an Egyptian restaurant, running on **LiveKit Agents**. The
agent receives a phone call (or web mic), recognises the customer in Egyptian Arabic,
captures the order/reservation/complaint, and submits to the restaurant backend.

## Architecture

One `Agent` class, one persona prompt, one live state snapshot per turn.

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────────────┐   ┌──────────┐
│  Caller  │──▶│  Soniox  │──▶│ GPT-4.1  │──▶│ Cloud TTS          │──▶│  Caller  │
│          │   │   STT    │   │   mini   │   │ (Chirp3-HD-Sulafat)│   │          │
└──────────┘   └──────────┘   └──────────┘   └────────────────────┘   └──────────┘
                                    │
                                    ▼  function tools
                             ┌──────────────┐
                             │ set_intent   │
                             │ set_name     │
                             │ set_phone    │   ─►   restaurant
                             │ update_order │       backend (HTTP)
                             │ set_*        │
                             │ confirm_*    │
                             └──────────────┘
```

The LLM tracks intent itself via `set_intent` and reads a `[CALL_STATE]` snapshot
every turn — no flow handoffs, no per-flow agent classes, no repetition detector.

## Requirements

- Python 3.11 or 3.12
- LiveKit server (Cloud or self-hosted)
- Restaurant backend exposing the endpoints in [§ Backend contract](#backend-contract)
- API keys / credentials (see § `.env`)

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## `.env`

Place at `agent/.env`. **Never commit it.**

```env
# ── LiveKit ─────────────────────────────────────────────────────────
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...

# ── Backend ─────────────────────────────────────────────────────────
BACKEND_BASE_URL=http://localhost:8000
BACKEND_API_KEY=...

# ── STT (Soniox, Egyptian Arabic) ───────────────────────────────────
SONIOX_API_KEY=...
SESSION_STT_MODEL=stt-rt-v4
SESSION_STT_LANGUAGE=ar
SESSION_STT_LANGUAGE_HINTS_STRICT=true

# ── LLM (OpenAI) ────────────────────────────────────────────────────
OPENAI_API_KEY=...
SESSION_LLM_MODEL=gpt-4.1-mini
SESSION_LLM_TEMPERATURE=0.25
SESSION_LLM_MAX_COMPLETION_TOKENS=200

# ── TTS (Google Cloud, Chirp3-HD) ───────────────────────────────────
# Auth via service-account JSON. Same JSON works for both
# Vertex AI (Gemini fallback) and Cloud TTS streaming.
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/service-account.json
SESSION_TTS_MODEL=cloud-tts/chirp3-hd
SESSION_TTS_VOICE=ar-XA-Chirp3-HD-Sulafat
SESSION_TTS_LANGUAGE=ar-XA

# ── Endpointing (tuned for Egyptian Arabic conversational pauses) ──
MIN_ENDPOINTING_DELAY_SECONDS=0.5
MAX_ENDPOINTING_DELAY_SECONDS=1.2
MIN_INTERRUPTION_DURATION_SECONDS=0.5
FALSE_INTERRUPTION_TIMEOUT_SECONDS=1.0
USER_AWAY_TIMEOUT_SECONDS=9.0

# ── Inactivity / no-speech ──────────────────────────────────────────
NO_SPEECH_PROMPT_SECONDS=12.0
NO_SPEECH_REPROMPT_LIMIT=2
NO_SPEECH_REPROMPT_GAP_SECONDS=8.0
NO_SPEECH_CLOSE_SECONDS=28.0

# ── Limits ──────────────────────────────────────────────────────────
MAX_CALL_DURATION=600        # seconds
MAX_TURNS_PER_SESSION=50
MAX_TOOL_STEPS=4
PROMPT_HISTORY_ITEMS=6
TURN_CHAT_CTX_MAX_ITEMS=16

# ── App env ─────────────────────────────────────────────────────────
APP_ENV=prod
LOG_LEVEL=INFO
AGENT_HEALTH_PORT=8082
```

### Voice options (Chirp3-HD, all `ar-XA`)
| Voice                       | Notes                  |
|-----------------------------|------------------------|
| `ar-XA-Chirp3-HD-Sulafat`   | warm female (default)  |
| `ar-XA-Chirp3-HD-Aoede`     | bright female          |
| `ar-XA-Chirp3-HD-Kore`      | mature female          |
| `ar-XA-Chirp3-HD-Charon`    | deep male              |
| `ar-XA-Chirp3-HD-Orus`      | mid male               |
| `ar-XA-Chirp3-HD-Puck`      | younger male           |

### Alternate TTS providers
Set `SESSION_TTS_MODEL` to switch — same env, no code changes:
- `hamsa/tts-realtime` + `SESSION_TTS_VOICE=Nermin` — fastest (~350ms TTFB)
- `gemini-3.1-flash-tts-preview` + `SESSION_TTS_VOICE=Sulafat` — non-streaming, ~1.5-6s TTFB
- `xai/...` — see `xai_tts.py`

## Run

| Mode      | Command                       | When |
|-----------|-------------------------------|------|
| `dev`     | `python main.py dev`          | local LiveKit Cloud agent with hot-reload |
| `console` | `python main.py console`      | text-mode, no LiveKit room (debug the LLM/tools) |
| `start`   | `python main.py start`        | production worker |

The legacy `python agent.py <mode>` shim still works (lazy-imports `main.server`).

## Tools available to the LLM

All return a short Arabic acknowledgement; LLM speaks naturally afterwards.

| Tool                  | Purpose                                                |
|-----------------------|--------------------------------------------------------|
| `set_intent`          | `takeaway` / `delivery` / `reservation` / `complaint` |
| `set_name`            | Customer name                                         |
| `set_phone`           | Egyptian phone — accepts chunks, buffers until valid  |
| `update_order`        | `[{name, qty}, ...]` — validates against menu         |
| `set_delivery_info`   | Address + optional landmark                           |
| `set_reservation_info`| Time, guests, branch                                  |
| `set_complaint`       | Free-text complaint + category                        |
| `get_menu`            | Read the menu back to the customer                    |
| `confirm_and_submit`  | Validates required slots, hits backend, idempotent    |

Per-turn, the agent reads a `[CALL_STATE]` system message containing the
customer's last utterance verbatim, every captured slot, and any pending
phone-digit buffer. The persona requires it to read the captured info back
to the customer and wait for explicit "تمام" before `confirm_and_submit`.

## Backend contract

### `GET /restaurant/config`
```json
{
  "name": "كشري التحرير",
  "phone": "19719",
  "address": "شارع طلعت حرب",
  "branches": [{"name": "وسط البلد"}],
  "hours": {"saturday": {"open": "10:00", "close": "23:00"}, "friday": {"closed": true}},
  "menu_items": [{"name": "كشري كبير", "price": 50, "available": true}],
  "is_open": true,
  "delivery_enabled": true,
  "delivery_minutes": 45,
  "delivery_fee": 15.0,
  "min_order": 80.0,
  "delivery_zones": ["المعادي", "دار السلام"],
  "min_guests": 1,
  "max_guests": 20,
  "wait_minutes": 20
}
```
Headers: `X-API-Key: <BACKEND_API_KEY>`. Optional `X-Restaurant-ID` for multi-tenant.

### `POST /orders` (takeaway + delivery)
```json
{
  "call_id": "a1b2c3d4",
  "type": "delivery",
  "customer_name": "أحمد",
  "customer_phone": "01012345678",
  "order_items": [{"name": "كشري كبير", "qty": 2, "price": 50.0}],
  "delivery_address": "شارع X، المعادي",
  "delivery_landmark": "جنب الصيدلية",
  "channel": "voice_agent"
}
```
Response: `{"order_id": "ORD-123", "estimated_time": 30}`. Idempotency key: `<call_id>-<takeaway|delivery>`.

### `POST /reservations`
```json
{
  "call_id": "a1b2c3d4",
  "customer_name": "سارة",
  "customer_phone": "01112345678",
  "reservation_time": "السبت الساعة 8 بالليل",
  "guests_count": 4,
  "branch": "وسط البلد",
  "channel": "voice_agent"
}
```
Response: `{"reservation_id": "RES-456"}`. Idempotency key: `<call_id>-reservation`.

### `POST /complaints`
```json
{
  "call_id": "a1b2c3d4",
  "customer_name": "محمد",
  "customer_phone": "01512345678",
  "complaint_text": "الطلب وصل بارد",
  "complaint_type": "توصيل",
  "channel": "voice_agent"
}
```
Idempotency key: `<call_id>-complaint`.

### `POST /calls/upsert`
Final per-call summary at session end (outcome, duration, last messages).
Idempotency key: `<call_id>-call-log`.

## Multi-tenant

Pass `restaurant_id` in the LiveKit room metadata. The agent reads it and forwards as
`X-Restaurant-ID` header + `restaurant_id` query param to `/restaurant/config`.

```json
{"restaurant_id": "restaurant_abc123"}
```

## Reliability

- **Disk-backed write queue** — failed `POST`s land in `.runtime/<env>/backend_write_queue.jsonl`
  and replay on the next worker start.
- **Per-endpoint circuit breaker** — opens after 3 consecutive failures, closes after 8s.
- **Idempotent writes** — every POST carries an `Idempotency-Key` header keyed on call_id
  + action.
- **Config cache** — fetch once per restaurant, refresh every 5 min in the background.
  Falls back to stale cache then to a degraded local config if both miss.
- **Graceful close** — inactivity watchdog says goodbye when the call has already
  produced its outcome instead of asking "are you still there?".
- **Health snapshot** — every worker writes its state to
  `.runtime/<env>/worker_health/<pid>.json`; aggregated at `GET /healthz` on
  `AGENT_HEALTH_PORT`.

## Latency budget (warm)

```
EOU detect       ~750-800ms   (Soniox + LiveKit endpointing)
LLM TTFT         ~700-1500ms  (gpt-4.1-mini, prompt ~2k tokens)
TTS TTFB           400-500ms  (Cloud TTS Chirp3-HD streaming)
─────────────────────────
user → first audio ~2.0-2.8s  perceived
```

If the agent reply spans multiple sentences, LiveKit's audio pipeline plays the first
sentence while later ones are still on the wire.

## Smoke check

```powershell
python -c "import main; print('ok')"
```

Then run a real test call: `python main.py dev`, open a LiveKit demo client (or your
own front-end), and say "محتاج أطلب بيتزا توصيل". Look for:

- `tool.called set_intent` and `tool.called update_order` on turn 1
- `tool.called set_phone` accepts chunked digits ("0155" then "8950 484") and validates
- `tool.called confirm_and_submit` only after the agent reads the order back and the
  customer says "تمام"
- `METRICS TTS ttfb=4XXms` — not seconds

## Project layout

```
agent/
├── main.py                 # entrypoint: AgentSession lifecycle, watchdogs, metrics
├── restaurant_agent.py     # the single Agent class + persona + state snapshot
├── restaurant_tools.py     # function tools the LLM calls
├── agent.py                # infrastructure: env, plugins, fetch_config, _post,
│                           #   queue, circuit breaker, telemetry, warmups
├── gemini_tts.py           # Gemini 3.1 Flash TTS adapter (slow but stable fallback)
├── gemini_live_tts.py      # Gemini Live API TTS (legacy, preview)
├── hamsa_tts.py            # Hamsa TTS (alternate)
├── xai_tts.py              # xAI TTS (alternate)
├── health.py               # /healthz HTTP server
├── backend/
│   ├── client.py           # httpx singleton + retry helpers
│   ├── config.py           # RestaurantConfig dataclass
│   └── queue.py            # disk-backed write queue + circuit breaker
├── state/
│   ├── user_data.py        # per-call state held in AgentSession.userdata
│   └── worker_context.py   # per-process shared state (cache, queue handle)
├── nlp/
│   ├── arabic.py           # normalisation + spoken-number → digit map
│   ├── name_extract.py
│   └── phone_extract.py    # validates 11-digit Egyptian numbers
├── observability/
│   └── call_metrics.py     # JSONL per-call metrics sink
├── core/
│   ├── config_env.py       # env-var registry
│   └── telemetry.py        # logger + emit_event
└── utils/
    ├── money.py            # money2ar, num2ar, phone2ar (number-to-Arabic-text)
    └── voice.py            # _voice_safe_text (TTS-safe truncation)
```

## Deployment (Docker)

See [`Dockerfile`](Dockerfile). Build and run:

```powershell
docker build -t restaurant-agent -f agent/Dockerfile .
docker run --rm `
  --env-file agent/.env `
  -v ${PWD}/agent/nimble-radio-476115-f2-8a719d5ce347.json:/app/credentials.json:ro `
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json `
  -p 8082:8082 `
  restaurant-agent
```

The image runs `python main.py start` and exposes `:8082` for the health endpoint.
Port `8082` answers `GET /healthz` with the worker pool's aggregated state.
