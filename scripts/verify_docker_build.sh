#!/usr/bin/env bash
# scripts/verify_docker_build.sh
# Run after docker compose build to verify images are correct

set -e

echo "=== Docker Build Verification ==="

echo "Image sizes:"
docker images | grep -E "documind|REPOSITORY" || echo "No documind images found"

BACKEND_SIZE=$(docker image inspect documind-backend:latest --format='{{.Size}}' 2>/dev/null || echo "0")
FRONTEND_SIZE=$(docker image inspect documind-frontend:latest --format='{{.Size}}' 2>/dev/null || echo "0")

if [ "$BACKEND_SIZE" -gt 0 ]; then
    BACKEND_GB=$(echo "scale=2; $BACKEND_SIZE / 1073741824" | bc)
    echo "Backend: ${BACKEND_GB}GB (target: <4GB)"
fi

if [ "$FRONTEND_SIZE" -gt 0 ]; then
    FRONTEND_MB=$(echo "scale=2; $FRONTEND_SIZE / 1048576" | bc)
    echo "Frontend: ${FRONTEND_MB}MB (target: <50MB)"
fi

echo ""
echo "Security checks:"
BACKEND_USER=$(docker run --rm --entrypoint whoami documind-backend:latest 2>/dev/null || echo "unknown")
echo "Backend user: $BACKEND_USER (expected: documind)"
[ "$BACKEND_USER" = "documind" ] && echo "✅ Non-root user confirmed" || echo "❌ NOT running as documind"

echo ""
echo "Starting services for smoke test..."
docker compose up -d 2>/dev/null || docker compose -f docker-compose.yml up -d
echo "Waiting 30s for startup..."
sleep 30

HEALTH=$(curl -sf http://localhost:8000/health 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])" 2>/dev/null || echo "unreachable")
echo "Backend health: $HEALTH"
[ "$HEALTH" = "ok" ] || [ "$HEALTH" = "degraded" ] && echo "✅ Backend responding" || echo "❌ Backend not healthy"

CHROMA=$(curl -sf http://localhost:8001/api/v1/heartbeat 2>/dev/null && echo "ok" || echo "unreachable")
echo "ChromaDB:       $CHROMA"

FRONTEND=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:3000/ 2>/dev/null || echo "0")
echo "Frontend HTTP:  $FRONTEND"
[ "$FRONTEND" = "200" ] && echo "✅ Frontend serving" || echo "❌ Frontend not responding"

echo ""
echo "=== Verification complete ==="