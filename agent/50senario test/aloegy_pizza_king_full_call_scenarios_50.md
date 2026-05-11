# AloEgy x Pizza King — 50 Full Call Flow Scenarios
Use this file as the human-readable QA reference. The JSONL file is better for automation with Claude/Codex.
## Global Pass Rules
- Do not submit incomplete orders.
- Do not hallucinate prices, zones, opening hours, tracking, or offers.
- Always confirm final state before submit.
- Latest customer correction wins.
- Escalate/handoff for angry complaints, human request, or unsupported cases.

## PK-GREEN-001 — Delivery order - simple pepperoni pizza
**Category:** green  
**Persona:** عميل هادي، عارف هو عاوز إيه  
**Mood:** calm  
**Noise:** quiet room  
**Expected:** Order confirmed with name, phone, items, address, payment method, and clear final summary.

### Caller Script
1. Caller: ألو مساء الخير، عاوز أطلب دليفري من بيتزا كينج.
2. Caller: عاوز بيتزا بيبروني لارج، وكولا واحد لتر.
3. Caller: آه الاسم إيهاب.
4. Caller: رقمي 01012345678.
5. Caller: العنوان المعادي، شارع 9، جنب مترو ثكنات المعادي، عمارة 12، الدور التالت.
6. Caller: تمام كده، كاش عند الاستلام.
7. Caller: مظبوط، شكراً.

### Must Check
- route_to_delivery
- capture_items
- capture_quantity
- capture_address
- confirm_order
- no_hallucinated_prices

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-002 — Takeaway order - pickup time
**Category:** green  
**Persona:** عميل مستعجل بس واضح  
**Mood:** normal  
**Noise:** street light noise  
**Expected:** Takeaway order confirmed with pickup timing and customer details.

### Caller Script
1. Caller: ألو، ينفع أعمل أوردر وأعدي أخده؟
2. Caller: عاوز مارجريتا ميديم واتنين تشيز رولز.
3. Caller: هعدي بعد نص ساعة تقريباً.
4. Caller: الاسم محمد عبد الله.
5. Caller: رقمي 01123456789.
6. Caller: آه تمام أكدلي الأوردر بس.

### Must Check
- route_to_takeaway
- capture_pickup_time
- capture_name_phone
- confirm_order

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-003 — Reservation for 4
**Category:** green  
**Persona:** عميلة منظمة  
**Mood:** calm  
**Noise:** quiet  
**Expected:** Reservation captured and confirmed with party size, date/time, name, phone.

### Caller Script
1. Caller: مساء الخير، عاوزة أحجز ترابيزة النهارده.
2. Caller: لأربع أفراد الساعة 8 ونص.
3. Caller: باسم سارة.
4. Caller: رقمي 01099887766.
5. Caller: تمام، شكراً.

### Must Check
- route_to_reservation
- capture_party_size
- capture_time
- confirm_reservation

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-004 — Delivery with upsell accepted
**Category:** green  
**Persona:** عميل مرن  
**Mood:** happy  
**Noise:** home TV low volume  
**Expected:** Order includes pizza, garlic dip, drink upsell, address and confirmation.

### Caller Script
1. Caller: عاوز أطلب بيتزا دليفري.
2. Caller: واحدة تشيكن رانش لارج.
3. Caller: ينفع تزودلي جارليك ديب؟
4. Caller: آه لو في عرض على الكولا حط واحدة.
5. Caller: الاسم كريم، الرقم 01222223333.
6. Caller: العنوان زهراء المعادي، شارع الخمسين، عمارة 7، الدور الرابع.
7. Caller: تمام أكد.

### Must Check
- capture_addons
- upsell_without_pressure
- confirm_order

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-005 — Complaint - late order
**Category:** green  
**Persona:** عميل متضايق لكن محترم  
**Mood:** mildly upset  
**Noise:** quiet  
**Expected:** Complaint captured; agent apologizes, collects order identifiers, does not invent tracking info if unavailable.

### Caller Script
1. Caller: أنا عندي أوردر متأخر بقاله ساعة.
2. Caller: الطلب باسم أحمد، ورقمي 01055556666.
3. Caller: كنت طالب من فرع المعادي.
4. Caller: محتاج أعرف هو فين بس.
5. Caller: تمام، سجل الشكوى.

