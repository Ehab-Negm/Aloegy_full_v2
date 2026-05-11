# AloEgy — VPS Deployment Guide (دليل النشر الكامل)

> آخر تحديث: **2026-05-11** — يغطي كل خدمة من الصفر، بكل env var، بكل port، بكل خطوة verification. مكتوب علشان حد جديد على المشروع يقدر يـ replicate كل اللي شغّال دلوقتي بدون ما يسأل سؤال.

---

## ٠. تحديثات هذه الجلسة (2026-05-11) — لازم تـ deploy فوراً

اتعمل ١٢ تعديل في الـ agent worker. **كل التعديلات في الـ agent فقط** — مفيش تعديل في الـ backend أو الـ LiveKit infra أو الـ frontend. كفاية تـ pull الكود وتـ rebuild الـ agent container.

### الـ Fixes الجديدة

| # | المشكلة قبل التعديل | السلوك بعد التعديل | الملف |
|---|---|---|---|
| 1 | تأخير ٩-١٠s بعد كل `set_intent` على SIP | < 500ms — VAD مظبوطة على comfort noise | `agent/agent.py:720-744` |
| 2 | كل الطلبات بتروح لـ `demo-restaurant` (الـ default) | Multi-tenant — `X-Restaurant-ID` header من room metadata | `agent/agent.py:2492-2787` + `state/user_data.py` + `main.py:614` |
| 3 | `update_order` بيكرر الكمية لو الموديل أعاد الـ call | Idempotent — نفس الـ items بنفس الـ qty = no-op | `agent/restaurant_tools.py:162-205` |
| 4 | `end_call(order_completed)` بيقفل قبل ما `confirm_and_submit` ينجح فعلاً | يـ refuse مع رسالة `end_call_refused_order_not_submitted` | `agent/restaurant_tools.py:670-700` |
| 5 | `end_call(customer_done)` يقفل بعد "شكراً" مبكرة قبل ما الزبون يطلب | يـ refuse لو المكالمة < 25s ومفيش order/reservation/complaint | `agent/restaurant_tools.py:646-664` |
| 6 | الموديل بيدخل loop يـ retry `confirm_and_submit` بدون ما يـ call `set_name` | Stuck-loop detector — message قوي بعد ٢ retries | `agent/restaurant_tools.py:786-815` |
| 7 | الـ farewell بيتقطع في النص قبل ما يخلص (الـ session بتقفل قبل الوقت) | Poll `agent_state` لحد ما الموديل يخلص يتكلم قبل الـ close | `agent/restaurant_tools.py:707-727` |
| 8 | في realtime mode الـ inactivity reprompt بيـ skip بصمت | Synthetic-turn injection — الموديل بيرد فعلاً | `agent/utils/voice.py:38-79` |
| 9 | الموديل يقول "أنا بتكلم عربي" لما الـ ASR misperceived | Persona: يفترض الزبون عربي ويرد طبيعي بدون إعلان عن لغته | `agent/restaurant_agent.py:252-262` |
| 10 | الـ persona تتبع gender المتصل من الـ ASR | "علي" — مذكّر دائماً | `agent/restaurant_agent.py` |
| 11 | Cloud BVC noise cancellation فاشل على self-hosted (404 audio filter) | DTLN — MIT, CPU-only, native 16kHz, <1ms | `agent/requirements.txt` + `agent/main.py` |
| 12 | Input transcripts بتطلع بلغات تانية في الـ logs | `AudioTranscriptionConfig` enabled — أنظف | `agent/agent.py:735-757` |

### Dependency واحدة بس جديدة

```
livekit-plugins-dtln>=0.1
```

### خطوات الـ deploy على VPS الحالي

```bash
# 1) SSH للـ VPS
ssh root@<vps-ip>

# 2) sync الكود
cd /root/platform/aloegy-fastapi    # ← أو wherever الـ agent code موجود (شوف §٤ لو مش متأكد)
git fetch origin
git pull origin main

# 3) install الـ dependency الجديدة (DTLN). لو الـ agent في docker:
docker compose build agent
docker compose up -d agent

# لو الـ agent يشتغل عبر venv + systemd (مش container):
source /opt/aloegy/venv/bin/activate
pip install -r agent/requirements.txt
systemctl restart aloegy-agent

# 4) verification — لازم تشوف الـ ٤ سطور دي في الـ logs:
docker compose logs --tail 100 agent | grep -E "VAD tuned for SIP|DTLN|tool_response_scheduling|registered worker"
```

المخرج المتوقع:
```
realtime model: tool_response_scheduling=INTERRUPT
realtime model: VAD tuned for SIP (end_sensitivity=HIGH, silence=200ms, no-interruption)
realtime model: dedicated audio transcription enabled | langs=auto-detect
plugin registered {"plugin": "DTLN", "version": "0.1.0"}
...
registered worker {"agent_name": "aloegy-agent", ...}
```

### Smoke test بعد الـ deploy

اضرب الـ DID بتاع `demo-restaurant` من موبايل خارجي واطلب: "محتاج 3 مارجريتا توصيل، عنواني المعادي شارع 9، اسمي أحمد، أكد".

