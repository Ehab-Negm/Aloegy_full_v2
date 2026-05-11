# إضافة SIP على نفس الـ Hostinger VPS

ده ملحق لـ [DEPLOY.md](../DEPLOY.md). بفترض إن الـ VPS الـ Hostinger KVM 16 شغّال بالفعل بـ:

- `livekit-server` كـ systemd service `livekit` على port 7880
- nginx بـ `lk.aloegy.ai`, `api.aloegy.ai`, `aloegy.ai`
- LiveKit API key: `APICCqUMXRRWoH9` / secret: `VPxSH79KUaf6kpafHIJRsdQqOihe41Ryz6IE1UfUi97B`

محدش هيتلمس من الإعداد ده. هنضيف بس **livekit-sip** كـ service جديد جنبه.

## Resource budget

| الخدمة | RAM | vCPU |
|---------|-----|------|
| livekit-server | ~500 MB | 1 |
| livekit-sip (الجديد) | ~200 MB | 1 |
| backend (gunicorn 2 workers) | ~400 MB | 1 |
| agent (Python worker) | ~600 MB | 1 |
| frontend (static via nginx) | negligible | - |
| nginx | ~100 MB | - |
| redis (الجديد) | ~50 MB | - |
| **مجموع** | **~2 GB** | **4** |

KVM 16 (16 vCPU / 96 GB RAM) فيه مساحة هائلة. تقدر تشغّل ٢٠٠+ مكالمة متزامنة من نفس الجهاز.

---

## Step 1 — DNS

في Hostinger DNS panel، ضيف:

```
A    sip    → YOUR_VPS_IP
```

نفس الـ IP بتاع `aloegy.ai`. ده هيخلي العميل يضرب SIP INVITE على `sip.aloegy.ai:5061` ويوصل لنا.

---

## Step 2 — Install

من جهازك المحلي، ارفع المجلد:

```bash
scp -r infra/livekit root@YOUR_VPS_IP:/opt/aloegy/infra-livekit
```

ادخل على الـ VPS:

```bash
ssh root@YOUR_VPS_IP
cd /opt/aloegy/infra-livekit
chmod +x install-sip.sh
LIVEKIT_API_SECRET=VPxSH79KUaf6kpafHIJRsdQqOihe41Ryz6IE1UfUi97B \
PUBLIC_IP=YOUR_VPS_IP \
./install-sip.sh
```

السكربت بيعمل:
1. ينزل `redis-server` لو مش موجود
2. ينزل `livekit-sip` binary من GitHub releases
3. يكتب `/etc/livekit/sip.yaml` و `/etc/livekit/sip.env` (mode 600)
4. ينشئ systemd service `aloegy-sip`
5. يفتح firewall ports: 5060/UDP+TCP، 5061/TCP، 10000-20000/UDP
6. يشغّل الـ service

---

## Step 3 — Verify

```bash
# الخدمة شغّالة؟
systemctl status aloegy-sip
journalctl -u aloegy-sip -n 50 --no-pager

# الـ ports مفتوحة؟
ss -tunlp | grep -E ':(5060|5061|10000|10500)'
ufw status verbose | grep -E '5060|5061|10000'

# DNS بيرجّع الصح؟
dig +short sip.aloegy.ai
# لازم يطلع YOUR_VPS_IP
```

---

## Step 4 — تأكد إن الـ backend عنده الـ keys

في `/opt/aloegy/backend/.env` تأكد:

```env
LIVEKIT_URL=wss://lk.aloegy.ai
LIVEKIT_API_KEY=APICCqUMXRRWoH9
LIVEKIT_API_SECRET=VPxSH79KUaf6kpafHIJRsdQqOihe41Ryz6IE1UfUi97B
LIVEKIT_AGENT_NAME=aloegy-agent
LIVEKIT_SIP_HOST=sip.aloegy.ai
```

لو مش موجود، ضيفه و:

```bash
systemctl restart aloegy-backend
```

---

## Step 5 — Provision أول عميل

خد بيانات العميل: الـ DID والـ public IP لـ Issabel.

```bash
# لو معاك admin token جاهز:
curl -X POST https://api.aloegy.ai/admin/restaurants/123/sip-provision \
     -H 'Authorization: Bearer YOUR_ADMIN_JWT' \
     -H 'Content-Type: application/json' \
     -d '{
       "did": "+201001234567",
       "issabelIp": "41.45.123.45"
     }'
```

الـ response:
```json
{
  "alreadyProvisioned": false,
  "trunkId": "ST_xxx",
  "dispatchRuleId": "SDR_xxx",
  "did": "+201001234567",
  "issabelIp": "41.45.123.45",
  "provisionedAt": "2026-05-10T...",
  "sipUri": "sip:201001234567@sip.aloegy.ai"
}
```

ابعت الـ `sipUri` للعميل + اللينك بتاع [docs/sip-integration-runbook.md](sip-integration-runbook.md) — هو هيظبط Issabel بنفسه على الـ checklist.

---

## Step 6 — Smoke test

بعد ما العميل يخلص setup:

```bash
# capture SIP packets live
apt install -y sngrep
sngrep -d any port 5060 or port 5061
```

اضرب الـ DID بتاع المطعم من موبايل خارجي. لازم تشوف:
1. INVITE قادم من IP العميل في sngrep
2. 200 OK رجع
3. agent log: `call=<call-id> | started | source=sip | caller=+20XXX | trunk=ST_xxx`
4. ليلي بترد بالعربي

---

## Diagnostics

### الـ INVITE بيوصل بس بيترفض بـ 403

سبب شائع: الـ IP بتاع العميل مش في الـ trunk allowlist.

```bash
# من الـ VPS
journalctl -u aloegy-sip -n 100 | grep -i 'reject\|403\|trunk'
```

اعمل re-provision بالـ IP الصحيح:
```bash
curl -X DELETE https://api.aloegy.ai/admin/restaurants/123/sip-provision \
     -H 'Authorization: Bearer YOUR_ADMIN_JWT'
# بعدها provision تاني بالـ IP الصح
```

### الصوت one-way (يسمع ليلي بس مش العكس، أو العكس)

NAT mishap. تأكد:
```bash
grep nat_external_ip /etc/livekit/sip.yaml
# لازم يطلع PUBLIC_IP الصحيح، مش 127.0.0.1
```

لو غلط، عدّل `/etc/livekit/sip.env` و:
```bash
systemctl restart aloegy-sip
```

### Codec mismatch

الزبون بيسمع silence أو الكلام مقطّع.

تأكد إن Issabel معاه `ulaw,alaw,opus` فقط enabled (مش g729). راجع [sip-integration-runbook.md](sip-integration-runbook.md) section 2.

---

## Operations يومية

```bash
# logs live
journalctl -u aloegy-sip -f

# active calls
ss -tun | grep -E ':5060|:5061' | wc -l

# restart بعد config change
systemctl restart aloegy-sip
```

---

## Backups

`/etc/livekit/sip.yaml` و `/etc/livekit/sip.env` يدخلوا في الـ backup الموجود. الـ trunks/dispatch rules نفسها مخزّنة في livekit-server's internal state + restaurants table في الـ DB، فالـ DB backup كافي للـ recovery.