### Must Check
- route_to_complaint
- empathy
- capture_phone
- no_fake_status
- handoff_if_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-006 — Menu question then delivery order
**Category:** green  
**Persona:** عميل محتار  
**Mood:** normal  
**Noise:** quiet  
**Expected:** Agent answers menu generally, avoids unsupported details, then routes to delivery and confirms.

### Caller Script
1. Caller: إيه أشهر بيتزا عندكم؟
2. Caller: طب خلاص عاوز سوبر سوبريم ميديم.
3. Caller: ومعاها بطاطس.
4. Caller: دليفري، الاسم مينا، الرقم 01111112222.
5. Caller: العنوان دجلة المعادي، شارع 200، عمارة 3.
6. Caller: تمام.

### Must Check
- answer_menu_question
- route_after_intent_change
- capture_order

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-007 — Change from takeaway to delivery
**Category:** green  
**Persona:** عميل غير رأيه مرة واحدة  
**Mood:** normal  
**Noise:** quiet  
**Expected:** Agent updates order type correctly and asks for delivery details.

### Caller Script
1. Caller: كنت عاوز أعمل أوردر وأستلمه من الفرع.
2. Caller: بيتزا خضار لارج.
3. Caller: لا معلش خليها دليفري أحسن.
4. Caller: العنوان شارع النصر، المعادي الجديدة، عمارة 18.
5. Caller: الاسم نادر، الرقم 01077778888.
6. Caller: أكد كده.

### Must Check
- state_update
- no_old_pickup_assumption
- confirm_delivery

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-008 — Reservation tomorrow
**Category:** green  
**Persona:** عميل واضح  
**Mood:** calm  
**Noise:** quiet  
**Expected:** Reservation date interpreted as tomorrow relative to test runtime and confirmed explicitly.

### Caller Script
1. Caller: عاوز أحجز بكرة الساعة 7.
2. Caller: لـ 6 أشخاص.
3. Caller: الاسم عمر.
4. Caller: رقمي 01234567890.
5. Caller: تمام.

### Must Check
- relative_date_handling
- confirm_date_time
- capture_party_size

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-009 — Delivery with special instructions
**Category:** green  
**Persona:** عميلة دقيقة  
**Mood:** calm  
**Noise:** quiet  
**Expected:** Special instructions captured without overpromising.

### Caller Script
1. Caller: عاوزة بيتزا تونة ميديم، من غير زيتون لو ينفع.
2. Caller: ومعاها مياه صغيرة.
3. Caller: العنوان كورنيش المعادي، أبراج عثمان، برج ب، الدور 9.
4. Caller: الاسم ياسمين، الرقم 01044445555.
5. Caller: خلي الدليفري يرن لما يوصل تحت.
6. Caller: تمام أكد.

### Must Check
- capture_modifiers
- delivery_notes
- confirm_order

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-GREEN-010 — Ask opening hours then end
**Category:** green  
**Persona:** عميل مستفسر فقط  
**Mood:** normal  
**Noise:** quiet  
**Expected:** Agent answers if config has info, otherwise says it can help with ordering/reservation and avoids inventing hours.

### Caller Script
1. Caller: هو الفرع شغال لحد الساعة كام؟
2. Caller: تمام شكراً، مش هطلب دلوقتي.
3. Caller: سلام.

### Must Check
- no_forced_order
- polite_close
- no_hallucination

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-011 — Fast speaker with full order in one breath
**Category:** stress  
**Persona:** عميل بيتكلم بسرعة جداً  
**Mood:** fast/urgent  
**Noise:** شارع وزحمة  
**Expected:** Agent must slow down, extract all fields, verify ambiguous quantities.

### Caller Script
1. Caller: ألو بسرعة لو سمحت عاوز اتنين بيتزا واحدة بيبروني لارج وواحدة تشيكن باربكيو ميديم ومعاهم بطاطس وكولا اتنين لتر والعنوان المعادي شارع 9 عمارة 22 الدور الخامس الاسم شريف الرقم 01022224444.
2. Caller: آه قلت اتنين بيتزا مش واحدة.
3. Caller: لا الكولا اتنين لتر واحدة بس.
4. Caller: تمام أكد.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-012 — Customer changes order many times
**Category:** stress  
**Persona:** عميل متردد جداً  
**Mood:** confused  
**Noise:** quiet  
**Expected:** Final order must reflect latest state only.

