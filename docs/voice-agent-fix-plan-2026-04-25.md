# Voice Agent Production-Readiness Fix Plan

**Date:** 2026-04-25
**Status:** Ready for implementation
**Owner:** Claude Code (next session)
**Goal:** رفع جودة الـ voice agent لمستوى production يضاهي أو يتفوق على الموظف الحقيقي.

---

## 0. كيف تستخدم الريبورت ده

ده ريبورت تنفيذي. كل قسم فيه:
- **المشكلة** + الـ file:line المحدد
- **السبب الجذري**
- **الحل** بالكود الكامل (قبل / بعد)
- **معايير القبول** (الاختبار)
- **مخاطر الـ rollback**

اشتغل بالترتيب من Phase 1 لـ Phase 4. **متفوّتش أي خطوة**. كل خطوة فيها checkbox — علّمها لما تكمل.

> ⚠️ **مهم جداً:** بعد كل Phase، اعمل manual smoke test (مكالمة كاملة من الأول للآخر) قبل ما تنتقل للـ Phase اللي بعدها.

---

## 1. Executive Summary

### 1.1 الـ Symptoms اللي بيعاني منها المستخدم

| # | العَرَض | التكرار | التأثير |
|---|--------|---------|---------|
| 1 | الـ agent بيسأل عن الأوردر مرتين | متكرر | 🔴 عالي |
| 2 | بينسى تفاصيل اتقالت قبل كده | متكرر | 🔴 عالي |
| 3 | مش فاهم الـ context الكامل للمكالمة | شائع | 🔴 عالي |
| 4 | الأداء inconsistent (نفس السيناريو، ردود مختلفة) | شائع | 🟡 متوسط |
| 5 | أحياناً أضعف من الموظف الحقيقي | شائع | 🟡 متوسط |
| 6 | بينسى أصناف من الطلب أو يكررها | أحياناً | 🟡 متوسط |

### 1.2 الأسباب الجذرية (Root Causes)

| الترتيب | السبب | الـ Impact على الأعراض |
|---------|--------|------------------------|
| 1 | **Context Window صغير** (`PROMPT_HISTORY_ITEMS=4`، `TURN_CHAT_CTX_MAX_ITEMS=14`) | يسبب 1, 2, 3 |
| 2 | **Handoff بيقص الـ context** (يفقد >75% من المحادثة) | يسبب 1, 2, 3 |
| 3 | **الموديل ضعيف** (`gpt-4.1-nano`) | يسبب 4, 5 |
| 4 | **Temperature عالي** (`0.85`) للـ task-oriented agent | يسبب 4 |
| 5 | **Tools مش idempotent** (`update_order` بيستبدل بدل ما يضيف) | يسبب 6 |
| 6 | **Max tokens صغير** (`180`) | يسبب 4 جزئياً |
| 7 | **State summary JSON-only** (مش conversational) | يسبب 1, 2 |
| 8 | **مفيش explicit handoff briefing** | يسبب 1, 2, 3 |
| 9 | **مفيش deduplication على conversation level** | يسبب 6 |
| 10 | **مفيش observability/metrics** | يسبب impossibility of diagnosis |

### 1.3 الـ Roadmap

| Phase | المدة | المخرجات | التوقع |
|-------|------|----------|--------|
| **Phase 1** | يوم | Config tuning (env + model) | 40-50% تحسن في consistency |
| **Phase 2** | 3-4 أيام | Handoff briefing + Tool idempotency + State-aware prompts | 70-80% تقليل تكرار الأسئلة |
| **Phase 3** | 2-3 أيام | Monitoring + Test scenarios | قدرة على القياس |
| **Phase 4** | شهر (اختياري) | Architecture refactor (Unified Agent) | 95%+ production quality |

---

## 2. التحليل المعماري الحالي

### 2.1 الـ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  LiveKit Agent Server (agent/agent.py)                          │
└────────────┬────────────────────────────────────────────────────┘
             │
             ├─► UserData (state/user_data.py) — single source of truth
             │   ├─ CustomerInfo (name, phone)
             │   ├─ OrderState (items, validated, total, ...)
             │   ├─ DeliveryState (address, zone, landmark)
             │   ├─ ReservationState (time, guests, branch)
             │   └─ ComplaintState (text, category)
             │
             ├─► 5 Flow Agents (inherit from BaseAgent):
             │   ├─ Greeter      (entry point, intent routing)
             │   ├─ Takeaway     (pickup orders)
             │   ├─ Delivery     (delivery orders)
             │   ├─ Reservation  (table bookings)
             │   └─ Complaint    (complaint handling)
             │
             └─► AgentSession (LiveKit SDK):
                 ├─ STT: Soniox stt-rt-v4
                 ├─ LLM: gpt-4.1-nano (temp=0.85, max=180)
                 ├─ TTS: Hamsa "Nermin" (Egyptian)
                 └─ VAD + Chat Context (14 items)

Backend (backend/main.py): FastAPI + SQLite
  - GET /config/{restaurant_id}  → menu, hours, zones
  - POST /order/{takeaway|delivery} → submit
  - POST /reservation, /complaint
```

### 2.2 الـ Files المهمة

| الملف | الدور | الحجم |
|-------|-------|-------|
| [agent/agent.py](../agent/agent.py) | Entrypoint + LLM/STT/TTS config + helpers | ~4000 سطر |
| [agent/base_agent.py](../agent/base_agent.py) | Base class + handoff logic + tools | ~730 سطر |
| [agent/flows/greeter.py](../agent/flows/greeter.py) | Routing agent | ~225 سطر |
| [agent/flows/delivery.py](../agent/flows/delivery.py) | Delivery flow | ~298 سطر |
| [agent/flows/takeaway.py](../agent/flows/takeaway.py) | Takeaway flow | ~194 سطر |
| [agent/flows/reservation.py](../agent/flows/reservation.py) | Reservation flow | — |
| [agent/state/user_data.py](../agent/state/user_data.py) | Shared state | ~280 سطر |
| [agent/.env](../agent/.env) | Configuration | 52 سطر |

---

## 3. المشاكل بالتفصيل (Problem Catalog)

### 🔴 P-01: Context Window صغير جداً

**الموقع:** [agent/.env:32-33](../agent/.env#L32-L33)
**الكود الحالي:**
```env
PROMPT_HISTORY_ITEMS=4
TURN_CHAT_CTX_MAX_ITEMS=14
```

**مرجع الكود:** [agent/agent.py:197-198](../agent/agent.py#L197-L198)
```python
PROMPT_HISTORY_ITEMS = _get_env_int("PROMPT_HISTORY_ITEMS", 2, min_value=2)
TURN_CHAT_CTX_MAX_ITEMS = _get_env_int("TURN_CHAT_CTX_MAX_ITEMS", 10, min_value=8)
```

**السبب الجذري:**
- بعد كل handoff، الـ chat context بيتقص لـ 4 رسائل بس (= 2 user + 2 agent).
- مكالمة طبيعية بتاخد 15-25 turn. ده معناه إن **80%+ من المحادثة بتختفي**.

**الـ Impact:**
- الـ LLM بيشوف "صفحة بيضا" بعد الـ handoff.
- مفيش طريقة يعرف بيها اللي اتقال قبل كده غير الـ `ud.summarize()` JSON.
- بيكرر الأسئلة لأنه ببساطة **مش شايف** إنه سأل قبل كده.

**الحل:** Phase 1 / Fix #1.

---

### 🔴 P-02: Handoff بيمحي الـ Context

**الموقع:** [agent/base_agent.py:115-152](../agent/base_agent.py#L115-L152)

**الكود الحالي (مختصر):**
```python
chat_ctx = self.chat_ctx.copy()
_strip_marked_system_messages(chat_ctx, ...)  # يشيل system prompts القديمة
_limit_chat_ctx_preserving_system(chat_ctx, max_non_system_items=PROMPT_HISTORY_ITEMS)  # يقص لـ 4

