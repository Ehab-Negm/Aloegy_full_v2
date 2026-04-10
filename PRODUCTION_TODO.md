# AloEgy — Production Readiness TODO

## Status: What's been fixed

- [x] `.gitignore` created (excludes `.env`, `__pycache__`, `node_modules`, `dist`, `*.db`, `.runtime/`, `storage/`)
- [x] `.env.example` files created for agent and backend
- [x] JWT / API key startup validation in production
- [x] OTP generation uses `secrets` (cryptographically secure)
- [x] File upload DoS fixed (size check before reading into memory)
- [x] Order status uses `Literal` validation instead of open `str`
- [x] Health endpoint no longer leaks database path
- [x] `/storage` static mount removed (files served through auth endpoint only)
- [x] Price validation with `_safe_price()` helper
- [x] CORS wildcard `["*"]` removed
- [x] Agent HTTP client race condition fixed (`asyncio.Lock`)
- [x] Agent write queue lock race condition fixed (eager init)
- [x] `call_id` extended to 16 chars (64-bit entropy)
- [x] XAI_API_KEY missing warning added
- [x] Blocking file I/O wrapped in `asyncio.to_thread()`
- [x] TTS changed from LiveKit inference proxy to direct x.ai REST API

---

## P0 — Must fix before production

### 1. Rotate ALL API keys
**Why:** Every API key is committed to git history in plain text. Even after `.gitignore`, the keys are still in past commits.
**How:**
```bash
# 1. Regenerate every key from each provider's dashboard:
#    - LiveKit: livekit.io/dashboard
#    - OpenAI: platform.openai.com/api-keys
#    - Google: console.cloud.google.com
#    - xAI: console.x.ai
#    - Soniox: console.soniox.com
#    - ElevenLabs: elevenlabs.io
#    - Deepgram: console.deepgram.com
#    - Cartesia: play.cartesia.ai
#    - Supabase: supabase.com/dashboard (reset DB password)
#
# 2. Remove .env files from git tracking:
git rm --cached agent/.env backend/.env
git rm --cached -r agent/.runtime/
git commit -m "Remove tracked secrets and runtime cache"
#
# 3. Update .env files locally with new keys
# 4. Consider using BFG Repo-Cleaner to purge keys from git history:
#    https://rtyley.github.io/bfg-repo-cleaner/
```

### 2. OTP delivery (SMS integration)
**Why:** OTP is generated but never sent. Auth only works via dev bypass `123456`.
**How:**
```python
# In backend/main.py, after OTP generation (around line 1272):
# Option A: Twilio
# pip install twilio
from twilio.rest import Client
twilio = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
twilio.messages.create(
    body=f"Your AloEgy code: {code}",
    from_=os.getenv("TWILIO_PHONE"),
    to=phone,
)

# Option B: Vonage (Nexmo) — cheaper for Egypt
# pip install vonage
import vonage
client = vonage.Client(key=os.getenv("VONAGE_KEY"), secret=os.getenv("VONAGE_SECRET"))
vonage.Sms(client).send_message({"from": "AloEgy", "to": phone, "text": f"Code: {code}"})

# Add to backend/.env:
# TWILIO_SID=your_sid
# TWILIO_AUTH_TOKEN=your_token
# TWILIO_PHONE=+1234567890
```

### 3. Rate limiting on auth endpoints
**Why:** No rate limiting = OTP brute-force in under 1M attempts.
**How:**
```bash
pip install slowapi
```
```python
# In backend/main.py, add after imports:
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# On auth endpoints:
@app.post("/auth/send-otp")
@limiter.limit("5/minute")
async def send_otp(request: Request, ...):
    ...

@app.post("/auth/verify-otp")
@limiter.limit("10/minute")
async def verify_otp(request: Request, ...):
    ...

# Also add OTP attempt counter — invalidate OTP after 3 failed attempts:
# Add column: otp_attempts INTEGER DEFAULT 0
# On each failed verify: user.otp_attempts += 1
# If otp_attempts >= 3: invalidate OTP, require new one
```

### 4. OTP lockout after failed attempts
**Why:** OTP is never invalidated on failed attempts. Unlimited guesses against same OTP.
**How:**
```python
# In the verify-otp endpoint:
if user.otp_attempts >= 3:
    raise HTTPException(400, "Too many attempts. Request a new OTP.")

if otp != correct_otp:
    user.otp_attempts += 1
    db.commit()
    raise HTTPException(400, "Invalid OTP")

# On success, reset:
user.otp_attempts = 0
```

---

## P1 — Fix before scaling

