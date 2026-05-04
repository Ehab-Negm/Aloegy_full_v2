# 🔌 Backend Integration Guide — Voice Agent

> **هذا الملف للمطوّر المسؤول عن الباك اند.**  
> بيشرح بالتفصيل كل endpoint الـ agent بيطلبه، وإيه اللي بيبعته، وإيه اللي بيتوقع يرجعه.

---

## أولاً: نظرة عامة

الـ agent بيكلم الباك اند في **4 حالات بس**:

| # | متى؟ | الـ Endpoint | الـ Method |
|---|------|-------------|-----------|
| 1 | أول المكالمة | `/restaurant/config` | GET |
| 2 | لما العميل يأكد طلب تيك اواي أو توصيل | `/orders` | POST |
| 3 | لما العميل يأكد حجز ترابيزة | `/reservations` | POST |
| 4 | لما العميل يعمل شكوى | `/complaints` | POST |

---

## الـ Authentication

**كل request** بيحتوي على:

```
X-API-Key: <BACKEND_API_KEY>
Content-Type: application/json
```

> القيمة جاية من env var: `BACKEND_API_KEY`

---

## 1. GET `/restaurant/config`

### متى بيتعمل؟
مرة واحدة في **بداية كل مكالمة**، وبعدين بيعتمد على cache لمدة 60 ثانية (قابلة للتخصيص).

### الـ Request

```
GET https://yourapi.com/restaurant/config
```

**Headers:**
```
X-API-Key: abc123
X-Restaurant-ID: rest_456    ← فقط لو multi-tenant
```

**Query Params:**
```
?restaurant_id=rest_456      ← نفس القيمة، للباك اند اللي بيقرا query params
```

> **ملاحظة:** لو عندك مطعم واحد — مفيش `restaurant_id` خالص.  
> لو عندك أكتر من مطعم — الـ `restaurant_id` بيجي من **room metadata** في LiveKit (اللي إنت بتحدده لما بتنشئ الـ room).

### الـ Response المطلوب

```json
{
  "name": "مطعم الكشري الأصيل",
  "phone": "01000000000",
  "address": "15 شارع التحرير، القاهرة",

  "branches": [
    { "name": "فرع المعادي",  "address": "8 شارع 9، المعادي" },
    { "name": "فرع مدينة نصر", "address": "أمام أركان مول" }
  ],

  "hours": {
    "saturday":  { "open": "10:00", "close": "23:00" },
    "sunday":    { "open": "10:00", "close": "23:00" },
    "monday":    { "open": "10:00", "close": "23:00" },
    "tuesday":   { "open": "10:00", "close": "23:00" },
    "wednesday": { "open": "10:00", "close": "23:00" },
    "thursday":  { "open": "10:00", "close": "00:00" },
    "friday":    { "closed": true }
  },

  "menu_items": [
    { "name": "كشري صغير",  "price": 15,   "available": true  },
    { "name": "كشري وسط",   "price": 20,   "available": true  },
    { "name": "كشري كبير",  "price": 25,   "available": true  },
    { "name": "عصير ليمون", "price": 12,   "available": true  },
    { "name": "مياه",       "price": 5,    "available": true  },
    { "name": "مكرونة",     "price": 30,   "available": false }
  ],

  "upsell_rules": [
    { "trigger": "كشري",   "suggestion": "عصير ليمون طازج بـ 12 جنيه?" },
    { "trigger": "توصيل",  "suggestion": "تحب تضيف مياه معدنية?" }
  ],

  "is_open": true,
  "closed_reason": "",

  "wait_minutes": 20,

  "min_guests": 1,
  "max_guests": 20,

  "delivery_enabled": true,
  "delivery_minutes": 45,
  "delivery_fee": 15.0,
  "min_order": 80.0,
  "delivery_zones": ["المعادي", "دار السلام", "طره", "حلوان"]
}
```

### الـ Fields بالتفصيل