### Caller Script
1. Caller: عاوز مارجريتا لارج.
2. Caller: لا خليها ميديم.
3. Caller: استنى، خليها تشيكن رانش لارج بدل مارجريتا.
4. Caller: وزود بطاطس.
5. Caller: شيل البطاطس وحط جارليك ديب.
6. Caller: لا خلاص رجع البطاطس وخلي الديب كمان.
7. Caller: دليفري على دجلة المعادي شارع 216 عمارة 10.
8. Caller: الاسم رامي، الرقم 01198765432.
9. Caller: أكدلي آخر نسخة بس.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-013 — Address ambiguity
**Category:** stress  
**Persona:** عميلة مش عارفة العنوان بدقة  
**Mood:** uncertain  
**Noise:** home noise  
**Expected:** Agent should ask clarifying address questions and not submit until address is specific enough.

### Caller Script
1. Caller: عاوزة دليفري.
2. Caller: بيتزا خضار ميديم وكولا.
3. Caller: أنا في المعادي جنب النادي، مش فاكرة اسم الشارع.
4. Caller: تقريباً شارع 77 أو 79.
5. Caller: استنى هسأل حد... أيوه شارع 79، عمارة 5.
6. Caller: الاسم نور، الرقم 01033334444.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-014 — Three people speaking
**Category:** stress  
**Persona:** أكتر من شخص بيتكلم في نفس المكالمة  
**Mood:** chaotic  
**Noise:** multiple speakers  
**Expected:** Agent should handle multiple speakers and confirm unified final order.

### Caller Script
1. Caller: ألو عاوزين نطلب... يا أحمد عاوز إيه؟
2. Caller: واحد بيقول: بيبروني. واحد تاني: لا تشيكن.
3. Caller: خلاص خليها بيبروني لارج وتشيكن ميديم.
4. Caller: لا لا، التشيكن سبايسي.
5. Caller: العنوان شارع النصر المعادي الجديدة، عمارة 14.
6. Caller: الاسم أحمد، الرقم 01200001111.
7. Caller: أكد.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-015 — Angry complaint
**Category:** stress  
**Persona:** عميل غضبان من أوردر قديم  
**Mood:** angry  
**Noise:** quiet  
**Expected:** Agent must de-escalate, apologize, collect info, trigger human handoff/escalation.

### Caller Script
1. Caller: إنتوا بجد أسوأ خدمة، الأوردر وصل ساقع ومش ناقص غير إنكم تنسوه!
2. Caller: أنا مش عاوز أطلب، أنا عاوز أشتكي.
3. Caller: رقمي 01066667777 والطلب كان من ساعة تقريباً.
4. Caller: مش فاكر رقم الأوردر.
5. Caller: عاوز حد يكلمني من الإدارة.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-016 — Silent gaps
**Category:** stress  
**Persona:** عميل بيسكت كتير  
**Mood:** slow  
**Noise:** silence  
**Expected:** Agent should tolerate silence, prompt gently, avoid hanging up too early.

### Caller Script
1. Caller: ألو... عاوز أطلب...
2. Caller: [silence 8 seconds]
3. Caller: بيتزا جبنة.
4. Caller: [silence 10 seconds]
5. Caller: دليفري.
6. Caller: العنوان... ثانية واحدة.
7. Caller: [silence 12 seconds]
8. Caller: المعادي شارع 9 عمارة 9، الاسم علي، الرقم 01012121212.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-017 — Noisy car call
**Category:** stress  
**Persona:** عميل سايق والعربية مزعجة  
**Mood:** distracted  
**Noise:** car noise/horn  
**Expected:** Agent must repeat back uncertain fields and ask for missing building details.

### Caller Script
1. Caller: ألو أنا سايق ومحتاج أطلب بسرعة.
2. Caller: بيتزا... تشيكن... لا سامعني؟
3. Caller: تشيكن رانش لارج، وبطاطس.
4. Caller: العنوان ثكنات المعادي جنب المترو.
5. Caller: الاسم حسام، الرقم 01099990000.
6. Caller: معلش الصوت وحش، أكدلي اللي سمعته.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-018 — Mixed Arabic English
**Category:** stress  
**Persona:** عميل بيكلم عربي وإنجليزي  
**Mood:** normal  
**Noise:** quiet  
**Expected:** Agent should handle code-switching and confirm in suitable language or Arabic.

