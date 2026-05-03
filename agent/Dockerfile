FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Soundfile / livekit native dependencies need a few system libs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY agent/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY agent/ ./

RUN useradd --system --uid 1001 --home-dir /app appuser \
    && mkdir -p /app/.runtime \
    && chown -R appuser:appuser /app

USER appuser

# LiveKit RTC ports are negotiated dynamically; the only fixed local port is
# the health server. Expose just that and let the RTC layer use the host net.
EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${AGENT_HEALTH_PORT:-8082}/healthz" || exit 1

CMD ["python", "main.py", "start"]
