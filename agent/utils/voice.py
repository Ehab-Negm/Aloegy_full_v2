from __future__ import annotations

import re


def _voice_safe_text(
    text: str,
    max_sentences: int = 2,
    max_chars: int = 120,
    *,
    critical: bool = False,
) -> str:
    """يقصّر النصوص الطويلة قبل الـ TTS من غير ما يغيّر المعنى الأساسي."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""

    if critical:
        return cleaned

    sentences = [part.strip(" ،") for part in re.split(r"[.!؟\n]+", cleaned) if part.strip(" ،")]
    if sentences:
        cleaned = "، ".join(sentences[:max_sentences])
    if len(cleaned) > max_chars:
        truncated = cleaned[: max_chars - 1]
        last_space = truncated.rfind(" ")
        if last_space > max_chars // 2:
            truncated = truncated[:last_space]
        cleaned = truncated.rstrip(" ،,.") + "…"
    return cleaned