في الـ log لازم تشوف بالتتابع:
```
set_intent → update_order → set_delivery_info → set_name → confirm_and_submit → POST /orders 200 OK → ORD-2026-XXXXX → end_call(order_completed)
```

والـ gap بين أي tool والـ tool اللي بعده **لازم يبقى < 500ms** (في الـ logs قارن timestamps `tool.called`).

---

## ١. الـ Architecture الكاملة

```
                                    INTERNET
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
   PSTN customer                  Web browser                  Issabel PBX
   (mobile/landline)               (Vercel CDN)                (customer site)
        │                              │                              │
        │ dials DID                    │ HTTPS (api.aloegy.ai)        │ SIP INVITE
        │                              │                              │ (5060/UDP)
        ▼                              ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       Hostinger VPS (KVM 16)                            │
│                                                                         │
│  ┌───────────┐    ┌──────────────────┐    ┌──────────────────────┐      │
│  │   Caddy   │───►│  livekit-server  │◄───│   livekit-sip        │      │
│  │  (TLS)    │    │  (WebRTC SFU)    │    │   (SIP gateway)      │      │
│  │  :443     │    │  :7880,:7881     │    │   :5060,:5061,       │      │
│  │           │    │  :50000-60000/u  │    │    :10000-20000/u    │      │
│  └─────┬─────┘    └────────┬─────────┘    └──────────┬───────────┘      │
│        │                   │                          │                  │
│        │                   ▼                          │                  │
│        │             ┌──────────────┐                 │                  │
│        │             │    redis     │◄────────────────┘                  │
│        │             │   :6379      │                                    │
│        │             └──────────────┘                                    │
│        │                                                                 │
│        ▼                                                                 │
│  ┌────────────────────┐         ┌───────────────────────────┐            │
│  │  backend FastAPI   │◄───────►│  agent worker(s)          │            │
│  │  :8000             │  HTTP   │  (registered with         │            │
│  │  (api.aloegy.ai)   │         │   livekit-server)         │            │
│  └─────────┬──────────┘         └───────────┬───────────────┘            │
│            │                                │                            │
└────────────┼────────────────────────────────┼────────────────────────────┘
             │                                │
             │ SQL                            │ HTTPS
             ▼                                ▼
       ┌─────────────┐                ┌────────────────┐
       │  Supabase   │                │  Gemini Live   │
       │  Postgres   │                │  + Soniox STT  │
       │  (DB)       │                │  + Cloud TTS   │
       └─────────────┘                └────────────────┘
```

### الخدمات (services)

| Service | Where | Port(s) | Owner | Purpose |
|---------|-------|---------|-------|---------|
| `caddy` | systemd or docker | 80, 443 | self | TLS termination + reverse proxy |
| `livekit-server` | systemd | 7880, 7881, 50000-60000/UDP | self | WebRTC SFU |
| `livekit-sip` | systemd (`aloegy-sip.service`) | 5060/UDP+TCP, 5061/TCP, 10000-20000/UDP | self | SIP→WebRTC gateway |
| `redis` | systemd | 6379 (internal) | self | LiveKit + SIP shared state |
| `backend` (FastAPI) | docker compose `aloegy-fastapi` | 8000 | self | REST API + LiveKit room dispatch |
| `agent` (Python worker) | docker compose `aloegy-fastapi` | 8082 (health) | self | Voice agent that joins LiveKit rooms |
| Postgres | Supabase Cloud | 6543 (pooler) | Supabase | Application database |
| Gemini Live API | Google Cloud | 443 | Google | Realtime audio model |
| Soniox STT | Soniox Cloud | 443 | Soniox | Backup transcription |
| Cloud TTS | Google Cloud | 443 | Google | Classic-pipeline TTS |
| Bird WhatsApp | Bird Cloud | 443 | Bird.com | OTP + order confirmation messages |
| Frontend | Vercel | 443 | Vercel | Static React app |

### DNS records

| Record | Type | Value | Used by |
|--------|------|-------|---------|
| `lk.aloegy.ai` | A | VPS IP | WebRTC signaling (wss) |
| `sip.aloegy.ai` | A | VPS IP | SIP signaling (customer Issabel → us) |
| `api.aloegy.ai` | A | VPS IP | Backend FastAPI |
| `aloegy.ai` | A or CNAME | Vercel | Frontend |
| `www.aloegy.ai` | CNAME | aloegy.ai | Frontend |

---

## ٢. الـ Layout على الـ VPS

```
/etc/livekit/                                    ← LiveKit native systemd configs
├── config.yaml                                  ← livekit-server config
├── sip.yaml                                     ← livekit-sip config
└── sip.env                                      ← LIVEKIT_API_SECRET, PUBLIC_IP (chmod 600)

/usr/local/bin/
├── livekit-server                               ← LiveKit binary
└── livekit-sip                                  ← SIP gateway binary

/etc/systemd/system/
├── livekit.service                              ← livekit-server unit
├── aloegy-sip.service                           ← livekit-sip unit
└── redis-server.service                         ← Redis (from apt)

/root/platform/aloegy-fastapi/                   ← Backend + agent (docker compose)
├── docker-compose.yml                           ← backend + agent + (optional) redis
├── .env                                         ← LIVEKIT keys, DB URL, BIRD keys, etc.
├── agent/                                       ← agent worker source (git clone)
│   ├── .env                                     ← agent-specific overrides
│   ├── requirements.txt                         ← Python deps (incl. livekit-plugins-dtln)
│   └── *.py
└── backend/                                     ← FastAPI source
    ├── main.py
    └── data/                                    ← SQLite fallback (NOT used in prod)

/etc/caddy/Caddyfile                             ← Caddy config (or nginx equivalent)

/root/backup/                                    ← Weekly backups (§٨)
```

