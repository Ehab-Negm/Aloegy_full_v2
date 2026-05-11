# AloEgy — Hostinger VPS Deployment Guide

> آخر تحديث: تغييرات الـ agent بتاريخ **2026-05-11** (راجع §٠ لخطوات النشر السريع).

## ٠. تحديثات 2026-05-11 — agent fixes (مهم لو ناوي تـ deploy)

في الـ session ده اتعمل ٧ تعديلات في الـ agent worker لازم تـ deploy كلها سوا.

### ملفات اللي اتعدلت (داخل `/agent`)

| ملف | السطور (تقريباً) | التغيير |
|------|-------|---------|
| [agent/agent.py](agent/agent.py) | 720-744 | إضافة `realtime_input_config` لـ Gemini Live — يعالج تأخير ٩-١٠ ثواني بعد كل tool call على SIP. **بدون env var** — hardcoded. |
| [agent/agent.py](agent/agent.py) | 2492-2595 | `_post()` بيقبل `restaurant_public_id` ويبعت `X-Restaurant-ID` header — multi-tenant routing. |
| [agent/agent.py](agent/agent.py) | 2608-2677 | `submit_takeaway/delivery/reservation/complaint` بيمرّروا الـ restaurant_public_id. |
| [agent/agent.py](agent/agent.py) | 2766-2787 | `/calls/upsert` بيمرّر restaurant_public_id كمان. |
| [agent/state/user_data.py](agent/state/user_data.py) | 135-150 | `restaurant_public_id`, `call_started_at`, `confirm_last_missing`, `confirm_missing_count` على UserData. |
| [agent/main.py](agent/main.py) | 614-621 | تعبئة الحقول الجديدة من room metadata. |
| [agent/restaurant_tools.py](agent/restaurant_tools.py) | 670-770 | `end_call` guards (يرفض `order_completed` لو الطلب لسه ما اتسجلش، `customer_done` لو < 25s). |
| [agent/restaurant_tools.py](agent/restaurant_tools.py) | 707-727 | Realtime farewell wait — يـ poll `agent_state` قبل ما يقفل الـ session. |
| [agent/restaurant_tools.py](agent/restaurant_tools.py) | 162-205 | `update_order` idempotent — مفيش doubling من الـ retries. |
| [agent/restaurant_tools.py](agent/restaurant_tools.py) | 786-808 | Stuck-loop detector في `confirm_and_submit`. |
| [agent/restaurant_agent.py](agent/restaurant_agent.py) | 252-262 | Persona: قواعد اللغة المعدّلة (مفيش "أنا بتكلم عربي" — يفترض الزبون عربي). |
| [agent/restaurant_agent.py](agent/restaurant_agent.py) | 326-328 | Persona: قواعد end_call(order_completed) بعد confirm_and_submit يرجّع `*_confirmed`. |
| [agent/utils/voice.py](agent/utils/voice.py) | 38-79 | `say_safe` في realtime mode بيـ inject synthetic turn — inactivity reprompts والـ farewell بيشتغلوا. |

### Dependency جديدة في requirements.txt

```
livekit-plugins-dtln>=0.1
```

DTLN noise cancellation، MIT, CPU-only, native 16 kHz, <1ms latency. مهم على SIP لأن الـ Cloud BVC مش متاحة على self-hosted. **لازم تتنصب على الـ VPS** عند الـ deploy.

### Env vars اللي ممكن تستخدمها (اختياري — كلها defaults معقولة)

```
# Inbound noise cancellation
SESSION_NOISE_CANCELLATION=dtln          # كان bvc_telephony — لازم dtln للـ self-hosted
DTLN_STRENGTH=0.5                         # 0.0 bypass → 1.0 aggressive. 0.4-0.5 موصى بيه

# الـ realtime VAD config مفيش env var ليها — hardcoded في agent.py
# لو محتاج تـ override، رجّع للسطر 720-744 وغيّر القيم
```

### خطوات الـ deploy على VPS الحالي

افترض إن الـ agent بيشتغل في container اسمه `aloegy-agent` (شوف §١ — لو الـ agent مش موجود كـ container، شوف §١.٥ لإضافته).

