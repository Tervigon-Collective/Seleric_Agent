FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    NUMEXPR_NUM_THREADS=2 \
    TORCH_NUM_THREADS=2 \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/models \
    HF_HUB_CACHE=/models/hub \
    CUDA_VISIBLE_DEVICES=""

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU-only PyTorch first, then Chronos + FastAPI deps.
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.6.0 \
    && pip install --no-cache-dir -r requirements.txt

COPY forecast.py app.py test_forecast.py benchmark.py ./

RUN useradd --create-home --uid 10001 chronos \
    && mkdir -p /models \
    && chown -R chronos:chronos /app /models

USER chronos

EXPOSE 8112

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8112/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8112", "--workers", "1"]
