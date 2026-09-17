# syntax=docker/dockerfile:1
# Production image for the Seleric Intelligence Swarm API.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

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

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LLM_PROVIDER=fake \
    PERSISTENCE_BACKEND=memory \
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
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["seleric-api"]
