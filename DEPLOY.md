# AloEgy — Hostinger VPS Deployment Guide

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
