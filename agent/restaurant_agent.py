"""RestaurantAgent — single LiveKit agent with deterministic guardrails.

The runtime still uses one LiveKit Agent and one persona prompt, but the
response path is intentionally hybrid:
  - deterministic fast-path handlers cover safe, common turns without waiting
    on the LLM;
  - the LLM handles flexible dialogue and calls the same tool surface;
  - repetition/corrective handling is turn-bound so stale background checks
    cannot hijack later turns.

All customer-facing state must remain in UserData and the state snapshot; any
new shortcut should either call the tool surface or preserve the same validation
and telemetry semantics.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from livekit.agents import StopResponse, llm, utils
from livekit.agents.voice import Agent

from backend.config import RestaurantConfig
from nlp.arabic import normalize_ar
from state.user_data import UserData
from utils.money import money2ar

import restaurant_tools

logger = logging.getLogger("restaurant.agent")

_STATE_PROMPT_MARKER = "[CALL_STATE]"
_FAST_PATH_ENABLED = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    try:
        return max(min_value, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    try:
        return max(min_value, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _load_gemini_tts_preroll() -> bytes:
    global _GEMINI_TTS_PREROLL_BYTES
    if _GEMINI_TTS_PREROLL_BYTES is not None:
        return _GEMINI_TTS_PREROLL_BYTES
    try:
        _GEMINI_TTS_PREROLL_BYTES = _GEMINI_TTS_PREROLL_PATH.read_bytes()
    except OSError as exc:
        logger.warning("gemini tts preroll unavailable | path=%s | %s", _GEMINI_TTS_PREROLL_PATH, exc)
        _GEMINI_TTS_PREROLL_BYTES = b""
    return _GEMINI_TTS_PREROLL_BYTES


def _strip_leading_ack(text: str) -> str:
    return _ACK_PREFIX_RE.sub("", text or "", count=1)


_ACTION_REVIEW_ENABLED = _env_bool("ACTION_REVIEW_ENABLED", False)
_ACTION_REVIEW_MODEL = os.getenv("ACTION_REVIEW_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
_ACTION_REVIEW_TIMEOUT_SECONDS = _env_float("ACTION_REVIEW_TIMEOUT_SECONDS", 2.5, min_value=0.2)
_ACTION_REVIEW_MAX_CONCURRENCY = _env_int("ACTION_REVIEW_MAX_CONCURRENCY", 2, min_value=1)
_ACTION_REVIEW_MAX_TEXT_CHARS = _env_int("ACTION_REVIEW_MAX_TEXT_CHARS", 700, min_value=120)
_ACTION_REVIEW_SAMPLE_RATE = _env_float("ACTION_REVIEW_SAMPLE_RATE", 0.1, min_value=0.0)
_ACTION_REVIEW_SEMAPHORE: asyncio.Semaphore | None = None
_ACTION_REVIEW_CLIENT: Any | None = None
_GEMINI_TTS_PREROLL_ENABLED = _env_bool("GEMINI_TTS_PREROLL_ENABLED", False)
_GEMINI_TTS_INSTANT_ACK_ENABLED = _env_bool("GEMINI_TTS_INSTANT_ACK_ENABLED", False)
_TARGET_E2E_FIRST_AUDIO_MS = _env_float("TARGET_E2E_FIRST_AUDIO_MS", 1000.0, min_value=100.0)
_GEMINI_TTS_PREROLL_PATH = Path(
    os.getenv(
        "GEMINI_TTS_PREROLL_PATH",
        str(Path(__file__).resolve().parent / "assets" / "tts_cache" / "gemini_3_1_leda_ar_eg_tamam.pcm"),
    )
)
_GEMINI_TTS_PREROLL_BYTES: bytes | None = None
_ACK_PREFIX_RE = re.compile(
    r"^\s*(?:\u062a\u0645\u0627\u0645|\u0645\u0627\u0634\u064a|\u062d\u0627\u0636\u0631)[\s\u060c,.\u061f!\-:]*",
    re.IGNORECASE,
)

_AR_NUMBERS = {
    "واحد": 1, "واحدة": 1, "اتنين": 2, "اثنين": 2, "اثنان": 2,
    "تلاتة": 3, "ثلاثة": 3, "تلات": 3, "ثلاث": 3,
    "اربعة": 4, "أربعة": 4, "اربع": 4, "أربع": 4,
    "خمسة": 5, "خمس": 5, "ستة": 6, "ست": 6,
    "سبعة": 7, "سبع": 7, "تمانية": 8, "ثمانية": 8,
    "تسعة": 9, "تسع": 9, "عشرة": 10, "عشر": 10,
}
_SLOT_QUESTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "order": (
        "تحب تطلب",
        "عايز تطلب",
        "تطلب ايه",
        "الأصناف",
        "الاصناف",
        "عايز ايه",
    ),
    "address": (
        "العنوان",
        "عنوانك",
        "توصل فين",
        "فين التوصيل",
        "المنطقة",
        "المنطقه",
    ),
    "name": (
        "اسمك",
        "الاسم",
        "اسم حضرتك",
        "اقول لمين",
        "أسجل باسم",
        "اسجل باسم",
    ),
    "reservation_time": (
        "ميعاد",
        "وقت الحجز",
        "امتى الحجز",
        "إمتى الحجز",
    ),
    "guests": (
        "كام فرد",
        "كام شخص",
        "عدد الأفراد",
        "عدد الافراد",
        "عدد الضيوف",
    ),
    "branch": (
        "فرع",
        "أنهي فرع",
        "انهي فرع",
        "اي فرع",
    ),
    "complaint": (
        "الشكوى",
        "الشكوي",
        "المشكلة",
        "المشكله",
        "ايه اللي حصل",
    ),
}
_CANCEL_CALL_PATTERNS = (
    "الغي الطلب",
    "إلغي الطلب",
    "الغى الطلب",
    "إلغى الطلب",
    "بلاش الطلب",
    "مش عايز الطلب",
    "مش عاوز الطلب",
    "مش محتاج الطلب",
    "cancel order",
    "cancel the order",
    "never mind",
)
_CUSTOMER_DONE_PATTERNS = (
    "لا شكرا",
    "لا شكرًا",
    "خلاص شكرا",
    "خلاص شكرًا",
    "مش عايز حاجة",
    "مش عاوز حاجة",
    "مش محتاج حاجة",
    "no thanks",
    "thank you bye",
)


def _build_persona_prompt(cfg: RestaurantConfig) -> str:
    name = cfg.name or "المطعم"

    if cfg.degraded_mode:
        return (
            f"أنت علي، موظف مصري بترد على تليفون مطعم \"{name}\".\n"
            "النظام عنده تحديث مؤقت دلوقتي — مش عارف المنيو ولا المواعيد.\n"
            "قول للعميل في جملة قصيرة إن في تحديث مؤقت، واطلب منه يقولّك طلبه أو يكلم المطعم تاني بعد شوية.\n"
            "متخمّنش معلومات غير موجودة عندك. متذكرش أسعار ولا أصناف من نفسك."
        )

    if cfg.delivery_enabled:
        zones_text = cfg.delivery_zones_text() if cfg.delivery_zones else ""
        if zones_text:
            delivery_status = (
                f"بنوصّل في المناطق دي بس: {zones_text}. "
                "أي حد يسأل \"بتوصلوا فين؟\" أو \"بتوصلوا لمنين؟\" → "
                "اقرا له القائمة دي بالاسم فوراً قبل ما تسأليه عن العنوان."
            )
        else:
            delivery_status = "بنوصّل برضه لو حد عايز توصيل."
    else:
        delivery_status = "⚠️ التوصيل مش متاح. لو حد طلب توصيل، اعرضله الاستلام."
    branches_line = (
        f"المطعم له فروع: {cfg.branch_names()}." if len(cfg.branches) > 1 else ""
    )
    hours_line = f"\nمواعيد العمل المؤكدة: {cfg.hours_text()}." if cfg.hours else ""
    menu_hint = cfg.menu_names() if cfg.menu_items else ""
    menu_line = f"\nأشهر الأصناف: {menu_hint}." if menu_hint else ""

    return f"""أنت علي، موظف مصري حقيقي في مطعم "{name}". اتكلم طبيعي.
{delivery_status}{menu_line}{hours_line}
{branches_line}

# نبرة المخاطبة (مهم جداً)
- ابدأ افتراضياً بصيغة المذكر مع الزبون (تحب/عايز/إنت/طلبك). ده الـ default المصري لما المتصل مش معروف جنسه.
- لو سمعت صوت الزبون أو كلمة منه واضح إنها أنثى ("عايزة"، "أنا"، اسم بنت) → حوّل لصيغة المؤنث فوراً وكمّل بيها (تحبي/إنتي/عايزة).
- لو رجع لصيغة مذكرة → ارجع معاه. اتبع الزبون.

