"""Direct x.ai TTS plugin — bypasses LiveKit inference for lower latency."""

from __future__ import annotations

import httpx
from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
BASE_URL = "https://api.x.ai/v1/tts"


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: str,
        voice: str = "leo",
        language: str = "ar",
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._api_key = api_key
        self._voice = voice
        self._language = language
        self._sample_rate = sample_rate
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=15),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._client.aclose()


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        payload = {
            "text": f"<fast>{self.input_text}</fast>",
            "voice_id": self._tts._voice,
            "language": self._tts._language,
            "output_format": {
                "codec": "pcm",
                "sample_rate": self._tts._sample_rate,
            },
        }

        try:
            async with self._tts._client.stream("POST", BASE_URL, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise APIStatusError(
                        message=f"x.ai TTS error {resp.status_code}: {body.decode(errors='replace')}",
                        status_code=resp.status_code,
                        request_id=resp.headers.get("x-request-id"),
                        body=body.decode(errors="replace"),
                    )

                output_emitter.initialize(
                    request_id=resp.headers.get("x-request-id", ""),
                    sample_rate=self._tts._sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm",
                )

                async for chunk in resp.aiter_bytes():
                    output_emitter.push(chunk)

            output_emitter.flush()

        except APIStatusError:
            raise
        except httpx.TimeoutException:
            raise APITimeoutError() from None
        except Exception as e:
            raise APIConnectionError() from e
