# Unified Backend

الباك هنا هو نقطة الربط الرئيسية بين:

- الفرونت `frontend/entameen-main`
- الـ voice agent في `agent/agent.py`
- LiveKit web demo sessions

## Run

```powershell
uvicorn backend.main:app --reload --port 8000
```

أو مباشرة:

```powershell
python backend/main.py
```

## Important env vars

```env
APP_ENV=dev
BACKEND_DB_PATH=backend/data/app.db
JWT_SECRET=change-me-in-production
BACKEND_API_KEY=mock_secret_key
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
DEMO_RESTAURANT_ID=demo-restaurant
```

## What it serves

- OTP login and role-based session tokens
- Owner / employee dashboard APIs
- Admin restaurant and sales management APIs
- Sales request and demo session APIs
- Agent contract endpoints:
  - `GET /restaurant/config`
  - `POST /orders`
  - `POST /reservations`
  - `POST /complaints`
- LiveKit browser demo endpoint:
  - `POST /demo/livekit-session`

## Seeded accounts

في وضع التطوير الباك بيعمل seeding تلقائي لأول تشغيل. أهم الأرقام:

- Admin: `+201094321642`
- Sales: `+201111111111`
- Sales: `+201222222222`
- Owner: `+201012345678`

أي OTP بيتولد هيتسجل في اللوج، وفي `APP_ENV != prod` هيرجع كمان في response تحت `devOtp`.

## Production notes

- غير `JWT_SECRET` و `BACKEND_API_KEY` قبل أي deploy حقيقي.
- استخدم قاعدة بيانات production بدل SQLite لو متوقع حمل فعلي أو أكتر من instance.
- حط الباك خلف reverse proxy وفعّل HTTPS.
- راقب حجم bundle في الفرونت وحط caching headers للملفات الثابتة.