```bash
# 1) sync source code
cd /root/platform/aloegy-fastapi   # أو wherever الـ agent code موجود
git fetch && git pull origin main

# 2) re-install Python deps (لازم بعد ما ضفنا livekit-plugins-dtln)
pip install -r agent/requirements.txt
# لو الـ agent في container:
docker compose build agent

# 3) restart الـ agent
docker compose restart agent
# أو لو systemd:
systemctl restart aloegy-agent

# 4) verify
docker compose logs --tail 30 agent | grep -E "VAD tuned for SIP|DTLN|registered worker"
```

### Verification بعد الـ deploy

في الـ startup logs لازم تشوف الـ ٣ سطور دي بالتتابع:

```
realtime model: tool_response_scheduling=INTERRUPT
realtime model: VAD tuned for SIP (end_sensitivity=HIGH, silence=200ms, no-interruption)
realtime model: dedicated audio transcription enabled | langs=auto-detect
...
plugin registered {"plugin": "DTLN", "version": "0.1.0"}
...
registered worker {"agent_name": "aloegy-agent", ...}
```

بعدها اعمل test call:

1. اضرب الـ DID بتاع `demo-restaurant` (أو أي عميل proviosioned).
2. اطلب طلب delivery في turn واحد: "محتاج 3 مارجريتا توصيل".
3. في الـ log قارن timestamps بين:
   - `tool.called | tool: set_intent`
   - `tool.called | tool: update_order` (التالي)
4. **المطلوب: الـ gap < 500ms** (قبل التعديل كان ٥-١٠ ثواني). لو لسه فيه stalling، الـ VAD `silence_duration_ms` يمكن تحتاج تتنزّل لـ 100 أو الـ `automatic_activity_detection.disabled=True` كـ last resort.

---

## ١. الحالة الحالية على الـ VPS

السيرفر `srv1386572` (Hostinger KVM 16) شغّال بـ Docker Compose. خمس خدمات:

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| `livekit-caddy` | `caddy:latest` | 80, 443 | TLS reverse proxy |
| `livekit-server` | `livekit/livekit-server` | 7880-7881, 50000-50100/UDP | WebRTC SFU |
| `livekit-sip` | `livekit/sip` | 5060/UDP, 10000-10100/UDP | SIP gateway |
| `livekit-redis` | `redis:7-alpine` | 6379 (internal) | shared state |
| `n8n` | `n8nio/n8n` | 5678 (internal) | automation (مش متعلق بالستاك) |

### Layout على الـ filesystem

```
/root/Livekit_backend/                  ← الـ LiveKit infra (الستاك اللي فوق)
├── docker-compose.yml
├── configs/
│   ├── sip.yaml                        ← mount → /sip/config.yaml
│   └── livekit.yaml                    ← mount → /etc/livekit.yaml
└── .env                                ← LIVEKIT_API_KEY, LIVEKIT_API_SECRET, PUBLIC_IP

/root/platform/aloegy-fastapi/          ← الـ Backend API (FastAPI)
├── docker-compose.yml
└── .env                                ← LIVEKIT_URL, LIVEKIT_API_KEY/SECRET, JWT_SECRET, BIRD_*

/opt/aloegy/                            ← قديم — مش مستخدم في الإنتاج
```

> ⚠️ الـ keys في `/root/Livekit_backend/.env` لازم يكونوا **متطابقين بالظبط** مع اللي في `/root/platform/aloegy-fastapi/.env`. أي عدم تطابق = الـ provisioning endpoint بيرجع 401 ولا تقدر تعمل dispatch rules.

---

## ٢. Operations يومية

### Logs
```bash
# LiveKit infra
cd /root/Livekit_backend
docker compose logs -f livekit-server
docker compose logs -f livekit-sip

# Backend API
cd /root/platform/aloegy-fastapi
docker compose logs -f
```

### Restart
```bash
# بعد ما تعدّل /root/Livekit_backend/configs/sip.yaml أو livekit.yaml:
cd /root/Livekit_backend
docker compose restart livekit-sip
docker compose restart livekit-server

# بعد ما تعدّل .env بتاع الـ infra:
docker compose down && docker compose up -d
```

