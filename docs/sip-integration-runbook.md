# دليل توصيل Issabel بـ AloEgy عبر SIP

دليل عملي للمسؤول التقني عند العميل (admin Issabel) لتحويل المكالمات الواردة على رقم المطعم لـ AloEgy AI agent.

**الهدف:** زبون يضرب رقم المطعم → الـ Issabel بتاعك يحوّل المكالمة لـ `sip.aloegy.ai` → ليلي ترد بالعربي وتاخد الطلب.

---

## ١. متطلبات قبل ما تبدأ

| المتطلب | لازم |
|---------|------|
| Issabel سيرفر شغال | ✅ |
| رقم E.164 ظاهر للزبون (مثلاً +201001234567) | ✅ |
| Public IP ثابت لـ Issabel السيرفر | ✅ مهم جداً |
| Codecs مدعومة: G.711 ulaw، G.711 alaw، أو Opus | ✅ |
| مفتوح Outbound في الـ firewall: UDP 5060، UDP 10000-20000 → `sip.aloegy.ai` | ✅ |
| اتصال إنترنت ثابت (latency < 200ms لـ Frankfurt) | مفضل |

**اللي AloEgy هتاخده منك:**
- الـ DID (الرقم اللي الزبون هيضربه)
- IP العام لـ Issabel السيرفر بتاعك (للـ allowlist عندنا)
- اسم المطعم slug (مثلاً `pizza-king`)

**اللي AloEgy هترسله لك:**
- SIP URI نوصلك بيه (مثلاً `sip:201001234567@sip.aloegy.ai`)
- Optional: SIP username/password لو اخترنا digest auth بدل IP allowlist

---

## ٢. خطوة ١ — Trunk جديد في Issabel

افتح Issabel admin panel → **PBX** → **Trunks** → **Add SIP (chan_pjsip) Trunk**

### General
- **Trunk Name:** `aloegy-out`
- **Outbound CallerID:** الـ DID بتاعك (مثلاً `"Pizza King" <+201001234567>`)
- **Maximum Channels:** ٣٠ (أو حسب الباقة)

### pjsip Settings → General
- **Username:** `<slug-الخاص-بك>` (AloEgy هتقولك عليه)
- **Secret:** فاضي (لو IP allowlist) أو الـ password اللي AloEgy بعتته
- **Authentication:** `Outbound` فقط
- **Registration:** `None` (لو IP allowlist) أو `Send` (لو digest auth)
- **Language Code:** `ar`

### pjsip Settings → Advanced
- **SIP Server:** `sip.aloegy.ai`
- **SIP Server Port:** `5060`
- **Transport:** `udp` فقط (TLS مش متاح حالياً في الـ gateway)
- **Outbound Proxy:** فاضي
- **From User:** `<slug-الخاص-بك>`
- **From Domain:** `sip.aloegy.ai`
- **Match (Permit):** فاضي
- **DTMF Mode:** `RFC4733`

### Codecs (مهم!)
**Allow:** `ulaw`, `alaw`, `opus`
**Disable:** كل الباقي (`g729`, `gsm`, `g726`, `ilbc`, `g722`)

أي codec غير دول هيرفض من LiveKit-SIP.

### SRTP / Encryption
- اتركها `No` — الـ gateway حالياً UDP عادي بدون TLS/SRTP. (هنفعّلها لاحقاً لما TLS port يفتح.)

اضغط **Submit** → **Apply Config**.

---

## ٣. خطوة ٢ — Inbound Route للـ DID

**PBX → Inbound Routes → Add Inbound Route**

- **Description:** `aloegy-pizza-king` (أو حسب اسم المطعم)
- **DID Number:** الرقم اللي الزبون هيضربه (مثلاً `201001234567`)
- **CallerID Number:** فاضي (يقبل أي زبون)

في تبويب **Set Destination:**
- **Destination:** `Trunks` → `aloegy-out`

في تبويب **Other:**
- **Failover Destination:** اختار **IVR محلي** أو **Voicemail** (مهم: ده اللي بيشتغل لو AloEgy وقعت)

اضغط **Submit** → **Apply Config**.

---

## ٤. خطوة ٣ — Outbound Route (لو لسه مش متظبط)

في حالات نادرة الـ Inbound Routes ما بيرسلش direct للـ Trunk بدون Outbound Route helper.

**PBX → Outbound Routes → Add Outbound Route**

- **Route Name:** `aloegy-helper`
- **Trunk Sequence for Matched Routes:** `aloegy-out`
- **Dial Pattern:** match `+20*` (أو `*` للسهولة)

---

## ٥. خطوة ٤ — Firewall checklist على VPS Issabel

```bash
# Outbound SIP signaling
sudo ufw allow out 5060/udp

# Outbound RTP (الـ gateway حالياً 10000-10100، خد range أوسع للأمان)
sudo ufw allow out 10000:20000/udp

# Inbound RTP من LiveKit
sudo ufw allow from <LIVEKIT_PUBLIC_IP> to any port 10000:20000 proto udp
```

