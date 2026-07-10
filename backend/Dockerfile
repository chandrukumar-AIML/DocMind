# backend/Dockerfile
# ════════════════════════════════════════════════════════════════
# Stage 1: Builder — Install deps + pre-download models
# ════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PADDLEOCR_HOME=/opt/paddle_models \
    HF_HOME=/opt/hf_models \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 libgl1 \
    poppler-utils curl git wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy requirements first for better layer caching
COPY requirements.txt .

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip setuptools wheel

# Install PaddlePaddle with --no-deps to avoid dependency conflicts
RUN pip install paddlepaddle==2.6.2 \
    -f https://www.paddlepaddle.org.cn/whl/linux/mkl/avx/stable.html \
    --no-cache-dir --no-deps

# Install Python requirements
RUN pip install -r requirements.txt && \
    # litestar (transitive dep) installs 'multipart' which conflicts with
    # FastAPI's 'python-multipart'. Uninstall the bad one and keep the right one.
    pip uninstall -y multipart || true && \
    pip install "python-multipart>=0.0.9,<0.1.0" --force-reinstall

# Skip PaddleOCR model pre-download at build time — Render's build servers
# lack AVX2 support required by PaddlePaddle 2.6.2, causing a SIGSEGV (exit 139).
# Models are downloaded on first OCR request at runtime from /data (persistent disk).
RUN mkdir -p /opt/paddle_models && echo "PaddleOCR models will download at runtime"

# ✅ FIXED: Pre-download cross-encoder with retry using heredoc syntax
RUN mkdir -p /opt/hf_models && python3 <<'PYEOF'
import os, sys, time
os.environ['HF_HOME'] = '/opt/hf_models'
for attempt in range(3):
    try:
        from sentence_transformers import CrossEncoder
        CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
        print('Cross-encoder cached.', file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f'Attempt {attempt+1}/3 failed: {e}', file=sys.stderr)
        if attempt < 2:
            time.sleep(10)
        else:
            print('WARNING: Cross-encoder will download at runtime.', file=sys.stderr)
            sys.exit(0)
PYEOF

# ════════════════════════════════════════════════════════════════
# Stage 2: Runtime — Minimal, secure, non-root
# ════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PADDLEOCR_HOME=/opt/paddle_models \
    HF_HOME=/opt/hf_models \
    PORT=8000 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    OCR_USE_GPU=false \
    CHROMA_PERSIST_DIR=/data/chroma \
    FAISS_INDEX_PATH=/data/faiss/index.bin \
    PYTHONDONTWRITEBYTECODE=1

# Install runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libxrender-dev libgomp1 libgl1 \
    poppler-utils curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy venv and models from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/paddle_models /opt/paddle_models
COPY --from=builder /opt/hf_models /opt/hf_models

WORKDIR /app

# Copy application code and migrations
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY __init__.py ./__init__.py

# Create data directories with proper permissions
RUN mkdir -p /data/{chroma,faiss,uploads,sample_docs} /tmp/documind && \
    useradd -m -u 1000 documind && \
    chown -R documind:documind /app /data /tmp/documind /opt/paddle_models /opt/hf_models

# Switch to non-root user (security best practice)
USER documind

EXPOSE 8000

# Health check for orchestration
HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --start-period=60s \
    --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

COPY entrypoint.sh ./entrypoint.sh

# Production: run migrations then start uvicorn
CMD ["sh", "entrypoint.sh"]