### Caller Script
1. Caller: Hi, I need delivery order.
2. Caller: One large pepperoni pizza, extra cheese, and Pepsi.
3. Caller: العنوان في Maadi Degla, street 200, building 8.
4. Caller: Name is Mark, phone 01155554444.
5. Caller: Can you confirm please?

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-019 — Price trap
**Category:** stress  
**Persona:** عميل بيحاول يثبت سعر من دماغه  
**Mood:** challenging  
**Noise:** quiet  
**Expected:** Agent must not hallucinate price; use menu/config only or say it will confirm.

### Caller Script
1. Caller: عاوز بيتزا بيبروني لارج، هي بـ 120 صح؟
2. Caller: أكيد يعني؟ عشان آخر مرة كانت كده.
3. Caller: طب لو مش عارف السعر قول مش عارف.
4. Caller: كمل الأوردر دليفري.
5. Caller: العنوان شارع 9 المعادي، الاسم سامح، الرقم 01010101010.

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-020 — Out-of-zone delivery
**Category:** stress  
**Persona:** عميل في منطقة ممكن برا التغطية  
**Mood:** normal  
**Noise:** quiet  
**Expected:** Agent should check configured delivery zones, not promise unsupported delivery.

### Caller Script
1. Caller: بتوصلوا لحدائق حلوان؟
2. Caller: عاوز بيتزا سوبر سوبريم لارج لو ينفع.
3. Caller: العنوان حدائق حلوان شارع الجامعة.
4. Caller: الاسم مصطفى، الرقم 01122221111.
5. Caller: ينفع ولا لأ؟

### Must Check
- robustness
- state_management
- clarification
- no_hallucination
- safe_finalization

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-021 — Customer refuses phone number
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent explains need for phone politely and cannot finalize without required contact.

### Caller Script
1. Caller: عاوز أطلب دليفري.
2. Caller: بيتزا مارجريتا ميديم.
3. Caller: العنوان شارع 9 المعادي.
4. Caller: مش حابب أدي الرقم.
5. Caller: ليه لازم الرقم؟

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-022 — Wrong/short phone number
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent detects invalid phone and asks again.

### Caller Script
1. Caller: دليفري بيتزا بيبروني.
2. Caller: الاسم هاني.
3. Caller: رقمي 12345.
4. Caller: آه هو ده الرقم.
5. Caller: العنوان دجلة المعادي شارع 200.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-023 — Customer asks unrelated question
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent handles off-topic then routes back to order.

### Caller Script
1. Caller: هو عندكم شغل؟
2. Caller: طب بتقبضوا كام؟
3. Caller: طيب بالمرة عاوز أطلب بيتزا.
4. Caller: بيتزا خضار لارج دليفري.
5. Caller: العنوان شارع النصر، الرقم 01045454545، الاسم وليد.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-024 — Child caller/prank risk
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should avoid submitting prank/incomplete order.

### Caller Script
1. Caller: ألو أنا عاوز 100 بيتزا.
2. Caller: هههه لا بهزر.
3. Caller: طب عاوز واحدة بس.
4. Caller: مش عارف العنوان.
5. Caller: ماما مش هنا.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-025 — Customer says previous agent got it wrong
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should collect correction details and not assume access if unavailable.

### Caller Script
1. Caller: أنا لسه مكلم حد وقاللي الأوردر اتسجل غلط.
2. Caller: عاوز أصححه.
3. Caller: هو المفروض تشيكن رانش مش بيبروني.
4. Caller: رقمي 01067676767.
5. Caller: الاسم فادي.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-026 — Complaint plus new order
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent must handle complaint note and order without mixing outcomes.

### Caller Script
1. Caller: عندي شكوى من آخر مرة البيتزا كانت محروقة.
2. Caller: بس دلوقتي عاوز أطلب تاني.
3. Caller: خليها مارجريتا لارج.
4. Caller: وسجل إن آخر مرة كانت وحشة.
5. Caller: العنوان المعادي شارع 77، الاسم دينا، الرقم 01078787878.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-027 — Customer interrupts agent repeatedly
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should manage barge-in and final confirmation.

### Caller Script
1. Caller: عاوز أطلب.
2. Caller: لا استنى متسألنيش دلوقتي.
3. Caller: بيتزا لارج.
4. Caller: لا مش بيبروني.
5. Caller: اقولك بس اسمعني.
6. Caller: تشيكن باربكيو لارج دليفري العنوان صقر قريش عمارة 20 الرقم 01189898989 الاسم طارق.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-028 — Customer uses vague item names
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should map cautiously or ask clarifying question.