| Field | النوع | مطلوب؟ | الوصف |
|-------|-------|---------|-------|
| `name` | string | **✅ required** | اسم المطعم — بيتحدث فيه الـ agent |
| `phone` | string | موصى | رقم تليفون المطعم لو في مشاكل |
| `address` | string | موصى | العنوان الرئيسي لو مفيش branches |
| `branches` | array | اختياري | فروع المطعم — لو فاضي بيستخدم `address` |
| `hours` | object | موصى | مفاتيح: `saturday/sunday/monday/...friday` |
| `hours[day].open` | string `"HH:MM"` | - | وقت الفتح |
| `hours[day].close` | string `"HH:MM"` | - | وقت الإغلاق |
| `hours[day].closed` | bool | - | لو `true` اليوم ده مغلق |
| `menu_items` | array | **✅ required** | قائمة الأصناف |
| `menu_items[].name` | string | **✅ required** | اسم الصنف |
| `menu_items[].price` | number | **✅ required** | السعر (جنيه) — لازم number مش string |
| `menu_items[].available` | bool | اختياري | افتراضي `true` |
| `upsell_rules` | array | اختياري | قواعد اقتراح أصناف إضافية |
| `upsell_rules[].trigger` | string | - | الكلمة اللي بتشغّل الاقتراح |
| `upsell_rules[].suggestion` | string | - | الجملة اللي يقولها الـ agent |
| `is_open` | bool | موصى | `false` = المطعم مغلق دلوقتي |
| `closed_reason` | string | اختياري | لو `is_open=false`، السبب |
| `wait_minutes` | int | اختياري | وقت انتظار التيك اواي (default: 20) |
| `min_guests` | int | اختياري | أقل حجز (default: 1) |
| `max_guests` | int | اختياري | أقصى حجز في مكالمة واحدة (default: 20) |
| `delivery_enabled` | bool | **✅ إذا كان التوصيل متاح** | `false` = agent مش هيعرض توصيل |
| `delivery_minutes` | int | لو delivery | وقت التوصيل المتوقع (minutes) |
| `delivery_fee` | float | لو delivery | رسوم التوصيل بالجنيه (0 = مجاني) |
| `min_order` | float | لو delivery | أقل قيمة طلب لقبول التوصيل (0 = بدون حد) |
| `delivery_zones` | string[] | لو delivery | قائمة مناطق التوصيل (فاضية = كل المناطق) |

### لو الباك اند مش شغال
الـ agent هيستخدم **fallback config** تلقائياً:
- `is_open = false`
- رسالة للعميل: "النظام مش متاح دلوقتي، اتصل بنا مباشرة"
- لا يتم أي طلب أو حجز

---

## 2. POST `/orders`

### متى بيتعمل؟
لما العميل **يأكد طلبه كامل** — بعد الاسم والرقم وتأكيد الطلب.

### الـ Request — تيك اواي

```
POST https://yourapi.com/orders
```

**Headers:**
```
X-API-Key: abc123
Content-Type: application/json
Idempotency-Key: a1b2c3d4-takeaway-f3a2b1c9d8e7f6a5
```

**Body:**
```json
{
  "call_id":          "a1b2c3d4",
  "type":             "takeaway",
  "customer_name":    "أحمد محمود",
  "customer_phone":   "01012345678",
  "order_items": [
    { "name": "كشري كبير",  "qty": 2, "price": 25.0 },
    { "name": "عصير ليمون", "qty": 1, "price": 12.0 }
  ],
  "special_requests": "بدون بصل محمر",
  "order_time":       "2026-03-01T02:30:00+00:00",
  "channel":          "voice_agent"
}
```

### الـ Request — توصيل

```json
{
  "call_id":           "a1b2c3d4",
  "type":              "delivery",
  "customer_name":     "سارة أحمد",
  "customer_phone":    "01112345678",
  "order_items": [
    { "name": "كشري وسط", "qty": 3, "price": 20.0 }
  ],
  "special_requests":  null,
  "delivery_address":  "8 شارع النيل، عمارة رقم 5، شقة 12",
  "delivery_zone":     "المعادي",
  "delivery_landmark": "أمام بنك مصر",
  "order_time":        "2026-03-01T02:35:00+00:00",
  "channel":           "voice_agent"
}
```

### الـ Fields

| Field | النوع | الوصف |
|-------|-------|-------|
| `call_id` | string | ID المكالمة — 8 أحرف hex |
| `type` | string | `"takeaway"` أو `"delivery"` |
| `customer_name` | string | اسم العميل |
| `customer_phone` | string | رقم مصري مثل `01012345678` |
| `order_items` | array | قائمة الأصناف المفصّلة |
| `order_items[].name` | string | اسم الصنف من المنيو |
| `order_items[].qty` | int | الكمية (دايماً ≥ 1) |
| `order_items[].price` | float | السعر من config وقت الطلب |
| `special_requests` | string\|null | طلبات خاصة أو `null` |
| `order_time` | string ISO 8601 UTC | وقت تسجيل الطلب |
| `channel` | string | دايماً `"voice_agent"` |
| `delivery_address` | string | **توصيل فقط** — العنوان كامل |
| `delivery_zone` | string | **توصيل فقط** — الحي / المنطقة |
| `delivery_landmark` | string | **توصيل فقط** — علامة مميزة قريبة |

