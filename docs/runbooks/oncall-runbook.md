# DocMind AI — On-Call Runbook

**Audience:** On-call engineer  
**Last updated:** 2026-07-10

---

## Quick links

| Resource | URL |
|----------|-----|
| Grafana dashboard | `http://grafana:3001/d/documind-overview` |
| Prometheus | `http://prometheus:9090` |
| Backend health | `https://api.docmind.ai/health` |
| GitHub Actions | `https://github.com/chandrukumar-aiml/DocMind/actions` |

---

## Alert playbooks

### 🔴 HIGH: Backend pod crash loop

**Symptoms:** `CrashLoopBackOff`, 5xx spike on Grafana error-rate panel.

1. `kubectl logs -n documind deployment/documind-backend --previous`
2. Check for DB connection errors (`FATAL: too many connections`) — restart pgbouncer or raise `max_connections`.
3. Check for OOM kills: `kubectl describe pod <pod> | grep -A5 OOMKilled`
4. If OOM: reduce `--workers` in uvicorn args or raise memory limit in `values.yaml`.
5. Rollback if new deploy: `helm rollback documind -n documind`

---

### 🔴 HIGH: P95 latency > 5s

**Symptoms:** Grafana `p95_latency` panel red, user complaints about slow responses.

1. Check RAG latency panel — is it the retrieval or LLM call?
2. If retrieval: `kubectl exec -it <backend-pod> -- python -c "from app.rag.retriever import health_check; import asyncio; asyncio.run(health_check())"`
3. If LLM: check OpenAI status page. Fallback model chain kicks in automatically via `CostGovernor`.
4. If DB: check PG connections panel. Run `SELECT * FROM pg_stat_activity WHERE state='active'` on the DB.

---

### 🟡 MEDIUM: RAGAs quality gate failure in CI

**Symptoms:** CI `ragas` job fails, PR blocked.

1. Check `ragas_scores.json` artifact in the failed run.
2. If `faithfulness < 0.60`: chunking regression — check recent changes to `strategy_dispatcher.py`.
3. If `answer_relevancy < 0.55`: embedding model drift — verify `all-MiniLM-L6-v2` version pinned.
4. Temporary override (max 24h, with team approval): set `RAGAS_THRESHOLD_*` env vars lower in the CI job.

---

### 🟡 MEDIUM: Celery task queue backlog

**Symptoms:** Ingestion queue depth > 50 tasks, documents stuck processing.

1. `kubectl exec -it <celery-pod> -- celery -A app.worker inspect active`
2. Check Redis memory: `kubectl exec -it <redis-pod> -- redis-cli info memory | grep used_memory_human`
3. Scale Celery workers: `kubectl scale deployment/documind-celery --replicas=6 -n documind`
4. If tasks are stuck (not failing): `celery -A app.worker purge` (last resort — clears queue).

---

### 🟡 MEDIUM: Workspace budget exhausted (LLM 429)

**Symptoms:** Users see "workspace token budget exceeded" errors.

1. Check cost governor: `GET /api/v1/workspaces/{id}/budget`
2. Temporarily raise budget: `PUT /api/v1/workspaces/{id}/budget` with admin JWT.
3. Alert workspace admin via email.
4. Review model fallback chain in `CostGovernor` — ensure gpt-3.5-turbo fallback is active.

---

## Deployment procedure

```bash
# 1. Confirm CI green on main
# 2. Pull latest image tags
helm upgrade documind ./helm/documind \
  --namespace documind \
  --values helm/documind/values.yaml \
  --values helm/documind/values-prod.yaml \
  --set backend.image.tag=$(git rev-parse --short HEAD) \
  --set frontend.image.tag=$(git rev-parse --short HEAD) \
  --wait --timeout 5m

# 3. Smoke test
curl -sf https://api.docmind.ai/health | jq .

# 4. Rollback if needed
helm rollback documind -n documind
```

---

## Database backup & restore

```bash
# Manual backup
./scripts/db_backup.sh

# Restore from latest S3 backup
./scripts/db_restore.sh s3://your-bucket/documind/backup_20260710_020000.sql.gz

# Run migrations after restore
cd backend && alembic upgrade head
```

---

## Escalation

| Severity | Response time | Contact |
|----------|--------------|---------|
| P0 — total outage | 15 min | On-call + CTO |
| P1 — degraded (>10% errors) | 1 hour | On-call |
| P2 — single feature down | 4 hours | On-call |
| P3 — cosmetic / slow | Next business day | Slack #eng |
