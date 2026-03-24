# Egyptian Restaurant Voice Agent

Voice agent لمطعم مصري شغال على `LiveKit`، وبيتعامل مع:

- `Greeter`
- `Takeaway`
- `Delivery`
- `Reservation`
- `Complaint`

الوكيل بيجيب config من الـ backend، وبيسجل الطلبات/الحجوزات/الشكاوى، ومعمول له hardening للـ latency والـ duplicate calls والـ no-speech والـ transfer loops.

## المتطلبات

- Python `3.11+`
- LiveKit server أو LiveKit Cloud
- API keys للخدمات المستخدمة
- Backend شغال ويرد على الـ endpoints المطلوبة

## تثبيت dependencies

يفضل تعمل virtual environment الأول:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## إعداد `.env`

أنشئ ملف `.env` في نفس فولدر `agent.py`.

مثال آمن:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# AI providers
SONIOX_API_KEY=your_soniox_key
GOOGLE_API_KEY=your_google_key
OPENAI_API_KEY=your_openai_key
XAI_API_KEY=your_xai_key

# Backend
BACKEND_BASE_URL=http://localhost:8000
BACKEND_API_KEY=your_backend_key

# Logging / limits
LOG_LEVEL=INFO
CONFIG_CACHE_TTL=60
MAX_CALL_DURATION=600

# Session tuning
MAX_TOOL_STEPS=10
MIN_INTERRUPTION_DURATION_SECONDS=0.35
MIN_ENDPOINTING_DELAY_SECONDS=0.25
MAX_ENDPOINTING_DELAY_SECONDS=2.0
FALSE_INTERRUPTION_TIMEOUT_SECONDS=1.5
USER_AWAY_TIMEOUT_SECONDS=9.0
NO_SPEECH_PROMPT_SECONDS=8.0
NO_SPEECH_CLOSE_SECONDS=18.0
NO_SPEECH_REPROMPT_LIMIT=1
NO_SPEECH_REPROMPT_GAP_SECONDS=6.0

# Models
SESSION_LLM_MODEL=gemini-2.5-flash-lite
SESSION_STT_MODEL=stt-rt-v4
SESSION_STT_LANGUAGE=ar
SESSION_STT_LANGUAGE_HINTS_STRICT=true
SESSION_STT_BASE_URL=wss://stt-rt.soniox.com/transcribe-websocket
SESSION_TTS_MODEL=xai/tts-1
SESSION_TTS_VOICE=leo
SESSION_TTS_LANGUAGE=multi
```

مهم:

- ما ترفعش `.env` على Git.
- لو عندك secrets قديمة متسربة، غيّرها فورًا.
- مع Soniox استخدم `SESSION_STT_LANGUAGE=ar` للعربي. الـ docs بتتعامل مع ISO code `ar`، مش dialect code زي `ar-EG`.

## التشغيل

### Development

```powershell
python agent.py dev
```

### Website demo session API

زرار `جرّب ألو إيچي` في الفرونت بقى بيعتمد على الـ backend الموحد من خلال endpoint:

```text
POST /demo/livekit-session
```

يعني في التشغيل الطبيعي محتاج:

```powershell
uvicorn backend.main:app --reload --port 8000
python agent.py dev
```

### Production

```powershell
python agent.py start
```

## اختبارات سريعة قبل التشغيل

### Compile check

```powershell
python -m py_compile agent.py smoke_tests.py
```

### Smoke tests

```powershell
python smoke_tests.py
```

الـ smoke tests بتراجع:

- uniqueness للـ tools
- invalid phone
- unavailable menu item
- partial speech fuzzy matching
- backend down
- duplicate confirms
- reservation validation
- complaint before name/phone
- transfer loop protection
- delivery total calculation
- session safeguards presence

## شكل التشغيل

المكالمة عادة بتمشي كده:

1. `Greeter` يستقبل العميل.
2. يحول حسب النية إلى:
   - `Takeaway`
   - `Delivery`
   - `Reservation`
   - `Complaint`
3. كل flow يجمع أقل قدر من البيانات المطلوبة.
4. عند التأكيد، يضرب الـ backend endpoint المناسب.

## Production hardening الموجود

- Shared tools موحدة ومفيش duplicate function names.
- `SESSION_VAD` reused بدل إعادة تحميله.
- fallback minimal وآمن لو الـ backend config وقع.
- no-speech watchdog مع reprompt ثم close.
- interruption settings متظبطة على مستوى `AgentSession`.
- حماية ضد self-transfer وmissing transfer target.
- order normalization موحد بين takeaway وdelivery.
- validation للـ `branch` و`reservation_time` و`complaint_type`.
- graceful shutdown للـ `httpx` client والـ session.

## Backend contract

الوكيل متوقع الـ endpoints دي:

### `GET /restaurant/config`

يرجع config المطعم، مثل:

```json
{
  "name": "مطعم الكشري",
  "phone": "01000000000",
  "address": "15 شارع التحرير",
  "branches": [{"name": "فرع المعادي", "address": "..."}],
  "hours": {
    "saturday": {"open": "10:00", "close": "23:00"},
    "friday": {"closed": true}
  },
  "menu_items": [
    {"name": "كشري كبير", "price": 25, "available": true},
    {"name": "عصير ليمون", "price": 15, "available": true}
  ],
  "upsell_rules": [],
  "is_open": true,
  "closed_reason": "",
  "wait_minutes": 20,
  "min_guests": 1,
  "max_guests": 20,
  "delivery_enabled": true,
  "delivery_minutes": 45,
  "delivery_fee": 15.0,
  "min_order": 80.0,
  "delivery_zones": ["المعادي", "دار السلام"]
}
```

### `POST /orders`

لـ `takeaway` و`delivery`.

```json
{
  "call_id": "a1b2c3d4",
  "type": "takeaway",
  "customer_name": "أحمد",
  "customer_phone": "01012345678",
  "order_items": [
    {"name": "كشري كبير", "qty": 2, "price": 25.0}
  ],
  "special_requests": "بدون بصل",
  "order_time": "2026-03-01T00:00:00+00:00",
  "channel": "voice_agent"
}
```

استجابة متوقعة:

```json
{"order_id": "ORD-123", "estimated_time": 20}
```

### `POST /reservations`

```json
{
  "call_id": "a1b2c3d4",
  "customer_name": "سارة",
  "customer_phone": "01112345678",
  "reservation_time": "السبت الساعة 8 بالليل",
  "guests_count": 4,
  "branch": "فرع المعادي",
  "notes": "عيد ميلاد",
  "channel": "voice_agent"
}
```

### `POST /complaints`

```json
{
  "call_id": "a1b2c3d4",
  "customer_name": "محمد",
  "customer_phone": "01512345678",
  "complaint_text": "الطلب وصل بارد",
  "complaint_type": "delivery",
  "logged_at": "2026-03-01T00:00:00+00:00",
  "channel": "voice_agent"
}
```

## Multi-tenant

لو عندك أكتر من مطعم، ابعت `restaurant_id` في `room metadata`.

مثال:

```json
{"restaurant_id": "restaurant_abc123"}
```

الوكيل هيقرأه ويبعت:

- Header: `X-Restaurant-ID`
- Query param: `restaurant_id`

## خطوة أخيرة قبل الـ go-live

حتى بعد نجاح `smoke_tests.py`، اعمل مكالمة حقيقية واحدة على الأقل وتأكد من:

- STT العربي
- interruption أثناء كلام البوت
- no-speech behavior
- backend logging
- جودة الصوت والـ latency
