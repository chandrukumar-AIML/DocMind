#!/usr/bin/env bash
# scripts/railway_setup.sh
# Run once before first deployment to configure Railway project

set -e

echo "=== DocuMind AI — Railway Setup ==="

if ! command -v railway &>/dev/null; then
    echo "Installing Railway CLI..."
    npm install -g @railway/cli
fi

echo "Logging in to Railway..."
railway login

echo "Linking Railway project..."
railway link

# FIXED: idempotent service creation
create_service_if_not_exists() {
    local name=$1
    if railway service list 2>/dev/null | grep -q "$name"; then
        echo "Service '$name' already exists — skipping"
    else
        railway add --name "$name"
        echo "Created service: $name"
    fi
}

echo "Creating services..."
create_service_if_not_exists "backend"
create_service_if_not_exists "frontend"
create_service_if_not_exists "chromadb"
create_service_if_not_exists "mlflow"

echo "Setting non-secret backend environment variables..."
railway variables set \
    --service backend \
    OCR_USE_GPU=false \
    CHROMA_PERSIST_DIR=/data/chroma \
    FAISS_INDEX_PATH=/data/faiss/index.bin \
    API_RELOAD=false \
    RAG_CHUNK_SIZE_CHILD=256 \
    RAG_CHUNK_SIZE_PARENT=1024 \
    RAG_CHUNK_OVERLAP_CHILD=30 \
    RAG_CHUNK_OVERLAP_PARENT=100 \
    RAG_TOP_K_RETRIEVAL=20 \
    RAG_TOP_K_RERANK=3 \
    MAX_UPLOAD_SIZE_MB=50 \
    LANGCHAIN_TRACING_V2=true \
    LANGCHAIN_PROJECT=documind-ai-prod \
    MLFLOW_EXPERIMENT_NAME=documind-ai-prod

echo ""
echo "=== Manual steps required ==="
echo ""
echo "⚠️  SECURITY: Never set API keys via CLI (they appear in shell history)"
echo "   Use Railway UI → Service → Variables → Add variable"
echo "   OR use Railway's secure prompt (omit value to be prompted):"
echo "   railway variables set --service backend OPENAI_API_KEY"
echo ""
echo "1. Set secrets in Railway UI → backend service → Variables:"
echo "   OPENAI_API_KEY=sk-..."
echo "   LANGCHAIN_API_KEY=ls__..."
echo ""
echo "2. Set private network references in Railway UI (use 'Add Reference' button):"
echo "   CHROMA_HOST → chromadb service → RAILWAY_PRIVATE_DOMAIN"
echo "   MLFLOW_TRACKING_URI → http://<mlflow private domain>:5000"
echo ""
echo "3. Add volumes in Railway UI → each service → Settings → Volume:"
echo "   backend:  mount /data  (2GB minimum)"
echo "   chromadb: mount /chroma (5GB minimum)"
echo "   mlflow:   mount /mlflow (1GB minimum)"
echo ""
echo "4. Deploy ChromaDB: Docker Image → chromadb/chroma:0.5.0"
echo "   Env: IS_PERSISTENT=TRUE, PERSIST_DIRECTORY=/chroma/chroma"
echo ""
echo "5. Deploy MLflow: Docker Image → ghcr.io/mlflow/mlflow:v2.13.0"
echo "   Start command: mlflow server --backend-store-uri sqlite:////mlflow/mlflow.db"
echo "                  --default-artifact-root /mlflow/artifacts"
echo "                  --host 0.0.0.0 --port \$PORT"
echo ""
echo "6. After backend deploys, get its public URL:"
echo "   railway open --service backend"
echo "   Set frontend VITE_API_URL to that URL in Railway UI"
echo ""
echo "=== Setup complete ==="