> ⚠️ كل الـ secrets لازم تطابق بين الـ files دي:
> - `/etc/livekit/sip.env` (LIVEKIT_API_SECRET)
> - `/root/platform/aloegy-fastapi/.env` (LIVEKIT_API_KEY + LIVEKIT_API_SECRET)
> - `/root/platform/aloegy-fastapi/agent/.env` (نفسهم)
>
> أي عدم تطابق = الـ provisioning endpoint يرجع 401، الـ agent مش هـيقدر يـ register، الـ SIP trunks مش هتشتغل.

---

## ٣. Prerequisites (قبل ما تبدأ النشر)

### ٣.١ VPS
- **Hostinger KVM 16** (16 vCPU / 96 GB RAM) — حالياً
- أي Ubuntu 22.04 LTS أو 24.04 يصلح
- Public IPv4 ثابت
- Root SSH access
- منفذ ٢٢ مفتوح (مع IP allowlist لو متاح)

### ٣.٢ Accounts + API keys
أنشئ الـ accounts دي وجمع الـ keys:

| Service | Variable | Where to get |
|---------|----------|--------------|
| Google AI | `GOOGLE_API_KEY` | https://aistudio.google.com/apikey |
| Google Cloud (TTS) | `GOOGLE_APPLICATION_CREDENTIALS` (JSON file) | console.cloud.google.com → IAM → Service Accounts |
| Soniox | `SONIOX_API_KEY` | https://console.soniox.com |
| Bird.com | `BIRD_API_KEY`, `BIRD_WORKSPACE_ID`, `BIRD_CHANNEL_ID`, `BIRD_OTP_TEMPLATE_PROJECT_ID` | bird.com dashboard |
| Supabase | `DATABASE_URL` | supabase.com → project → Connection String → Transaction pooler |

### ٣.٣ Domain + DNS
ادخل لمزود الـ DNS، أضف الـ ٤ A records من جدول §١.

تأكد من propagation قبل ما تكمل:
```bash
dig +short lk.aloegy.ai
dig +short sip.aloegy.ai
dig +short api.aloegy.ai
# الـ ٣ لازم يرجعوا public IP بتاع الـ VPS
```

---

## ٤. Initial Server Setup

### ٤.١ basic hardening
```bash
# على الـ VPS
apt update && apt upgrade -y
apt install -y curl wget gnupg ca-certificates ufw fail2ban git

# Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        # SSH (تأكد إنه شغّال قبل ما تـ enable)
ufw allow 80/tcp        # HTTP (Caddy ACME)
ufw allow 443/tcp       # HTTPS (Caddy)
ufw allow 5060/udp      # SIP
ufw allow 5060/tcp      # SIP
ufw allow 5061/tcp      # SIPS
ufw allow 7880/tcp      # LiveKit signaling (لو مش بتـ proxy بـ Caddy)
ufw allow 50000:60000/udp   # LiveKit WebRTC RTP
ufw allow 10000:20000/udp   # LiveKit-SIP RTP
ufw --force enable

# Fail2ban على SSH
systemctl enable --now fail2ban
```

### ٤.٢ Docker
```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
# (اختياري) خلي يوزر non-root يقدر يستخدم docker:
# usermod -aG docker <username>
```

### ٤.٣ Python (للـ agent venv mode، optional)
```bash
apt install -y python3.12 python3.12-venv python3-pip
```

---

## ٥. Service Deployment (واحدة واحدة)

### ٥.١ Redis
```bash
apt install -y redis-server
sed -i 's/^# maxmemory-policy.*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
systemctl enable --now redis-server
redis-cli ping        # → PONG
```

### ٥.٢ LiveKit Server (systemd)

#### download + install
```bash
curl -sSL https://get.livekit.io | bash
# يحط الـ binary في /usr/local/bin/livekit-server
livekit-server --version
```

#### config
```bash
mkdir -p /etc/livekit
cat > /etc/livekit/config.yaml <<'EOF'
port: 7880
bind_addresses:
  - "0.0.0.0"

keys:
  # placeholder — overridden by env var LIVEKIT_KEYS
  PLACEHOLDER: PLACEHOLDER

redis:
  address: "127.0.0.1:6379"

rtc:
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true
  tcp_port: 7881

logging:
  level: info
  pion_level: warn
  json: true

room:
  auto_create: true
  empty_timeout: 60
  max_participants: 8
EOF
```