### Caller Script
1. Caller: عاوز البيتزا اللي عليها فراخ وصوص أبيض.
2. Caller: مش فاكر اسمها.
3. Caller: آه تقريباً رانش.
4. Caller: خليها لارج.
5. Caller: دليفري للمعادي الجديدة، الاسم إسلام، الرقم 01090909090.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-029 — Allergy warning
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent must not guarantee medical safety unless menu data confirms; capture allergy note.

### Caller Script
1. Caller: عاوز بيتزا بس عندي حساسية من المشروم.
2. Caller: في أي بيتزا من غير مشروم؟
3. Caller: خليها بيبروني لو مفيهاش مشروم.
4. Caller: العنوان شارع 9، الاسم ندى، الرقم 01023232323.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-030 — Customer wants custom impossible request
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should clarify impossible math/customization and offer valid alternatives.

### Caller Script
1. Caller: عاوز بيتزا نصها بيبروني ونصها جمبري ونصها خضار.
2. Caller: آه تلات أنصاف عادي.
3. Caller: طب اعملها إزاي عندكم.
4. Caller: عاوزها لارج.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-031 — Payment method confusion
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent final payment should be cash only.

### Caller Script
1. Caller: هحاسب فيزا.
2. Caller: لا معايا كاش بس.
3. Caller: استنى ممكن إنستاباي؟
4. Caller: خلاص كاش.
5. Caller: الطلب بيبروني ميديم دليفري، العنوان المعادي، الرقم 01056565656، الاسم مازن.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-032 — Caller asks to remove item after confirmation
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent must update after confirmation and re-confirm.

### Caller Script
1. Caller: أكد أوردر بيبروني لارج وبطاطس.
2. Caller: لا ثانية، شيل البطاطس.
3. Caller: لا خلاص أكد من غير بطاطس.
4. Caller: العنوان شارع 9، الاسم شادي، الرقم 01057575757.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-033 — Delivery instruction conflict
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should store latest delivery instruction.

### Caller Script
1. Caller: خلي الدليفري يطلع الدور الخامس.
2. Caller: لا متطلعش، يسيبها مع البواب.
3. Caller: لا البواب مش موجود، خليه يرنلي.
4. Caller: الاسم بسمة، الرقم 01058585858، العنوان زهراء المعادي عمارة 11.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-034 — Customer says numbers in confusing way
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent must repeat normalized phone for confirmation.

### Caller Script
1. Caller: رقمي صفر عشرة، اتناشر، اتناشر، أربعة وتلاتين، خمسة وستين.
2. Caller: مش عارف كتبته إزاي؟
3. Caller: الاسم محمود، طلب مارجريتا لارج دليفري شارع النصر.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-035 — Customer asks for discount
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should only mention configured offers, no fake discounts.

### Caller Script
1. Caller: في خصم؟
2. Caller: طب اعملي أي عرض.
3. Caller: لو مفيش عرض مش هطلب.
4. Caller: طب خلاص بيبروني ميديم بس.
5. Caller: دليفري شارع 9، الاسم جابر، الرقم 01060606060.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-036 — Late-night closing risk
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should verify availability/hours if config provides; otherwise avoid promise.

### Caller Script
1. Caller: إنتوا فاتحين دلوقتي؟
2. Caller: أنا عاوز أطلب لو لسه مفتوح.
3. Caller: بيتزا تونة لارج.
4. Caller: العنوان المعادي القديمة، الاسم ليلى، الرقم 01061616161.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-037 — Reservation with impossible time
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should validate against opening hours if available and escalate/offer alternative.

### Caller Script
1. Caller: عاوز أحجز الساعة 3 الفجر.
2. Caller: لـ 10 أشخاص.
3. Caller: بكرة.
4. Caller: الاسم يوسف، الرقم 01062626262.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-038 — Complaint no identifiers
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should apologize but explain required info for follow-up.

### Caller Script
1. Caller: الأوردر وحش.
2. Caller: مش هديك رقمي.
3. Caller: مش فاكر الاسم.
4. Caller: بس عاوز تعويض.
5. Caller: إيه الحل؟

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-039 — Customer speaks Gulf Arabic
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should understand Gulf phrasing and continue.

### Caller Script
1. Caller: السلام عليكم، أبي أطلب بيتزا توصيل.
2. Caller: واحدة ببروني كبير ومعاها بطاطس.
3. Caller: العنوان المعادي شارع تسعة.
4. Caller: اسمي عبدالعزيز ورقمي 01063636363.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-040 — Customer speaks Upper Egyptian dialect
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should handle dialect and zone validation.

