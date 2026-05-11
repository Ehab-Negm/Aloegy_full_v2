from __future__ import annotations

import logging
import re
from typing import Any


logger = logging.getLogger("restaurant.agent")


async def say_safe(
    session: Any,
    text: str,
    *,
    add_to_chat_ctx: bool = True,
    allow_interruptions: bool = True,
) -> None:
    """Speak ``text`` if the session has a TTS attached; otherwise nudge the
    realtime model to speak it.

    Classic pipeline (``session.tts`` set):
        Forwards to ``await session.say(...)`` which renders via the TTS plugin.

    Realtime pipeline (``session.tts is None``):
        Gemini 3.1 Flash Live rejects ``generate_reply`` /
        ``update_instructions`` / ``update_chat_ctx``, but it DOES accept a
        synthetic ``LiveClientContent`` user turn pushed via the plugin's
        ``_send_client_event``. We pass the desired line as a hidden user
        instruction; the model produces an audio reply that satisfies it.
        This is what lets the opening greeting and inactivity reprompts
        actually reach the customer in realtime mode — without this, the
        web widget hears total silence because every deterministic prompt
        gets dropped.

    Errors are swallowed and logged at DEBUG; speaking a deterministic line is
    best-effort and should never crash the call.
    """
    if not text:
        return
    if getattr(session, "tts", None) is None:
        await _nudge_realtime_with_text(session, text)
        return
    await session.say(
        text,
        add_to_chat_ctx=add_to_chat_ctx,
        allow_interruptions=allow_interruptions,
    )


async def _nudge_realtime_with_text(session: Any, text: str) -> None:
    """Push ``text`` into the realtime model as a hidden user turn.

    The text reads to the model as a system-style instruction (rendered in
    a user-role frame because that's the only frame type Gemini 3.1 Live
    consumes on this code path). The model then emits its audio reply,
    which is what the customer actually hears.
    """
    try:
        from google.genai import types as _gt
    except ImportError:
        logger.debug("realtime nudge: google.genai not importable | skipping | text=%s", text[:60])
        return

    activity = getattr(session, "_activity", None)
    rt_session = getattr(activity, "realtime_llm_session", None) if activity else None
    send_event = getattr(rt_session, "_send_client_event", None) if rt_session else None
    if not callable(send_event):
        logger.debug(
            "realtime nudge: _send_client_event not available yet | skipping | text=%s",
            text[:60],
        )
        return

    # Frame the line as an instruction the model should *say*, not as
    # something the customer said. Otherwise the model would treat it as a
    # new utterance to react to. The Arabic prefix keeps the model from
    # echoing the English meta-text.
    instruction = f"قل للزبون بالعربي: {text}"
    try:
        send_event(
            _gt.LiveClientContent(
                turns=[_gt.Content(role="user", parts=[_gt.Part(text=instruction)])],
                turn_complete=True,
            ),
        )
        logger.info("realtime nudge | text=%s", text[:60])
    except Exception as exc:  # noqa: BLE001
        logger.debug("realtime nudge failed | %s | text=%s", exc, text[:60])


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