#### secrets
```bash
# ولّد LIVEKIT_API_KEY (16+ alphanumeric) و LIVEKIT_API_SECRET (32+ random)
LIVEKIT_API_KEY="APICCqUMXRRWoH9"                                # اختار اسم سهل تعرفه
LIVEKIT_API_SECRET="$(openssl rand -hex 32)"
echo "Save these:"
echo "  LIVEKIT_API_KEY=${LIVEKIT_API_KEY}"
echo "  LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}"

mkdir -p /etc/livekit
cat > /etc/livekit/server.env <<EOF
LIVEKIT_KEYS=${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}
EOF
chmod 600 /etc/livekit/server.env
```

#### systemd unit
```bash
cat > /etc/systemd/system/livekit.service <<'EOF'
[Unit]
Description=LiveKit Server
After=network.target redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=root
EnvironmentFile=/etc/livekit/server.env
ExecStart=/usr/local/bin/livekit-server --config /etc/livekit/config.yaml
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now livekit
systemctl status livekit       # active (running)
```

### ٥.٣ LiveKit-SIP (systemd)

استخدم الـ install script اللي موجود:
```bash
cd /root
git clone https://github.com/<your-org>/aloegy.git platform     # لو لسه ما clonedش
cd /root/platform

LIVEKIT_API_KEY=APICCqUMXRRWoH9 \
LIVEKIT_API_SECRET="$(cat /etc/livekit/server.env | sed 's/.*: //')" \
PUBLIC_IP="$(curl -fsS ifconfig.me)" \
  ./infra/livekit/install-sip.sh
```

الـ script:
- ينزل livekit-sip binary
- يحط `/etc/livekit/sip.yaml`
- يحط `/etc/livekit/sip.env` (بـ chmod 600)
- ينشئ `/etc/systemd/system/aloegy-sip.service`
- يفتح الـ firewall ports
- يـ enable + start الخدمة

تأكد:
```bash
systemctl status aloegy-sip
journalctl -u aloegy-sip -f --since '1 min ago'
```

### ٥.٤ Caddy (TLS reverse proxy)

```bash
# install
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | tee /etc/apt/trusted.gpg.d/caddy-stable.asc
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy

# config
cat > /etc/caddy/Caddyfile <<'EOF'
lk.aloegy.ai {
    reverse_proxy 127.0.0.1:7880 {
        flush_interval -1
    }
}

api.aloegy.ai {
    reverse_proxy 127.0.0.1:8000
}

:80 {
    @health path /healthz
    handle @health {
        respond "ok" 200
    }
    redir https://{host}{uri}
}
EOF

systemctl restart caddy
# تأكد:
curl -I https://lk.aloegy.ai
curl -I https://api.aloegy.ai/healthz
```

### ٥.٥ Backend (FastAPI) + Agent Worker via docker-compose

#### filesystem
```bash
mkdir -p /root/platform/aloegy-fastapi
cd /root/platform/aloegy-fastapi
# لو الـ repo مش clone هنا بالفعل، اعمل:
# git clone https://github.com/<your-org>/aloegy.git .
```

#### `/root/platform/aloegy-fastapi/.env`
ده الـ env المشترك للـ backend + agent. **املأ كل القيم**:
```bash
cat > /root/platform/aloegy-fastapi/.env <<EOF
# Environment
APP_ENV=prod

# Backend
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_API_KEY=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 48)
CORS_ORIGINS=https://aloegy.ai,https://www.aloegy.ai

# Database (Supabase Postgres)
DATABASE_URL=postgresql://postgres.<project>:<password>@aws-1-eu-west-1.pooler.supabase.com:6543/postgres

# LiveKit (نفس الـ keys بتاعت /etc/livekit/server.env)
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=APICCqUMXRRWoH9
LIVEKIT_API_SECRET=<نفس السر اللي ولّدته في §٥.٢>
LIVEKIT_AGENT_NAME=aloegy-agent
LIVEKIT_SIP_HOST=sip.aloegy.ai

# Bird WhatsApp
BIRD_API_KEY=<من bird.com>
BIRD_WORKSPACE_ID=<من bird.com>
BIRD_CHANNEL_ID=<من bird.com>
BIRD_OTP_TEMPLATE_PROJECT_ID=<من bird.com>
BIRD_OTP_TEMPLATE_VERSION=latest
BIRD_OTP_TEMPLATE_LOCALE=ar
BIRD_OTP_TEMPLATE_VARIABLE=otp

# Order confirmation template (optional)
BIRD_ORDER_CONFIRM_TEMPLATE_PROJECT_ID=<من bird.com>
BIRD_ORDER_CONFIRM_VAR_NAME=name
BIRD_ORDER_CONFIRM_VAR_ITEMS=items
BIRD_ORDER_CONFIRM_VAR_ADDRESS=address
BIRD_ORDER_CONFIRM_VAR_TOTAL=total
BIRD_ORDER_CONFIRM_TAKEAWAY_ADDRESS=استلام من المطعم

# Agent env vars (الـ agent بيقرأ من نفس الـ file)
GOOGLE_API_KEY=<من aistudio.google.com>
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json
SONIOX_API_KEY=<من Soniox>
BACKEND_BASE_URL=http://backend:8000
LOG_LEVEL=INFO

# Realtime mode (production default)
SESSION_REALTIME_ENABLED=1
SESSION_REALTIME_MODEL=gemini-3.1-flash-live-preview
SESSION_REALTIME_VOICE=zephyr
SESSION_REALTIME_LANGUAGE=ar-EG
SESSION_REALTIME_TEMPERATURE=0.6
SESSION_REALTIME_USE_VERTEX=false

# Noise cancellation — DTLN (لازم — Cloud BVC مش متاح على self-hosted)
SESSION_NOISE_CANCELLATION=dtln
DTLN_STRENGTH=0.5

# Endpointing
MIN_INTERRUPTION_DURATION_SECONDS=0.55
MIN_ENDPOINTING_DELAY_SECONDS=0.30
MAX_ENDPOINTING_DELAY_SECONDS=0.85
FALSE_INTERRUPTION_TIMEOUT_SECONDS=0.6

# STT fallback (لو الـ realtime فشل)
SESSION_STT_LANGUAGE=ar
SESSION_STT_MODEL=stt-rt-v4
SESSION_STT_BASE_URL=wss://stt-rt.soniox.com/transcribe-websocket

# Cache + budgets
CONFIG_CACHE_TTL=300
CONFIG_FETCH_TOTAL_BUDGET_SECONDS=1.5
MAX_CALL_DURATION=600
TARGET_E2E_FIRST_AUDIO_MS=600

# Telemetry
TELEMETRY_LOG_PATH=/app/.runtime/prod/telemetry.jsonl
CALL_METRICS_PATH=/app/.runtime/prod/call_metrics.jsonl
QA_TRANSCRIPT_EVENTS_ENABLED=true
AGENT_HEALTH_HEARTBEAT_SECONDS=30
EOF
chmod 600 /root/platform/aloegy-fastapi/.env
```