### Caller Script
1. Caller: عايز أطلب يا باشا بيتزا فراخ كبيرة كده.
2. Caller: وتوصلها دار السلام جنب المزلقان.
3. Caller: اسمي عبدالرحمن ورقمي 01064646464.
4. Caller: أيوه أكد.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-041 — Caller asks agent identity
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent can be transparent and proceed naturally.

### Caller Script
1. Caller: إنت بني آدم ولا روبوت؟
2. Caller: طب هتعرف تاخد الأوردر؟
3. Caller: ماشي عاوز بيبروني لارج.
4. Caller: دليفري شارع 9، الاسم تامر، الرقم 01065656565.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-042 — Customer requests human immediately
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should trigger human handoff if configured.

### Caller Script
1. Caller: وصلني بموظف.
2. Caller: مش عاوز أتكلم مع AI.
3. Caller: عندي مشكلة في أوردر.
4. Caller: رقمي 01066660000.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-043 — Spam/abusive caller
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should stay polite and end/mark spam after attempts.

### Caller Script
1. Caller: إنتوا نصابين.
2. Caller: مش هطلب حاجة.
3. Caller: هفضل فاتح الخط.
4. Caller: هاهاها.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-044 — Customer gives two addresses
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent must use final address.

### Caller Script
1. Caller: ابعته على شارع 9.
2. Caller: لا لا أنا في دجلة دلوقتي.
3. Caller: العنوان النهائي دجلة شارع 200 عمارة 4.
4. Caller: الاسم هبة، الرقم 01067670000، الطلب مارجريتا ميديم.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-045 — Customer orders unavailable item
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should redirect to valid pizza menu without inventing.

### Caller Script
1. Caller: عاوز برجر.
2. Caller: مش عندكم؟ طب فرايد تشيكن؟
3. Caller: طب خلاص بيتزا بيبروني.
4. Caller: لارج دليفري شارع النصر، الاسم خالد، الرقم 01068686868.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-046 — Customer asks full menu dump
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should summarize/categories, avoid huge dump if not available, then proceed.

### Caller Script
1. Caller: اقرالي المنيو كله.
2. Caller: كل الأحجام وكل الأسعار.
3. Caller: لا بسرعة.
4. Caller: طب رشحلي حاجة.
5. Caller: خلاص سوبر سوبريم ميديم.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-047 — Customer confirms wrong summary trap
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should not alter size; final summary must be medium.

### Caller Script
1. Caller: عاوز بيبروني ميديم.
2. Caller: لو قلت لارج هقولك آه وخلاص.
3. Caller: العنوان شارع 9، الاسم شريف، الرقم 01069696969.
4. Caller: آه أكد.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-048 — Poor ASR word confusion
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should resolve ASR confusion explicitly.

### Caller Script
1. Caller: عاوز بيتزا تونة.
2. Caller: لا مش تونة؟ قلت تونة آه.
3. Caller: ممكن تكون سمعت ثوم، لا تونة.
4. Caller: دليفري المعادي، الاسم رانيا، الرقم 01070707070.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-049 — Customer wants scheduled delivery
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should capture scheduled time if supported or explain limitation.

### Caller Script
1. Caller: ينفع أطلب دلوقتي وتوصل بعد ساعتين؟
2. Caller: بيتزا خضار لارج.
3. Caller: العنوان المعادي الجديدة، الاسم عادل، الرقم 01071717171.
4. Caller: عاوزها الساعة 9 بالظبط.

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

## PK-STRESS-050 — End call before completion
**Category:** stress  
**Persona:** عميل سوق حقيقي / edge case  
**Mood:** varies  
**Noise:** varies  
**Expected:** Agent should not submit incomplete order and should mark abandoned.

### Caller Script
1. Caller: عاوز دليفري بيتزا بيبروني.
2. Caller: العنوان شارع 9.
3. Caller: استنى عندي مكالمة تانية.
4. Caller: [caller hangs up before phone/name]

### Must Check
- routing
- state_management
- missing_info_handling
- no_hallucination
- handoff_when_needed

### Fail If
- Agent confirms an order with missing required fields.
- Agent invents prices, availability, delivery zones, or tracking status not in config.
- Agent ignores latest customer correction.
- Agent routes to wrong flow and never recovers.
- Agent is rude or escalates anger.
- Agent fails to summarize final order/reservation/complaint before submission.

---

