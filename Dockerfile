# syntax=docker/dockerfile:1
# Production image for the Seleric Intelligence Swarm API.
# The app is installed into the same image that holds the dependency venv.
# Copying that venv onto python:3.12-slim fails the patch check (3.12.12 vs
# 3.12.16) and uv recreates it, so every source edit re-exported ~400MB.

FROM node:22-bookworm-slim AS ui-builder
WORKDIR /ui
COPY office-ui/package.json office-ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY office-ui/ ./
RUN npx tsc -p tsconfig.build.json && npx vite build --base=/ui/

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin seleric \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies only. A source edit must not rebuild this layer.
# voice-api is small (JWT minting) and lets the API serve POST /v1/voice/token.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project --extra voice-api

COPY config ./config
COPY contracts ./contracts
COPY schemas ./schemas
COPY migrations ./migrations
COPY --from=ui-builder /ui/dist ./office-ui/dist

COPY src ./src
COPY pyproject.toml uv.lock README.md ./
# Local wheel only. Must stay in this image: a later stage with a different
# CPython patch makes uv delete .venv and reinstall every dependency.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra voice-api

RUN mkdir -p /app/.data/attachments \
        /app/.data/domain_health_snapshots \
        /app/.data/reports \
    && chown -R seleric:seleric /app/.data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LLM_PROVIDER=azure_openai_compatible \
    PERSISTENCE_BACKEND=postgres \
    CHECKPOINT_BACKEND=postgres \
    CANCELLATION_BACKEND=redis \
    EVENT_NOTIFIER_BACKEND=redis \
    BLOB_BACKEND=minio \
    MALWARE_SCANNER_BACKEND=clamav \
    LANGFUSE_TRACING=true \
    LANGSMITH_TRACING=false \
    MCP_CONFIG_PATH=config/mcp_servers.yaml

USER seleric
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/readyz || exit 1

CMD ["seleric-api"]

# Voice worker runtime — same app image, plus the LiveKit agent stack.
# livekit-agents is installed here rather than declared in pyproject because it
# constrains openai<3 and would downgrade the shared lock for every service
# (see the note in pyproject.toml). Build with:
#   docker build --target voice-runtime -t seleric-voice .
FROM runtime AS voice-runtime
USER root
ARG LIVEKIT_AGENTS_VERSION=">=1.0"
# livekit-plugins-silero is a separate package from livekit-agents itself
# (plugins ship independently); worker.py's build_server() imports
# livekit.plugins.silero unconditionally for VAD, so without this the worker
# raises ModuleNotFoundError on startup and never joins a room.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy \
    uv pip install --python /app/.venv/bin/python \
        "livekit-agents${LIVEKIT_AGENTS_VERSION}" \
        livekit-plugins-silero \
        livekit-plugins-openai \
        livekit-plugins-azure
USER seleric
# Not an HTTP service; the API healthcheck does not apply.
HEALTHCHECK NONE
CMD ["seleric-voice"]