# الكلام (طبيعي مصري)
- جمل قصيرة (لحد ١٥ كلمة)، الـreadback أطول شوية (لحد ٣٠).
- نوّع في كلمات التأكيد: "تمام"، "ماشي"، "حاضر"، "أيوة"، "آه"، "أوكي"، أو ابدأ بدون كلمة تأكيد لو الموقف عادي. **متبدأش بنفس الكلمة في كل جملة** — موظف حقيقي بيتنوّع.
- "يا فندم" ١-٢ مرة في المكالمة كلها بس (مش كل جملة).
- مش فاهم → "معلش، ممكن تعيدها؟" أو "آسف، مش وصلتني، تاني؟". متخمّنش.
- ممنوع: "بكل سرور/يسعدني/أتشرف/زبون/حضرتك الكريم" — دي رسمية ميتة.
- لو الزبون متردد ("ممكن دي ولا دي؟"، "هفكر شوية") → ساعده بسؤال مفيد، **متضغطش**. مثال: "حابب أرشحلك حاجة؟" أو "خد وقتك".
- لو قال "بس كده" / "خلاص" → اعتبره عاوز يقفل، اقرا الـ summary وأكد.
- Small talk (إزيك/مساء الخير) → ردّ بكلمة واحدة وارجع للطلب فوراً.

# سؤال واحد في الـ turn (مهم جداً)
- **ممنوع تسأل أكتر من سؤال في نفس الجملة/الـ turn**. سؤال واحد بس، استنى الرد، بعدين اللي بعده.
- مثال غلط: "تحب توصيل ولا استلام؟ وايه طلبك؟ وعنوانك إيه؟" — ده ٣ أسئلة، الزبون هيتلخبط.
- مثال صح: "تحب توصيل ولا استلام؟" → استنى → "تمام، عاوز تطلب إيه؟" → استنى → "ممكن العنوان؟"
- لو محتاج تجمع بيانات كتير، اسألها واحدة واحدة بالترتيب: نية → طلب → عنوان (لو توصيل) → اسم → تأكيد.
- استثناء وحيد: لما تقرا الـ summary قبل `confirm_and_submit` بتقول الطلب والعنوان والإجمالي في جملة، وفي الآخر سؤال واحد "تمام؟".

# قواعد ذهبية ضد التخمين (أهم حاجة)
- **ممنوع تنده tool إلا لو الزبون قال المعلومة بوضوح في turn-ه الحالي.**
- لو الكلام مش واضح أو ASR رجّع لغة تانية أو كلام مش متعلق → **متندهش tool خالص**. رد طبيعي زي "معلش يا فندم، مش واضح صوتك، ممكن تعيد؟" أو "اتفضل، أمرك؟".
- مثال صحيح: زبون قال "عايز ٢ بيبروني" → نده update_order. لو سمعت "Mesh you live in New York" أو أي transcript مش عربي → **متندهش حاجة**، اطلب يعيد بصياغة طبيعية ("الصوت قاطع، ممكن تعيد؟").
- مثال صحيح: زبون قال "عنواني شارع التحرير" → نده set_delivery_info. زبون قال جملة فلسفية أو تقنية → **متستخرجش عنوان منها**.

# قواعد اللغة الصارمة (أهم قاعدة على الإطلاق)
- **اللغة الوحيدة المسموحة في كل output هي العربي المصري**. ممنوع تنطق أو تكتب أي كلمة بأي لغة تانية — لا إنجليزي، لا صيني، لا هندي، لا إسباني.
- ممنوع حتى الكلمات الإنجليزية الشائعة: "okay" → "تمام"، "hi" → "أهلاً"، "thanks" → "شكراً"، "yes" → "أيوة"، "no" → "لأ".
- لو على وشك تنطق كلمة بلغة تانية، **استبدلها بمرادف عربي أو اسكت بدلها**.
- ٩٩٪ من المكالمات عربي مصري. لو الـ ASR رجّع transcript بلغة تانية → **اعتبره misrecognition، مش الزبون فعلاً قال إنجليزي**. متعملش tool call.
- **ممنوع** تقول للزبون "أنا بتكلم عربي" أو "بالعربي يا فندم، أنا من المطعم" أو أي إعلان عن لغتك. الزبون مصري بيكلمك من رقم مصري على مطعم مصري — افتراضياً بيتكلم عربي حتى لو الـ ASR ضايع. كمّل عربي طبيعي زي ما الزبون متفهم تماماً، وقولّه "معلش، الصوت مش واضح، ممكن تعيد؟" — مفيش داعي تذكر اللغة خالص.
- لو الـ ASR كرر لغة تانية تلات مرات متتاليين → قول "الصوت قاطع، ممكن تجرب تكلمني من مكان أهدى؟" — مش عن اللغة، عن الجودة.

# قواعد الاسم (مهم — منعاً للهلوسة)
- ممنوع تنده `set_name` إلا لو الزبون قال **اسم ذاتي صريح** (مثل: "اسمي أحمد"، "أنا محمد"، "علي معاك").
- ممنوع تخترع اسم من جنس أو نوع الكلام. كلمات زي "بنت/راجل/ست/فندم/مدام" مش أسماء.
- لو الزبون مش قال اسمه، نادي عليه بـ "يا فندم" ولا تطلب الاسم إلا قبل التأكيد النهائي.

# قواعد الطلب (مهم — منع الهلوسة)
- **ممنوع تضيف أي صنف ما قاله الزبون حرفياً**. مثال: قال "هات لنا كولا" → ابعت `[{{name:"كولا", qty:N}}]` فقط. **متضيفش شيبس أو حاجة تانية من دماغك**.
- ممنوع تخمين كميات لأصناف مش مذكورة. لو قال "هات لنا واحد" بدون اسم صنف → اسأله عن الصنف.
- قبل ما تنده `update_order`، اعمل في عقلك list من الأصناف اللي الزبون فعلاً قالها في الـ turn ده. ابعت بس اللي في الـ list.
- لو الـ tool رجّع `none_on_menu | unknown=X` → اعتذر للزبون عن X، **متضيفش بدائل من غير ما يطلبها**.

# قواعد العنوان (مهم — منعاً للهلوسة)
- ممنوع تنده `set_delivery_info` إلا لو فيه شارع / منطقة / رقم واضح.
- جمل عامة زي "عندي شراكة"، "هنا قريب"، "اللي قلتلك عليه" → مش عنوان. اسأل توضيح.
- لو الـ tool رجع `address_ambiguous` → اعتذر واسأل تاني: "ممكن العنوان تاني يا فندم؟ اسم الشارع والمنطقة."

# الـtools — استدعيهم فوراً (لما المعلومة واضحة فقط)
أي معلومة واضحة (نية/اسم/صنف/عنوان/ميعاد/شكوى) → نده الـtool في نفس الـturn. ممكن أكتر من tool معاً.
- `set_intent(takeaway)` لو قال "أعدي أخده/استلام/تيك أواي/من الفرع/من المطعم".
- `set_intent(delivery)` لو قال صراحة "توصيل/ديليفري" أو ذكر منطقة كعنوان توصيل. تعليمات زي "الدور الخامس" مش عنوان.
- `update_order` ضيف بيها أصناف جديدة بس. لا تستخدمها لتعديل كمية صنف موجود.
- **`set_order_item_qty`** لما الزبون يغيّر كمية صنف موجود ("خليها ٢ بدل ٣"، "زود واحد"). qty=0 يشيله.
- **`remove_order_item`** لما يقول "شيل/الغي/اطلع" صنف معيّن.
- `clear_order` بس لما يقول "الغي الطلب كله/هبدأ من الأول".
- **تغيير الـintent (delivery ↔ takeaway)**: نده `set_intent` بالنوع الجديد. **متمسحش الـ order** — هو لسه عاوز نفس الأصناف، بيغيّر بس طريقة الاستلام.
- الفرع لازم بس للحجز. للـtakeaway/delivery متسألش عن الفرع.

# قراءة رد الـ tools (مهم — مش بتتقالش للزبون)
الـtools بترجع كود قصير بالإنجليزي. هي signals ليك إنت، **ممنوع تقولها للزبون**:
- `name_saved`/`address_saved`/`reservation_saved`/`item_added`/`qty_updated`/`item_removed` → تمام، أكّد للزبون بكلامك الطبيعي وكمّل.
- `name_rejected_generic` → الاسم اللي عرضته كان غلط ("بنت"/"راجل"/"فندم")، لا تستخدمه. خاطب الزبون بـ "يا فندم".
- `name_rejected_invalid` → الاسم مش صالح، اطلبه تاني.
- `name_empty`/`address_empty`/`items_empty`/`missing_*` → اسأل الزبون عن الناقص بكلامك.
- `address_ambiguous` → اللي قاله مش عنوان واضح، اطلب تاني بطريقة محددة: "ممكن اسم الشارع والمنطقة؟"
- `address_outside_zone | zones=X,Y,Z` → اقرأ المناطق X,Y,Z للزبون واعرض عليه.
- `none_on_menu | unknown=X | menu=...` → اعتذر عن X واقترح من المنيو.
- `item_not_in_order` → قول للزبون إن الصنف ده مش في طلبه أصلاً.
- `delivery_not_enabled` → اعتذر، اعرض الاستلام.
- `below_min_order | total=X | min=Y` → قول الإجمالي والحد الأدنى، اعرض زيادة.
- `menu_already_loaded` → المنيو محفوظ عندك من tool call سابق، **متندهش `get_menu` تاني**. ارجع للمنيو في الذاكرة.
- `*_confirmed | wait_minutes=N` → أكّد بصيغة طبيعية مع اسم الزبون والمدة.
- `backend_failed` / `backend_unavailable` → اعتذر بصيغة لطيفة، قول إن في مشكلة فنية، **لا تقول إن الطلب اتسجل**.
- `submit_queued` → "تمام، الطلب اتسجل وهيتعالج خلال دقايق."
- `already_confirmed` → الطلب اتسجل خلاص، رد على أي سؤال تاني للزبون.

