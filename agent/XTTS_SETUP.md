# XTTS v2 Setup Guide

## Overview

The voice agent uses **XTTS v2** as the TTS (Text-to-Speech) engine via two components:

- **`xtts-api-server`** — Self-hosted XTTS v2 server with streaming support (~150-200ms latency)
- **`livekit-plugins-xtts`** — LiveKit plugin that connects the agent to the XTTS server

```
User <--> LiveKit Agent (agent.py) <--> XTTS Server (xtts-api-server)
              |                              |
         STT + LLM                     GPU (CUDA)
```

---

## 1. XTTS Server Setup

### Option A: pip install (recommended for development)

```bash
pip install xtts-api-server
```

Run with streaming mode:

```bash
python -m xtts_api_server \
  --host 0.0.0.0 \
  --port 8020 \
  --device cuda \
  --streaming-mode
```

For better quality with complex languages (Arabic):

```bash
python -m xtts_api_server \
  --host 0.0.0.0 \
  --port 8020 \
  --device cuda \
  --streaming-mode-improve
```

> `--streaming-mode-improve` uses a better tokenizer but needs ~2GB more VRAM.

### Option B: Docker (recommended for production)

```bash
docker run -d \
  --name xtts-server \
  --gpus=all \
  -p 8020:8000 \
  -v ./speakers:/app/speakers \
  daswer123/xtts-api-server
```

### Option C: Cloud GPU (no local GPU)

| Provider | GPU | Cost |
|----------|-----|------|
| RunPod | RTX 3090 / A40 | ~$0.2-0.6/hr |
| Vast.ai | RTX 3090 | ~$0.2/hr |
| Google Colab Pro | T4 / A100 | $10/month |

Run the server on the cloud, then set `XTTS_BASE_URL` to the public URL.

---

## 2. Voice Cloning (Optional)

XTTS v2 supports voice cloning from a short audio sample.

### Prepare a speaker WAV file:

- **Duration**: 6-15 seconds (clean speech, no background noise)
- **Format**: WAV, 16-bit, mono
- **Content**: Clear speech in the target language

### Place the file:

```bash
# Local setup
cp your_voice.wav ./speakers/

# Docker setup (mounted via -v ./speakers:/app/speakers)
cp your_voice.wav ./speakers/
```

### Set the env var:

```env
XTTS_SPEAKER=your_voice.wav
```

---

## 3. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `XTTS_BASE_URL` | `http://localhost:8020` | XTTS server URL |
| `XTTS_LANGUAGE` | `ar` | Language code (ar, en, es, fr, de, etc.) |
| `XTTS_SPEAKER` | `male.wav` | Speaker WAV file for voice cloning |
| `XTTS_API_KEY` | `1234567890` | API key (if server requires auth) |

Add these to your `.env` file:

```env
XTTS_BASE_URL=http://localhost:8020
XTTS_LANGUAGE=ar
XTTS_SPEAKER=male.wav
```

---

## 4. GPU Requirements

| GPU | VRAM | Streaming Mode | Latency |
|-----|------|----------------|---------|
| RTX 3060 | 12GB | Yes | ~300-500ms |
| RTX 3090 | 24GB | Yes | ~150-200ms |
| A40 / A100 | 48/80GB | Yes | ~100-150ms |
| T4 (Colab) | 16GB | Yes | ~400-600ms |
| CPU only | - | No | 2-5s (not recommended) |

> Minimum: 8GB VRAM. Recommended: 12GB+ VRAM.

---

## 5. Supported Languages

XTTS v2 supports 17 languages:

`ar` `cs` `de` `en` `es` `fr` `hi` `hu` `it` `ja` `ko` `nl` `pl` `pt` `ru` `tr` `zh`

---

## 6. API Endpoints

The `xtts-api-server` exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/docs` | GET | Swagger UI |
| `/tts_stream` | GET | Streaming TTS (used by the plugin) |
| `/tts_to_audio` | POST | Full audio generation |
| `/voices` | GET | List available voices |
| `/set_tts_settings` | POST | Update model settings at runtime |

### Test the server:

```bash
curl "http://localhost:8020/tts_stream?text=hello&speaker_wav=male.wav&language=en" \
  --output test.wav
```

---

## 7. Troubleshooting

### Server won't start

```
RuntimeError: CUDA out of memory
```

- Close other GPU processes: `nvidia-smi` to check
- Use `--streaming-mode` instead of `--streaming-mode-improve` (saves ~2GB)
- Reduce batch size or use a smaller GPU instance

### High latency (>1s)

- Make sure `--streaming-mode` is enabled
- Check GPU utilization: `nvidia-smi`
- If using remote server, check network latency: `ping <server-ip>`
- Reduce `chunk_length_schedule` in agent.py for faster first chunk

### Audio quality issues

- Use `--streaming-mode-improve` for Arabic (better tokenizer)
- Provide a longer/cleaner speaker WAV (10-15 seconds of clear speech)
- Try different speaker samples

### Connection refused

- Verify server is running: `curl http://localhost:8020/docs`
- Check firewall/port settings
- If Docker: ensure `-p 8020:8000` port mapping is correct

---

## 8. Architecture in agent.py

```python
from livekit.plugins import xtts

SHARED_TTS = xtts.TTS(
    voice=xtts.Voice(id="main", name="restaurant_voice"),
    language=os.getenv("XTTS_LANGUAGE", "ar"),
    base_url=os.getenv("XTTS_BASE_URL", "http://localhost:8020"),
    encoding="pcm_22050",
    chunk_length_schedule=[50, 80, 120, 200],
)
```

- `encoding="pcm_22050"` — Raw PCM, no encoding overhead
- `chunk_length_schedule=[50, 80, 120, 200]` — Small first chunk for faster time-to-first-byte