if isinstance(ud.prev_agent, Agent):
    prev_ctx = ud.prev_agent.chat_ctx.copy(...)
    prev_items = _recent_chat_ctx_non_system_items(prev_ctx, max_items=PROMPT_HISTORY_ITEMS)  # 4 بس!
    seen = {getattr(item, "id", None) or f"object:{id(item)}" for item in chat_ctx.items}
    chat_ctx.items.extend(item for item in prev_items if ... not in seen)

_strip_marked_system_messages(chat_ctx, ...)  # يشيل تاني (مرتين!)
```

**المشاكل:**
1. الـ deduplication بـ `f"object:{id(item)}"` (Python object ID) مش valid عبر agents مختلفة.
2. Stripping بيحصل **مرتين** بدون سبب واضح.
3. مفيش explicit briefing للـ agent الجديد عن اللي اتجمع.
4. الـ LLM بيبقى عنده fragments من المحادثة، مش flow متماسك.

**الحل:** Phase 2 / Fix #5.

---

### 🟡 P-03: الموديل ضعيف للمهمة

**الموقع:** [agent/.env:24](../agent/.env#L24)
```env
SESSION_LLM_MODEL=gpt-4.1-nano
```

**السبب:**
- `gpt-4.1-nano` موديل خفيف جداً (distilled).
- مهمة restaurant ordering بالعربية المصرية + multi-agent + tool calling **محتاجة موديل أقوى**.
- نتيجته: hallucinations، عدم اتباع التعليمات بدقة، تكرار أسئلة.

**الحل:** Phase 1 / Fix #2. الخيارات:
- `gpt-4o-mini` — توازن سرعة/جودة (موصى به).
- `gpt-4o` — أقوى لكن أبطأ وأغلى.
- `claude-haiku-4-5` — سريع ودقيق للعربية.

---

### 🟡 P-04: Temperature عالي للـ Task-Oriented Agent

**الموقع:** [agent/.env:28](../agent/.env#L28)
```env
SESSION_LLM_TEMPERATURE=0.85
```

**السبب:**
- 0.85 مناسب للـ creative writing، مش للـ ordering.
- بيخلي الـ LLM "يبدع" في الردود → inconsistency.
- Best practice للـ task agents: **0.2 - 0.4**.

**الحل:** Phase 1 / Fix #2.

---

### 🟡 P-05: Max Completion Tokens محدود

**الموقع:** [agent/.env:27](../agent/.env#L27)
```env
SESSION_LLM_MAX_COMPLETION_TOKENS=180
```

**السبب:**
- 180 token = ~50-70 كلمة عربية.
- لو الـ agent عايز يقول reply طبيعي + يستدعي tool → ممكن يتقطع.
- في بعض السيناريوهات، الـ agent بيقطع جملته ويبدأ tool call ناقص.

**الحل:** Phase 1 / Fix #2.

---

### 🔴 P-06: Tools مش Idempotent

**الموقع:** [agent/flows/delivery.py:93-110](../agent/flows/delivery.py#L93-L110), [agent/flows/takeaway.py:105-120](../agent/flows/takeaway.py#L105-L120)

**الكود الحالي (delivery.py):**
```python
async def update_order(
    self,
    items: list[str],
    context: RunContext_T,
) -> str:
    return self._process_order_update(
        items,
        context,
        flow_name="delivery",
        min_order_total=self.min_order_total,
    )
```

**في `_process_order_update` ([base_agent.py:259+](../agent/base_agent.py#L259)):**
- بياخد `items` ويستبدل `ud.order` بالكامل.
- **مفيش check** للقيم الحالية في `ud.order`.
- لو الـ LLM استدعى `update_order(["كشري"])` مرتين، تاني مرة بتستبدل أول مرة (مش بتضيف).
- لكن لو الـ LLM استدعى `update_order(["كشري", "بيبسي"])` بعد ما `ud.order = ["كشري"]`، الكود مش هيعرف هل الـ "كشري" نفس القديم ولا جديد.

**الـ Impact:**
- العميل ممكن يقول "كشري كبير" → tool يضيفه.
- العميل يقول "أيوه أكدلي" → الـ LLM بيخمن ويستدعي `update_order(["كشري كبير"])` تاني → مش بيتضاعف بس مفيش validation.
- لو العميل قال "ضيف بيبسي"، الـ LLM لازم يبعت `["كشري كبير", "بيبسي"]` — لو نسي وبعت `["بيبسي"]` بس، الكشري **بيتمسح**.

**الحل:** Phase 2 / Fix #6.

---

### 🟡 P-07: State Summary JSON-Only

**الموقع:** [agent/state/user_data.py:213-229](../agent/state/user_data.py#L213-L229)

**الكود الحالي:**
```python
def summarize(self) -> str:
    return _json.dumps({
        "name": self.customer_name or "—",
        "phone": self.customer_phone or "—",
        "order": self.order or "—",
        ...
    }, ensure_ascii=False)