#### Google Cloud service account JSON
```bash
# ارفع الـ JSON من جهازك للـ VPS:
scp service-account.json root@<vps>:/root/platform/aloegy-fastapi/credentials.json
chmod 600 /root/platform/aloegy-fastapi/credentials.json
```

#### `/root/platform/aloegy-fastapi/docker-compose.yml`
```yaml
services:
  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
    container_name: aloegy-backend
    restart: unless-stopped
    env_file: .env
    ports:
      - "127.0.0.1:8000:8000"      # Caddy proxies to this
    volumes:
      - ./backend/data:/app/data    # SQLite fallback dir (لو DATABASE_URL مش متاح)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3

  agent:
    build:
      context: .
      dockerfile: agent/Dockerfile
    container_name: aloegy-agent
    restart: unless-stopped
    env_file: .env
    # الـ agent بيـ register على livekit-server المحلي عبر host network
    # عشان WebRTC ports تتحرر صح والـ in-process DTLN يشتغل
    network_mode: host
    volumes:
      - ./credentials.json:/app/credentials.json:ro
      - ./agent/.runtime:/app/.runtime
    depends_on:
      - backend
    # Health endpoint على :8082 (host network)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8082/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
```

#### build + run
```bash
cd /root/platform/aloegy-fastapi
docker compose build
docker compose up -d
docker compose ps     # backend + agent: Up (healthy)
docker compose logs --tail 50 backend
docker compose logs --tail 50 agent
```

#### verification
```bash
# Backend
curl https://api.aloegy.ai/healthz       # → "ok"

# Agent registered with LiveKit
docker compose logs agent | grep "registered worker"
# → registered worker {"agent_name": "aloegy-agent", "url": "ws://127.0.0.1:7880", ...}

# Agent's new features loaded
docker compose logs agent | grep -E "VAD tuned|DTLN|tool_response_scheduling"
```

### ٥.٦ Frontend (Vercel)
```bash
# على جهازك (مش الـ VPS)
cd frontend/entameen-main

# .env.production
cat > .env.production <<EOF
VITE_API_BASE_URL=https://api.aloegy.ai
VITE_SESSION_API_BASE_URL=https://api.aloegy.ai
VITE_ADMIN_PHONE=+201094321642
EOF

# Deploy
vercel --prod
# على Vercel dashboard: ضع `aloegy.ai` و `www.aloegy.ai` كـ custom domains
```

---

## ٦. الـ Env Var Matrix الكاملة

### ٦.١ Backend FastAPI

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `APP_ENV` | yes | `dev` | `prod` يفعّل صرامة JWT/Bird |
| `BACKEND_HOST` | no | `0.0.0.0` | Listen address |
| `BACKEND_PORT` | no | `8000` | Listen port |
| `BACKEND_API_KEY` | yes | — | Agent → Backend auth (`X-API-Key` header) |
| `JWT_SECRET` | yes | — | تشفير JWTs الـ admin |
| `CORS_ORIGINS` | yes | — | comma-separated origins للـ frontend |
| `DATABASE_URL` | recommended | SQLite | Postgres connection string |
| `LIVEKIT_URL` | yes | — | `ws://127.0.0.1:7880` على نفس الـ VPS |
| `LIVEKIT_API_KEY` | yes | — | لـ provisioning + access tokens |
| `LIVEKIT_API_SECRET` | yes | — | نفسه |
| `LIVEKIT_AGENT_NAME` | yes | `aloegy-agent` | الاسم اللي الـ agent يـ register بيه |
| `LIVEKIT_SIP_HOST` | yes | `sip.aloegy.ai` | يـ embed في الـ `sipUri` المرجَّع للـ tenants |
| `BIRD_API_KEY` | prod yes | — | WhatsApp delivery |
| `BIRD_WORKSPACE_ID` | prod yes | — | نفسه |
| `BIRD_CHANNEL_ID` | prod yes | — | UUID للـ WhatsApp channel |
| `BIRD_OTP_TEMPLATE_PROJECT_ID` | prod yes | — | OTP template ID |
| `BIRD_OTP_TEMPLATE_LOCALE` | no | `en` | `ar` للعربي |
| `BIRD_ORDER_CONFIRM_TEMPLATE_PROJECT_ID` | optional | — | لو فعّلت order WhatsApp confirmations |

