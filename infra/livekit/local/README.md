# Local SIP Testing — Windows + Issabel على نفس الشبكة

اختبار end-to-end بدون ما تلمس السيرفر:
**Issabel (LAN) → Windows (Docker Desktop) → agent محلي → Gemini Live**

## Prerequisites

- Docker Desktop شغّال
- Windows LAN IP: **192.168.8.5** (شيك بـ `ipconfig`)
- Issabel على نفس الشبكة (IP زي `192.168.8.X`)
- الـ Python agent جاهز يشتغل من `agent/main.py dev`

---

## Step 1 — افتح Windows Firewall

PowerShell كـ **Administrator**:

```powershell
cd "d:\lovable livekit\infra\livekit\local"
.\open-firewall.ps1
```

السكربت بيعمل ٤ rules: TCP 7880، UDP 5060، UDP 10000-10020، UDP 50000-50020.

تأكد:
```powershell
Get-NetFirewallRule -DisplayName "AloEgy-Local-*" | Format-Table DisplayName, Enabled
```

---

## Step 2 — جهّز الـ .env

```powershell
cd "d:\lovable livekit\infra\livekit\local"
copy .env.example .env
notepad .env
```

عدّل:
- `PUBLIC_IP=192.168.8.5` (الـ LAN IP بتاعك)
- `LIVEKIT_API_KEY=devkey`
- `LIVEKIT_API_SECRET=` (generate hex: `python -c "import secrets; print(secrets.token_hex(32))"`)

---

## Step 3 — شغّل الـ docker stack

```powershell
docker compose up -d
docker compose ps
# لازم 3 services Up: aloegy-local-redis, aloegy-local-livekit, aloegy-local-sip
```

شيك الـ logs:
```powershell
docker compose logs livekit-server | Select-String -Pattern "started|ready|error" | Select-Object -Last 10
docker compose logs livekit-sip | Select-String -Pattern "started|listening|error" | Select-Object -Last 10
```

لو في error في livekit-sip زي "cannot resolve livekit-server"، استنى ٥ ثواني وأعد `docker compose restart livekit-sip`.

---

## Step 4 — جهّز الـ agent يكلم الستاك المحلي

في `agent\.env` ضيف/عدّل:

```env
LIVEKIT_URL=ws://192.168.8.5:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=نفس-اللي-في-الـ-infra/.env
LIVEKIT_AGENT_NAME=aloegy-agent
```

شغّل الـ agent من cmd جديد:
```cmd
cd /d "d:\lovable livekit\agent"
python main.py dev
```

لازم تشوف:
```
agent registered with worker | name=aloegy-agent
```

---

## Step 5 — Provision trunk اختبار

اطلع الـ Issabel LAN IP بتاعك (مثلاً `192.168.8.10`). من PowerShell:

```powershell
$body = @{
    slug         = "local-test"
    did          = "+201001234567"
    issabel_ip   = "192.168.8.10"   # ← IP الـ Issabel
} | ConvertTo-Json

# لو الـ backend مش شغّال بعد، استخدم الـ CLI script مباشر:
$env:LIVEKIT_URL="ws://192.168.8.5:7880"
$env:LIVEKIT_API_KEY="devkey"
$env:LIVEKIT_API_SECRET="نفس-اللي-في-الـ-infra/.env"

cd "d:\lovable livekit"
python -m agent.ops.provision_tenant `
    --slug local-test `
    --did +201001234567 `
    --issabel-ip 192.168.8.10
```

الـ output بيرجّع `trunk_id` و `dispatch_rule_id`. خلّيهم في كليبورد.

---

## Step 6 — ظبط Issabel

في Issabel admin (`https://192.168.8.10`):

### Trunks → Add SIP Trunk
- **Trunk Name:** `aloegy-local`
- **Trunk Type:** chan_pjsip
- **Username:** `local-test`
- **Authentication:** Outbound (no password — IP allowlist)
- **Registration:** None
- **SIP Server:** `192.168.8.5` (الـ Windows LAN IP)
- **SIP Server Port:** `5060`
- **Transport:** `udp`
- **From User:** `local-test`
- **From Domain:** `192.168.8.5`
- **Codecs:** `ulaw, alaw, opus` فقط (disable g729 وغيرهم)
- **DTMF Mode:** RFC4733
- **Media Encryption:** None

اضغط **Submit** → **Apply Config**.

### Inbound Routes → Add Route
- **DID Number:** `201001234567` (نفس الـ DID اللي عملته provision)
- **Set Destination:** Trunks → `aloegy-local`

---

## Step 7 — Smoke test

من **softphone** (Linphone/MicroSIP) أو موبايل تاني على نفس الشبكة، اضرب **+201001234567**.

### راقب ٣ نوافذ في نفس الوقت:

**نافذة ١ — SIP traffic:**
```powershell
docker compose logs -f livekit-sip
```
لازم تشوف INVITE قادم من 192.168.8.10، ثم 200 OK.

**نافذة ٢ — agent:**
في الـ cmd بتاع الـ agent، لازم تشوف:
```
call=<sip-call-id> | started | source=sip | caller=+201001234567 | trunk=ST_xxx
```

**نافذة ٣ — Issabel:**
```bash
ssh root@192.168.8.10
asterisk -rvvv
# هتشوف INVITE outbound + 200 OK جاي + RTP flowing
```

---

## Common Failures

| الأعراض | السبب | الحل |
|---------|------|-----|
| Issabel: "All circuits are busy" أو timeout | Windows Firewall بيرفض UDP 5060 | شغّل `open-firewall.ps1` كـ admin |
| `INVITE` بيوصل بس `403 Forbidden` | الـ Issabel IP في الـ allowlist غلط | اعمل provision تاني بالـ IP الصحيح |
| `200 OK` بيرجع لكن الصوت silence | NAT غلط — الـ SDP بيقول 172.x.x.x بدل 192.168.8.5 | تأكد `PUBLIC_IP=192.168.8.5` في `.env` و `docker compose restart livekit-sip` |
| `livekit-sip: cannot connect to ws://livekit-server:7880` | الستاك startup race | `docker compose restart livekit-sip` بعد ١٠ ثواني |
| الـ agent مش شغّال | الـ `LIVEKIT_URL` في agent/.env غلط | لازم `ws://192.168.8.5:7880` (مش localhost — لأن الـ docker بيـ bind على 0.0.0.0) |

---

## التنظيف بعد الاختبار

```powershell
docker compose down
# لو عاوز تمسح الـ trunk من LiveKit (مش من DB، الـ DB مش جزء من ده)
python -m agent.ops.provision_tenant deprovision --slug local-test  # (لو ضفت أمر التنظيف)
```

---

## بعد ما تنجح محلياً

نقل الإعدادات للسيرفر:
1. غيّر `LIVEKIT_URL` في الـ FastAPI .env إلى `wss://lk.aloegy.ai`
2. تأكد إن `LIVEKIT_API_KEY/SECRET` متطابقين بين السيرفر والـ FastAPI
3. تأكد `nat_external_ip` في `/root/Livekit_backend/configs/sip.yaml` = الـ public IP بتاع السيرفر
4. اعمل provision على السيرفر بـ IP الـ Issabel الحقيقي بتاع العميل