```

**الـ Impact:**
- بيُحقن في الـ system prompt كـ JSON ([base_agent.py:169](../agent/base_agent.py#L169)).
- الـ LLM بيشوف `"name": "أحمد"` لكن مش بيشوف **سياق** متى وإزاي قاله العميل.
- النتيجة: الـ LLM ممكن يسأل "ممكن أعرف اسمك؟" حتى لو الاسم موجود — لأنه مش حاطّه في الـ conversation history.

**الحل:** Phase 2 / Fix #7.

---

### 🔴 P-08: مفيش Handoff Briefing

**الموقع:** [agent/base_agent.py:172-180](../agent/base_agent.py#L172-L180)

**الكود الحالي:**
```python
chat_ctx.add_message(
    role="system",
    content=(
        f"{_FLOW_CONTEXT_PROMPT_MARKER}\n"
        f"أنت دلوقتي في {self.__class__.__name__}. "
        "رد طبيعي بالمصري وكمّل على اللي ناقص. "
        "لو العميل سألك سؤال جانبي جاوبه الأول وبعدين ارجع للموضوع."
    ),
)
```

**المشكلة:**
- بيقول للـ agent الجديد "أنت في Delivery"، بس **مش بيقوله إيه اللي اتجمع** ولا **إيه المتبقي**.
- الـ LLM بيستنتج من الـ JSON (P-07) بدل ما يبقى عنده briefing واضح.

**الحل:** Phase 2 / Fix #5.

---

### 🟡 P-09: System Prompts مش State-Aware

**الموقع:** [agent/base_agent.py:154-180](../agent/base_agent.py#L154-L180), كل الـ flows.

**الكود الحالي (مثال):**
```python
chat_ctx.add_message(role="system", content=(
    "...\n"
    "- متكررش حاجة العميل قالها قبل كده.\n\n"
    f"بيانات العميل: {ud.summarize()}"
))
```

**المشكلة:**
- القاعدة "متكررش" عامة جداً.
- مفيش تعليمات صريحة زي "الاسم اتسأل عنه قبل كده، **متسألش تاني**".
- الـ LLM بيتعامل مع كل turn كأنه يبدأ من الصفر.

**الحل:** Phase 2 / Fix #7.

---

### 🟡 P-10: Stripping Logic مكررة

**الموقع:** [agent/base_agent.py:116-122](../agent/base_agent.py#L116-L122) و [agent/base_agent.py:148-152](../agent/base_agent.py#L148-L152)

**المشكلة:**
- `_strip_marked_system_messages` بيتنادى **مرتين** في نفس الـ `on_enter`.
- مرة قبل الـ context merge، مرة بعده.
- ده ممكن يخسر معلومات بدون قصد.

**الحل:** Phase 2 / Fix #5 (ضمن إعادة كتابة `on_enter`).

---

### 🟢 P-11: Order Validation Flag مش بيتـ Reset

**الموقع:** [agent/state/user_data.py:OrderState](../agent/state/user_data.py), [agent/agent.py:_normalize_order_items](../agent/agent.py)

**المشكلة:**
- `ud.order_validated = True` لما الطلب يتأكد على المنيو.
- لو العميل غيّر الطلب بعد كده، الـ flag بيفضل True.
- ممكن يقدّم طلب فيه أصناف مش على المنيو.

**الحل:** Phase 2 / Fix #8.

---

### 🟢 P-12: Backend Circuit Breaker Aggressive

**الموقع:** [agent/agent.py:238-239](../agent/agent.py#L238-L239)
```python
BACKEND_WRITE_CIRCUIT_FAILURE_THRESHOLD = 3
BACKEND_WRITE_CIRCUIT_OPEN_SECONDS = 8.0
```

**المشكلة:**
- بعد 3 failures، الكتابة بتتعطل 8 ثواني.
- ممكن يخسر طلبات بدون feedback واضح للعميل.

**الحل:** Phase 3 / Fix #11.

---

### 🟢 P-13: مفيش Per-Call Metrics

**الموقع:** عام (الـ logs بس).

**المشكلة:**
- مفيش تتبع لـ:
  - عدد الـ turns
  - عدد الـ tool calls
  - completion rate (مكالمات اكتملت / مكالمات بدأت)
  - repetition detection
  - latency per turn

**الحل:** Phase 3 / Fix #12.

---

### 🟢 P-14: مفيش Test Scenarios

**الموقع:** عام.

**المشكلة:**
- مفيش طريقة موحدة لاختبار التغييرات.
- كل تعديل محتاج manual testing.

**الحل:** Phase 3 / Fix #13.

---

## 4. الحلول بالتفصيل (Implementation Guide)

### Phase 1 — Quick Wins (يوم واحد)

#### ✅ Fix #1: زود الـ Context Window

**الملف:** [agent/.env](../agent/.env)

**التعديل:**
```diff
- PROMPT_HISTORY_ITEMS=4
- TURN_CHAT_CTX_MAX_ITEMS=14
+ PROMPT_HISTORY_ITEMS=20
+ TURN_CHAT_CTX_MAX_ITEMS=40
```

**ملاحظة:** الـ default في [agent/agent.py:197-198](../agent/agent.py#L197-L198) `min_value=2` و `min_value=8` — لازم القيم الجديدة فوقهم.

**Acceptance Criteria:**
- [ ] الـ env values مطبّقة (شغل `cat agent/.env | grep HISTORY` للتأكد).
- [ ] في مكالمة فيها 15 turn، الـ agent **لازم يفتكر** اللي اتقال في turn 3.

**Rollback:** ارجع للقيم القديمة في الـ .env.

---

#### ✅ Fix #2: غيّر الموديل + Temperature + Max Tokens

**الملف:** [agent/.env](../agent/.env)

**التعديل:**
```diff
- SESSION_LLM_MODEL=gpt-4.1-nano
- SESSION_LLM_MAX_COMPLETION_TOKENS=180
- SESSION_LLM_TEMPERATURE=0.85
+ SESSION_LLM_MODEL=gpt-4o-mini
+ SESSION_LLM_MAX_COMPLETION_TOKENS=400
+ SESSION_LLM_TEMPERATURE=0.3
```

**اختياري (لو عايز أحسن جودة):**
```env
SESSION_LLM_MODEL=gpt-4o
SESSION_LLM_MAX_COMPLETION_TOKENS=500
SESSION_LLM_TEMPERATURE=0.3
```

**Acceptance Criteria:**
- [ ] الـ agent بيرد بسرعة أقل من 1.5 ثانية (مش أبطأ من قبل).
- [ ] نفس السيناريو يدّي ردود متشابهة (مش متطابقة بس متماسكة).
- [ ] مفيش responses متقطعة بسبب max_tokens.

**Rollback:** ارجع للقيم القديمة. لو في performance issues، جرّب `gpt-4o-mini` مع `temperature=0.4`.

---

#### ✅ Fix #3: تأكد إن الـ env بتتحمّل صح

**شغّل:**
```bash
cd agent
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('MODEL:', os.getenv('SESSION_LLM_MODEL')); print('TEMP:', os.getenv('SESSION_LLM_TEMPERATURE')); print('HISTORY:', os.getenv('PROMPT_HISTORY_ITEMS'))"
```

**المتوقع:**
```
MODEL: gpt-4o-mini
TEMP: 0.3
HISTORY: 20
```

---

### Phase 2 — Architectural Fixes (3-4 أيام)

#### ✅ Fix #4: أضف `confirmed_facts` للـ UserData

**الملف:** [agent/state/user_data.py](../agent/state/user_data.py)

**أضف بعد class `CustomerInfo`:**
```python
@dataclass
class ConfirmedFacts:
    """Tracks what was already explicitly confirmed by the customer.
    Used to prevent the LLM from re-asking confirmed information."""
    name_confirmed: bool = False
    phone_confirmed: bool = False
    order_confirmed_at_turn: int = 0  # Turn number when order was last confirmed
    address_confirmed: bool = False
    landmark_confirmed: bool = False
    reservation_time_confirmed: bool = False
    guests_count_confirmed: bool = False
