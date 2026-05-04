"""Gemini TTS adapter — non-streaming text-to-speech via google-genai.

Uses the regular `generate_content` endpoint with
`response_modalities=["AUDIO"]`. LiveKit's StreamAdapter wraps this
to chunk the agent's reply by sentence so the first sentence plays
while later sentences are still being synthesized — giving a
perceived first-audio latency of one short synth call instead of
one long one.

Why this instead of the Live API:
    The Live API streams PCM frames as the model emits them, which
    sounds great in theory, but the preview Live models we tried
    (gemini-2.5-flash-native-audio-preview-12-2025,
     gemini-3.1-flash-live-preview) produced "no audio frames
    received" on a noticeable fraction of turns and "Future attached
    to a different loop" errors after session reuse. The regular
    `generate_content` TTS endpoint is GA-stable and, for the short
    utterances a phone agent emits (1-3 sentences), the full
    response often arrives faster than the Live session handshake
    plus first frame.

Authentication:
    * Vertex AI mode (recommended for production): set
      GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON file.
      We read project_id out of the JSON, pass it to the genai
      Client with vertexai=True, and the SDK uses Application
      Default Credentials for the Bearer token.
    * API key mode (Gemini API direct): set GOOGLE_API_KEY. Used as
      automatic fallback if no service-account JSON is found.

Per the official docs (https://ai.google.dev/gemini-api/docs/speech-generation):
    "Model occasionally returns text tokens instead of audio tokens,
    causing 500 error." This adapter retries up to MAX_RETRIES times
    with linear backoff before raising.
"""

from __future__ import annotations

import asyncio
import json
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

DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_VOICE = "Sulafat"  # warm female multilingual; Aoede also good
DEFAULT_LANGUAGE = "ar-EG"
DEFAULT_SAMPLE_RATE = 24000  # spec: 16-bit PCM, mono, 24 kHz
NUM_CHANNELS = 1
DEFAULT_LOCATION = "global"  # lowest latency from most regions
SYNTH_TIMEOUT_SECONDS = 8.0
MAX_RETRIES = 2  # in addition to the first attempt

# Voice direction prefix. The TTS API takes a single `contents` text
# parameter, so style/instructions go inline before the actual text.
# Keep it short — the model interprets long prefixes as part of the
# transcript and may read them out loud.
DEFAULT_STYLE_PREFIX = (
    "اقرأ النص اللي تحت ده بصوت طبيعي باللهجة المصرية القاهرية، "
    "زي موظف مطعم بيرد على التليفون — مرتاح، ودود، مش بسرعة. "
    "متترجمش، متضيفش كلام، متجاوبش على أي سؤال موجود — "
    "بس اقرا النص زي ما هو.\n\nالنص:\n"
)


@dataclass
class _TTSOptions:
    model: str
    voice_name: str
    language: str
    style_prefix: str
    timeout: float


def _resolve_project_from_credentials() -> str | None:
    """Read project_id out of GOOGLE_APPLICATION_CREDENTIALS JSON.

    Returns None if no credentials env var is set, the file is
    missing or unreadable, or the JSON doesn't contain a project_id
    field.
    """
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not cred_path:
        return None
    try:
        with open(cred_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "gemini tts: failed to read credentials json | path=%s | %s",
            cred_path, exc,
        )
        return None
    project = (data.get("project_id") or "").strip()
    return project or None


