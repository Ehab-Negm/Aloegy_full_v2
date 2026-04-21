"""Hamsa AI realtime TTS plugin — WebSocket streaming for low-latency Arabic synthesis."""

from __future__ import annotations

import asyncio
import json
import struct
from urllib.parse import quote

import websockets
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

WS_URL = "wss://api.tryhamsa.com/v1/realtime/ws"
NUM_CHANNELS = 1
MAX_TEXT_LEN = 2000
DEFAULT_PCM_SAMPLE_RATE = 22050  # used only if WAV header is missing
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

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        return None


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

        url = f"{WS_URL}?api_key={quote(self._tts._api_key)}"
        timeout = self._conn_options.timeout if self._conn_options else 30.0

        try:
            async with websockets.connect(
                url,
                additional_headers={"X-Api-Key": self._tts._api_key},
                open_timeout=timeout,
                close_timeout=5,
                max_size=None,
            ) as ws:
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
                                # No WAV header — treat as raw PCM with default sample rate.
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

            output_emitter.flush()

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