```

**عدّل `UserData` class — ضيف الحقل:**
```python
confirmed_facts: ConfirmedFacts = field(default_factory=ConfirmedFacts)
```

**عدّل `summarize`:**
```python
def summarize(self) -> str:
    return _json.dumps({
        "name": self.customer_name or "—",
        "phone": self.customer_phone or "—",
        "order": self.order or "—",
        # ... existing fields ...
        "confirmed_facts": {
            "name": self.confirmed_facts.name_confirmed,
            "phone": self.confirmed_facts.phone_confirmed,
            "address": self.confirmed_facts.address_confirmed,
        },
    }, ensure_ascii=False)
```

**Acceptance Criteria:**
- [ ] `ud.confirmed_facts.name_confirmed` accessible.
- [ ] الـ JSON serialization شغّال.
- [ ] مفيش errors في agent startup.

---

#### ✅ Fix #5: إعادة كتابة `on_enter` مع Handoff Briefing

**الملف:** [agent/base_agent.py:91-188](../agent/base_agent.py#L91-L188)

**الكود الجديد (استبدال كامل لـ `on_enter`):**
```python
async def on_enter(self) -> None:
    from agent import (
        PROMPT_HISTORY_ITEMS,
        _FLOW_CONTEXT_PROMPT_MARKER,
        _FLOW_STYLE_PROMPT_MARKER,
        _TURN_CAP_PROMPT_MARKER,
        _TURN_GUARD_PROMPT_MARKER,
        _flow_missing_phone,
        _limit_chat_ctx_preserving_system,
        _recent_chat_ctx_non_system_items,
        _set_phone_capture_mode,
        _strip_marked_system_messages,
    )
    ud: UserData = self.session.userdata
    flow = self.__class__.__name__.lower()
    ud.active_flow = flow
    logger.info("call=%s | agent=%s", ud.call_id, self.__class__.__name__)

    desired_phone_mode = _flow_missing_phone(flow, ud)
    if desired_phone_mode != ud.phone_capture_mode:
        _set_phone_capture_mode(ud, desired_phone_mode)
    if ud.last_guard_flow and ud.last_guard_flow != flow:
        ud.last_guard_flow = None
        ud.last_guard_signature = None

    # 1. ابدأ بـ chat context نظيف
    chat_ctx = self.chat_ctx.copy()

    # 2. شيل الـ flow-specific markers (مرة واحدة بس)
    _strip_marked_system_messages(
        chat_ctx,
        _FLOW_STYLE_PROMPT_MARKER,
        _FLOW_CONTEXT_PROMPT_MARKER,
        _TURN_GUARD_PROMPT_MARKER,
        _TURN_CAP_PROMPT_MARKER,
    )

    # 3. اقصّ المحادثة لكن احتفظ بأكبر قدر ممكن
    _limit_chat_ctx_preserving_system(
        chat_ctx,
        max_non_system_items=PROMPT_HISTORY_ITEMS,
    )

    # 4. لو فيه prev_agent، انقل آخر turns بطريقة آمنة
    if isinstance(ud.prev_agent, Agent):
        prev_ctx = ud.prev_agent.chat_ctx.copy(
            exclude_instructions=True,
            exclude_function_call=False,
            exclude_handoff=True,
            exclude_config_update=True,
        )
        prev_items = _recent_chat_ctx_non_system_items(
            prev_ctx,
            max_items=PROMPT_HISTORY_ITEMS,
        )
        # Better dedup: by content + role hash, not just object id
        seen_signatures = set()
        for item in chat_ctx.items:
            sig = self._chat_item_signature(item)
            if sig:
                seen_signatures.add(sig)
        for item in prev_items:
            sig = self._chat_item_signature(item)
            if sig and sig in seen_signatures:
                continue
            chat_ctx.items.append(item)
            if sig:
                seen_signatures.add(sig)

    # 5. أضف الـ persona/style system prompt
    chat_ctx.add_message(
        role="system",
        content=(
            f"{_FLOW_STYLE_PROMPT_MARKER}\n"
            "شخصيتك:\n"
            "أنت إنسان شغال في مطعم — مش بوت ولا روبوت. اتكلم كأنك بتكلم صاحبك على التليفون.\n"
            "- خليك ودود وطبيعي، استخدم كلام مصري عادي زي ما الناس بتتكلم في الحياة.\n"
            "- نوّع في ردودك — متقولش نفس الجملة مرتين.\n"
            "- لو حد سألك سؤال عادي زي 'أنت مين' رد عليه طبيعي الأول وبعدين كمّل شغلك.\n"
            "- متقولش كلام رسمي. مفيش 'يسعدنا خدمتكم' ولا 'هل تود إضافة شيء آخر'.\n"
            "- خليك مختصر بس مش جاف.\n"
            "- الأرقام بالكلام والأسماء بالعربي.\n"
            "- متضيفش أصناف أو كميات من عندك.\n"
            "- متكررش حاجة العميل قالها قبل كده.\n"
        ),
    )

    # 6. أضف الـ HANDOFF BRIEFING (الجديد)
    briefing = self._build_handoff_briefing(ud)
    if briefing:
        chat_ctx.add_message(
            role="system",
            content=f"{_FLOW_CONTEXT_PROMPT_MARKER}\n{briefing}",
        )

    # 7. أضف الـ flow-specific context
    flow_context = self._build_flow_context(ud)
    chat_ctx.add_message(
        role="system",
        content=(
            f"{_FLOW_CONTEXT_PROMPT_MARKER}\n"
            f"أنت دلوقتي في {self.__class__.__name__}.\n"
            f"{flow_context}"
        ),
    )

    await self.update_chat_ctx(chat_ctx)
    self._sync_phone_capture_mode()

    if self._opening:
        ud.last_agent_message = self._opening
        await self.session.say(self._opening, add_to_chat_ctx=True)
    else:
        self.session.generate_reply(tool_choice="none")