### Health check
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
# كل الـ 5 containers لازم Up + healthy
```

---

## ٣. توصيل عميل Issabel جديد

ده الـ flow الأساسي (multi-tenant). كل مرة عميل جديد، تكرّر الخطوات.

### Step 1 — اطلب من العميل
- الـ DID (الرقم بصيغة E.164، مثلاً `+201001234567`)
- Public IPv4 لسيرفر Issabel (`curl ifconfig.me` عنده)
- اسم slug للمطعم (مثلاً `pizza-king`)

### Step 2 — provision على الـ backend

```bash
# على جهازك أو على الـ VPS — السيرفر مش لازم
curl -X POST https://api.aloegy.ai/admin/restaurants/<RESTAURANT_ID>/sip-provision \
     -H "Authorization: Bearer <ADMIN_JWT>" \
     -H "Content-Type: application/json" \
     -d '{
       "did": "+201001234567",
       "issabelIp": "41.45.123.45"
     }'
```

الـ response هيرجّع `sipUri` زي:
```json
{
  "trunkId": "ST_xxx",
  "dispatchRuleId": "SDR_xxx",
  "sipUri": "sip:201001234567@sip.aloegy.ai"
}
```

اللي حصل تحت الكاپوت:
- LiveKit-SIP عمل **inbound trunk** بـ allowlist على IP العميل (لا حد تاني يقدر يبعت INVITE)
- LiveKit-SIP عمل **dispatch rule** بـ metadata = `{ "restaurant_id": "<slug>", "source": "sip" }`
- الـ FastAPI خزّن الـ trunk_id + rule_id في `restaurants.sip_provisioning_json`

### Step 3 — ابعت للعميل

ابعت email/whatsapp فيه:
- الـ `sipUri` اللي رجع من الـ provision
- لينك [docs/sip-integration-runbook.md](docs/sip-integration-runbook.md) يعمل بيه setup Issabel
- ⚠️ **مهم**: قول له يستخدم port **5060/UDP** (مش 5061/TLS — لسه مش متاح، شوف Section 5)
- Codecs مدعومة: `ulaw, alaw, opus` فقط (يـ disable g729 وغيرهم)

### Step 4 — Smoke test

العميل يضرب الـ DID من موبايل خارجي. على الـ VPS:

```bash
docker compose -f /root/Livekit_backend/docker-compose.yml logs --tail 50 livekit-sip
# هتشوف INVITE قادم من IP العميل، 200 OK، room created

# لو محتاج تشوف SIP packets live:
apt install -y sngrep
sngrep -d any port 5060
```

في الـ agent logs (الـ FastAPI compose):
```
call=<sip-call-id> | started | source=sip | caller=+20100xxx | trunk=ST_xxx
```

---

## ٤. Tear-down عميل

لو عميل وقّف الخدمة، احذف الـ trunk علشان الـ allowlist ميفضلش مفتوح:

```bash
curl -X DELETE https://api.aloegy.ai/admin/restaurants/<RESTAURANT_ID>/sip-provision \
     -H "Authorization: Bearer <ADMIN_JWT>"
```

---

## ٥. تحسينات بعدين (مش blockers)

### ٥.١ افتح port 5061 TLS
العملاء اللي عندهم compliance أو شبكة شركات هيحتاجوا TLS. عدّل `/root/Livekit_backend/docker-compose.yml`:

```yaml
services:
  livekit-sip:
    ports:
      - "5060:5060/udp"
      - "5061:5061/tcp"   # ← ضيف ده
      - "10000-10100:10000-10100/udp"
```

ضيف في `/root/Livekit_backend/configs/sip.yaml`:
```yaml
sips_port: 5061
# tls section حسب شهادة Caddy أو Let's Encrypt مباشرة
```

ثم `docker compose up -d livekit-sip`.

### ٥.٢ وسّع RTP range
حالياً `10000-10100` = ١٠٠ ports = ~٢٥-٥٠ مكالمة متزامنة. لو هتوسّع:

```yaml
# في docker-compose.yml
ports:
  - "10000-12000:10000-12000/udp"   # ← ٢٠٠٠ ports = ~٥٠٠ مكالمة

# في sip.yaml
rtp_port:
  start: 10000
  end: 12000