### 5. Pagination on all list endpoints
**Why:** `/orders`, `/calls`, `/files`, `/issues`, `/users`, `/admin/restaurants` all load every row. Will slow down with data growth.
**How:**
```python
# Add to each list endpoint:
@app.get("/orders")
async def list_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    ...
):
    query = select(Order).where(...).offset(skip).limit(limit)
    total = db.scalar(select(func.count(Order.id)).where(...))
    return {"items": orders, "total": total, "skip": skip, "limit": limit}
```

### 6. SQLite `strftime` breaks on PostgreSQL
**Why:** Analytics queries use `func.strftime()` which is SQLite-only. Production uses PostgreSQL.
**How:**
```python
# Replace:
func.strftime("%w", Order.created_at).label("dow")
# With:
from sqlalchemy import case, extract
extract("dow", Order.created_at).label("dow")

# Replace:
func.strftime("%Y-%m", Order.created_at).label("month")
# With:
func.to_char(Order.created_at, "YYYY-MM").label("month")

# Or use a helper that picks the right function based on dialect:
def month_label(col):
    if "sqlite" in str(engine.url):
        return func.strftime("%Y-%m", col)
    return func.to_char(col, "YYYY-MM")
```

### 7. Race condition in order ID generation
**Why:** Two concurrent requests can get same `ORD-2026-00042`. UniqueConstraint will crash one with 500.
**How:**
```python
# Option A: Use database sequence
# In PostgreSQL, create a sequence and use nextval()

# Option B: Retry on IntegrityError
from sqlalchemy.exc import IntegrityError
for attempt in range(3):
    try:
        order.public_id = generate_public_order_id(db)
        db.commit()
        break
    except IntegrityError:
        db.rollback()
        continue
```

### 8. SSE thread exhaustion
**Why:** `OrderEventBroker` uses `queue.Queue.get(timeout=15)` which blocks a thread. Under load, exhausts Uvicorn thread pool.
**How:**
```python
# Replace queue.Queue with asyncio.Queue:
import asyncio

class OrderEventBroker:
    def __init__(self):
        self._subscribers: dict[str, asyncio.Queue] = {}

    def subscribe(self, restaurant_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self._subscribers.setdefault(restaurant_id, []).append(q)
        return q

    async def publish(self, restaurant_id: str, event: dict):
        for q in self._subscribers.get(restaurant_id, []):
            await q.put(event)

# In SSE endpoint:
async def event_stream():
    q = broker.subscribe(restaurant_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        broker.unsubscribe(restaurant_id, q)
```

### 9. `compute_customer_profiles` loads all orders into memory
**Why:** No pagination. 100k orders = OOM.
**How:**
```python
# Replace Python-side grouping with SQL GROUP BY:
stmt = (
    select(
        Order.customer_phone,
        func.count(Order.id).label("order_count"),
        func.sum(Order.total_amount).label("total_spent"),
        func.max(Order.created_at).label("last_order"),
    )
    .where(Order.restaurant_id == restaurant_id)
    .group_by(Order.customer_phone)
)
```

### 10. Request body size limit
**Why:** No limit on request body. Large payloads = DoS.
**How:**
```python
# Add middleware:
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class LimitBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB
            return Response(status_code=413, content="Request too large")
        return await call_next(request)

app.add_middleware(LimitBodySizeMiddleware)
```

---

## P2 — Nice to have

### 11. Dockerfiles
```dockerfile
# backend/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# agent/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py xai_tts.py ./
CMD ["python", "agent.py", "start"]
```

### 12. CI/CD pipeline
```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r backend/requirements.txt
      - run: python -m py_compile backend/main.py
      - run: pip install -r agent/requirements.txt
      - run: python -m py_compile agent/agent.py
```

### 13. Pin `pydantic` in agent/requirements.txt
```
# Change:
pydantic>=2.8.2
# To:
pydantic==2.8.2
```

### 14. Database indexes
```sql
CREATE INDEX idx_orders_restaurant_status ON orders(restaurant_id, status);
CREATE INDEX idx_orders_restaurant_created ON orders(restaurant_id, created_at);
CREATE INDEX idx_calls_restaurant_created ON calls(restaurant_id, created_at);
```

### 15. Split `backend/main.py` into modules
```
backend/
  app/
    __init__.py        # FastAPI app factory
    config.py          # Settings, env vars
    models.py          # SQLAlchemy models
    auth.py            # Auth endpoints + JWT logic
    routes/
      orders.py
      reservations.py
      menu.py
      admin.py
      agent.py
    middleware.py       # Rate limiting, body size, CORS
    services/
      analytics.py
      events.py        # SSE broker
  main.py              # Entry point
```