### الـ Response المطلوب

```json
{
  "order_id":       "ORD-2026-00123",
  "estimated_time": 20
}
```

| Field | النوع | الوصف |
|-------|-------|-------|
| `order_id` | string | الـ agent بيقوله للعميل صوتياً |
| `estimated_time` | int (minutes) | وقت الانتظار — لو مش موجود بيستخدم قيمة الـ config |

### الـ Idempotency Key
```
Format: {call_id}-{type}-{sha256_hex_16}
Example: a1b2c3d4-takeaway-f3a2b1c9d8e7f6a5
```
لو الـ agent بعت نفس الطلب مرتين (بسبب retry) — الباك اند **لازم يعمل** noop ويرجّع نفس الـ `order_id`.

---

## 3. POST `/reservations`

### متى بيتعمل؟
لما العميل **يأكد حجز الترابيزة** — بعد الوقت والعدد والاسم والرقم.

### الـ Request

```
POST https://yourapi.com/reservations
```

**Headers:**
```
X-API-Key: abc123
Content-Type: application/json
Idempotency-Key: a1b2c3d4-reservation-d8e7f6a5b4c3d2e1
```

**Body:**
```json
{
  "call_id":          "a1b2c3d4",
  "customer_name":    "محمد علي",
  "customer_phone":   "01512345678",
  "reservation_time": "السبت الجاي الساعة 8 بالليل",
  "guests_count":     4,
  "branch":           "فرع المعادي",
  "notes":            "عيد ميلاد، محتاجين كيكة",
  "channel":          "voice_agent"
}
```

### الـ Fields

| Field | النوع | الوصف |
|-------|-------|-------|
| `call_id` | string | ID المكالمة |
| `customer_name` | string | اسم العميل |
| `customer_phone` | string | رقم تليفون |
| `reservation_time` | string | **نص حر** كما قاله العميل — الباك اند بيفسّره |
| `guests_count` | int | عدد الضيوف (بين min_guests و max_guests) |
| `branch` | string\|null | اسم الفرع — `null` لو فرع واحد |
| `notes` | string\|null | طلبات خاصة للحجز |
| `channel` | string | دايماً `"voice_agent"` |

> ⚠️ **مهم:** `reservation_time` بيبقى **نص عربي حر** مثل "السبت الجاي الساعة 8 بالليل". الباك اند هو المسؤول عن تحويله لـ datetime صح.

### الـ Response المطلوب

```json
{
  "reservation_id": "RES-2026-00456"
}
```

| Field | النوع | الوصف |
|-------|-------|-------|
| `reservation_id` | string | الـ agent بيقوله للعميل صوتياً |

---

## 4. POST `/complaints`

### متى بيتعمل؟
فور ما الـ agent يسمع شكوى ويسجّلها — **بدون انتظار تأكيد العميل** (fire-and-forget).

### الـ Request

```
POST https://yourapi.com/complaints
```

**Headers:**
```
X-API-Key: abc123
Content-Type: application/json
Idempotency-Key: a1b2c3d4-complaint-c3d2e1f0a9b8c7d6
```

**Body:**
```json
{
  "call_id":        "a1b2c3d4",
  "customer_name":  "خالد سمير",
  "customer_phone": "01212345678",
  "complaint_text": "الطلب وصل بارد والأكل ناقص صنف",
  "complaint_type": "delivery",
  "logged_at":      "2026-03-01T02:45:00+00:00",
  "channel":        "voice_agent"
}
```

### الـ Fields

| Field | النوع | الوصف |
|-------|-------|-------|
| `call_id` | string | ID المكالمة |
| `customer_name` | string\|null | اسم العميل لو اتعرف |
| `customer_phone` | string\|null | رقم التليفون لو اتأخذ |
| `complaint_text` | string | ملخص الشكوى بالعربي |
| `complaint_type` | string | نوع الشكوى — القيم ممكنة: |
| | | `order_issue` — طلب غلط أو ناقص |
| | | `quality` — جودة الأكل |
| | | `service` — خدمة سيئة |
| | | `delivery` — مشكلة توصيل |
| | | `other` — غير ذلك |
| `logged_at` | string ISO 8601 UTC | وقت تسجيل الشكوى |
| `channel` | string | دايماً `"voice_agent"` |

### الـ Response
الـ agent **بيتجاهل الـ response** للشكاوى — أي response بـ 2xx كافي.  
في حالة فشل الـ request، الـ agent هيكمل المكالمة عادي بس هيلوج الـ error.