استبدل `<LIVEKIT_PUBLIC_IP>` بالـ IP اللي AloEgy هتديك.

---

## ٦. خطوة ٥ — NAT (لو Issabel ورا router/firewall)

في `/etc/asterisk/pjsip.conf` أو من الـ panel:

- **External Signaling Address:** `<public-ip-of-issabel>`
- **External Media Address:** نفسه
- **Local Network:** `192.168.0.0/16` (أو شبكتك الداخلية)

ده مهم جداً — لو ساكتة، الـ SDP هيحط الـ private IP في الـ media offer وLiveKit مش هيقدر يبعت RTP لك.

---

## ٧. خطوة ٦ — Smoke test

### من الـ Issabel:
```bash
# تأكد إن الـ trunk مسجل / متاح
sudo asterisk -rx "pjsip show endpoint aloegy-out"
# لازم Status = Available
```

### اضرب الـ DID من موبايل خارجي:
١. اضرب الرقم
٢. لازم تسمع رنين قصير (٢-٣ ثواني)
٣. ليلي بترد: "السلام عليكم، أهلاً بحضرتك في {اسم المطعم}، تحبي تطلبي إيه؟"

### لو في مشكلة، شوف الـ SIP traffic live:
```bash
sudo apt install sngrep
sudo sngrep -d any port 5060
```

### الـ logs المهمة:
- **Issabel:** `tail -f /var/log/asterisk/full | grep aloegy-out`
- **AloEgy backend:** AloEgy هتديك link لـ Grafana dashboard (`Active SIP calls per tenant`, `INVITE 4xx/5xx rate`)

---

## ٨. مشاكل شائعة وحلولها

### "ما يردش" — INVITE بيتبعت بس مفيش 200 OK
- **سبب:** الـ IP بتاعك مش في allowlist عندنا
- **الحل:** كلمنا بـ IP الفعلي بتاعك (شيك بـ `curl ifconfig.me`)

### يسمع ليلي صوته بس هي مش بتسمعه (one-way audio)
- **سبب:** NAT مش متظبط على Issabel، أو RTP ports مغلقة
- **الحل:** راجع خطوة ٥ و٦

### "Codec mismatch" أو الصوت متقطّع
- **سبب:** فعّلت codec غير المدعومة (g729 خصوصاً)
- **الحل:** disable كل codec غير ulaw/alaw/opus

### الـ trunk Status = Unavailable في Issabel
- **سبب:** Issabel بيعمل OPTIONS keepalive لكن LiveKit-SIP مش بيرد عليها (depending on version)
- **الحل:** ضع `qualify_frequency=0` في pjsip endpoint (يعطل keepalive). الـ trunk هيشتغل صح حتى لو Status = Unavailable.

### رقم الزبون مش بيوصل للـ AloEgy (UserData.caller_phone فاضي)
- **سبب:** Issabel بيبعت `From: anonymous@...` بدلاً من رقم الزبون
- **الحل:** فعّل **Send PAI (P-Asserted-Identity)** في إعدادات الـ trunk

---

## ٩. Failover (مهم جداً)

لو AloEgy وقع لأي سبب، الـ Inbound Route لازم يحوّل المكالمة لـ:

- **IVR محلي** يقول "للأسف الخدمة غير متاحة دلوقتي، حضرتك ممكن تتصل تاني بعد دقايق"
- أو **Voicemail** علشان الزبون يسجّل طلبه
- أو **Extension موظف** مباشرة (لو فيه موظف على الـ desk)

اعمل ده من **Inbound Route → Other → Failover Destination**.

---

## ١٠. الأمان

- **شيلت IP allowlist؟** ده الحماية الأساسية — بيمنع أي حد غير IP بتاعك يبعت INVITE لـ trunk بتاعك في AloEgy
- **متشاركش الـ SIP credentials** مع حد لو AloEgy استخدمت digest auth
- **Outbound calls معطّلة افتراضياً** — حتى لو حد اخترق الـ trunk، مش هيقدر يعمل toll fraud من خلاله
- **TLS/SRTP encryption مش متاحة حالياً** — هنضيفها لاحقاً. الحماية حالياً معتمدة على IP allowlist

---

## ١١. Operations يومية

| نشاط | أداة | تكرار |
|-----|------|-------|
| مراقبة الـ active calls | Grafana dashboard | live |
| مراجعة INVITE failures | Grafana / `sngrep` | يومياً |
| backup config Issabel | snapshot + git | أسبوعي |

---

## أرقام للتواصل

- **Support:** support@aloegy.ai
- **Emergency:** WhatsApp +20XXXXXXXXX (متاح ٢٤/٧)
- **Docs:** https://docs.aloegy.ai/sip