# قواعد المنيو الصارمة
- اسم الصنف لازم يطابق صنف من الـ get_menu **حرفياً**. متضيفش/تختصرش حروف.
- لو الزبون قال صنف مش موجود في المنيو حرفياً، اعتذر واقترح البديل الأقرب من المنيو فعلاً.
  مثال: المنيو فيها "بيتزا فراخ" بس. الزبون قال "فرخة مشوية" → "للأسف مفيش فرخة مشوية، عندنا بيتزا فراخ، تحب تجربها؟"
- متخترعش أسعار/أصناف/مواعيد. الـtools بتجيبهم. لو مش متأكد، نده get_menu.
- لو الـ update_order tool رجع رسالة فيها "مش متاح" أو "ناقص" → اعتذر للزبون وقول اللي فعلاً موجود.

# قواعد التوصيل الصارمة
- لو الزبون سأل سؤال عام عن التوصيل ("بتوصلوا فين؟" / "أنتوا بتوصلوا لمنين؟" / "في توصيل عندكم؟") → **اقرا له قائمة المناطق المذكورة في أول البرومبت فوراً، قبل ما تسأله عن العنوان**. متقولش "بنوصّل لمناطق معينة" بدون ما تسمّيها.
  مثال: "أيوه، بنوصّل في: المعادي، دار السلام، المقطم، حلوان. عنوانك في إحداهم؟"
- لما الزبون يقول عنوان توصيل، نده set_delivery_info **مرة واحدة بس** بكلامه.
- لو الـ tool رجع رسالة "خارج نطاق التوصيل ... التوصيل متاح في X, Y, Z" → **اقرا للزبون قائمة X, Y, Z** المحددة (مش عام)، ثم اعرض عليه: إما عنوان في إحدى المناطق دي، أو يستلم من الفرع.
  مثال: "للأسف ده خارج نطاق التوصيل. بنوصّل بس في: المعادي، دار السلام، حلوان. تحب عنوان في إحداهم ولا تستلم من الفرع؟"
- لو رفضت العنوان مرتين → **اعرض الاستلام بشكل أوضح**: "أنصحك تستلم من الفرع علشان أسرع، تمام؟"

# [CALL_STATE]
الـuser turn بييجي بـprefix `[CALL_STATE]` فيه intent/order/name/phone/address/time/guests/asked_once. دي **المرجع** — اللي فيها ممنوع تسأله تاني.

# مهم
1. **رقم التليفون متسجل تلقائياً من رقم اللي بيكلّمنا (Caller ID).** ممنوع تسأل عن رقمه — هو مكلّم من نفس الرقم. لو قال اسمه+رقمه، خد الاسم وتجاهل الرقم.
2. **قبل `confirm_and_submit`**: اقرا الطلب/الميعاد/العنوان/الإجمالي **من الـ [CALL_STATE]** طبيعي وانتظر "أيوه/تمام". لو قال "لا" → اسأله إيه اللي يتغير → نده update_order/set_delivery_info → اقرا تاني.
3. لو الكل جاهز والعميل قال "أيوه/أكد/تمام/صح" بعد ملخصك → نده `confirm_and_submit` فوراً، متعملش ملخص تاني.
4. **بعد التأكيد**: نده `end_call('order_completed')` فوراً. متسألش "حاجة تانية".
   - "التأكيد" = الـ `confirm_and_submit` رجّع `takeaway_confirmed`/`delivery_confirmed`/`reservation_confirmed`/`complaint_logged` فيهم `order_id` أو `reservation_id`.
   - **ممنوع** تنده `end_call('order_completed')` لو `confirm_and_submit` لسه ما اشتغلش أو رجّع `missing_name`/`backend_failed`/`submit_queued`/`missing_*`. لو رجّع `missing_*` → اجمع المعلومة الناقصة وأعد `confirm_and_submit`. لو رجّع `backend_failed` → اعتذر للزبون وقول هاتصل بيه بعدين، وقفل بـ `end_call('other')` مش `order_completed`.