def _chat_item_signature(self, item) -> str | None:
    """Generate a stable signature for dedup based on content + role."""
    try:
        role = getattr(item, "role", None) or ""
        content = getattr(item, "content", None) or getattr(item, "text", None) or ""
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        content_str = str(content).strip()[:200]
        if not content_str:
            return None
        return f"{role}:{content_str}"
    except Exception:
        return None


def _build_handoff_briefing(self, ud: UserData) -> str:
    """Build an explicit briefing of what was already collected.
    The LLM uses this to AVOID re-asking captured information."""
    facts = []
    forbidden = []

    if ud.customer_name:
        facts.append(f"- الاسم: {ud.customer_name}")
        forbidden.append("الاسم")
    if ud.customer_phone:
        facts.append(f"- التليفون: {ud.customer_phone}")
        forbidden.append("التليفون")
    if ud.order:
        order_str = "، ".join(ud.order) if isinstance(ud.order, list) else str(ud.order)
        facts.append(f"- الطلب المسجل: {order_str}")
        forbidden.append("تفاصيل الطلب الأساسية")
    if ud.special_requests:
        facts.append(f"- ملاحظات خاصة: {ud.special_requests}")
    if ud.delivery_address:
        facts.append(f"- العنوان: {ud.delivery_address}")
        forbidden.append("العنوان")
    if ud.delivery_zone:
        facts.append(f"- المنطقة: {ud.delivery_zone}")
    if ud.delivery_landmark:
        facts.append(f"- العلامة المميزة: {ud.delivery_landmark}")
    if ud.reservation_time:
        facts.append(f"- ميعاد الحجز: {ud.reservation_time}")
        forbidden.append("ميعاد الحجز")
    if ud.guests_count:
        facts.append(f"- عدد الأشخاص: {ud.guests_count}")
    if ud.selected_branch:
        facts.append(f"- الفرع: {ud.selected_branch}")

    if not facts:
        return ""

    parts = ["📋 ملخص المعلومات المسجلة من العميل:"]
    parts.extend(facts)
    if forbidden:
        parts.append("")
        parts.append(
            f"⚠️ ممنوع تسأل العميل تاني عن: {'، '.join(forbidden)}. "
            f"المعلومات دي مأكدة ومسجلة."
        )
    return "\n".join(parts)


def _build_flow_context(self, ud: UserData) -> str:
    """Override in subclasses to provide flow-specific next-action hints."""
    return "كمّل اللي ناقص. لو العميل سألك سؤال جانبي جاوبه الأول وبعدين ارجع للموضوع."
```

**Acceptance Criteria:**
- [ ] لو العميل قال اسمه في Greeter، الـ Delivery agent **لازم يقوله "أهلاً يا [الاسم]"** بدل ما يسأل تاني.
- [ ] الـ briefing بيظهر في الـ logs (لو شغّلت LOG_LEVEL=DEBUG).
- [ ] مفيش regression في الـ flows الموجودة.

**Rollback:** احتفظ بنسخة من الـ on_enter القديمة في commit منفصل.

---

#### ✅ Fix #6: Tool Idempotency للـ `update_order`

**الملف:** [agent/base_agent.py:259-433](../agent/base_agent.py#L259-L433) (دالة `_process_order_update`)

**التعديل:** أضف logic للتعامل مع الإضافة vs الاستبدال.

**في بداية `_process_order_update`، بعد `if not items:` وقبل `if not _available_menu_items`:**
```python
ud = context.userdata

# NEW: Detect duplicate calls (LLM repeating same order)
new_items_normalized = sorted(item.strip().lower() for item in items if item.strip())
existing_items_normalized = sorted(
    item.strip().lower() for item in (ud.order or []) if item.strip()
)
if new_items_normalized and new_items_normalized == existing_items_normalized:
    logger.info(
        "call=%s | %s update_order: duplicate call ignored | items=%s",
        ud.call_id, flow_name, items,
    )
    return _voice_safe_text("الطلب مسجل عندي بالفعل.")

# NEW: Detect ADDITION pattern (new items contains all existing)
if (
    existing_items_normalized
    and set(existing_items_normalized).issubset(set(new_items_normalized))
):
    # User is adding more items — this is fine, proceed with new full list
    logger.info(
        "call=%s | %s update_order: addition detected | old=%s new=%s",
        ud.call_id, flow_name, ud.order, items,
    )
elif (
    existing_items_normalized
    and not set(existing_items_normalized).issubset(set(new_items_normalized))
):
    # WARNING: New list doesn't contain old items — possible LLM hallucination
    # Log it but proceed (LLM might be intentionally replacing)
    logger.warning(
        "call=%s | %s update_order: REPLACEMENT detected (old items dropped) | "
        "old=%s new=%s",
        ud.call_id, flow_name, ud.order, items,
    )
```

**Acceptance Criteria:**
- [ ] لو الـ LLM استدعى `update_order(["كشري"])` مرتين متتاليتين، التاني بيرجع "الطلب مسجل بالفعل".
- [ ] لو ud.order = ["كشري"] والـ LLM بعت ["كشري", "بيبسي"]، الكود يعتبره addition.
- [ ] الـ logs بتظهر warning لو فيه replacement مش متوقع.

---

#### ✅ Fix #7: State-Aware System Prompts (Per Flow)

**الملف:** [agent/flows/delivery.py](../agent/flows/delivery.py)

**أضف method جديد للـ `Delivery` class:**
```python
def _build_flow_context(self, ud) -> str:
    """Provide explicit next-action hints based on current state."""
    next_actions = []

    if not ud.order:
        next_actions.append("اسأل عن الطلب الأول.")
    elif not ud.delivery_address:
        next_actions.append(
            "الطلب مسجل ✓. اسأل عن العنوان دلوقتي. "
            "**ممنوع تسأل عن الطلب تاني**."
        )
    elif not ud.customer_name:
        next_actions.append(
            "الطلب والعنوان مسجلين ✓. اسأل عن الاسم. "
            "**ممنوع تسأل عن الطلب أو العنوان تاني**."
        )
    elif not ud.customer_phone:
        next_actions.append(
            "كل التفاصيل اتسجلت ما عدا التليفون ✓. اسأل عن التليفون. "
            "**ممنوع تسأل عن أي حاجة تانية**."
        )
    else:
        next_actions.append(
            "كل التفاصيل مكتملة ✓. اطلب من العميل التأكيد ولما يأكد استدعِ confirm_delivery."
        )

    return "\n".join(next_actions) + (
        "\nلو العميل سألك سؤال جانبي جاوبه الأول وبعدين ارجع للموضوع."
    )