### ٦.٢ Agent Worker

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `LIVEKIT_URL` | yes | — | نفس الـ backend |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | yes | — | لـ register بـ livekit-server |
| `LIVEKIT_AGENT_NAME` | yes | `aloegy-agent` | الاسم اللي الـ backend بيـ dispatch بيه |
| `GOOGLE_API_KEY` | yes | — | Gemini Live + Gemini text |
| `GOOGLE_APPLICATION_CREDENTIALS` | yes (لـ Cloud TTS) | — | Path to service-account JSON |
| `SONIOX_API_KEY` | yes (fallback STT) | — | لو الـ realtime فشل |
| `BACKEND_BASE_URL` | yes | — | `http://backend:8000` (في docker network) أو `http://127.0.0.1:8000` |
| `BACKEND_API_KEY` | yes | — | نفس قيمة الـ backend (للـ `X-API-Key`) |
| `SESSION_REALTIME_ENABLED` | yes | `1` | يشغّل realtime mode |
| `SESSION_REALTIME_MODEL` | yes | `gemini-3.1-flash-live-preview` | الـ model |
| `SESSION_REALTIME_VOICE` | yes | `zephyr` | Puck/Zephyr/Aoede/Charon/... |
| `SESSION_REALTIME_LANGUAGE` | yes | `ar-EG` | locale hint |
| `SESSION_REALTIME_TEMPERATURE` | no | `0.6` | 0.0-1.0 |
| `SESSION_NOISE_CANCELLATION` | yes | `dtln` | للـ self-hosted لازم `dtln` |
| `DTLN_STRENGTH` | no | `0.4` | 0.0-1.0 (0.5 موصى بيه) |
| `MAX_CALL_DURATION` | no | `600` | حد أقصى للمكالمة بالثواني |
| `LOG_LEVEL` | no | `INFO` | DEBUG/INFO/WARNING |
| `TELEMETRY_LOG_PATH` | no | `.runtime/prod/telemetry.jsonl` | JSONL telemetry |

> ⚠️ بعد تعديل 2026-05-11، VAD config للـ realtime model **hardcoded** في `agent/agent.py:720-744`. لو احتجت تعدّلها، عدّل الكود مباشرة (مش env var).

### ٦.٣ Frontend (Vite)

| Variable | Required? | Default | Purpose |
|----------|-----------|---------|---------|
| `VITE_API_BASE_URL` | yes | — | `https://api.aloegy.ai` |
| `VITE_SESSION_API_BASE_URL` | yes | — | نفسه (للـ widget) |
| `VITE_ADMIN_PHONE` | no | — | رقم للـ admin contact in UI |

### ٦.٤ LiveKit Server + SIP (systemd env files)

```bash
# /etc/livekit/server.env
LIVEKIT_KEYS=<API_KEY>: <API_SECRET>

# /etc/livekit/sip.env (chmod 600)
LIVEKIT_API_SECRET=<نفس السر>
PUBLIC_IP=<VPS public IPv4>
```

---

## ٧. توصيل عميل Issabel جديد (Multi-tenant onboarding)

### ٧.١ اطلب من العميل
- الـ DID (الرقم بصيغة E.164، مثلاً `+201001234567`)
- Public IPv4 لسيرفر Issabel (`curl ifconfig.me` عنده)
- اسم slug للمطعم (مثلاً `pizza-king`)

### ٧.٢ Provision على الـ backend

```bash
curl -X POST https://api.aloegy.ai/admin/restaurants/<RESTAURANT_ID>/sip-provision \
     -H "Authorization: Bearer <ADMIN_JWT>" \
     -H "Content-Type: application/json" \
     -d '{
       "did": "+201001234567",
       "issabelIp": "41.45.123.45"
     }'
```

الـ response:
```json
{
  "trunkId": "ST_xxx",
  "dispatchRuleId": "SDR_xxx",
  "sipUri": "sip:201001234567@sip.aloegy.ai"
}
```

اللي حصل under the hood:
- LiveKit-SIP عمل **inbound trunk** بـ allowlist على IP العميل
- LiveKit-SIP عمل **dispatch rule** بـ metadata = `{ "restaurant_id": "<slug>", "source": "sip" }`
- الـ FastAPI خزّن الـ trunk_id + rule_id في `restaurants.sip_provisioning_json`

