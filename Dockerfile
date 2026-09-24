# syntax=docker/dockerfile:1
# Production image for the Seleric Intelligence Swarm API.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# voice-api is small (JWT minting) and lets the API serve POST /v1/voice/token.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra voice-api

# Voice worker image. livekit-agents is installed here rather than declared in
# pyproject because it constrains openai<3 and would downgrade the shared lock
# for every service (see the note in pyproject.toml). Build with:
#   docker build --target voice-runtime -t seleric-voice .
FROM builder AS voice-builder
ARG LIVEKIT_AGENTS_VERSION=">=1.0"
# livekit-plugins-silero is a separate package from livekit-agents itself
# (plugins ship independently); worker.py's build_server() imports
# livekit.plugins.silero unconditionally for VAD, so without this the worker
# raises ModuleNotFoundError on startup and never joins a room.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python \
        "livekit-agents${LIVEKIT_AGENTS_VERSION}" \
        livekit-plugins-silero

FROM python:3.12-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin seleric \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY config ./config
COPY prompts ./prompts
COPY contracts ./contracts
COPY schemas ./schemas
COPY migrations ./migrations
COPY pyproject.toml README.md ./

RUN mkdir -p /app/.data/attachments \
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
    LANGSMITH_TRACING=false \
    ALLOW_WRITE_ACTIONS=false \
    MCP_CONFIG_PATH=config/mcp_servers.yaml \
    METRIC_REGISTRY_PATH=config/metric_registry.yaml \
    PROMPT_VERSIONS_PATH=config/prompt_versions.yaml \
    PROMPTS_DIR=prompts \
    COORDINATOR_POLICIES_PATH=config/coordinator_policies.yaml

USER seleric
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/readyz || exit 1

CMD ["seleric-api"]

# Voice worker runtime — same app image, plus the LiveKit agent stack.
FROM runtime AS voice-runtime
USER root
COPY --from=voice-builder /app/.venv /app/.venv
USER seleric
# Not an HTTP service; the API healthcheck does not apply.
HEALTHCHECK NONE
CMD ["seleric-voice"]