```

**كرر نفس الـ pattern لـ:**
- [agent/flows/takeaway.py](../agent/flows/takeaway.py) — order → name → phone → confirm
- [agent/flows/reservation.py](../agent/flows/reservation.py) — time → guests → notes → branch → name → phone → confirm
- [agent/flows/greeter.py](../agent/flows/greeter.py) — intent detection → routing
- [agent/flows/complaint.py](../agent/flows/complaint.py) — text capture → submit

**Acceptance Criteria:**
- [ ] الـ system prompt في turn N بيختلف عن turn N+1 لو الـ state اتغيّر.
- [ ] الـ LLM **مش بيسأل** عن حاجة موجودة في الـ state.
- [ ] الـ next_actions واضحة وواحدة بس في الوقت.

---

#### ✅ Fix #8: Reset `order_validated` لما الطلب يتغير

**الملف:** [agent/base_agent.py:_process_order_update](../agent/base_agent.py#L259)

**في `_process_order_update`، لما الطلب الجديد مختلف عن القديم:**
```python
# When order items change, invalidate the validation flag
if existing_items_normalized != new_items_normalized:
    ud.order_validated = False
    ud.order_total = 0.0
```

**Acceptance Criteria:**
- [ ] لو العميل غيّر الطلب بعد التحقق، `order_validated` يرجع False.
- [ ] الـ confirm tool لازم يعيد التحقق قبل الإرسال.

---

#### ✅ Fix #9: تسجيل Tool Outcomes في Chat Context

**الملف:** [agent/base_agent.py:_process_order_update](../agent/base_agent.py#L259) — في النهاية.

**أضف بعد ما الطلب يتسجل بنجاح:**
```python
# Log tool outcome to chat context so LLM "remembers" what it did
try:
    self.chat_ctx.add_message(
        role="system",
        content=(
            f"[TOOL_OUTCOME] update_order نجح. "
            f"الطلب الحالي: {', '.join(ud.order)}. "
            f"الإجمالي: {ud.order_total} جنيه. "
            f"⚠️ متسألش العميل عن الطلب تاني — هو مسجل ومأكد."
        ),
    )
except Exception as e:
    logger.debug("call=%s | failed to add tool outcome: %s", ud.call_id, e)
```

**كرر نفس الـ pattern لـ:**
- `update_name` → `[TOOL_OUTCOME] الاسم اتسجل: {name}. متسألش تاني.`
- `update_phone` → `[TOOL_OUTCOME] التليفون اتسجل: {phone}. متسألش تاني.`
- `update_delivery_address` → نفس الفكرة.

**Acceptance Criteria:**
- [ ] بعد كل tool نجح، فيه system message بيتضاف للـ chat context.
- [ ] الـ LLM في الـ turn اللي بعده بيشوف الـ outcome ده ومش بيعيد السؤال.

---

#### ✅ Fix #10: Confirmed Facts Tracking في Tools

**الملف:** [agent/base_agent.py:684-732](../agent/base_agent.py#L684-L732)

**عدّل `update_name`:**
```python
@function_tool()
async def update_name(self, name: str, context: RunContext_T) -> str:
    ud = context.userdata
    name = name.strip()
    if not name:
        return "الاسم فاضي."
    ud.customer_name = name
    ud.confirmed_facts.name_confirmed = True  # NEW
    # ... existing tool outcome logging from Fix #9 ...
    return f"تمام يا {name}."
```

**عدّل `update_phone` بنفس الفكرة، بس مع `phone_confirmed`.**

**عدّل `update_delivery_address` (في delivery.py):**
```python
ud.confirmed_facts.address_confirmed = True
```

**عدّل `confirm_delivery` و `confirm_takeaway`:**
```python
ud.confirmed_facts.order_confirmed_at_turn = ud.turn_count  # If you track turn count
```

**Acceptance Criteria:**
- [ ] `confirmed_facts` بيتحدث صح بعد كل tool.
- [ ] الـ briefing من Fix #5 بيستخدم الـ flags دي.

---

### Phase 3 — Observability & Testing (2-3 أيام)

#### ✅ Fix #11: Per-Call Metrics

**الملف الجديد:** [agent/observability/call_metrics.py](../agent/observability/call_metrics.py) (إنشئه)

**الكود:**
```python
"""Per-call metrics tracking for production monitoring."""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger("call_metrics")


@dataclass
class CallMetrics:
    call_id: str
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    
    # Counts
    user_turns: int = 0
    agent_turns: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    tool_failures: dict[str, int] = field(default_factory=dict)
    flow_transitions: list[tuple[str, str]] = field(default_factory=list)
    
    # Quality flags
    repetition_detected: bool = False
    repeated_questions: list[str] = field(default_factory=list)
    backend_failures: int = 0
    
    # Outcome
    completed: bool = False
    completion_reason: str = ""  # "order_submitted", "user_hung_up", "turn_cap", "error"
    final_intent: str = ""  # "delivery", "takeaway", "reservation", "complaint", "none"
    
    # Latency (in ms)
    avg_llm_latency_ms: float = 0.0
    avg_stt_latency_ms: float = 0.0
    avg_tts_latency_ms: float = 0.0
    
    def record_tool_call(self, name: str, success: bool):
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
        if not success:
            self.tool_failures[name] = self.tool_failures.get(name, 0) + 1
    
    def record_flow_transition(self, from_flow: str, to_flow: str):
        self.flow_transitions.append((from_flow, to_flow))
    
    def finalize(self, reason: str = "unknown"):
        self.ended_at = time.time()
        self.completion_reason = reason
        self.completed = reason == "order_submitted"
    
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at
    
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds()
        return d
    
    def emit(self):
        """Log structured metrics for ingestion by monitoring."""
        logger.info("CALL_METRICS | %s", json.dumps(self.to_dict(), ensure_ascii=False))


# Global registry (in-memory, per-process)
_active_calls: dict[str, CallMetrics] = {}


def get_or_create(call_id: str) -> CallMetrics:
    if call_id not in _active_calls:
        _active_calls[call_id] = CallMetrics(call_id=call_id)
    return _active_calls[call_id]


def finalize_call(call_id: str, reason: str = "unknown"):
    metrics = _active_calls.pop(call_id, None)
    if metrics:
        metrics.finalize(reason)
        metrics.emit()
```

**Integration:**
1. في [agent/agent.py](../agent/agent.py) — entrypoint:
```python
from observability.call_metrics import get_or_create, finalize_call

# عند بدء المكالمة:
metrics = get_or_create(ud.call_id)