### ٧.٣ سلّم للعميل
- الـ `sipUri` اللي رجع
- لينك [docs/sip-integration-runbook.md](docs/sip-integration-runbook.md) للـ setup
- استخدم port **5060/UDP** (مش 5061/TLS — لسه مش متاح)
- Codecs مدعومة: `ulaw, alaw, opus` فقط

### ٧.٤ Smoke test
```bash
# اضرب الـ DID من موبايل خارجي. على الـ VPS:
journalctl -u aloegy-sip --since '1 min ago'
# هتشوف INVITE قادم من IP العميل، 200 OK، room created

# Agent logs:
docker compose -f /root/platform/aloegy-fastapi/docker-compose.yml logs --tail 50 agent
# → call=... | started | source=sip | caller=+20100xxx | trunk=ST_xxx

# Live SIP capture (debugging):
apt install -y sngrep
sngrep -d any port 5060
```

### ٧.٥ Tear-down عميل
```bash
curl -X DELETE https://api.aloegy.ai/admin/restaurants/<RESTAURANT_ID>/sip-provision \
     -H "Authorization: Bearer <ADMIN_JWT>"
```
يحذف الـ trunk والـ dispatch rule. الـ allowlist يقفل عشان مفيش حد تاني يقدر يبعت INVITE.

---

## ٨. Operations اليومية

### ٨.١ Logs
```bash
# LiveKit infra (systemd)
journalctl -u livekit -f --since '5 min ago'
journalctl -u aloegy-sip -f --since '5 min ago'
journalctl -u redis-server -f

# Backend + Agent (docker compose)
cd /root/platform/aloegy-fastapi
docker compose logs -f backend
docker compose logs -f agent
docker compose logs --tail 200 agent | grep ERROR
```

### ٨.٢ Restart
```bash
# Agent بعد update الكود:
cd /root/platform/aloegy-fastapi
git pull
docker compose build agent
docker compose up -d agent

# Backend:
docker compose restart backend

# SIP بعد تعديل /etc/livekit/sip.yaml:
systemctl restart aloegy-sip

# LiveKit server:
systemctl restart livekit
```

### ٨.٣ Health checks
```bash
docker compose ps      # الـ ٢ containers لازم Up (healthy)
systemctl status livekit aloegy-sip redis-server caddy
curl -s https://api.aloegy.ai/healthz
curl -s http://127.0.0.1:8082/healthz       # agent health
```

### ٨.٤ Resource usage
```bash
htop                    # CPU + RAM
docker stats --no-stream
df -h                   # disk
journalctl --disk-usage
```

---

## ٩. Backup checklist (أسبوعياً)

```bash
mkdir -p /root/backup
BACKUP_DATE=$(date +%F)

# LiveKit infra configs
tar czf /root/backup/livekit-${BACKUP_DATE}.tgz \
    /etc/livekit/ \
    /etc/systemd/system/livekit.service \
    /etc/systemd/system/aloegy-sip.service

# Application configs
tar czf /root/backup/aloegy-${BACKUP_DATE}.tgz \
    /root/platform/aloegy-fastapi/.env \
    /root/platform/aloegy-fastapi/docker-compose.yml \
    /root/platform/aloegy-fastapi/credentials.json

# Caddy config
cp /etc/caddy/Caddyfile /root/backup/Caddyfile-${BACKUP_DATE}

# Database — Supabase has its own backups, BUT pull a logical dump weekly:
docker run --rm -e PGPASSWORD='<supabase-password>' postgres:15-alpine \
    pg_dump -h aws-1-eu-west-1.pooler.supabase.com -p 6543 \
            -U postgres.<project> -d postgres \
            > /root/backup/db-${BACKUP_DATE}.sql

# Redis state
redis-cli BGSAVE
sleep 5
cp /var/lib/redis/dump.rdb /root/backup/redis-${BACKUP_DATE}.rdb

# Upload to S3/R2 (اختياري لكن recommended)
# rclone copy /root/backup/ remote:aloegy-backups/
```

---

## ١٠. Troubleshooting

| الأعراض | السبب الغالب | الحل |
|---------|--------------|------|
| `INVITE` بيوصل بس بـ `403 Forbidden` | IP العميل مش في allowlist | DELETE الـ provision ثم POST تاني بالـ IP الصحيح |
| العميل بيقول "لا يرد" | DNS غلط أو firewall outbound مغلق عنده | تأكد `dig +short sip.aloegy.ai` يرجّع IP الـ VPS |
| One-way audio | NAT mishap | `nat_external_ip` في `sip.yaml` = public IP، عند العميل `external_media_address` = public IP بتاعه |
| Codec mismatch / silence | g729 enabled | يـ disable كل codec ما عدا ulaw/alaw/opus |
| الكلام مقطع | Network packet loss | يفعّل Opus، يعمل speedtest |
| `dispatch rule create failed` من الـ backend | LIVEKIT_API_KEY/SECRET مش متطابقين | شوف §٢ — لازم تطابق |
| Agent بيـ stall بعد كل tool call | DTLN مش installed أو الـ VAD config مش loaded | شوف §٠ verification — لازم `VAD tuned for SIP` يظهر |
| Agent مش بيرد على web widget بس SIP شغّال | الـ agent worker مش running أو الـ docker stack مش up | `docker compose ps agent` لازم healthy |
| Backend بيرفض `/orders` POST بـ 401 | `BACKEND_API_KEY` في الـ agent .env مش متطابق مع backend .env | match the value |
| Order مش بيظهر في الـ dashboard | الـ `X-Restaurant-ID` غلط أو الـ admin login لمطعم مختلف | شوف §٠ multi-tenant — افتح بحساب الـ slug الصح |
| `audio filter cannot be enabled: LiveKit Cloud is required` | benign — هو probe من livekit-rtc | يتجاهَل — DTLN لسه شغّال in-process |

