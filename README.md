<div align="center">

# AloEgy — ألو إيچي

### AI-Powered Voice Ordering Platform for Restaurants

منصة طلبات صوتية بالذكاء الاصطناعي للمطاعم المصرية

---

[Live Demo](#quick-start) · [Architecture](#architecture) · [API Docs](#api-documentation) · [Deployment](#deployment)

</div>

---

## Overview

**AloEgy** is a multi-tenant SaaS platform that enables restaurants to accept orders, reservations, and complaints through an **AI-powered Arabic voice agent**. Customers call the restaurant's dedicated number and interact with a natural-sounding voice assistant that understands Egyptian Arabic, takes orders from the menu, and confirms details — all without human intervention.

### Key Features

| Feature | Description |
|---|---|
| **Voice Ordering** | LiveKit-based real-time voice agent fluent in Egyptian Arabic |
| **Multi-Tenant** | Each restaurant is fully isolated with its own menu, settings, and data |
| **Smart Dashboard** | Real-time analytics, order management, and revenue tracking |
| **Role-Based Access** | Admin, Sales, Owner, and Employee roles with granular permissions |
| **Reservation System** | Voice and dashboard-based reservation management |
| **Complaint Tracking** | Automated complaint intake and resolution workflow |
| **Upsell Engine** | AI-driven suggestions to increase average order value |
| **Admin Panel** | Central management for all restaurants, users, and subscriptions |
| **Sales Dashboard** | Lead tracking, onboarding pipeline, and performance metrics |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend                           │
│            React + TypeScript + Vite + Tailwind         │
│         LiveKit Client · shadcn/ui · Recharts           │
├─────────────────────────────────────────────────────────┤
│                      Backend API                        │
│              FastAPI + SQLAlchemy + Pydantic            │
│           JWT Auth · RBAC · SSE Streaming               │
├──────────────────────┬──────────────────────────────────┤
│     Voice Agent      │          Database                │
│  LiveKit + OpenAI    │   PostgreSQL (Supabase)          │
│  Soniox STT · gTTS   │   SQLite (development)           │
│  Circuit Breaker     │                                  │
└──────────────────────┴──────────────────────────────────┘
```

### Tech Stack

| Layer | Technologies |
|---|---|
| **Frontend** | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, Recharts, livekit-client |
| **Backend** | Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, Uvicorn |
| **Voice Agent** | LiveKit Agents SDK, OpenAI GPT-4, Soniox STT, Google TTS |
| **Database** | PostgreSQL (production via Supabase), SQLite (development) |
| **Auth** | OTP-based authentication, JWT tokens, HMAC-SHA256 |

---

## Project Structure

```
aloegy/
├── backend/
│   ├── main.py              # API server — models, endpoints, business logic
│   ├── requirements.txt     # Python dependencies
│   ├── smoke_test.py        # API smoke tests
│   ├── data/                # Seed data and assets
│   └── .env                 # Backend configuration
│
├── agent/
│   ├── agent.py             # LiveKit voice agent
│   ├── requirements.txt     # Agent dependencies
│   └── .env                 # Agent API keys
│
├── frontend/
│   └── entameen-main/
│       ├── src/
│       │   ├── pages/       # Login, Dashboard, Admin, Sales, Pricing
│       │   ├── components/  # Reusable UI components
│       │   ├── services/    # API client layer
│       │   └── hooks/       # Custom React hooks
│       └── package.json
│
└── README.md
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- npm

### 1. Clone & Install

```bash
# Clone the repository
git clone https://github.com/your-org/aloegy.git
cd aloegy

# Create Python virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# Install backend & agent dependencies
pip install -r backend/requirements.txt
pip install -r agent/requirements.txt

# Install frontend dependencies
cd frontend/entameen-main
npm install
cd ../..
```

### 2. Configure Environment

Copy the example env files and fill in your credentials:

**`backend/.env`**
```env
APP_ENV=dev
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_API_KEY=your_secret_key
CORS_ORIGINS=http://localhost:8080
DATABASE_URL=postgresql://user:pass@host:port/db
```

**`agent/.env`**
```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_key
LIVEKIT_API_SECRET=your_secret
OPENAI_API_KEY=sk-...
SONIOX_API_KEY=your_key
BACKEND_API_KEY=your_secret_key
```

### 3. Run

Open three terminal windows:

```bash
# Terminal 1 — Backend
python backend/main.py
# → http://127.0.0.1:8000

# Terminal 2 — Voice Agent (optional)
cd agent && python agent.py dev

# Terminal 3 — Frontend
cd frontend/entameen-main && npm run dev
# → http://localhost:8080
```

### 4. Test Login

In development mode, use OTP bypass code **`123456`** with any registered phone number.

**Default test accounts:**

| Role | Phone |
|---|---|
| Admin | `+201094321642` |
| Sales | `+201111111111` |
| Owner | `+201012345678` |

---

## API Documentation

Interactive API docs available at **`http://127.0.0.1:8000/docs`** (Swagger UI) when the backend is running.

### Core Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/send-otp` | Request OTP for registered phone |
| `POST` | `/auth/verify-otp` | Verify OTP and receive JWT |
| `GET` | `/me` | Current user profile |
| `GET` | `/stats` | Dashboard statistics |
| `GET` | `/analytics` | Revenue & order analytics |
| `GET` | `/orders` | List orders (paginated) |
| `POST` | `/agent/order` | Create order (voice agent) |
| `POST` | `/agent/reservation` | Create reservation (voice agent) |
| `GET` | `/agent/config/{id}` | Restaurant config for agent |
| `GET` | `/admin/restaurants` | All restaurants (admin only) |
| `POST` | `/admin/restaurants` | Create restaurant (admin only) |

> Full documentation: 40+ endpoints covering orders, reservations, complaints, menu, employees, settings, files, and admin operations.

---

## Deployment

### Production Checklist

- [ ] Set `APP_ENV=prod` in `backend/.env`
- [ ] Set a strong `JWT_SECRET` (32+ random characters)
- [ ] Set a strong `BACKEND_API_KEY`
- [ ] Configure `CORS_ORIGINS` to your production domain
- [ ] Use PostgreSQL via Supabase Pooler URL
- [ ] Configure SMS provider for real OTP delivery
- [ ] Set up HTTPS (reverse proxy with Nginx/Caddy)
- [ ] Configure LiveKit Cloud for production traffic

### Recommended Infrastructure

| Component | Recommendation |
|---|---|
| **Backend** | Railway, Render, or VPS with Uvicorn + Gunicorn |
| **Frontend** | Vercel, Netlify, or Cloudflare Pages |
| **Database** | Supabase PostgreSQL (Session Pooler) |
| **Voice** | LiveKit Cloud |
| **Domain** | Custom domain with SSL |

---

## Security

- OTP-based authentication with HMAC-SHA256 hashing
- JWT tokens with configurable expiration
- Role-based access control (RBAC) on all endpoints
- Phone number validation and normalization
- File upload restrictions (type whitelist + 10MB size limit)
- Registered-only login — unregistered numbers are rejected
- API key authentication for agent-to-backend communication
- SQL injection protection via SQLAlchemy ORM

---

## Testing

```bash
# Backend syntax check
python -m py_compile backend/main.py

# API smoke tests (start backend first)
python backend/smoke_test.py --base-url http://127.0.0.1:8000

# Frontend build check
cd frontend/entameen-main && npm run build
```

---

## License

Proprietary — All rights reserved.

---

<div align="center">

**Built with Ehab Negm **

AloEgy © 2025

</div>