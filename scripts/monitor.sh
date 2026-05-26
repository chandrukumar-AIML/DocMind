#!/usr/bin/env bash
# scripts/monitor.sh
# Quick production health check

BACKEND_URL="${BACKEND_URL:-https://documind-backend-production.up.railway.app}"
FRONTEND_URL="${FRONTEND_URL:-https://documind-frontend-production.up.railway.app}"

echo "=== DocuMind AI — Production Health Check ==="
echo "Time: $(date -u)"
echo ""

# FIXED: use jq if available, fall back to python3
parse_json() {
    local json="$1"
    local key="$2"
    local default="$3"
    if command -v jq &>/dev/null; then
        echo "$json" | jq -r "$key // \"$default\"" 2>/dev/null || echo "$default"
    else
        echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print($key)" 2>/dev/null || echo "$default"
    fi
}

echo "Backend:"
HEALTH=$(curl -sf --max-time 10 "$BACKEND_URL/health" 2>/dev/null)
if [ $? -eq 0 ] && [ -n "$HEALTH" ]; then
    if command -v jq &>/dev/null; then
        STATUS=$(echo "$HEALTH" | jq -r '.status // "unknown"')
        CHUNKS=$(echo "$HEALTH" | jq -r '.vector_store.chroma_chunks // 0')
        DOCS=$(echo "$HEALTH" | jq -r '.vector_store.documents // 0')
    else
        STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))")
        CHUNKS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('vector_store',{}).get('chroma_chunks',0))")
        DOCS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('vector_store',{}).get('documents',0))")
    fi
    echo "  Status:  $STATUS"
    echo "  Chunks:  $CHUNKS"
    echo "  Docs:    $DOCS"
else
    echo "  UNREACHABLE"
    echo "  Diagnosis:"
    curl -sv "$BACKEND_URL/health" 2>&1 | tail -10 | sed 's/^/    /'
fi

echo ""
echo "Frontend:"
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --max-time 10 "$FRONTEND_URL/" 2>/dev/null || echo "ERR")
echo "  HTTP: $HTTP_CODE"

echo ""
echo "API Latency:"
START=$(date +%s%3N)
curl -sf --max-time 10 "$BACKEND_URL/health" >/dev/null 2>&1
END=$(date +%s%3N)
echo "  /health: $((END - START))ms"

# FIXED: query test only in --full mode (avoids real LLM costs)
if [[ "${1:-}" == "--full" ]]; then
    echo ""
    echo "Query Test (full mode — uses real LLM):"
    RESULT=$(curl -sf --max-time 30 -X POST "$BACKEND_URL/api/v1/query" \
        -H "Content-Type: application/json" \
        -d '{"question":"hello","stream":false}' 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$RESULT" ]; then
        LATENCY=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('latency_seconds','?'))" 2>/dev/null || echo "?")
        echo "  Query latency: ${LATENCY}s"
    else
        echo "  Query failed (no documents indexed or backend error)"
    fi
fi

echo ""
echo "=== Check complete ==="