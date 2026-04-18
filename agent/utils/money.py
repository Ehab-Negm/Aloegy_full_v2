from __future__ import annotations

import re


_ONES = ["", "واحد", "اتنين", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة", "تمانية", "تسعة"]
_TEENS = ["عشرة", "حداشر", "اتناشر", "تلاتاشر", "أربعتاشر", "خمستاشر",
          "ستاشر", "سبعتاشر", "تمنتاشر", "تسعتاشر"]
_TENS = ["", "عشرة", "عشرين", "تلاتين", "أربعين", "خمسين", "ستين", "سبعين", "تمانين", "تسعين"]
_HUNDREDS = ["", "مية", "ميتين", "تلتمية", "ربعمية", "خمسمية",
             "ستمية", "سبعمية", "تمنمية", "تسعمية"]
_DIGIT_AR = ["زيرو", "واحد", "اتنين", "تلاتة", "أربعة",
             "خمسة", "ستة", "سبعة", "تمانية", "تسعة"]
_PHONE_PREFIX_SPOKEN = {
    "010": "زيرو عشرة",
    "011": "زيرو حداشر",
    "012": "زيرو اتناشر",
    "015": "زيرو خمستاشر",
}


def _int_to_ar(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 0:
        return f"سالب {_int_to_ar(-n)}"

    parts = []
    if n >= 1000:
        thousands = n // 1000
        if thousands == 1:
            parts.append("ألف")
        elif thousands == 2:
            parts.append("ألفين")
        elif 3 <= thousands <= 10:
            parts.append(f"{_ONES[thousands]} تآلاف")
        else:
            parts.append(f"{_int_to_ar(thousands)} ألف")
        n %= 1000

    if n >= 100:
        parts.append(_HUNDREDS[n // 100])
        n %= 100

    if n >= 20:
        ones = n % 10
        tens = n // 10
        if ones:
            parts.append(f"{_ONES[ones]} و{_TENS[tens]}")
        else:
            parts.append(_TENS[tens])
    elif n >= 10:
        parts.append(_TEENS[n - 10])
    elif n >= 1:
        parts.append(_ONES[n])

    return " و".join(parts)


def num2ar(n: int | float) -> str:
    """يحوّل أرقام صحيحة وكسور بسيطة لكلمات مصرية بدون فقدان الجزء العشري."""
    value = float(n)
    if value.is_integer():
        return _int_to_ar(int(value))

    sign = "سالب " if value < 0 else ""
    value = abs(value)
    whole = int(value)
    fractional = round(value - whole, 2)

    if fractional == 0:
        return f"{sign}{_int_to_ar(whole)}".strip()

    if whole == 0:
        if abs(fractional - 0.5) < 0.001:
            return f"{sign}نص".strip()
        if abs(fractional - 0.25) < 0.001:
            return f"{sign}ربع".strip()

    base = _int_to_ar(whole) if whole else ""
    if abs(fractional - 0.5) < 0.001:
        return f"{sign}{base} ونص".strip()
    if abs(fractional - 0.25) < 0.001:
        return f"{sign}{base} وربع".strip()

    fraction_hundredths = int(round(fractional * 100))
    fraction_text = _int_to_ar(fraction_hundredths)
    return f"{sign}{base} فاصلة {fraction_text}".strip()


def money2ar(value: float) -> str:
    """تمثيل أوضح للأسعار والرسوم مع الحفاظ على القيم العشرية المهمة."""
    return num2ar(float(value))


def phone2ar(phone: str) -> str:
    """ينطق رقم الموبايل بصيغة أقرب للمصريين."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    parts: list[str] = []
    if len(digits) >= 3 and digits[:3] in _PHONE_PREFIX_SPOKEN:
        parts.append(_PHONE_PREFIX_SPOKEN[digits[:3]])
        digits = digits[3:]
    parts.extend(_DIGIT_AR[int(d)] for d in digits)
    return " ".join(parts)

