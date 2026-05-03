# Voice Agent Production Audit - 2026-04-26

## Executive Summary

تم عمل scan عملي لمسار الـ voice agent والـ backend auth. المشكلة الأساسية كانت حقيقية: إعدادات التشغيل كانت مهيأة بشكل يخلي الايجنت ينسى ويهلوس (`gpt-4.1-nano`, temperature عالي, context صغير)، والـ handoff كان ينقل أجزاء حديثة فقط بدون briefing واضح عن البيانات المؤكدة.

اتطبقت إصلاحات آمنة ومباشرة، وكل الـ smoke tests عدت بعد التعديل.

## Fixed Now

1. **LLM production tuning**
   - `SESSION_LLM_MODEL`: من `gpt-4.1-nano` إلى `gemini-2.5-flash`.
   - `SESSION_LLM_TEMPERATURE`: من `0.85` إلى `0.25`.
   - `SESSION_LLM_MAX_COMPLETION_TOKENS`: من `180` إلى `260`.
   - `PROMPT_HISTORY_ITEMS`: من `4` إلى `12`.
   - `TURN_CHAT_CTX_MAX_ITEMS`: من `14` إلى `36`.

2. **Handoff memory**
   - أضفنا `UserData.conversational_summary()` كملخص كلامي واضح بجانب الـ JSON.
   - كل flow جديد بعد handoff يستقبل ملخص المكالمة والـ next step.
   - أضفنا تعليم صريح: ممنوع سؤال العميل عن معلومة موجودة في الملخص.
   - تم تحسين dedupe لسياق الـ handoff بالـ role/text بدل الاعتماد على object id.

3. **Order preservation**
   - لو العميل قال "ضيف/زود/كمان..." والـ LLM بعت الصنف الجديد فقط، `update_order` يدمجه مع الطلب الحالي بدل ما يمسح الطلب القديم.
   - لو العميل قال "بدل/خليه/شيل..." يظل السلوك replacement.
   - تكرار نفس tool call لنفس الإضافة لا يضاعف الصنف.

4. **Post-completion safety**
   - بعد تأكيد الطلب/الحجز/الشكوى، أي كلام غير واضح يتم الرد عليه deterministic بدل ما يقع للـ LLM ويبدأ الفلو من جديد.

5. **Backend OTP bypass**
   - لا يوجد default bypass code.
   - الاختصار يعمل فقط لو `APP_ENV=dev` و`DEV_OTP_BYPASS` متضبط صراحة.

## Verification

Command:

```bash
cd agent && python smoke_tests.py
```

Result:

```text
FAILED_COUNT: 0
```

New coverage added:

- `incremental_order_add_preserves_existing_once`
- `replace_order_hint_still_replaces`
- `handoff_briefing_includes_confirmed_facts`
- `post_completion_generic_does_not_fall_to_llm`

## Remaining Production Blockers

1. **Secrets are present in local `.env` files.**
   - Rotate LiveKit/OpenAI/Google/Soniox/Hamsa/Bird/Supabase credentials before any public launch.
   - Move secrets to deployment secret manager.
   - Do not commit `.env`.

2. **Live call QA is still required.**
   - Run at least 20 Arabic Egyptian calls:
     - delivery happy path
     - takeaway happy path
     - add item after order exists
     - replace item after order exists
     - handoff greeter -> delivery after name captured
     - post-confirmation "تمام/كويس/شكرا"

3. **Deployment config must be locked.**
   - Backend production must use `APP_ENV=prod`.
   - CORS origins must be explicit production domains.
   - `DEV_OTP_BYPASS` must be unset in production.

4. **Monitoring still needs dashboards/alerts.**
   - Existing telemetry emits useful events, but production alerts should watch:
     - repeated questions
     - backend queue growth
     - circuit breaker open
     - LLM TTFT
     - TTS TTFB
     - failed call logs

## Launch Gate

Production is not approved until:

- smoke tests pass in CI
- secrets are rotated
- deployment env is reviewed
- live call QA passes
- backend and agent health endpoints are monitored

