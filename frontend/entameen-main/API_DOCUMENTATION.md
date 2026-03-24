# API Documentation - Aloegy

كل الـ APIs اللي الفرونت محتاجها موجودة في `src/services/api.ts`.

## Setup

```ts
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.trim() || "http://127.0.0.1:8000";
const SESSION_API_BASE_URL = import.meta.env.VITE_SESSION_API_BASE_URL?.trim() || API_BASE_URL;
```

- `VITE_API_BASE_URL`
  للباک الموحد: اللوجين، الداشبورد، الأدمن، السيلز، والـ agent contract.
- `VITE_SESSION_API_BASE_URL`
  اختياري. افتراضيًا بيساوي نفس الباك الموحد لأن endpoint `POST /demo/livekit-session` بقى جواه.

## Main endpoints

### Public

| Method | Endpoint | Body | Response | Used in |
|---|---|---|---|---|
| `POST` | `/contact` | `{ restaurantName, phone, message }` | `true` | `src/pages/Index.tsx` |
| `POST` | `/demo/livekit-session` | `{ restaurantId?: string, participantName?: string }` | `{ livekitUrl, roomName, token, participantIdentity, participantName, restaurantId, roomMetadata, expiresInSeconds }` | `src/components/VoiceAssistantWidget.tsx` |

### Authentication

| Method | Endpoint | Body | Response | Used in |
|---|---|---|---|---|
| `POST` | `/auth/send-otp` | `{ phone }` | `{ success, devOtp? }` | `src/pages/Login.tsx` |
| `POST` | `/auth/verify-otp` | `{ phone, otp }` | `{ success, token, role }` | `src/pages/Login.tsx` |
| `GET` | `/me` | - | `{ id, name, phone, role, restaurantId, restaurantName }` | `src/pages/Dashboard.tsx` |

### Owner / Employee dashboard

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/stats` | يدعم `restaurantId` للأدمن |
| `GET` | `/calls` | يدعم `restaurantId` للأدمن |
| `GET` | `/users` | يدعم `restaurantId` للأدمن |
| `GET` | `/orders` | يدعم `restaurantId` للأدمن |
| `PATCH` | `/orders/{orderId}` | تحديث حالة الأوردر ورقم السائق |
| `GET` | `/settings` | يدعم `restaurantId` للأدمن |
| `PUT` | `/settings` | تحديث بيانات المطعم والوكيل |
| `GET` | `/menu-items` | يدعم `restaurantId` للأدمن |
| `POST` | `/menu-items` | إضافة صنف |
| `PUT` | `/menu-items/{itemId}` | تعديل صنف |
| `DELETE` | `/menu-items/{itemId}` | حذف صنف |
| `GET` | `/employees` | يدعم `restaurantId` للأدمن |
| `POST` | `/employees` | إضافة موظف |
| `DELETE` | `/employees/{employeeId}` | حذف موظف |
| `GET` | `/issues` | يدعم `restaurantId` للأدمن |
| `PATCH` | `/issues/{issueId}` | تحديث حالة البلاغ |
| `GET` | `/analytics` | يدعم `restaurantId` للأدمن |

### Files

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/files` | قائمة الملفات |
| `POST` | `/files/upload` | `multipart/form-data` |
| `GET` | `/files/{id}/download` | تحميل الملف |
| `GET` | `/files/{id}/preview` | رابط preview مباشر |

### Admin

| Method | Endpoint | Used in |
|---|---|---|
| `GET` | `/admin/restaurants` | `src/pages/Admin.tsx` |
| `POST` | `/admin/restaurants` | `src/pages/Admin.tsx` |
| `GET` | `/admin/sales-team` | `src/pages/Admin.tsx` |
| `POST` | `/admin/sales-team` | `src/pages/Admin.tsx` |
| `DELETE` | `/admin/sales-team/{memberId}` | `src/pages/Admin.tsx` |
| `GET` | `/admin/sales-requests` | `src/pages/Admin.tsx` |
| `PATCH` | `/admin/sales-requests/{requestId}` | `src/pages/Admin.tsx` |
| `GET` | `/admin/overview` | `src/pages/Admin.tsx` |

### Sales

| Method | Endpoint | Used in |
|---|---|---|
| `GET` | `/sales/requests` | `src/pages/SalesDashboard.tsx` |
| `POST` | `/sales/requests` | `src/pages/SalesDashboard.tsx` |
| `GET` | `/sales/demo-sessions` | `src/pages/SalesDashboard.tsx` |
| `POST` | `/sales/demo-sessions` | `src/pages/SalesDashboard.tsx` |

## Agent contract

الـ agent بيستهلك endpoints دي من نفس الباك:

- `GET /restaurant/config`
- `POST /orders`
- `POST /reservations`
- `POST /complaints`

والتوثيق التنفيذي بتاعها موجود في `backend/main.py` و`agent/agent.py`.

## Wiring guide

1. شغّل الباك الموحد على `http://127.0.0.1:8000`.
2. شغّل الـ agent نفسه عشان LiveKit rooms تشتغل فعلاً.
3. اضبط `VITE_API_BASE_URL` لو الباك على دومين أو بورت مختلف.
4. سيب `VITE_SESSION_API_BASE_URL` فاضي إلا لو endpoint الـ LiveKit session رايح لخدمة تانية عندك.

## Notes

- التوكن بيتخزن في `localStorage` تحت `auth_token`.
- الدور بيتخزن تحت `user_role`.
- رقم الموبايل بيتخزن تحت `user_phone`.
- الصفحات المحمية في الفرونت بقت متغطية بـ route guards بسيطة، لكن الـ authorization الحقيقي من الباك نفسه.