# عند نهاية المكالمة:
finalize_call(ud.call_id, reason="order_submitted")  # أو "user_hung_up", إلخ
```

2. في [agent/base_agent.py:_transfer_live](../agent/base_agent.py#L213):
```python
from observability.call_metrics import get_or_create
get_or_create(ud.call_id).record_flow_transition(current_name, name)
```

3. في كل tool — بعد النجاح/الفشل:
```python
get_or_create(ud.call_id).record_tool_call("update_order", success=True)
```

**Acceptance Criteria:**
- [ ] كل مكالمة بتطلع log line زي: `CALL_METRICS | {"call_id": "...", "duration_seconds": 45.2, ...}`
- [ ] الـ metrics بتشمل: turns, tool_calls, flow_transitions, completed, completion_reason.
- [ ] مفيش performance overhead ملحوظ.

---

#### ✅ Fix #12: Repetition Detection

**الملف:** [agent/observability/repetition_detector.py](../agent/observability/repetition_detector.py) (إنشئه)

**الكود:**
```python
"""Detects when the agent asks the same question twice."""
from __future__ import annotations
import logging
import re
from typing import Optional

logger = logging.getLogger("repetition_detector")

# Common question patterns (Egyptian Arabic)
QUESTION_PATTERNS = [
    (r"اسم|اسمك|اسم حضرتك", "name"),
    (r"تليفون|رقم|نمرة", "phone"),
    (r"عنوان|فين", "address"),
    (r"تطلب|طلب|عاوز|عايز", "order"),
    (r"ميعاد|إمتى|وقت", "time"),
    (r"كام واحد|عدد", "guests"),
]


def classify_question(text: str) -> Optional[str]:
    """Return the question category if the text contains a question pattern."""
    if not text or "?" not in text and "؟" not in text:
        return None
    for pattern, category in QUESTION_PATTERNS:
        if re.search(pattern, text):
            return category
    return None


class RepetitionTracker:
    def __init__(self):
        self.asked_questions: list[tuple[str, int]] = []  # (category, turn_num)
        self.repetitions: list[tuple[str, int, int]] = []  # (category, first_turn, repeat_turn)
    
    def record_agent_message(self, message: str, turn_num: int) -> bool:
        """Returns True if a repetition was detected."""
        category = classify_question(message)
        if not category:
            return False
        
        # Check if this category was already asked
        for prev_cat, prev_turn in self.asked_questions:
            if prev_cat == category:
                self.repetitions.append((category, prev_turn, turn_num))
                logger.warning(
                    "REPETITION DETECTED | category=%s | first=turn_%d | repeat=turn_%d",
                    category, prev_turn, turn_num,
                )
                return True
        
        self.asked_questions.append((category, turn_num))
        return False