```

⚠️ تذكّر تفتح الـ range في firewall: `ufw allow 10000:12000/udp`.

### ٥.٣ وسّع WebRTC range للـ livekit-server
نفس الـ pattern في `livekit.yaml`:
```yaml
rtc:
  port_range_start: 50000
  port_range_end: 52000   # بدل 50100
```
وفي compose: `"50000-52000:50000-52000/udp"`.

---

## ٦. Troubleshooting

| الأعراض | السبب الغالب | الحل |
|---------|--------------|------|
| `INVITE` بيوصل بس بـ `403 Forbidden` | IP العميل مش في allowlist | اعمل DELETE provision ثم POST تاني بالـ IP الصحيح |
| العميل بيقول "لا يرد" | DNS غلط أو firewall outbound مغلق عنده | تأكد `dig +short sip.aloegy.ai` يرجّع IP الـ VPS |
| One-way audio | NAT mishap | تأكد `nat_external_ip` في `sip.yaml` = public IP، عند العميل `external_media_address` = الـ public IP بتاعه |
| Codec mismatch / silence | g729 enabled | يـ disable كل codec ما عدا ulaw/alaw/opus |
| الكلام مقطع | Network packet loss | يفعّل Opus، يعمل speedtest |
| `dispatch rule create failed` من الـ backend | LIVEKIT_API_KEY/SECRET في الـ FastAPI .env مش متطابق مع `/root/Livekit_backend/.env` | شوف Section 1 — لازم تطابق |

### Diagnostics commands

```bash
# الـ trunks الحالية
docker exec livekit-server livekit-cli sip-trunk list \
    --api-key "$LK_KEY" --api-secret "$LK_SECRET" \
    --url ws://livekit-server:7880

# الـ dispatch rules
docker exec livekit-server livekit-cli sip-dispatch list ...

# capture SIP traffic
sngrep -d any port 5060 or port 5061
```

---

## ٧. أرقام Resources الحالية

السيرفر = KVM 16 (16 vCPU / 96 GB RAM):

| الخدمة | RAM فعلي | vCPU peak |
|--------|----------|-----------|
| livekit-server | ~500 MB | 1-2 |
| livekit-sip | ~200 MB | <1 |
| livekit-redis | ~50 MB | <0.1 |
| livekit-caddy | ~30 MB | <0.1 |
| backend FastAPI | ~400 MB | 1 |
| agent worker | ~600 MB / call | 1-2 / call |
| n8n | ~250 MB | <0.5 |

**Capacity:** يكفي لـ ٢٠٠+ مكالمة متزامنة بعد توسيع RTP range. الـ bottleneck الأول هيبقى الـ Gemini Live API (per-minute audio billing) مش الجهاز.

---

## ٨. Backup checklist (أسبوعياً)

```bash
# الـ infra configs
tar czf /root/backup/livekit-$(date +%F).tgz \
    /root/Livekit_backend/.env \
    /root/Livekit_backend/configs/ \
    /root/Livekit_backend/docker-compose.yml

# الـ FastAPI configs
tar czf /root/backup/fastapi-$(date +%F).tgz \
    /root/platform/aloegy-fastapi/.env \
    /root/platform/aloegy-fastapi/docker-compose.yml

# الـ database (لو SQLite)
docker exec aloegy-fastapi sqlite3 /app/data/app.db ".backup /tmp/app.db"
docker cp aloegy-fastapi:/tmp/app.db /root/backup/app-$(date +%F).db

# Redis state
docker exec livekit-redis redis-cli BGSAVE
docker cp livekit-redis:/data/dump.rdb /root/backup/redis-$(date +%F).rdb
```

ارفع للـ S3/R2 أو Hostinger backup.

---

## ٩. Quick reference

- **اضف عميل**: `POST /admin/restaurants/{id}/sip-provision`
- **شيل عميل**: `DELETE /admin/restaurants/{id}/sip-provision`
- **logs**: `docker compose -f /root/Livekit_backend/docker-compose.yml logs -f livekit-sip`
- **restart الستاك كله**: `cd /root/Livekit_backend && docker compose restart`
- **runbook العميل**: [docs/sip-integration-runbook.md](docs/sip-integration-runbook.md)
- **DNS**: `sip.aloegy.ai` و `lk.aloegy.ai` و `api.aloegy.ai` و `aloegy.ai` كلها → IP الـ VPS