### Diagnostics commands
```bash
# SIP trunks الحالية
livekit-cli sip-trunk list \
    --api-key "$LIVEKIT_API_KEY" --api-secret "$LIVEKIT_API_SECRET" \
    --url ws://127.0.0.1:7880

# Dispatch rules
livekit-cli sip-dispatch list ...

# Capture SIP traffic
sngrep -d any port 5060 or port 5061

# Real-time agent metrics
docker compose -f /root/platform/aloegy-fastapi/docker-compose.yml exec agent \
    tail -f /app/.runtime/prod/telemetry.jsonl | jq

# Test agent worker register
docker compose logs agent | grep registered
```

---

## ١١. Resources & Capacity

| الخدمة | RAM فعلي | vCPU peak |
|--------|----------|-----------|
| `livekit-server` | ~500 MB | 1-2 |
| `livekit-sip` | ~200 MB | <1 |
| `redis-server` | ~50 MB | <0.1 |
| `caddy` | ~30 MB | <0.1 |
| `backend` (FastAPI) | ~400 MB | 1 |
| `agent` worker | ~600 MB base + ~50 MB per call | 1-2 per call |

**Capacity** على الـ KVM 16 الحالي:
- ~200 مكالمة متزامنة قبل ما تنزل الـ port range (10000-20000 = 10k ports = ~2500 sessions theoretical)
- الـ Gemini Live billing هو الـ bottleneck الأول (per-minute audio)، مش الجهاز

---

## ١٢. Production Checklist

قبل ما تـ open الـ DID لزبون حقيقي:

- [ ] DNS records (4) propagated globally
- [ ] HTTPS works on `api.aloegy.ai`, `lk.aloegy.ai`, `aloegy.ai`
- [ ] `systemctl status livekit aloegy-sip redis-server caddy` → الـ ٤ active
- [ ] `docker compose ps` → backend + agent Up (healthy)
- [ ] Agent logs بيظهر: `VAD tuned for SIP`, `DTLN`, `registered worker`
- [ ] `curl https://api.aloegy.ai/healthz` → 200
- [ ] Backend → Postgres connection works (`docker compose logs backend | grep database`)
- [ ] Bird WhatsApp keys تشتغل (test OTP delivery)
- [ ] Test call من web widget — agent بيرد بصوت عربي
- [ ] Test call من SIP softphone — agent بيرد بصوت عربي
- [ ] Test order submission ينجح ويظهر في الـ dashboard
- [ ] Backup script يشتغل (`tar` lines في §٩) ويـ upload لـ S3/R2
- [ ] Monitoring/alerting: على الأقل uptime monitor على `api.aloegy.ai` + `lk.aloegy.ai`
- [ ] All secrets محفوظين خارج الـ VPS (1Password / Bitwarden)

---

## ١٣. Quick reference (cheatsheet)

```bash
# اضف عميل SIP جديد
curl -X POST https://api.aloegy.ai/admin/restaurants/{id}/sip-provision -H "Authorization: Bearer <jwt>" -d '{"did":"+...","issabelIp":"..."}'

# شيل عميل SIP
curl -X DELETE https://api.aloegy.ai/admin/restaurants/{id}/sip-provision -H "Authorization: Bearer <jwt>"

# Agent logs
docker compose -f /root/platform/aloegy-fastapi/docker-compose.yml logs -f agent

# SIP logs
journalctl -u aloegy-sip -f

# Restart agent بعد git pull
cd /root/platform/aloegy-fastapi && git pull && docker compose build agent && docker compose up -d agent

# Restart الـ ستاك كله
cd /root/platform/aloegy-fastapi && docker compose restart && systemctl restart aloegy-sip livekit

# Live SIP capture
sngrep -d any port 5060

# Customer runbook (للـ ops)
cat docs/sip-integration-runbook.md
```

---

## ١٤. ملفات مرجعية

- [docs/sip-integration-runbook.md](docs/sip-integration-runbook.md) — runbook العميل لـ Issabel
- [docs/DEPLOY-SIP-HOSTINGER.md](docs/DEPLOY-SIP-HOSTINGER.md) — سابق، تركيز على SIP فقط
- [infra/livekit/install-sip.sh](infra/livekit/install-sip.sh) — script تنصيب livekit-sip
- [infra/livekit/docker-compose.yml](infra/livekit/docker-compose.yml) — compose alternative للـ greenfield
- [agent/.env.example](agent/.env.example) — template كامل لـ agent env
- [backend/.env.example](backend/.env.example) — template كامل لـ backend env
