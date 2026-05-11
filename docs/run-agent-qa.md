# Run Agent QA

Use PowerShell from the repo root.

## 1. Install dependencies

```powershell
cd "D:\lovable livekit\agent"
pip install -r requirements.txt
```

## 2. Configure `agent\.env`

Use real values, not placeholders:

```env
APP_ENV=prod
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
BACKEND_BASE_URL=http://localhost:8000
BACKEND_API_KEY=...
SONIOX_API_KEY=...
HAMSA_API_KEY=...
GROQ_API_KEY=...
SESSION_LLM_MODEL=groq/llama-3.3-70b-versatile
TELEMETRY_LOG_PATH=.runtime/prod/telemetry.jsonl
QA_TRANSCRIPT_EVENTS_ENABLED=true
SESSION_PREEMPTIVE_GENERATION=true
SESSION_TTS_STREAMING_ENABLED=true
TARGET_E2E_FIRST_AUDIO_MS=1000
MIN_ENDPOINTING_DELAY_SECONDS=0.12
MAX_ENDPOINTING_DELAY_SECONDS=0.35
SESSION_STT_ENABLE_LANGUAGE_IDENTIFICATION=false
SESSION_STT_MAX_ENDPOINT_DELAY_MS=500
SESSION_LLM_MAX_COMPLETION_TOKENS=64
```

## 3. Preflight

```powershell
cd "D:\lovable livekit\agent"
python qa_live_preflight.py --telemetry .runtime\prod\telemetry.jsonl --min-calls 50 --target-ms 1000
```

Fix every `errors` entry before collecting market QA calls.

After starting services, verify the running backend, agent health endpoint, and
frontend URL. This also mints a short-lived demo LiveKit token and verifies the
response shape without printing the token:

```powershell
cd "D:\lovable livekit"
python agent\qa_service_preflight.py
```

For a final production deploy check, make non-prod backend/agent modes fail:

```powershell
cd "D:\lovable livekit"
python agent\qa_service_preflight.py --strict-prod
```

## 4. Reset old QA artifacts

If you already have a failing/old telemetry batch, archive it before the next
run so old failures do not mix with new evidence:

```powershell
cd "D:\lovable livekit\agent"
python qa_reset_batch.py --telemetry .runtime\prod\telemetry.jsonl --matrix ..\docs\qa-call-matrix-live.csv
```

## 5. Start the worker

```powershell
cd "D:\lovable livekit\agent"
python main.py start
```

For local text debugging only:

```powershell
python main.py console
```

## 6. Fill the live matrix

```powershell
cd "D:\lovable livekit"
Copy-Item docs\qa-call-matrix-template.csv docs\qa-call-matrix-live.csv
```

While running calls, fill `call_id`, `scenarios`, and set `audio_reviewed=true`
only after a human accepts the call audio.

Use `docs\qa-live-50-call-plan.csv` as the live batch plan. It has 50 planned
calls covering the required flows and edge cases. Copy each completed call ID
from telemetry into `docs\qa-call-matrix-live.csv`, keep the matching scenario
labels, and set `audio_reviewed=true` only after listening to the recording.

To avoid manually copying call IDs, sync completed calls from telemetry into
the matrix after or during the batch:

```powershell
cd "D:\lovable livekit"
python agent\qa_sync_matrix.py --telemetry agent\.runtime\prod\telemetry.jsonl --matrix docs\qa-call-matrix-live.csv
```

The sync command adds new completed calls with `audio_reviewed=false`; keep it
false until a human reviews the recording, then add any extra scenarios covered
by that call.

To see compact progress during the batch:

```powershell
cd "D:\lovable livekit"
python agent\qa_progress.py --telemetry agent\.runtime\prod\telemetry.jsonl --matrix docs\qa-call-matrix-live.csv --min-calls 50 --target-ms 1000
```

## 7. Final gate

```powershell
cd "D:\lovable livekit"
python agent\qa_market_gate.py --telemetry agent\.runtime\prod\telemetry.jsonl --matrix docs\qa-call-matrix-live.csv --min-calls 50 --target-ms 1000 --require-flows takeaway,delivery,reservation,complaint
```

The agent is not market-ready until this command passes.