5. **end_call('customer_done') ممنوع** لو الزبون **لسه ما طلبش حاجة** أو الـ order/reservation/complaint لسه فاضي. كلمة "شكراً"/"اوكي"/"تمام" لوحدها مش هانج-أب — هي ردّ مهذّب. اسأله "تحب تطلب إيه؟" بدل ما تقفل. اقفل بـ customer_done **بس** لو قال صريح: "مع السلامة"، "هقفل"، "خلاص خلصنا"، "هكلمك بعدين"، "غلط رقم".
6. **متبدأش** الترحيب بأي حاجة غير سؤال الطلب. لو الزبون قال جملة مش متعلقة بالطلب (امتحان، شغل، …) قول: "أهلاً بحضرتك — تحب تطلب إيه النهارده؟" ومتقفلش المكالمة."""


class RestaurantAgent(Agent):
    """Single agent class — no flow handoffs, no per-flow subclasses."""

    def __init__(self, cfg: RestaurantConfig) -> None:
        self.cfg = cfg
        super().__init__(
            instructions=_build_persona_prompt(cfg),
            tools=[
                restaurant_tools.set_intent,
                restaurant_tools.set_name,
                restaurant_tools.update_order,
                restaurant_tools.remove_order_item,
                restaurant_tools.set_order_item_qty,
                restaurant_tools.clear_order,
                restaurant_tools.set_delivery_info,
                restaurant_tools.set_reservation_info,
                restaurant_tools.set_complaint,
                restaurant_tools.get_menu,
                restaurant_tools.confirm_and_submit,
                restaurant_tools.end_call,
            ],
        )

    async def tts_node(self, text, model_settings):
        """Prepend a Gemini-generated cached acknowledgment after user turns.

        Live Gemini Cloud TTS streams reliably here but its first audio chunk
        measures above the 600ms target. This pre-roll is generated by the same
        Gemini 3.1 voice and yields immediately while the dynamic Gemini stream
        is synthesized concurrently.
        """
        ud = getattr(self.session, "userdata", None)
        should_preroll = (
            _GEMINI_TTS_PREROLL_ENABLED
            and getattr(ud, "turn_count", 0) > 0
            and getattr(ud, "gemini_instant_ack_turn", 0) != getattr(ud, "turn_count", 0)
            and not getattr(ud, "session_transitional_state", False)
        )
        preroll = _load_gemini_tts_preroll() if should_preroll else b""
        if not preroll:
            async for frame in Agent.default.tts_node(self, text, model_settings):
                yield frame
            return

        async def filtered_text():
            first = True
            async for chunk in text:
                if first:
                    chunk = _strip_leading_ack(chunk)
                    first = False
                    if not chunk:
                        continue
                yield chunk

        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)

        async def collect_dynamic_tts() -> None:
            try:
                async for frame in Agent.default.tts_node(self, filtered_text(), model_settings):
                    await queue.put(frame)
            except Exception as exc:
                await queue.put(exc)
            finally:
                await queue.put(None)

        dynamic_task = asyncio.create_task(collect_dynamic_tts(), name="gemini_dynamic_tts")
        try:
            byte_stream = utils.audio.AudioByteStream(
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=480,
                progressive=True,
            )
            for frame in byte_stream.push(preroll):
                yield frame
            for frame in byte_stream.flush():
                yield frame

            while True:
                frame = await queue.get()
                if frame is None:
                    break
                if isinstance(frame, Exception):
                    raise frame
                yield frame
        finally:
            if not dynamic_task.done():
                dynamic_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await dynamic_task

    def _is_realtime_session(self) -> bool:
        """True when the session is driven by a realtime speech-to-speech model.

        In realtime mode there is no standalone TTS (``self.session.tts is
        None``), so any code path that calls ``session.say(...)`` directly
        will crash with ``"trying to generate speech from text without a TTS
        model"``. ``_speak()`` is the realtime-safe wrapper.
        """
        return getattr(self.session, "tts", None) is None

    async def _speak(
        self,
        text: str,
        *,
        add_to_chat_ctx: bool = True,
        allow_interruptions: bool = True,
    ) -> None:
        """Speak a deterministic line on the call.

        Classic pipeline → forwards to ``session.say()`` (TTS render).
        Realtime mode    → silent no-op. Gemini 3.1 Flash Live rejects every
        text-injection method, and trying to speak deterministic text via a
        side TTS causes audio echo (the realtime model treats its own TTS
        output as user speech). The realtime model handles the natural
        dialogue itself; deterministic phrases are simply not spoken here.
        """
        if not text:
            return
        if self._is_realtime_session():
            logger.debug(
                "realtime mode | skipping deterministic _speak | text=%s",
                text[:60],
            )
            return
        await self.session.say(
            text,
            add_to_chat_ctx=add_to_chat_ctx,
            allow_interruptions=allow_interruptions,
        )

    def _say_instant_ack(self, turn_num: int) -> None:
        if not _GEMINI_TTS_INSTANT_ACK_ENABLED:
            return
        # Pre-rolled cached-ack audio is incompatible with the realtime model
        # (no `session.say(audio=...)` path when there's no TTS); the realtime
        # model handles its own TTFB internally so the manual ack would just
        # collide with its outbound audio stream.
        if self._is_realtime_session():
            return
        ud: UserData = self.session.userdata
        if getattr(ud, "gemini_instant_ack_turn", 0) == turn_num:
            return
        if getattr(ud, "session_transitional_state", False):
            return
        preroll = _load_gemini_tts_preroll()
        if not preroll:
            return

        async def audio_frames():
            from agent import _emit_event

            emitted = False
            t_start = time.monotonic()
            reference = (
                getattr(ud, "last_user_speech_end_at", 0.0)
                or getattr(ud, "last_user_final_at", 0.0)
                or t_start
            )
            byte_stream = utils.audio.AudioByteStream(
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=480,
                progressive=True,
            )
            for frame in byte_stream.push(preroll):
                if not emitted:
                    emitted = True
                    first_audio_ms = (time.monotonic() - reference) * 1000
                    _emit_event(
                        "latency.e2e",
                        call_id=ud.call_id,
                        flow=(ud.active_flow or "").lower(),
                        user_to_first_audio_ms=round(first_audio_ms, 3),
                        target_ms=round(_TARGET_E2E_FIRST_AUDIO_MS, 3),
                        breach=first_audio_ms > _TARGET_E2E_FIRST_AUDIO_MS,
                        source="gemini_cached_instant_ack",
                        turn=turn_num,
                    )
                    _emit_event(
                        "latency.stage",
                        call_id=ud.call_id,
                        flow=(ud.active_flow or "").lower(),
                        stage="tts_cached_ack",
                        ttfb_ms=round((time.monotonic() - t_start) * 1000, 3),
                        audio_duration_s=round(len(preroll) / 2 / 24000, 3),
                    )
                yield frame
            for frame in byte_stream.flush():
                yield frame

        ud.gemini_instant_ack_turn = turn_num
        self.session.say(
            "تمام.",
            audio=audio_frames(),
            allow_interruptions=True,
            add_to_chat_ctx=False,
        )

    async def on_enter(self) -> None:
        ud: UserData = self.session.userdata
        opening = self._opening_line()
        ud.last_agent_message = opening
        if self._is_realtime_session():
            # Gemini 3.1 Live is reactive-only — it never speaks first. We
            # inject a synthetic user turn so the model produces an opening
            # greeting on its own (the model thinks the customer said
            # "السلام عليكم" and replies with the persona-driven greeting).
            asyncio.create_task(
                self._bootstrap_realtime_greeting(),
                name=f"realtime_greeting_{ud.call_id}",
            )
            return
        await self._speak(opening, add_to_chat_ctx=True)

    async def _bootstrap_realtime_greeting(self) -> None:
        """Trigger the realtime model to produce an opening greeting.

        Sends a hidden ``LiveClientContent`` with a user turn whose text
        nudges the model to greet. The text is never spoken by anyone; only
        the model's response audio reaches the caller.

        Why the private API: ``livekit-plugins-google==1.5.2`` rejects every
        public greeting trigger for ``gemini-3.1-flash-live-preview``:
        ``generate_reply`` logs ``"generate_reply is not compatible"`` and
        no-ops, ``update_instructions`` is rejected the same way, and
        ``session.say`` raises because realtime mode has no standalone TTS.
        The only path the model actually accepts is ``_send_client_event``
        with a ``LiveClientContent`` carrying a synthetic user turn — that's
        the model's native "you have a new user message, please respond"
        protocol. If the plugin internals change in a future upgrade, the
        call falls through to a silent no-op and the agent waits for the
        customer to speak first.
        """
        from google.genai import types as _gt  # noqa: WPS433

        ud = self.session.userdata
        rt_session = None
        # Two-stage wait. Just having ``realtime_llm_session`` is not enough —
        # the AgentSession can still be in ``initializing`` state, in which
        # case the model's audio response is dropped by the plugin with a
        # ``"received server content but no active generation"`` warning and
        # the caller hears 20+ seconds of silence until the inactivity
        # watchdog re-nudges.
        #
        # Stage 1: poll for the realtime session object to exist.
        # Stage 2: wait for ``session.agent_state == "listening"`` so the
        # plugin's generation lifecycle is ready to route the response.
        # Total budget 3s (50ms × 60); the typical wait is 200-700ms.
        for _ in range(60):
            activity = getattr(self.session, "_activity", None)
            if activity is not None:
                candidate = getattr(activity, "realtime_llm_session", None)
                if candidate is not None:
                    rt_session = candidate
                    if getattr(self.session, "agent_state", None) == "listening":
                        break
            await asyncio.sleep(0.05)

        if rt_session is None:
            logger.warning(
                "call=%s | realtime greeting nudge: rt_session never appeared",
                ud.call_id,
            )
            return
        if getattr(self.session, "agent_state", None) != "listening":
            logger.warning(
                "call=%s | realtime greeting nudge: agent_state still %r after 3s, "
                "firing anyway (may be dropped)",
                ud.call_id,
                getattr(self.session, "agent_state", None),
            )

        send_event = getattr(rt_session, "_send_client_event", None)
        if not callable(send_event):
            logger.warning(
                "call=%s | realtime greeting nudge: _send_client_event missing "
                "(plugin internals changed?) — caller will need to speak first",
                ud.call_id,
            )
            return

        try:
            send_event(
                _gt.LiveClientContent(
                    turns=[
                        _gt.Content(
                            role="user",
                            parts=[_gt.Part(text="السلام عليكم")],
                        ),
                    ],
                    turn_complete=True,
                ),
            )
            logger.info(
                "call=%s | realtime greeting nudged via synthetic user turn",
                ud.call_id,
            )
        except Exception as exc:
            logger.warning(
                "call=%s | realtime greeting nudge failed | %s",
                ud.call_id,
                exc,
            )

    def _opening_line(self) -> str:
        cfg = self.cfg
        if cfg.degraded_mode:
            from agent import _degraded_user_message
            return _degraded_user_message(cfg)
        if not cfg.is_open:
            return f"ألو، معاك علي من {cfg.name}. للأسف إحنا مقفولين دلوقتي."
        # Vary the greeting slightly per call so successive customers don't
        # hear an identical recording. Hash the call_id to pick deterministically
        # within a call (consistent if the entry-point runs twice).
        ud = self.session.userdata
        greetings = (
            f"ألو، أهلاً بحضرتك في {cfg.name}، معاك علي. أساعدك؟",
            f"أهلاً وسهلاً في {cfg.name}، معاك علي، أمرك؟",
            f"معاك علي من {cfg.name}، نورتنا، تحب تطلب إيه؟",
            f"ألو، {cfg.name} معاك، خدمة؟",
        )
        idx = abs(hash(getattr(ud, "call_id", "") or "")) % len(greetings)
        return greetings[idx]

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        from agent import (
            MAX_TURNS_PER_SESSION,
            TURN_CHAT_CTX_MAX_ITEMS,
            _emit_event,
            _increment_turn_count,
        )

        ud: UserData = self.session.userdata
        text = self._extract_text(new_message)
        normalized = normalize_ar(text)

        if not normalized:
            logger.info("call=%s | empty transcript ignored", ud.call_id)
            raise StopResponse()

        ud.last_user_message = text[:280]

        done_reply = self._deterministic_done_reply(text)
        if done_reply:
            ud.pending_corrective_response = ""
            ud.pending_corrective_turn = 0
            ud.pending_corrective_source = ""
            ud.session_transitional_state = True
            ud.end_call_reason = "customer_done"
            ud.last_agent_message = done_reply
            await self._speak(done_reply, add_to_chat_ctx=True)
            self._close_session_soon()
            raise StopResponse()

        if ud.pending_corrective_response:
            if ud.pending_corrective_turn == ud.turn_count:
                corrective = ud.pending_corrective_response
                ud.pending_corrective_response = ""
                ud.pending_corrective_turn = 0
                ud.pending_corrective_source = ""
                ud.last_agent_message = corrective
                await self._speak(corrective, add_to_chat_ctx=True)
                raise StopResponse()
            logger.info(
                "call=%s | stale corrective dropped | corrective_turn=%s | current_turn=%s | source=%s",
                ud.call_id,
                ud.pending_corrective_turn,
                ud.turn_count,
                ud.pending_corrective_source,
            )
            ud.pending_corrective_response = ""
            ud.pending_corrective_turn = 0
            ud.pending_corrective_source = ""

        turn_num = _increment_turn_count(ud.call_id or "")
        ud.turn_count = turn_num
        _emit_event("turn.received", call_id=ud.call_id, turn=turn_num)

        if turn_num >= MAX_TURNS_PER_SESSION:
            logger.warning(
                "call=%s | turn cap reached | turns=%d", ud.call_id, turn_num
            )
            await self._speak(
                "معلش يا فندم المكالمة طولت شوية. كلمنا تاني في أي وقت، نورتنا!",
                add_to_chat_ctx=True,
            )
            raise StopResponse()

        fast_reply = self._deterministic_fast_reply(text)
        if fast_reply:
            ud.last_agent_message = fast_reply
            await self._speak(fast_reply, add_to_chat_ctx=True)
            raise StopResponse()

        self._say_instant_ack(turn_num)
        self._strip_state_messages(turn_ctx)
        self._trim_chat_ctx(turn_ctx, max_items=TURN_CHAT_CTX_MAX_ITEMS)
        # Embed [CALL_STATE] inside the user message itself rather than as a
        # separate system message. Two reasons:
        #   1. The persona prompt prefix stays bit-identical across turns →
        #      OpenAI prompt-caching keeps hitting (50% off cached input,
        #      faster TTFT because cached tokens skip prefill).
        #   2. Preemptive generation no longer fires its
        #      "chat context or tools have changed" warning. The state is
        #      part of the user turn the framework already accounts for.
        self._embed_state_in_user_msg(new_message, self._build_state_snapshot())

    def _build_state_snapshot(self) -> str:
        ud: UserData = self.session.userdata
        cfg = self.cfg

        intent = (ud.active_flow or "").strip()
        parts: list[str] = [_STATE_PROMPT_MARKER]
        # Quiet snapshot: only surface facts the model needs. No 'asked_once'
        # plumbing, no 'rule=never_repeat' meta-instruction — those leaked
        # into audio replies as if they were conversation content.
        if intent:
            parts.append(f"intent={intent}")
        if ud.order:
            order_str = "، ".join(ud.order)
            if ud.order_total:
                parts.append(f"order=[{order_str}] total={money2ar(ud.order_total)}ج")
            else:
                parts.append(f"order=[{order_str}]")
        if ud.customer_name:
            parts.append(f"name={ud.customer_name}")
        if ud.customer_phone:
            # Phone captured from Caller-ID on SIP calls. Flagging the source
            # tells the model "do not ask the customer to repeat their own
            # number." Web/text channels leave phone empty until the customer
            # provides it.
            source = "sip_caller_id" if getattr(ud, "call_source", "") == "sip" else "spoken"
            parts.append(f"phone={ud.customer_phone} ({source}, confirmed)")
        if ud.delivery_address:
            parts.append(f"address={ud.delivery_address}")
        if ud.delivery_landmark:
            parts.append(f"landmark={ud.delivery_landmark}")
        if ud.reservation_time:
            parts.append(f"time={ud.reservation_time}")
        if ud.guests_count:
            parts.append(f"guests={ud.guests_count}")
        if ud.selected_branch:
            parts.append(f"branch={ud.selected_branch}")
        elif intent == "reservation" and len(cfg.branches) > 1:
            parts.append(f"branches_available={cfg.branch_names()}")
        if ud.complaint_text:
            parts.append(f'complaint="{ud.complaint_text[:80]}"')
        if ud.backend_unavailable:
            parts.append("backend_unavailable=true")
        if ud.order_confirmed:
            parts.append(f"done=order_id={ud.order_id or '-'}")
        if ud.reservation_confirmed:
            parts.append(f"done=reservation_id={ud.reservation_id or '-'}")
        if ud.complaint_logged:
            parts.append("done=complaint_logged")
        return " | ".join(parts)

    @staticmethod
    def _embed_state_in_user_msg(msg: llm.ChatMessage, state_prefix: str) -> None:
        """Prepend the [CALL_STATE] block to the user message content.

        Keeping state inside the user turn (rather than as a separate system
        message) preserves the chat-ctx prefix bit-for-bit across turns, which:
          - lets OpenAI prompt-caching hit on the persona+tools prefix
          - stops preemptive generation from invalidating every turn
        """
        if not state_prefix:
            return
        body = f"{state_prefix}\n---\n"
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            msg.content = body + content
            return
        if isinstance(content, list):
            new_parts: list[Any] = list(content)
            for i, part in enumerate(new_parts):
                if isinstance(part, str):
                    new_parts[i] = body + part
                    msg.content = new_parts
                    return
            new_parts.insert(0, body)
            msg.content = new_parts
            return
        msg.content = body

    def _strip_state_messages(self, ctx: llm.ChatContext) -> None:
        """Remove all prior [CALL_STATE] system messages so the prompt doesn't bloat."""
        kept: list[Any] = []
        for item in ctx.items:
            if getattr(item, "role", "") == "system":
                content = self._item_text(item)
                if content.startswith(_STATE_PROMPT_MARKER):
                    continue
            kept.append(item)
        ctx.items.clear()
        ctx.items.extend(kept)

    def _trim_chat_ctx(self, ctx: llm.ChatContext, *, max_items: int) -> None:
        """Keep all system messages; trim non-system history to N most recent items.

        FunctionCall and FunctionCallOutput items must stay paired by call_id —
        if we drop the call but keep the output, the LLM provider warns
        "function output missing the corresponding function call, ignoring".
        After picking the keep window, expand it to include any partner items.
        """
        non_system_indices = [
            idx for idx, it in enumerate(ctx.items)
            if getattr(it, "role", "") != "system"
        ]
        if len(non_system_indices) <= max_items:
            return
        keep_idx = set(non_system_indices[-max_items:])

        # Collect call_ids appearing in the keep window, then add any
        # FunctionCall/FunctionCallOutput sharing those ids regardless of
        # position. This keeps every tool exchange whole.
        kept_call_ids: set[str] = set()
        for idx in keep_idx:
            item = ctx.items[idx]
            cid = getattr(item, "call_id", None)
            if cid:
                kept_call_ids.add(cid)
        for idx, item in enumerate(ctx.items):
            cid = getattr(item, "call_id", None)
            if cid and cid in kept_call_ids:
                keep_idx.add(idx)

        kept: list[Any] = []
        for idx, item in enumerate(ctx.items):
            if getattr(item, "role", "") == "system" or idx in keep_idx:
                kept.append(item)
        ctx.items.clear()
        ctx.items.extend(kept)

    @staticmethod
    def _extract_text(message: llm.ChatMessage) -> str:
        return RestaurantAgent._item_text(message)

    @staticmethod
    def is_explicit_cancel_or_done(text: str) -> bool:
        normalized = normalize_ar(text or "")
        if not normalized:
            return False
        patterns = _CANCEL_CALL_PATTERNS + _CUSTOMER_DONE_PATTERNS
        return any(normalize_ar(pattern) in normalized for pattern in patterns)

    def _deterministic_done_reply(self, text: str) -> str:
        if not RestaurantAgent.is_explicit_cancel_or_done(text):
            return ""
        ud: UserData = self.session.userdata
        if ud.order_confirmed or ud.reservation_confirmed or ud.complaint_logged:
            return "تمام يا فندم، نورتنا."
        if ud.order:
            ud.order = []
            ud.order_total = 0.0
            ud.order_validated = False
            ud.order_submit_in_flight = False
            return "تمام يا فندم، لغيت الطلب. نورتنا."
        if ud.reservation_time or ud.guests_count or ud.selected_branch:
            ud.reservation_time = None
            ud.reservation_time_iso = None
            ud.guests_count = None
            ud.selected_branch = None
            ud.reservation_notes = None
            ud.reservation_submit_in_flight = False
            return "تمام يا فندم، لغيت الحجز. نورتنا."
        if ud.complaint_text:
            ud.complaint_text = None
            ud.complaint_type = None
            ud.complaint_submit_in_flight = False
            return "تمام يا فندم، مش هسجل الشكوى. نورتنا."
        return "تمام يا فندم، نورتنا."

    def _deterministic_fast_reply(self, text: str) -> str:
        if not _FAST_PATH_ENABLED:
            return ""
        ud: UserData = self.session.userdata
        cfg = self.cfg
        phone_present = self._has_phone_number(text)
        working_text = self._strip_phone_number(text) if phone_present else text
        normalized = normalize_ar(working_text or "")
        if not normalized:
            return self._phone_turn_reply(text) if phone_present else ""

        safety_reply = self._safety_or_abuse_reply(normalized)
        if safety_reply:
            return safety_reply
        if "[silence" in (text or "").lower():
            return "أنا معاك يا فندم، كمل لما تكون جاهز."
        special_reply = self._special_request_reply(normalized)
        if special_reply:
            return special_reply

        if self._is_hearing_check(normalized):
            return "أيوه سامعك، تحب تطلب إيه؟"
        if self._is_greeting_only(normalized):
            return "أهلاً بيك، تحب تطلب ولا تحجز؟"
        if self._is_hours_question(normalized):
            return f"مواعيدنا يا فندم: {cfg.hours_text()}."
        if self._is_menu_question(normalized):
            return cfg.menu_text()
        order_start = self._has_order_start(normalized)
        if order_start and not ud.active_flow:
            ud.active_flow = "delivery" if self._has_delivery_hint(normalized) else "takeaway"
            ud.intents_seen.add(ud.active_flow)
            self._emit_fast_tool_event("set_intent")

        has_address_hint = self._contains_any(normalized, ("العنوان", "عنواني"))
        if self._has_delivery_hint(normalized) and cfg.delivery_enabled:
            if ud.active_flow != "delivery":
                ud.active_flow = "delivery"
                ud.intents_seen.add("delivery")
                self._emit_fast_tool_event("set_intent")
            if has_address_hint:
                pass
            elif ud.order and not ud.delivery_address:
                return "تمام، هنخليه دليفري. العنوان فين يا فندم؟"
            elif not ud.order:
                return "تمام، دليفري. تحب تطلب إيه؟"
        elif self._has_takeaway_hint(normalized) and not self._is_payment_phrase(normalized):
            if ud.active_flow != "takeaway":
                ud.active_flow = "takeaway"
                ud.intents_seen.add("takeaway")
                self._emit_fast_tool_event("set_intent")
            if ud.order and not ud.customer_name:
                return "تمام، هنخليه استلام. الاسم إيه يا فندم؟"
            if not ud.order:
                return "تمام، استلام من الفرع. تحب تطلب إيه؟"
        elif self._has_reservation_hint(normalized):
            if ud.active_flow != "reservation":
                ud.active_flow = "reservation"
                ud.intents_seen.add("reservation")
                self._emit_fast_tool_event("set_intent")
        elif self._has_complaint_hint(normalized):
            if ud.active_flow != "complaint":
                ud.active_flow = "complaint"
                ud.intents_seen.add("complaint")
                self._emit_fast_tool_event("set_intent")

        captured_name = self._extract_customer_name(working_text)
        if captured_name and not ud.customer_name:
            ud.customer_name = captured_name
            self._emit_fast_tool_event("set_name")

        if ud.active_flow in {"takeaway", "delivery"}:
            order_reply = self._try_capture_order(working_text)
            address_reply = self._try_capture_address(working_text)
            if self._is_pickup_time_note(normalized) and ud.active_flow == "takeaway":
                return "تمام، أجهزهولك. الاسم إيه يا فندم؟"
            if self._is_unknown_address(normalized) and ud.active_flow == "delivery":
                return "تمام، لما تعرف العنوان قولي. من غير عنوان مش هقدر أأكد الدليفري."
            if order_reply or address_reply or captured_name:
                return self._next_order_reply()
            if order_start and not ud.order:
                return "تمام، تحب تطلب إيه؟"

        if ud.active_flow == "reservation":
            if self._try_capture_reservation(working_text) or captured_name:
                if not ud.reservation_time or not ud.guests_count:
                    return "تمام، الميعاد والعدد كام يا فندم؟"
                if not ud.customer_name:
                    return "تمام، أسجل الحجز باسم مين؟"
                return (
                    f"تمام يا {ud.customer_name}، حجز لـ{ud.guests_count} نفر "
                    f"{ud.reservation_time}. كده تمام؟"
                )

        if ud.active_flow == "complaint":
            complaint_text = self._extract_complaint_text(working_text)
            if complaint_text and not ud.complaint_text:
                ud.complaint_text = complaint_text
                ud.complaint_type = self._complaint_category(complaint_text)
                self._emit_fast_tool_event("set_complaint")
                return "معلش يا فندم، سجلت الشكوى. تحب أأكد تسجيلها؟"
            if complaint_text:
                ud.complaint_text = (ud.complaint_text or "") + " " + complaint_text
                self._emit_fast_tool_event("set_complaint")
                return "تمام، ضفت التفاصيل على الشكوى. أأكد تسجيلها؟"

        return self._phone_turn_reply(text) if phone_present else ""

    def _phone_turn_reply(self, text: str) -> str:
        if not self._has_phone_number(text):
            return ""
        ud: UserData = self.session.userdata
        if ud.active_flow == "delivery":
            if not ud.order:
                return "تمام، مش محتاجين رقم موبايل. تحب تطلب إيه؟"
            if not ud.delivery_address:
                return "تمام، مش محتاجين رقم موبايل. العنوان فين يا فندم؟"
            if not ud.customer_name:
                return "تمام، مش محتاجين رقم موبايل. الاسم إيه يا فندم؟"
        if ud.active_flow == "takeaway":
            if not ud.order:
                return "تمام، مش محتاجين رقم موبايل. تحب تطلب إيه؟"
            if not ud.customer_name:
                return "تمام، مش محتاجين رقم موبايل. الاسم إيه يا فندم؟"
        if ud.active_flow == "reservation":
            if not ud.customer_name:
                return "تمام، مش محتاجين رقم موبايل. أسجل الحجز باسم مين؟"
        if ud.active_flow == "complaint":
            return "تمام، مش محتاجين رقم موبايل. احكيلي تفاصيل الشكوى يا فندم."
        return "تمام، مش محتاجين رقم موبايل يا فندم."

    @staticmethod
    def _has_phone_number(text: str) -> bool:
        return bool(re.search(r"\d[\d\s+\-()]{6,}", text or ""))

    @staticmethod
    def _strip_phone_number(text: str) -> str:
        stripped = re.sub(r"\d[\d\s+\-()]{6,}", " ", text or "")
        stripped = re.sub(r"\b(?:الرقم|رقمي|موبايلي|تليفوني|phone|mobile)\b\s*[:：]?", " ", stripped, flags=re.I)
        return re.sub(r"\s+", " ", stripped).strip(" ،,.")

    @staticmethod
    def _contains_any(normalized: str, words: tuple[str, ...]) -> bool:
        return any(normalize_ar(word) in normalized for word in words)

    @classmethod
    def _has_delivery_hint(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("دليفري", "توصيل", "وصل", "وصّل", "العنوان", "delivery"))

    @classmethod
    def _has_takeaway_hint(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("استلام", "استلمه", "اخده", "أخده", "اعدي", "أعدي", "تيك اواي", "تيك أواي", "pickup", "pick up", "من الفرع"))

    @classmethod
    def _is_payment_phrase(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("كاش", "الدفع", "فيزا", "عند الاستلام"))

    @classmethod
    def _has_reservation_hint(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("احجز", "حجز", "ترابيزة", "طربيزة"))

    @classmethod
    def _has_complaint_hint(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("شكوى", "شكوي", "مشكلة", "متأخر", "محروقة", "وحشة"))

    @classmethod
    def _has_order_start(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("عاوز اطلب", "عايز اطلب", "أطلب", "اطلب", "اوردر", "أوردر", "order"))

    @classmethod
    def _safety_or_abuse_reply(cls, normalized: str) -> str:
        if cls._contains_any(normalized, ("نصاب", "نصابين", "مش هطلب", "هفضل فاتح", "هاها", "هههه", "بهزر")):
            return "تمام يا فندم، لو محتاج طلب أو شكوى حقيقية أنا معاك."
        if cls._contains_any(normalized, ("مش فاكر الاسم", "مش هديك رقمي")):
            return "تمام، مش مشكلة. احكيلي تفاصيل الطلب أو الشكوى اللي فاكرها."
        return ""

    @classmethod
    def _is_menu_question(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, (
            "المنيو", "منيو", "عندكم ايه", "فيه ايه", "فيها ايه",
            "البيتزا فيه", "البيتزا فيها", "ايه البيتزا", "الأصناف",
            "الاصناف", "اقرالي", "الاسعار", "الأسعار", "رشحلي",
        ))

    @classmethod
    def _is_hearing_check(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, (
            "سامعني", "سامعاني", "سامع", "الو", "ألو", "هالو",
        ))

    @classmethod
    def _is_greeting_only(cls, normalized: str) -> bool:
        cleaned = re.sub(r"\s+", " ", normalized or "").strip()
        if not cleaned:
            return False
        greetings = (
            "اهلا", "اهلا بكم", "اهلا بيك", "اهلا بيكي", "السلام عليكم", "مساء الخير",
            "صباح الخير", "ازيك", "ازي حضرتك", "مرحبا",
        )
        return any(cleaned == normalize_ar(g) for g in greetings)

    @classmethod
    def _is_hours_question(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("شغال لحد", "مواعيد", "بتقفل", "بتفتح", "الساعة كام", "فاتحين", "مفتوح"))

    @classmethod
    def _special_request_reply(cls, normalized: str) -> str:
        if cls._contains_any(normalized, ("نصها", "انصاف", "أنصاف", "جمبري")):
            return "معلش، التقسيمة دي مش متاحة. اختار صنف واحد من المنيو وأنا أسجله."
        if cls._contains_any(normalized, ("حساسية", "مشروم")):
            return "تمام، هنراعي الحساسية ومش هنضيف مشروم. تحب تختار بيتزا إيه؟"
        if cls._contains_any(normalized, ("فيزا", "كاش", "انستاباي", "إنستاباي")):
            return "تمام، الدفع كاش أو حسب المتاح عند الاستلام. نكمل الطلب؟"
        return ""

    @classmethod
    def _is_pickup_time_note(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("بعد نص ساعة", "بعد نصف ساعة", "هعدي", "هاجي", "اجي اخده"))

    @classmethod
    def _is_unknown_address(cls, normalized: str) -> bool:
        return cls._contains_any(normalized, ("مش عارف العنوان", "مش عارفة العنوان"))

    @staticmethod
    def _extract_customer_name(text: str) -> str:
        match = re.search(r"(?:الاسم|باسم|اسمي)\s+([\u0600-\u06FF]{2,20})", text or "")
        if not match:
            match = re.search(r"(?:name is|my name is)\s+([A-Za-z]{2,30})", text or "", flags=re.I)
        return match.group(1).strip(" ،,.") if match else ""

    @staticmethod
    def _extract_qty(text: str) -> int:
        normalized = normalize_ar(text or "")
        if RestaurantAgent._contains_any(
            normalized,
            ("شارع", "عمارة", "الدور", "طابق", "شقة", "برج", "رقم"),
        ):
            return 1
        digit_match = re.search(r"\d+", text or "")
        if digit_match:
            try:
                return max(1, min(99, int(digit_match.group(0))))
            except ValueError:
                return 1
        for word, value in _AR_NUMBERS.items():
            if normalize_ar(word) in normalized:
                return value
        return 1

    def _try_capture_order(self, text: str) -> bool:
        ud: UserData = self.session.userdata
        cfg = self.cfg
        if not cfg.menu_items:
            return False
        normalized = normalize_ar(text or "")
        matched_names: list[str] = []
        for item in cfg.menu_items:
            name = str(item.get("name", "")).strip()
            if not name or not item.get("available", True):
                continue
            if self._menu_item_mentioned(name, normalized):
                qty = self._extract_qty(text)
                matched_names.append(name if qty == 1 else f"{name} x {qty}")
        if not matched_names:
            return False
        try:
            from agent import _normalize_order_items

            normalized_items, unknown, total = _normalize_order_items((ud.order or []) + matched_names, cfg.menu_items)
        except Exception:
            normalized_items, unknown, total = matched_names, [], 0.0
        ud.order = normalized_items
        ud.order_total = total
        ud.order_validated = not unknown
        ud.order_confirmed = False
        if not ud.active_flow:
            ud.active_flow = "delivery" if self._has_delivery_hint(normalized) else "takeaway"
            ud.intents_seen.add(ud.active_flow)
            self._emit_fast_tool_event("set_intent")
        self._emit_fast_tool_event("update_order")
        return True

    @staticmethod
    def _menu_item_mentioned(name: str, normalized_text: str) -> bool:
        name_norm = normalize_ar(name)
        if name_norm and name_norm in normalized_text:
            return True
        aliases = {
            "pepperoni": "بيبروني",
            "peperoni": "بيبروني",
            "margarita": "مارجريتا",
            "margherita": "مارجريتا",
            "veggie": "خضار",
            "vegetable": "خضار",
            "chicken ranch": "تشيكن رانش",
            "ranch": "تشيكن رانش",
            "pepsi": "بيبسي",
            "coke": "كولا",
            "cola": "كولا",
            "cheese rolls": "تشيز رولز",
            "cheese roll": "تشيز رولز",
            "large": "لارج",
            "medium": "ميديم",
        }
        expanded = normalized_text
        for en, ar in aliases.items():
            if en in normalized_text.lower():
                expanded += " " + normalize_ar(ar)
        ar_aliases = {
            "سدق": "سجق",
            "سودق": "سجق",
            "سوجق": "سجق",
            "سق": "سجق",
            "ببروني": "بيبروني",
            "ببرونى": "بيبروني",
            "مرجريتا": "مارجريتا",
            "مارغريتا": "مارجريتا",
        }
        for noisy, canonical in ar_aliases.items():
            if normalize_ar(noisy) in normalized_text:
                expanded += " " + normalize_ar(canonical)
        tokens = [t for t in re.split(r"\s+", name_norm.replace("بيتزا", "").strip()) if len(t) > 1]
        if not tokens:
            return False
        flavor_tokens = [t for t in tokens if t not in {"لارج", "ميديم"}]
        size_tokens = [t for t in tokens if t in {"لارج", "ميديم"}]
        flavor_ok = flavor_tokens and all(t in expanded for t in flavor_tokens)
        size_ok = not size_tokens or any(t in expanded for t in size_tokens)
        return bool(flavor_ok and size_ok)

    def _try_capture_address(self, text: str) -> bool:
        ud: UserData = self.session.userdata
        cfg = self.cfg
        normalized = normalize_ar(text or "")
        address_like = self._has_delivery_hint(normalized) or (
            ud.active_flow == "delivery"
            and self._contains_any(normalized, ("شارع", "عمارة", "المعادي", "دجلة", "صقر قريش"))
        )
        if not address_like:
            return False
        address = self._extract_address_candidate(text)
        if len(address) < 5 or not self._address_has_required_token(address, cfg):
            return False
        if cfg.delivery_zones:
            try:
                from agent import _match_delivery_zone

                zone = _match_delivery_zone(address, cfg.delivery_zones)
            except Exception:
                zone = None
            if not zone:
                ud.delivery_address = None
                ud.delivery_zone = None
                return False
            ud.delivery_zone = zone
        ud.delivery_address = address
        ud.active_flow = "delivery"
        ud.intents_seen.add("delivery")
        self._emit_fast_tool_event("set_delivery_info")
        return True

    @classmethod
    def _extract_address_candidate(cls, text: str) -> str:
        raw = (text or "").strip()
        match = re.search(r"(?:العنوان|عنواني|دليفري|توصيل)\s+(.+)$", raw)
        if match:
            raw = match.group(1)
        raw = re.split(r"(?:،|,|\.)\s*(?:الاسم|اسمي|باسم)\b", raw, maxsplit=1)[0]
        return raw.strip(" ،,.")

    @classmethod
    def _address_has_required_token(cls, address: str, cfg: RestaurantConfig) -> bool:
        normalized = normalize_ar(address or "")
        if cls._contains_any(
            normalized,
            ("شارع", "عمارة", "الدور", "طابق", "شقة", "برج", "بجوار", "جنب", "قدام", "خلف"),
        ):
            return True
        return any(normalize_ar(zone) in normalized for zone in (cfg.delivery_zones or []))

    def _try_capture_reservation(self, text: str) -> bool:
        ud: UserData = self.session.userdata
        changed = False
        if not ud.reservation_time and re.search(r"(?:الساعة|بكرة|النهارده|اليوم|غدا|غداً)", text or ""):
            ud.reservation_time = (text or "").strip()
            changed = True
        guests = self._extract_guest_count(text)
        if not ud.guests_count and guests:
            ud.guests_count = guests
            changed = True
        if len(self.cfg.branches) == 1 and not ud.selected_branch:
            ud.selected_branch = self.cfg.branches[0].get("name", "") or ""
        if changed:
            self._emit_fast_tool_event("set_reservation_info")
        return changed

    @classmethod
    def _extract_guest_count(cls, text: str) -> int | None:
        raw = text or ""
        guest_pattern = r"(?:أشخاص|اشخاص|افراد|أفراد|نفر|ضيوف)"
        digit_before = re.search(r"(\d{1,2})\s*" + guest_pattern, raw)
        if digit_before:
            return max(1, min(200, int(digit_before.group(1))))
        digit_after = re.search(guest_pattern + r"\s*(\d{1,2})", raw)
        if digit_after:
            return max(1, min(200, int(digit_after.group(1))))
        normalized = normalize_ar(raw)
        if not re.search(guest_pattern, raw):
            return None
        for word, value in _AR_NUMBERS.items():
            if normalize_ar(word) in normalized:
                return value
        return None

    @classmethod
    def _extract_complaint_text(cls, text: str) -> str:
        normalized = normalize_ar(text or "")
        if not cls._has_complaint_hint(normalized) and len(normalized) < 12:
            return ""
        return (text or "").strip()

    @classmethod
    def _complaint_category(cls, text: str) -> str:
        normalized = normalize_ar(text or "")
        if cls._contains_any(normalized, ("متأخر", "اتأخر", "فين")):
            return "تأخير"
        if cls._contains_any(normalized, ("محروق", "وحش", "بارد", "غلط")):
            return "جودة"
        return "general"

    def _next_order_reply(self) -> str:
        ud: UserData = self.session.userdata
        flow = ud.active_flow or "takeaway"
        if not ud.order:
            return "تمام، تحب تطلب إيه؟"
        if flow == "delivery" and not ud.delivery_address:
            return "تمام، العنوان فين يا فندم؟"
        if not ud.customer_name:
            return "تمام، الاسم إيه يا فندم؟"
        total = f" الإجمالي {money2ar(ud.order_total)} جنيه." if ud.order_total else ""
        if flow == "delivery":
            return f"تمام يا {ud.customer_name}، الطلب للتوصيل اتسجل.{total} أأكد كده؟"
        return f"تمام يا {ud.customer_name}، الطلب للاستلام اتسجل.{total} أأكد كده؟"

    def _emit_fast_tool_event(self, tool_name: str, *, success: bool = True) -> None:
        try:
            from agent import _emit_event
            from observability.call_metrics import get_or_create as _get_call_metrics

            ud: UserData = self.session.userdata
            _emit_event(
                "tool.called",
                call_id=ud.call_id or "",
                flow=(ud.active_flow or "").lower(),
                tool=tool_name,
                success=success,
                synthetic=True,
                source="fast_path",
            )
            if ud.call_id:
                _get_call_metrics(ud.call_id).record_tool_call(tool_name, success=success)
        except Exception as exc:
            logger.debug("fast tool telemetry skipped | tool=%s | %s", tool_name, exc)

    def schedule_action_review(
        self,
        *,
        user_text: str,
        agent_reply: str,
        source: str = "assistant",
    ) -> None:
        """Run a second LLM review out-of-band.

        The task is intentionally fire-and-forget. It never blocks speech; if it
        finds a risky reply, it prepares a corrective response for the next user
        turn and emits telemetry for QA review.
        """
        if not _ACTION_REVIEW_ENABLED:
            return
        if not self._action_review_sampled_call():
            return
        if not (user_text or "").strip() or not (agent_reply or "").strip():
            return
        try:
            ud: UserData = self.session.userdata
            state_snapshot = self._build_state_snapshot()
            review_turn = ud.turn_count
            task = asyncio.create_task(
                self._run_action_review(
                    ud=ud,
                    review_turn=review_turn,
                    user_text=user_text,
                    agent_reply=agent_reply,
                    state_snapshot=state_snapshot,
                    source=source,
                ),
                name=f"action_review_{ud.call_id or 'call'}",
            )
            ud.background_tasks.append(task)
            task.add_done_callback(self._log_action_review_task_result)
        except RuntimeError:
            logger.debug("action review skipped | no running event loop")
        except Exception as exc:
            logger.debug("action review scheduling skipped | %s", exc)

    @staticmethod
    def _log_action_review_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("action review task failed | %s", exc)

    async def _run_action_review(
        self,
        *,
        ud: UserData,
        review_turn: int,
        user_text: str,
        agent_reply: str,
        state_snapshot: str,
        source: str,
    ) -> None:
        from agent import _emit_event

        client = self._action_review_client()
        if client is None:
            _emit_event(
                "action.review",
                call_id=ud.call_id or "",
                flow=(ud.active_flow or "").lower(),
                source=source,
                status="skipped",
                reason="missing_api_key",
            )
            return

        semaphore = self._action_review_semaphore()
        started = time.perf_counter()
        try:
            async with semaphore:
                verdict = await asyncio.wait_for(
                    self._request_action_review(
                        client,
                        user_text=user_text,
                        agent_reply=agent_reply,
                        state_snapshot=state_snapshot,
                        source=source,
                    ),
                    timeout=_ACTION_REVIEW_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            _emit_event(
                "action.review",
                call_id=ud.call_id or "",
                flow=(ud.active_flow or "").lower(),
                source=source,
                status="timeout",
                latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            )
            return
        except Exception as exc:
            _emit_event(
                "action.review",
                call_id=ud.call_id or "",
                flow=(ud.active_flow or "").lower(),
                source=source,
                status="error",
                error=type(exc).__name__,
                latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
            )
            logger.debug("action review failed | %s", exc)
            return

        risk = str(verdict.get("risk", "warn")).strip().lower()
        reason = str(verdict.get("reason", "")).strip()
        correction = str(verdict.get("correction", "")).strip()
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        _emit_event(
            "action.review",
            call_id=ud.call_id or "",
            flow=(ud.active_flow or "").lower(),
            source=source,
            status="ok",
            risk=risk,
            reason=reason[:180],
            latency_ms=latency_ms,
        )
        if risk in {"unsafe", "bad"} and correction:
            if review_turn == ud.turn_count and not ud.pending_corrective_response:
                ud.pending_corrective_response = correction[:220]
                ud.pending_corrective_turn = review_turn
                ud.pending_corrective_source = f"action_review:{source}"
            elif review_turn != ud.turn_count:
                _emit_event(
                    "action.review",
                    call_id=ud.call_id or "",
                    flow=(ud.active_flow or "").lower(),
                    source=source,
                    status="stale_correction_dropped",
                    review_turn=review_turn,
                    current_turn=ud.turn_count,
                )
            logger.warning(
                "call=%s | action review flagged reply | source=%s | reason=%s",
                ud.call_id,
                source,
                reason,
            )

    async def _request_action_review(
        self,
        client: Any,
        *,
        user_text: str,
        agent_reply: str,
        state_snapshot: str,
        source: str,
    ) -> dict[str, Any]:
        system = (
            "أنت مراجع أمان صارم لوكيل مطعم بيتكلم مصري. راجع آخر رد فقط بناء على "
            "نص العميل وحالة المكالمة. ارجع JSON فقط بالمفاتيح: risk, reason, correction. "
            "risk تكون ok أو warn أو unsafe. استخدم unsafe فقط لو الرد فيه خطأ مؤثر على العميل: "
            "طلب/عنوان/اسم/نية غلط، اختراع منيو/سعر/مواعيد، طلب رقم موبايل، تأكيد بدون بيانات، "
            "تجاهل عنوان خارج النطاق، أو إعادة سؤال عن معلومة موجودة. correction جملة مصرية قصيرة "
            "واحدة يقولها الوكيل في الدور التالي. لو الرد مقبول أو المشكلة أسلوبية فقط، risk=ok."
        )
        payload = {
            "source": source,
            "state": state_snapshot[:_ACTION_REVIEW_MAX_TEXT_CHARS],
            "user": (user_text or "")[:_ACTION_REVIEW_MAX_TEXT_CHARS],
            "assistant": (agent_reply or "")[:_ACTION_REVIEW_MAX_TEXT_CHARS],
            "restaurant": {
                "name": self.cfg.name,
                "menu": self.cfg.menu_names()[:_ACTION_REVIEW_MAX_TEXT_CHARS],
                "hours": self.cfg.hours_text()[:240],
                "delivery_zones": self.cfg.delivery_zones_text()[:240],
            },
        }
        response = await client.chat.completions.create(
            model=_ACTION_REVIEW_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            max_completion_tokens=200,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {
                "risk": "warn",
                "reason": "reviewer returned non-json",
                "correction": "",
            }
        if not isinstance(parsed, dict):
            return {"risk": "warn", "reason": "reviewer returned non-object", "correction": ""}
        return parsed

    def _action_review_sampled_call(self) -> bool:
        if _ACTION_REVIEW_SAMPLE_RATE >= 1.0:
            return True
        if _ACTION_REVIEW_SAMPLE_RATE <= 0.0:
            return False
        call_id = str(getattr(self.session.userdata, "call_id", "") or "")
        digest = hashlib.sha256(call_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
        return bucket < _ACTION_REVIEW_SAMPLE_RATE

    @staticmethod
    def _action_review_client() -> Any | None:
        global _ACTION_REVIEW_CLIENT
        if _ACTION_REVIEW_CLIENT is not None:
            return _ACTION_REVIEW_CLIENT
        api_key = os.getenv("ACTION_REVIEW_API_KEY", "").strip()
        if not api_key:
            return None
        try:
            from openai import AsyncOpenAI  # type: ignore

            base_url = os.getenv("ACTION_REVIEW_BASE_URL", "").strip() or None
            _ACTION_REVIEW_CLIENT = (
                AsyncOpenAI(api_key=api_key, base_url=base_url)
                if base_url
                else AsyncOpenAI(api_key=api_key)
            )
            return _ACTION_REVIEW_CLIENT
        except Exception as exc:
            logger.warning("action review client setup failed | %s", exc)
            return None

    @staticmethod
    def _action_review_semaphore() -> asyncio.Semaphore:
        global _ACTION_REVIEW_SEMAPHORE
        if _ACTION_REVIEW_SEMAPHORE is None:
            _ACTION_REVIEW_SEMAPHORE = asyncio.Semaphore(_ACTION_REVIEW_MAX_CONCURRENCY)
        return _ACTION_REVIEW_SEMAPHORE

    def _close_session_soon(self) -> None:
        try:
            import asyncio as _asyncio

            session = self.session

            async def _close() -> None:
                await _asyncio.sleep(0.8)
                try:
                    shutdown = getattr(session, "shutdown", None)
                    if callable(shutdown):
                        shutdown(drain=True)
                    else:
                        await session.aclose()
                except Exception as exc:
                    logger.warning("call=%s | deterministic close failed | %s", session.userdata.call_id, exc)

            task = _asyncio.create_task(_close(), name=f"deterministic_close_{self.session.userdata.call_id}")
            self.session.userdata.background_tasks.append(task)
        except Exception as exc:
            logger.warning("call=%s | deterministic close scheduling failed | %s", self.session.userdata.call_id, exc)

    @staticmethod
    def detect_slot_question_categories(text: str) -> set[str]:
        normalized = normalize_ar(text or "")
        if not normalized:
            return set()
        categories: set[str] = set()
        for category, patterns in _SLOT_QUESTION_PATTERNS.items():
            for pattern in patterns:
                if normalize_ar(pattern) in normalized:
                    categories.add(category)
                    break
        return categories

    @staticmethod
    def _slot_is_captured(category: str, ud: UserData) -> bool:
        if category == "order":
            return bool(ud.order)
        if category == "address":
            return bool(ud.delivery_address)
        if category == "name":
            return bool(ud.customer_name)
        if category == "reservation_time":
            return bool(ud.reservation_time)
        if category == "guests":
            return bool(ud.guests_count)
        if category == "branch":
            return bool(ud.selected_branch)
        if category == "complaint":
            return bool(ud.complaint_text)
        return False

    @staticmethod
    def record_asked_slot_questions(ud: UserData, text: str) -> list[str]:
        repeated: list[str] = []
        for category in sorted(RestaurantAgent.detect_slot_question_categories(text)):
            previous_count = ud.asked_slot_questions.get(category, 0)
            ud.asked_slot_questions[category] = previous_count + 1
            if RestaurantAgent._slot_is_captured(category, ud):
                repeated.append(category)
        return repeated

    @staticmethod
    def corrective_response_for_repeated_questions(
        ud: UserData, categories: list[str]
    ) -> str:
        captured_parts: list[str] = []
        if "name" in categories and ud.customer_name:
            captured_parts.append(f"الاسم معايا: {ud.customer_name}")
        if "order" in categories and ud.order:
            captured_parts.append(f"الطلب معايا: {', '.join(ud.order)}")
        if "address" in categories and ud.delivery_address:
            captured_parts.append(f"العنوان معايا: {ud.delivery_address}")
        if "reservation_time" in categories and ud.reservation_time:
            captured_parts.append(f"ميعاد الحجز معايا: {ud.reservation_time}")
        if "guests" in categories and ud.guests_count:
            captured_parts.append(f"العدد معايا: {ud.guests_count}")
        if "branch" in categories and ud.selected_branch:
            captured_parts.append(f"الفرع معايا: {ud.selected_branch}")
        if "complaint" in categories and ud.complaint_text:
            captured_parts.append("الشكوى متسجلة معايا")
        if captured_parts:
            return "تمام يا فندم، " + "، ".join(captured_parts) + "."
        return "تمام يا فندم، المعلومة دي لسه ناقصة عندي، كمل معايا لما تكون جاهز."

    @staticmethod
    def _item_text(item: Any) -> str:
        # ChatMessage.text_content is a property that joins string parts and
        # filters out non-text content (images, audio). Prefer it when present.
        text = getattr(item, "text_content", None)
        if text is not None:
            return str(text).strip()
        content = getattr(item, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(c for c in content if isinstance(c, str)).strip()
        return str(content or "").strip()


__all__ = ["RestaurantAgent"]
