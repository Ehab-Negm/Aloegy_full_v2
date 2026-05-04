# Restaurant voice agent — production image
#
# Build (from repo root):
#   docker build -t restaurant-agent -f agent/Dockerfile .
#
# Run:
#   docker run --rm \
#     --env-file agent/.env \
#     -v $PWD/agent/service-account.json:/app/credentials.json:ro \
#     -e GOOGLE_APPLICATION_CREDENTIALS=/app/credentials.json \
#     -p 8082:8082 \
#     restaurant-agent
#
# Notes:
#   * The service-account JSON is mounted read-only at runtime — never
#     bake it into the image.
#   * .env is loaded by the agent itself via python-dotenv; either pass
#     --env-file or set the variables individually.
#   * Health endpoint listens on AGENT_HEALTH_PORT (default 8082).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libs for: livekit-agents native bits, audio i/o (libsndfile),
# ffmpeg (used by some plugin paths), curl (HEALTHCHECK probe).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libsndfile1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps in a separate layer so source changes don't bust the cache.
COPY agent/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Copy the agent source. .runtime/ is created at start so the worker can
# write the disk-backed write queue and per-pid health snapshots.
COPY agent/ ./

RUN useradd --system --uid 1001 --home-dir /app appuser \
    && mkdir -p /app/.runtime \
    && chown -R appuser:appuser /app

USER appuser

# LiveKit RTC ports are negotiated dynamically over the LiveKit signal
# server — only the local health endpoint is fixed. Expose just that
# and let the RTC layer ride the host network.
EXPOSE 8082

# Health probe hits the worker's aggregated /healthz. start-period=30s
# gives the worker time to fetch initial config and warm up plugins
# before the first probe; longer than that means a real problem.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${AGENT_HEALTH_PORT:-8082}/healthz" || exit 1

# main.py is the canonical entrypoint: starts the LiveKit AgentServer,
# runs the parent-side health server, wires graceful shutdown.
CMD ["python", "main.py", "start"]
