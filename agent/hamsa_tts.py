"""Hamsa AI realtime TTS plugin — WebSocket streaming for low-latency Arabic synthesis."""

from __future__ import annotations

import asyncio
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

        # ── Pre-warmed WebSocket pool ────────────────────────────────
        # Each TTS request used to pay TCP+TLS+WS handshake cost
        # (200-300ms) on top of Hamsa's synthesis time. With this pool
        # we keep ONE connection ready in the background so the next
        # synthesize call can send the TTS request immediately and
        # only pays the model latency.
        #
        # Lifecycle:
        #   - Each call to ``_run`` takes the warm WS (or opens cold
        #     if pool is empty), uses it for ONE synthesis (Hamsa
        #     closes after ``end``), then schedules a fresh warm
        #     connection for the next caller in the background.
        self._next_ws: "websockets.WebSocketClientProtocol | None" = None
        self._next_ws_lock = asyncio.Lock()
        self._prewarm_in_flight = False

    @property
    def _ws_url(self) -> str:
        return f"{WS_URL}?api_key={quote(self._api_key)}"

    async def _open_ws(self, timeout: float):
        return await websockets.connect(
            self._ws_url,
            additional_headers={"X-Api-Key": self._api_key},
            open_timeout=timeout,
            close_timeout=5,
            max_size=None,
        )

    async def _take_warm_ws(self, timeout: float):
        """Return a ready-to-use WebSocket. Use the pre-warmed one
        if available; otherwise open cold. Caller owns lifecycle."""
        async with self._next_ws_lock:
            ws = self._next_ws
            self._next_ws = None
        if ws is not None:
            try:
                if not ws.closed:
                    return ws
            except Exception:
                pass
        # Pool was empty or closed — open a fresh connection
        return await self._open_ws(timeout)

    async def _prewarm_async(self) -> None:
        """Open a fresh WebSocket and stash it for the next caller.
        Errors are swallowed: on failure ``_take_warm_ws`` falls back
        to opening cold, which is exactly the pre-pre-warm behaviour."""
        async with self._next_ws_lock:
            if self._prewarm_in_flight:
                return
            if self._next_ws is not None and not self._next_ws.closed:
                return
            self._prewarm_in_flight = True
        try:
            ws = await self._open_ws(timeout=10.0)
        except Exception as exc:
            logger.debug("hamsa tts | prewarm failed | err=%s", exc)
            ws = None
        async with self._next_ws_lock:
            if ws is not None and (self._next_ws is None or self._next_ws.closed):
                self._next_ws = ws
            elif ws is not None:
                # Lost a race — close the redundant one
                try:
                    await ws.close()
                except Exception:
                    pass
            self._prewarm_in_flight = False

    def schedule_prewarm(self) -> None:
        """Fire-and-forget pre-warm. Safe to call from any async ctx;
        if no loop is running we silently skip."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._prewarm_async())

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        async with self._next_ws_lock:
            ws = self._next_ws
            self._next_ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = (self.input_text or "").strip()
        if not text:
            return
        if len(text) > MAX_TEXT_LEN:
            text = text[:MAX_TEXT_LEN]

        # ──── TTS cache fast-path ────
        # Common replies (menu, post-completion, opening, …) hit the
        # exact same text on every call. Replaying cached audio
        # eliminates the 900-1100 ms ttfb the websocket synthesis pays.
        try:
            from core.tts_cache import GLOBAL_CACHE as _TTS_CACHE
        except ImportError:  # pragma: no cover - cache module is part of the repo
            _TTS_CACHE = None
        cache_model = f"hamsa:{self._tts._voice}:{self._tts._dialect}:{int(self._tts._mulaw)}"
        cached_entry = (
            _TTS_CACHE.get(model=cache_model, text=text) if _TTS_CACHE else None
        )
        if cached_entry is not None:
            mime = "audio/mulaw" if self._tts._mulaw else "audio/pcm"
            output_emitter.initialize(
                request_id="cache",
                sample_rate=cached_entry.sample_rate,
                num_channels=NUM_CHANNELS,
                mime_type=mime,
            )
            output_emitter.push(cached_entry.audio)
            output_emitter.flush()
            logger.info(
                "hamsa tts | cache hit | chars=%d | bytes=%d",
                len(text),
                len(cached_entry.audio),
            )
            return

        # Buffer audio bytes as they arrive so we can store them after a
        # successful synthesis. The cache only saves the rendered PCM
        # payload — the same format we replay above.
        cache_buffer = bytearray()
        cache_sample_rate = self._tts._sample_rate

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

        # Take a pre-warmed WebSocket if available, else open cold.
        # Eagerly schedule the NEXT pre-warm before this synthesis even
        # starts so the connection is ready by the time this turn ends.
        ws = await self._tts._take_warm_ws(timeout)
        self._tts.schedule_prewarm()
        try:
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
                            cache_sample_rate = sample_rate
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
                                cache_buffer.extend(payload)
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
                            cache_buffer.extend(pending_audio)
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
                    cache_buffer.extend(msg)
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
                    initialized = True
                output_emitter.push(bytes(pending_audio))
                cache_buffer.extend(pending_audio)

            output_emitter.flush()

            # Save the rendered audio for next time. ``put`` decides
            # whether the model+text combination is in the cacheable
            # set; here we just hand it the bytes.
            if _TTS_CACHE is not None and cache_buffer and initialized:
                try:
                    _TTS_CACHE.put(
                        model=cache_model,
                        text=text,
                        audio=bytes(cache_buffer),
                        sample_rate=cache_sample_rate,
                    )
                except Exception:  # pragma: no cover - cache writes must not crash TTS
                    pass

        except APIStatusError:
            raise
        except APITimeoutError:
            raise
        except (websockets.InvalidStatus, websockets.InvalidHandshake) as e:
            status = getattr(getattr(e, "response", None), "status_code", 0) or 0
            raise APIStatusError(
                message=f"Hamsa TTS handshake failed: {e}",
                status_code=status,
                request_id=None,
                body=str(e),
            ) from e
        except (websockets.ConnectionClosedError, websockets.ConnectionClosedOK) as e:
            raise APIConnectionError() from e
        except Exception as e:
            raise APIConnectionError() from e
        finally:
            try:
                await ws.close()
            except Exception:
                pass
