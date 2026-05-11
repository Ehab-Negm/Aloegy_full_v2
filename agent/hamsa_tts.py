"""Hamsa AI realtime TTS plugin — WebSocket streaming for low-latency Arabic synthesis."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
from urllib.parse import quote

import websockets
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("restaurant.hamsa_tts")

WS_URL = "wss://api.tryhamsa.com/v1/realtime/ws"
NUM_CHANNELS = 1
MAX_TEXT_LEN = 2000
# Per Hamsa REST streaming docs: "after collecting the chunks, you need to add
# the wav header manually to the data" — i.e. the stream is raw PCM without a
# header and Hamsa does not document the exact sample rate anywhere public.
# Empirically the realtime endpoint emits 16 kHz PCM for the prebuilt Arabic
# voices. Override via HAMSA_DEFAULT_SAMPLE_RATE if your voice sounds faster
# (rate too high) or slower (rate too low) than natural:
#   voice sounds sped up / chipmunky -> lower the rate
#   voice sounds slowed / deep       -> raise the rate
# Common valid values: 8000, 16000, 22050, 24000, 48000.
DEFAULT_PCM_SAMPLE_RATE = int(os.getenv("HAMSA_DEFAULT_SAMPLE_RATE", "16000"))
MULAW_SAMPLE_RATE = 8000


def _env_float(name: str, default: float, *, min_value: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return max(min_value, float(raw))
    except ValueError:
        return default


PREWARM_OPEN_TIMEOUT_SECONDS = _env_float(
    "HAMSA_PREWARM_OPEN_TIMEOUT_SECONDS",
    2.5,
    min_value=0.2,
)


def _parse_wav_header(data: bytes) -> tuple[int, int] | None:
    # Returns (sample_rate, header_size) if a valid RIFF/WAVE PCM header is found at start.
    if len(data) < 44 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    sample_rate = 0
    data_offset = 0
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        chunk_size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if chunk_id == b"fmt " and pos + 8 + chunk_size <= len(data):
            sample_rate = struct.unpack("<I", data[pos + 12:pos + 16])[0]
        elif chunk_id == b"data":
            data_offset = pos + 8
            break
        pos += 8 + chunk_size
    if sample_rate and data_offset:
        return sample_rate, data_offset
    return None


def _ws_open(ws) -> bool:
    """Best-effort liveness check across websockets versions."""
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        # websockets >=11 exposes State enum (OPEN=1)
        name = getattr(state, "name", None)
        if name is not None:
            return name == "OPEN"
        return int(state) == 1
    closed = getattr(ws, "closed", None)
    if closed is not None:
        return not closed
    return True


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "Salma",
        dialect: str = "egy",
        language_id: str = "ar",
        mulaw: bool = False,
    ) -> None:
        sample_rate = MULAW_SAMPLE_RATE if mulaw else DEFAULT_PCM_SAMPLE_RATE
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._api_key = api_key
        self._voice = voice
        self._dialect = dialect
        self._language_id = language_id
        self._mulaw = mulaw
        self._sample_rate = sample_rate

        # Persistent WS pool (size=1). Holding one warm connection eliminates
        # the TLS+WS handshake (200–600ms) on every utterance.
        self._ws_lock = asyncio.Lock()
        self._ws: websockets.WebSocketClientProtocol | None = None

    @property
    def _ws_url(self) -> str:
        return f"{WS_URL}?api_key={quote(self._api_key)}"

    async def _connect_ws(self, *, open_timeout: float):
        return await websockets.connect(
            self._ws_url,
            additional_headers={"X-Api-Key": self._api_key},
            open_timeout=open_timeout,
            close_timeout=5,
            max_size=None,
            ping_interval=20,
            ping_timeout=10,
        )

    async def _acquire_ws(self, *, open_timeout: float):
        """Caller must hold self._ws_lock. Returns a live WS, reconnecting if stale."""
        if _ws_open(self._ws):
            return self._ws
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
        self._ws = await self._connect_ws(open_timeout=open_timeout)
        logger.info("hamsa tts | ws (re)connected")
        return self._ws

    async def _drop_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def _prewarm_async(self) -> None:
        """Open and hold a WS so the first synthesis avoids handshake."""
        async with self._ws_lock:
            try:
                await self._acquire_ws(open_timeout=PREWARM_OPEN_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.debug("hamsa prewarm failed | %s", exc)

    def prewarm(self) -> None:
        """LiveKit StreamAdapter calls this synchronously; schedule best-effort warmup."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._prewarm_async())

        def _log_prewarm_failure(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.debug("hamsa prewarm failed | %s", exc)

        task.add_done_callback(_log_prewarm_failure)

    async def warmup(self) -> None:
        """Awaitable warmup used by the agent startup path."""
        await self._prewarm_async()

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        async with self._ws_lock:
            await self._drop_ws()


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = (self.input_text or "").strip()
        if not text:
            # LiveKit requires the emitter to be initialized before _run returns,
            # otherwise end_input() raises "AudioEmitter isn't started".
            output_emitter.initialize(
                request_id="",
                sample_rate=self._tts._sample_rate,
                num_channels=NUM_CHANNELS,
                mime_type="audio/mulaw" if self._tts._mulaw else "audio/pcm",
            )
            output_emitter.flush()
            return
        if len(text) > MAX_TEXT_LEN:
            text = text[:MAX_TEXT_LEN]

        request = {
            "type": "tts",
            "payload": {
                "text": text,
                "speaker": self._tts._voice,
                "dialect": self._tts._dialect,
                "languageId": self._tts._language_id,
                "mulaw": self._tts._mulaw,
            },
        }

        timeout = self._conn_options.timeout if self._conn_options else 30.0

        # One synthesis at a time per TTS instance. Voice agent only ever
        # renders one utterance at a time, so contention is naturally zero.
        async with self._tts._ws_lock:
            await self._render_with_retry(request, timeout, output_emitter)

    async def _render_with_retry(
        self,
        request: dict,
        timeout: float,
        output_emitter: tts.AudioEmitter,
    ) -> None:
        # First attempt may use a stale persistent WS. If send/recv fails
        # because the server closed it, drop and try once more with a fresh WS.
        for attempt in (1, 2):
            try:
                ws = await self._tts._acquire_ws(open_timeout=timeout)
                await self._render_once(ws, request, timeout, output_emitter)
                return
            except (
                websockets.ConnectionClosedError,
                websockets.ConnectionClosedOK,
            ) as exc:
                await self._tts._drop_ws()
                if attempt == 2:
                    raise APIConnectionError() from exc
                logger.info("hamsa tts | ws closed mid-stream, reconnecting")
                continue
            except APIStatusError:
                raise
            except APITimeoutError:
                await self._tts._drop_ws()
                raise
            except (websockets.InvalidStatus, websockets.InvalidHandshake) as e:
                await self._tts._drop_ws()
                status = getattr(getattr(e, "response", None), "status_code", 0) or 0
                raise APIStatusError(
                    message=f"Hamsa TTS handshake failed: {e}",
                    status_code=status,
                    request_id=None,
                    body=str(e),
                ) from e
            except Exception as e:
                await self._tts._drop_ws()
                raise APIConnectionError() from e

    async def _render_once(
        self,
        ws,
        request: dict,
        timeout: float,
        output_emitter: tts.AudioEmitter,
    ) -> None:
        await ws.send(json.dumps(request))

        initialized = False
        header_stripped = self._tts._mulaw  # mulaw stream has no WAV header
        pending_audio = bytearray()
        request_id = ""

        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                raise APITimeoutError() from None

            if isinstance(msg, (bytes, bytearray)):
                if not header_stripped:
                    pending_audio.extend(msg)
                    parsed = _parse_wav_header(bytes(pending_audio))
                    if parsed is not None:
                        sample_rate, header_size = parsed
                        logger.info(
                            "hamsa tts | detected WAV header | sample_rate=%dHz | header_size=%d",
                            sample_rate, header_size,
                        )
                        if not initialized:
                            output_emitter.initialize(
                                request_id=request_id,
                                sample_rate=sample_rate,
                                num_channels=NUM_CHANNELS,
                                mime_type="audio/pcm",
                            )
                            initialized = True
                        payload = bytes(pending_audio[header_size:])
                        pending_audio.clear()
                        header_stripped = True
                        if payload:
                            output_emitter.push(payload)
                    elif len(pending_audio) >= 4 and pending_audio[:4] != b"RIFF":
                        # No WAV header — treat as raw PCM at the configured
                        # default rate. Log the first bytes so we can adjust
                        # HAMSA_DEFAULT_SAMPLE_RATE if the voice sounds off.
                        logger.warning(
                            "hamsa tts | no WAV header detected | first_bytes=%r | using_sample_rate=%dHz",
                            bytes(pending_audio[:16]), self._tts._sample_rate,
                        )
                        if not initialized:
                            output_emitter.initialize(
                                request_id=request_id,
                                sample_rate=self._tts._sample_rate,
                                num_channels=NUM_CHANNELS,
                                mime_type="audio/pcm",
                            )
                            initialized = True
                        output_emitter.push(bytes(pending_audio))
                        pending_audio.clear()
                        header_stripped = True
                    continue

                if not initialized:
                    output_emitter.initialize(
                        request_id=request_id,
                        sample_rate=self._tts._sample_rate,
                        num_channels=NUM_CHANNELS,
                        mime_type="audio/mulaw" if self._tts._mulaw else "audio/pcm",
                    )
                    initialized = True
                output_emitter.push(bytes(msg))
                continue

            try:
                parsed_msg = json.loads(msg)
            except (TypeError, ValueError):
                continue

            msg_type = parsed_msg.get("type")
            payload = parsed_msg.get("payload") or {}

            if msg_type == "ack":
                request_id = payload.get("requestId") or payload.get("id") or ""
            elif msg_type == "end":
                break
            elif msg_type == "error":
                err_msg = str(payload.get("message", "unknown")).lower()
                logger.warning("hamsa tts | error message received: %s", payload)
                # Hamsa sends "aborted" when client closed the stream mid-way
                # (e.g. user interrupted). Treat it as a graceful end, not an error.
                if "abort" in err_msg:
                    break
                raise APIStatusError(
                    message=f"Hamsa TTS error: {payload.get('message', 'unknown')}",
                    status_code=400,
                    request_id=request_id or None,
                    body=json.dumps(parsed_msg),
                )

        if pending_audio:
            if not initialized:
                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=self._tts._sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type="audio/mulaw" if self._tts._mulaw else "audio/pcm",
                )
        if not initialized:
            logger.warning("hamsa tts | stream ended without initialization | request=%r", request)
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=self._tts._sample_rate,
                num_channels=NUM_CHANNELS,
                mime_type="audio/mulaw" if self._tts._mulaw else "audio/pcm",
            )
        output_emitter.flush()
