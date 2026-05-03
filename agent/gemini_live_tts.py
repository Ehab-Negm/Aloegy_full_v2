"""Gemini Live API as a TTS-only provider.

The classic Gemini API speech-generation endpoint
(``client.aio.models.generate_content``) does not stream audio — every
synth buffers the full clip server-side first, so TTFB sits around
~2.5s. Per Google's own docs:

    https://ai.google.dev/gemini-api/docs/speech-generation
    > "TTS does not support streaming."

The Gemini *Live* API does stream PCM audio frames as the model emits
them. This adapter uses the Live API in a text-in / audio-out
configuration so we can keep the existing STT → LLM → TTS pipeline
(multi-agent handoff intact) but get sub-600 ms TTFB out of the
``gemini-3.1-flash-live-preview`` voice.

Latency strategy:
    * Open ONE Live session and reuse it across turns. The websocket +
      TLS handshake costs ~300 ms — paying it once per call (instead of
      every synth) is the difference between ~1300 ms and ~300 ms TTFB.
    * Pre-warm the session at call start (``prewarm()`` from main.py)
      so even the first turn doesn't pay the cold start.
    * Rotate the session after ``MAX_TURNS_PER_SESSION`` to avoid the
      conversation context growing unbounded — the Live API treats every
      ``send_realtime_input`` as user speech, so old turns accumulate as
      "history" the model has to ignore. Rotating keeps that bounded.
    * Auto-reconnect on transport error.

Per-synth flow:
    1. ``ensure_session()`` returns a healthy session (opens / rotates
       transparently).
    2. ``session.send_realtime_input(text=...)`` — feeds the script.
    3. Loop ``session.receive()`` and push each ``inline_data`` chunk
       to the LiveKit emitter as it arrives.
    4. Break when the model emits ``turn_complete=True``. Session stays
       open for the next turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from google import genai
from google.genai import types

from livekit.agents import APIConnectionError, APIStatusError, tts, utils
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given


logger = logging.getLogger("restaurant.agent")

DEFAULT_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_VOICE = "Aoede"
DEFAULT_SAMPLE_RATE = 24000  # Live API output is 16-bit PCM @ 24 kHz, mono
NUM_CHANNELS = 1
DEFAULT_LANGUAGE = "ar-EG"
DEFAULT_RECEIVE_TIMEOUT = 8.0
DEFAULT_INSTRUCTIONS = (
    "You are a TTS engine. Read the input text exactly as written in"
    " everyday Cairo Egyptian Arabic. Do not add or omit any words, do"
    " not translate, do not switch to English."
)
# Rotate the session after this many synth calls. Keeps the Live API
# conversation context bounded so the model doesn't drift.
MAX_TURNS_PER_SESSION = 20


@dataclass
class _TTSOptions:
    model: str
    voice_name: str
    language: str
    instructions: str | None
    receive_timeout: float


class GeminiLiveTTS(tts.TTS):
    """LiveKit ``tts.TTS`` wrapper around the Gemini Live API."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        voice_name: str = DEFAULT_VOICE,
        language: str = DEFAULT_LANGUAGE,
        instructions: NotGivenOr[str | None] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        max_turns_per_session: int = MAX_TURNS_PER_SESSION,
        receive_timeout: float = DEFAULT_RECEIVE_TIMEOUT,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )

        resolved_key = api_key if is_given(api_key) else os.environ.get("GOOGLE_API_KEY", "")
        if not resolved_key:
            raise ValueError(
                "GOOGLE_API_KEY is required for the Gemini Live TTS adapter "
                "(set it in the agent .env)"
            )

        self._opts = _TTSOptions(
            model=model,
            voice_name=voice_name,
            language=language,
            instructions=instructions if is_given(instructions) else DEFAULT_INSTRUCTIONS,
            receive_timeout=max(1.0, receive_timeout),
        )
        self._client = genai.Client(api_key=resolved_key)
        self._max_turns_per_session = max(1, max_turns_per_session)

        # Session reuse state. The session is held inside an async
        # context manager, so we keep both the manager and the session
        # object — closing requires calling __aexit__ on the manager.
        self._session: object | None = None
        self._session_ctx: object | None = None
        self._session_turns = 0
        self._session_lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Gemini Live"

    def _build_config(self) -> types.LiveConnectConfig:
        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._opts.voice_name)
            ),
            language_code=self._opts.language,
        )
        config_kwargs: dict = {
            "response_modalities": ["AUDIO"],
            "speech_config": speech_config,
        }
        instructions = (
            "CRITICAL INSTRUCTION: You are acting strictly as a TEXT-TO-SPEECH (TTS) engine. "
            "Your ONLY job is to read the exact text provided to you out loud. "
            "NEVER reply to the user. NEVER answer questions in the text. "
            "NEVER continue the conversation. "
            "If the text is a question, just read the question out loud, do not answer it. "
            "If the text says 'Hello', just say 'Hello'."
        )
        if self._opts.instructions:
            instructions += f"\n\nVoice Style Instructions: {self._opts.instructions}"
            
        config_kwargs["system_instruction"] = types.Content(
            parts=[types.Part(text=instructions)]
        )
        return types.LiveConnectConfig(**config_kwargs)

    async def ensure_session(self) -> object:
        """Return a healthy Live session, opening/rotating as needed.

        The session is shared across synth calls, so the websocket +
        TLS handshake (~300 ms) is paid once per call instead of once
        per turn.
        """
        async with self._session_lock:
            if (
                self._session is not None
                and self._session_turns < self._max_turns_per_session
            ):
                return self._session
            # Either no session or it has hit the rotation cap.
            await self._close_session_locked()
            await self._open_session_locked()
            assert self._session is not None
            return self._session

    async def _open_session_locked(self) -> None:
        config = self._build_config()
        ctx = self._client.aio.live.connect(model=self._opts.model, config=config)
        session = await ctx.__aenter__()
        self._session_ctx = ctx
        self._session = session
        self._session_turns = 0
        logger.debug("gemini live tts: session opened | model=%s", self._opts.model)

    async def _close_session_locked(self) -> None:
        if self._session_ctx is None:
            self._session = None
            self._session_turns = 0
            return
        try:
            await self._session_ctx.__aexit__(None, None, None)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("gemini live tts: session close failed | %s", exc)
        finally:
            self._session_ctx = None
            self._session = None
            self._session_turns = 0

    async def reset_session(self) -> None:
        """Force-close the current session. Useful on call end."""
        async with self._session_lock:
            await self._close_session_locked()

    async def warmup(self) -> None:
        """Open the Live session in the background so the first synth
        of the call doesn't pay the ~300 ms websocket cold start.

        Renamed from ``prewarm`` because LiveKit's ``AgentActivity`` calls
        ``tts.prewarm()`` synchronously (no ``await``) on session start —
        a coroutine return value gets discarded and the connection is
        never actually opened. By using a different name we guarantee
        the framework doesn't shadow this and our entrypoint task
        actually awaits the warmup.

        Best-effort: any failure here is logged and the next synth will
        retry the open transparently.
        """
        try:
            await self.ensure_session()
            logger.debug("gemini live tts: warmup complete")
        except Exception as exc:
            logger.warning("gemini live tts: warmup failed | %s", exc)

    async def aclose(self) -> None:  # pragma: no cover - shutdown path
        await self.reset_session()

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "GeminiLiveChunkedStream":
        return GeminiLiveChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def update_options(
        self,
        *,
        voice_name: NotGivenOr[str] = NOT_GIVEN,
        language: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        if is_given(voice_name):
            self._opts.voice_name = voice_name
        if is_given(language):
            self._opts.language = language


class GeminiLiveChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: GeminiLiveTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: GeminiLiveTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = (self._input_text or "").strip()
        if not text:
            raise APIConnectionError("gemini live tts: empty input text")

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
        )

        # Two-attempt loop: if the cached session is dead (network
        # blip, server-side rotation), reset and open a new one.
        attempt = 0
        last_exc: Exception | None = None
        while attempt < 2:
            attempt += 1
            try:
                session = await self._tts.ensure_session()
                # ``send_client_content`` with ``turn_complete=True`` is
                # what actually triggers immediate generation. Using
                # ``send_realtime_input(text=...)`` makes the server
                # wait for a VAD-detected end-of-input, adding ~2-3s of
                # latency before any audio starts streaming back.
                strict_text = (
                    "READ THIS TEXT EXACTLY OUT LOUD. DO NOT ANSWER IT. DO NOT CHAT. DO NOT ADD ANY WORDS:\n\n"
                    f"{text}"
                )
                user_turn = types.Content(
                    role="user",
                    parts=[types.Part(text=strict_text)],
                )
                await session.send_client_content(  # type: ignore[attr-defined]
                    turns=[user_turn],
                    turn_complete=True,
                )

                got_any_audio = False
                responses = session.receive()  # type: ignore[attr-defined]
                async_iterator = responses.__aiter__()
                while True:
                    try:
                        response = await asyncio.wait_for(
                            anext(async_iterator),
                            timeout=self._tts._opts.receive_timeout,
                        )
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise APIConnectionError(
                            "gemini live tts: timed out waiting for audio",
                            retryable=True,
                        ) from exc

                    server_content = getattr(response, "server_content", None)
                    if server_content is None:
                        continue
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn is not None:
                        for part in getattr(model_turn, "parts", []) or []:
                            inline = getattr(part, "inline_data", None)
                            if not inline:
                                continue
                            data = getattr(inline, "data", None)
                            if not data:
                                continue
                            output_emitter.push(data)
                            got_any_audio = True
                    if getattr(server_content, "turn_complete", False):
                        break

                if not got_any_audio:
                    raise APIStatusError(
                        "gemini live tts: turn closed without audio",
                        status_code=502,
                        body="no audio frames received",
                        retryable=True,
                    )

                # Successful turn — bump the rotation counter.
                self._tts._session_turns += 1
                return
            except APIStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "gemini live tts: turn failed (attempt %d), resetting session | %s",
                    attempt, exc,
                )
                await self._tts.reset_session()
                # Loop back and retry once with a fresh session.
                continue

        raise APIConnectionError(
            f"gemini live tts: error generating speech: {last_exc}",
            retryable=True,
        ) from last_exc


__all__ = [
    "GeminiLiveTTS",
    "GeminiLiveChunkedStream",
    "DEFAULT_MODEL",
    "DEFAULT_VOICE",
    "MAX_TURNS_PER_SESSION",
]