```

**Integration:** في [agent/base_agent.py](../agent/base_agent.py) `on_user_turn_completed` أو الـ session say wrapper.

**Acceptance Criteria:**
- [ ] لو الـ agent سأل عن الاسم مرتين، فيه warning في الـ logs.
- [ ] الـ metrics بتسجل `repetition_detected = True`.

---

#### ✅ Fix #13: Test Scenarios

**Folder الجديد:** [agent/tests/scenarios/](../agent/tests/scenarios/) (إنشئها)

**أنشئ:**

1. `agent/tests/scenarios/happy_delivery.json`:
```json
{
  "name": "Happy Path: Delivery Order",
  "transcript": [
    {"role": "user", "text": "ألو، عاوز أطلب delivery"},
    {"role": "agent_expected", "tool": "to_delivery"},
    {"role": "user", "text": "كشري كبير وبيبسي"},
    {"role": "agent_expected", "tool": "update_order", "args": {"items": ["كشري كبير", "بيبسي"]}},
    {"role": "user", "text": "العنوان شارع الجمهورية، التحرير"},
    {"role": "agent_expected", "tool": "update_delivery_address"},
    {"role": "user", "text": "اسمي أحمد"},
    {"role": "agent_expected", "tool": "update_name", "args": {"name": "أحمد"}},
    {"role": "user", "text": "تليفوني صفر واحد صفر اتنين تلاتة..."},
    {"role": "agent_expected", "tool": "update_phone"},
    {"role": "user", "text": "أيوه أكدلي"},
    {"role": "agent_expected", "tool": "confirm_delivery"}
  ],
  "forbidden_behaviors": [
    "asking for name twice",
    "asking for phone twice",
    "asking for order after it was confirmed"
  ],
  "expected_final_state": {
    "customer_name": "أحمد",
    "order": ["كشري كبير", "بيبسي"],
    "delivery_address": "شارع الجمهورية، التحرير",
    "order_confirmed": true
  }
}
```

2. `agent/tests/scenarios/handoff_repeat_test.json`:
```json
{
  "name": "Handoff Doesn't Lose Context",
  "transcript": [
    {"role": "user", "text": "ألو معاكم، اسمي محمد"},
    {"role": "agent_expected", "tool": "update_name"},
    {"role": "user", "text": "عاوز أطلب delivery"},
    {"role": "agent_expected", "tool": "to_delivery"},
    {"role": "agent_expected", "behavior": "should_address_user_by_name"},
    {"role": "user", "text": "كشري كبير"},
    {"role": "agent_expected", "tool": "update_order"}
  ],
  "forbidden_behaviors": [
    "asking 'ما اسمك' after handoff to delivery"
  ]
}
```

3. ضيف كمان scenarios لـ:
- `address_change.json` — العميل بيغيّر العنوان في النص
- `repeated_confirmation.json` — العميل بيقول "أيوه" 3 مرات
- `unknown_item.json` — العميل بيطلب صنف مش على المنيو
- `multi_item_order.json` — طلب 5+ أصناف
- `out_of_hours.json` — مكالمة بعد ساعات العمل
- `interrupted_order.json` — العميل بيقاطع الـ agent

**Acceptance Criteria:**
- [ ] على الأقل 6 سيناريوهات موجودة.
- [ ] في runner script يقدر يشغّلهم (manual أو automated).

---

### Phase 4 — Architecture Refactor (شهر، اختياري)

ده الخيار النهائي لو لسه فيه مشاكل بعد Phases 1-3.

#### Fix #14: Unified Agent Architecture

**الفكرة:**
- Agent واحد بدل 5
- الـ "flow phase" بيبقى متغير في الـ state
- مفيش handoff = مفيش context loss

**التفاصيل:** ده يحتاج design document منفصل. ابدأ بيه بس لو الـ Phases 1-3 ما حلوش 90%+ من المشاكل.

---

## 5. Verification Checklist

### بعد Phase 1:
- [ ] الـ agent بيرد بسرعة (latency < 1.5s)
- [ ] نفس الـ scenario بيدّي ردود متماسكة
- [ ] مفيش ردود متقطعة
- [ ] مكالمة من 15 turn بدون نسيان

### بعد Phase 2:
- [ ] لو الاسم اتقال في turn 3، الـ agent مش بيسأله تاني في turn 10
- [ ] بعد handoff Greeter→Delivery، الـ Delivery agent بيعرف الاسم والطلب
- [ ] `update_order` المتكرر مش بيدمّر الطلب
- [ ] الـ system prompts بتتغير حسب الـ state

### بعد Phase 3:
- [ ] كل مكالمة بتطلع `CALL_METRICS` log line
- [ ] الـ repetition detector بيشتغل
- [ ] على الأقل 6 test scenarios موجودة

---

## 6. Test Scenarios للـ Manual QA

اعمل المكالمات دي بعد كل Phase وسجّل الـ behavior:

### 🧪 Test 1: الـ Happy Path
```
العميل: ألو
الـ agent: أهلاً، معاك [الاسم]، أقدر أساعدك في إيه؟
العميل: عاوز أطلب delivery
[handoff]
الـ agent: تمام، تحب تطلب إيه؟
العميل: كشري كبير وبيبسي
[tool: update_order]
الـ agent: تمام، الطلب: كشري كبير وبيبسي. العنوان فين؟
العميل: شارع الجمهورية
[tool: update_delivery_address]
الـ agent: تمام. اسم حضرتك؟
العميل: أحمد
الـ agent: تمام يا أحمد. التليفون؟
العميل: 01001234567
الـ agent: تمام. أأكدلك الطلب؟
العميل: أيوه
[tool: confirm_delivery]
```
**النتيجة المطلوبة:** كل الـ tools بتنادى مرة واحدة، مفيش تكرار أسئلة.

### 🧪 Test 2: التكرار
```
العميل: ألو، اسمي محمد
الـ agent: أهلاً يا محمد...
العميل: عاوز delivery
[handoff to Delivery]
```
**النتيجة المطلوبة:** الـ Delivery agent **لازم يقول "أهلاً يا محمد"** أو يكمّل بدون ما يسأل عن الاسم تاني.

### 🧪 Test 3: تغيير الطلب
```
العميل: كشري كبير
[update_order: ["كشري كبير"]]
العميل: لأ، خليه وسط
```
**النتيجة المطلوبة:** الـ agent يفهم إن العميل بيعدّل، يستدعي `update_order(["كشري وسط"])` (مش يضيف).

### 🧪 Test 4: التأكيد المتكرر
```
الـ agent: أأكدلك الطلب: كشري كبير. صح؟
العميل: أيوه
العميل: أيوه أكدلي
العميل: أيوه أيوه
```
**النتيجة المطلوبة:** `confirm_delivery` ينادى **مرة واحدة بس**، المكالمات الزيادة بتترفض أو بتتجاهل.

### 🧪 Test 5: السؤال الجانبي
```
العميل: عندك كشري؟
الـ agent: أيوه عندنا.
العميل: طب الكشري بكام؟
الـ agent: [يقوله السعر]
العميل: تمام، عاوز كشري كبير
```
**النتيجة المطلوبة:** الـ agent يجاوب الأسئلة الجانبية بدون ما يستدعي tools خطأ.

### 🧪 Test 6: الـ Handoff العكسي
```
[في Delivery، الطلب اتسجل]
العميل: لأ، أنا مش delivery، أنا عاوز آجي بنفسي
[handoff to Takeaway]
```
**النتيجة المطلوبة:** الـ Takeaway agent يفتكر الطلب اللي اتسجل في Delivery، مش يسأل عنه تاني.

---

## 7. Rollback Plan

كل Phase ليه rollback strategy:

### Phase 1 Rollback:
```bash
git checkout agent/.env  # restore old values
```

### Phase 2 Rollback:
```bash
git revert <commit-of-phase-2>
# OR
git checkout agent/base_agent.py agent/state/user_data.py agent/flows/
```

### Phase 3 Rollback:
- الـ observability code معزول، احذف الـ folder بدون تأثير على الـ flows.

---

## 8. Future Considerations (مش مطلوب دلوقتي)

- **LLM-as-Judge للـ QA:** استخدم gpt-4o عشان يقيّم transcripts تلقائياً.
- **A/B Testing:** قارن version جديد vs قديم على 100 مكالمة حقيقية.
- **Speculative Execution:** نفّذ الـ tool prediction قبل ما الـ LLM يخلص (تقليل latency).
- **Custom Fine-tuned Model:** بعد ما يتجمع 1000+ مكالمة ناجحة، اعمل fine-tune لموديل أصغر وأسرع.
- **Multi-language Support:** دلوقتي عربي بس، ممكن إنجليزي يكون مفيد.

---

## 9. Implementation Order Summary (TL;DR)

```
Day 1: Phase 1 (Quick Wins)
  └─ Fix #1, #2, #3 (.env tuning) → 30 min
  └─ Manual smoke test → 30 min
  └─ Total: 1 hour

Day 2-4: Phase 2 (Architecture)
  └─ Fix #4 (ConfirmedFacts) → 1 hour
  └─ Fix #5 (Handoff Briefing) → 4 hours
  └─ Fix #6 (Tool Idempotency) → 2 hours
  └─ Fix #7 (State-Aware Prompts) → 4 hours (per flow)
  └─ Fix #8 (order_validated reset) → 30 min
  └─ Fix #9 (Tool Outcomes) → 2 hours
  └─ Fix #10 (ConfirmedFacts integration) → 1 hour
  └─ Manual testing → 4 hours
  └─ Total: 18-20 hours

Day 5-7: Phase 3 (Observability)
  └─ Fix #11 (Metrics) → 4 hours
  └─ Fix #12 (Repetition Detector) → 2 hours
  └─ Fix #13 (Test Scenarios) → 4 hours
  └─ Integration testing → 4 hours
  └─ Total: 14 hours

Optional Day 30+: Phase 4 (Refactor)
  └─ Unified Agent design + implementation
```

---

## 10. خاتمة للـ Claude Code Session اللي هينفّذ ده

اشتغل بترتيب الـ Phases. **متعدّش حاجة**:

1. **اقرا الريبورت كله الأول** قبل ما تبدأ.
2. كل Phase له **acceptance criteria** — ما تعدّيش لـ Phase تاني قبل ما تتأكد كلها passed.
3. **commit بعد كل Fix** (مش بعد كل Phase) — عشان الـ rollback يبقى granular.
4. **test يدوياً** بعد كل Phase — مكالمة كاملة من start to end.
5. لو في حاجة مش واضحة، **اسأل** — متخمنش.

**أهم 3 حاجات لازم تتحقق:**
1. ✅ الـ context window زاد لـ 20+ items
2. ✅ الـ handoff briefing موجود ومتكامل
3. ✅ الموديل اتغيّر لـ gpt-4o-mini على الأقل

لو الـ 3 دول اتعملوا، **80% من المشاكل اللي المستخدم ذكرها هتتحل**.

---

**End of Report.**