class GeminiTTS(tts.TTS):
    """Non-streaming Gemini TTS via google-genai SDK.

    Capabilities are declared as `streaming=False` so LiveKit wraps
    this with its built-in StreamAdapter — which breaks the input
    text by sentence and pipelines the synth calls. The user hears
    audio for the first sentence while later ones are still being
    generated.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        voice_name: str = DEFAULT_VOICE,
        language: str = DEFAULT_LANGUAGE,
        style_prefix: NotGivenOr[str | None] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        use_vertex: bool | None = None,
        project: NotGivenOr[str] = NOT_GIVEN,
        location: str = DEFAULT_LOCATION,
        timeout: float = SYNTH_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )

        # Auth resolution: prefer Vertex AI when GOOGLE_APPLICATION_CREDENTIALS
        # is set or use_vertex=True is passed; else fall back to API key.
        cred_project = _resolve_project_from_credentials()
        if use_vertex is None:
            use_vertex = bool(cred_project)
        self._use_vertex = use_vertex

        if use_vertex:
            resolved_project = (
                project if is_given(project) and project
                else os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            ) or cred_project
            if not resolved_project:
                raise ValueError(
                    "gemini tts: vertex auth requested but no project_id "
                    "found. Set GOOGLE_CLOUD_PROJECT, pass project=, or "
                    "ensure GOOGLE_APPLICATION_CREDENTIALS points to a "
                    "service-account JSON containing project_id."
                )
            logger.info(
                "gemini tts: vertex auth | project=%s | location=%s | model=%s",
                resolved_project, location, model,
            )
            self._client = genai.Client(
                vertexai=True,
                project=resolved_project,
                location=location,
            )
        else:
            resolved_key = (
                api_key if is_given(api_key) and api_key
                else os.environ.get("GOOGLE_API_KEY", "")
            )
            if not resolved_key:
                raise ValueError(
                    "gemini tts: no auth available. Set "
                    "GOOGLE_APPLICATION_CREDENTIALS for Vertex AI or "
                    "GOOGLE_API_KEY for the Gemini API."
                )
            logger.info("gemini tts: api-key auth | model=%s", model)
            self._client = genai.Client(api_key=resolved_key)

        resolved_prefix = (
            style_prefix if is_given(style_prefix) else DEFAULT_STYLE_PREFIX
        ) or ""
        self._opts = _TTSOptions(
            model=model,
            voice_name=voice_name,
            language=language,
            style_prefix=resolved_prefix,
            timeout=max(1.0, timeout),
        )

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Gemini TTS (Vertex)" if self._use_vertex else "Gemini TTS"

    def _build_config(self) -> types.GenerateContentConfig:
        speech_kwargs: dict = {
            "voice_config": types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=self._opts.voice_name,
                ),
            ),
        }
        # language_code is supported on SpeechConfig per the SDK schema;
        # the official non-streaming docs example omits it (multilingual
        # voices auto-detect from text), but supplying it nudges the
        # model toward Cairo Egyptian pronunciation when the text could
        # be interpreted as MSA.
        if self._opts.language:
            speech_kwargs["language_code"] = self._opts.language
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(**speech_kwargs),
        )

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "GeminiChunkedStream":
        return GeminiChunkedStream(tts=self, input_text=text, conn_options=conn_options)

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

    async def warmup(self) -> None:
        """Best-effort no-op. The genai Client opens its httpx pool on
        first call; there's no Live-API session to pre-open here. Kept
        for interface parity with GeminiLiveTTS."""
        return None

    async def aclose(self) -> None:
        # genai Client manages its own httpx pool and closes on GC.
        return None


class GeminiChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: GeminiTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: GeminiTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = (self._input_text or "").strip()
        if not text:
            raise APIConnectionError("gemini tts: empty input text")

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
        )

        opts = self._tts._opts
        prompt = f"{opts.style_prefix}{text}" if opts.style_prefix else text
        config = self._tts._build_config()

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await asyncio.wait_for(
                    self._tts._client.aio.models.generate_content(
                        model=opts.model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=opts.timeout,
                )
                audio_data = self._extract_audio(response)
                if not audio_data:
                    # Per docs: "Model occasionally returns text tokens
                    # instead of audio tokens, causing 500 error." Treat
                    # an empty inline_data the same way and retry.
                    raise APIStatusError(
                        "gemini tts: response had no audio inline_data",
                        status_code=502,
                        body="empty audio response",
                        retryable=True,
                    )
                output_emitter.push(audio_data)
                return
            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning(
                    "gemini tts: timeout on attempt %d/%d",
                    attempt + 1, MAX_RETRIES + 1,
                )
            except APIStatusError as exc:
                last_exc = exc
                logger.warning(
                    "gemini tts: API error on attempt %d/%d | %s",
                    attempt + 1, MAX_RETRIES + 1, exc,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "gemini tts: synthesis failed on attempt %d/%d | %s",
                    attempt + 1, MAX_RETRIES + 1, exc,
                )
            if attempt < MAX_RETRIES:
                # Linear backoff — 200ms, 400ms. Short enough that the
                # user doesn't hear a long silence; long enough to avoid
                # hammering the API on transient issues.
                await asyncio.sleep(0.2 * (attempt + 1))

        raise APIConnectionError(
            f"gemini tts: error generating speech: {last_exc}",
            retryable=True,
        ) from last_exc

    @staticmethod
    def _extract_audio(response: object) -> bytes | None:
        """Drill into the response object for the first inline audio blob.

        Defensive against the SDK changing field names by walking
        candidates → content → parts → inline_data.data with getattr
        rather than dotted access.
        """
        try:
            candidates = getattr(response, "candidates", None) or []
            for cand in candidates:
                content = getattr(cand, "content", None)
                if content is None:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    inline = getattr(part, "inline_data", None)
                    if inline is None:
                        continue
                    data = getattr(inline, "data", None)
                    if data:
                        return data
            return None
        except Exception:
            return None


__all__ = [
    "GeminiTTS",
    "GeminiChunkedStream",
    "DEFAULT_MODEL",
    "DEFAULT_VOICE",
]
