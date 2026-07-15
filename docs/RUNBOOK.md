# DocMind AI — Operations & Disaster Recovery Runbook

> Last updated: 2026-07-15  
> Audience: On-call engineers, SREs, DevOps

---

## Table of Contents

1. [Service Overview](#1-service-overview)
2. [On-Call Contacts](#2-on-call-contacts)
3. [Alert Playbooks](#3-alert-playbooks)
   - 3.1 ServiceDown
   - 3.2 HighErrorRate
   - 3.3 LLMCircuitBreakerOpen
   - 3.4 RateLimiterFailOpen
   - 3.5 PostgresConnectionPoolExhausted
   - 3.6 RedisDown
   - 3.7 CeleryNoWorkers
   - 3.8 DiskSpaceLow
4. [Backup & Restore Procedures](#4-backup--restore-procedures)
5. [Disaster Recovery (DR) Scenarios](#5-disaster-recovery-dr-scenarios)
6. [Deployment Rollback](#6-deployment-rollback)
7. [Health Checks & Smoke Tests](#7-health-checks--smoke-tests)
8. [Scaling Procedures](#8-scaling-procedures)

---

## 1. Service Overview

| Component | Technology | Port | Notes |
|-----------|-----------|------|-------|
| API backend | FastAPI / Python 3.11 | 8000 | Stateless; horizontally scalable |
| Database | PostgreSQL 16 | 5432 | Primary source of truth for user/workspace data |
| Cache / queues | Redis 7 | 6379 | Rate limiting, token revocation, Celery broker |
| Vector store | ChromaDB | 8001 | Document embeddings; lives in persistent volume |
| Worker | Celery | — | Document ingestion & async tasks |
| Object storage | S3-compatible | — | Raw uploaded files; separate from DB |
| Monitoring | Prometheus + Grafana | 9090 / 3000 | Alertmanager at 9093 |

**RTO target**: 30 minutes (core query path)  
**RPO target**: 1 hour (PostgreSQL WAL archiving)

---

## 2. On-Call Contacts

| Role | Contact |
|------|---------|
| Primary on-call | PagerDuty rotation — `#docmind-alerts` |
| Backend lead | `@backend-lead` on Slack |
| AI/ML lead | `@ai-lead` on Slack |
| Infra lead | `@infra-lead` on Slack |
| Escalation | CTO |

---

## 3. Alert Playbooks

### 3.1 ServiceDown

**Symptom**: Backend unreachable, health endpoint returns non-200 or times out.

```bash
# 1. Check pod/container status
kubectl get pods -n docmind
kubectl logs -n docmind deployment/backend --tail=50

# 2. Check recent deployments
kubectl rollout history deployment/backend -n docmind

# 3. If OOMKilled — check memory limits
kubectl describe pod <pod-name> -n docmind | grep -A5 "OOMKilled"

# 4. Restart if stuck
kubectl rollout restart deployment/backend -n docmind
```

**Escalate if**: Pod restarts > 3 times in 10 minutes, or restart does not help.

---

### 3.2 HighErrorRate

**Symptom**: >5% of requests returning 5xx over 2 minutes.

```bash
# 1. Check error logs for patterns
kubectl logs -n docmind deployment/backend --tail=200 | grep "ERROR\|CRITICAL"

# 2. Check DB connectivity
kubectl exec -n docmind deployment/backend -- python -c "
from app.database.engine import get_async_engine
import asyncio
asyncio.run(get_async_engine().connect())
print('DB OK')
"

# 3. Check OpenAI/LLM status
curl https://status.openai.com/api/v2/status.json | jq .status.indicator

# 4. If DB related — see §3.5
# 5. If LLM related — see §3.3
```

---

### 3.3 LLMCircuitBreakerOpen

**Symptom**: `rag_llm_circuit_breaker_open == 1`. RAG queries fail or return degraded answers.

**Cause**: 5+ consecutive LLM API failures. The circuit breaker auto-probes every 60 s.

```bash
# 1. Check OpenAI status
curl https://status.openai.com/api/v2/status.json

# 2. Check app logs for LLM errors
kubectl logs -n docmind deployment/backend --tail=100 | grep "CircuitBreaker\|openai\|LLM"

# 3. Manual reset (if OpenAI is healthy but circuit is stuck)
kubectl exec -n docmind deployment/backend -- python -c "
import asyncio
from app.rag.chain import _llm_breaker
asyncio.run(_llm_breaker.reset())
print('Circuit reset')
"

# 4. If quota issue — check OpenAI billing dashboard
# 5. If auth issue — verify OPENAI_API_KEY secret is current
kubectl get secret docmind-secrets -n docmind -o yaml | grep OPENAI
```

---

### 3.4 RateLimiterFailOpen

**Symptom**: `rate_limiter_fail_open_total` counter increasing. Redis may be unreachable.

In **production** the rate limiter fails **closed** (denies requests when Redis is down).  
This alert fires whenever the fallback path is exercised.

→ **Investigate Redis first** (see §3.6).

---

### 3.5 PostgresConnectionPoolExhausted

**Symptom**: `pg_stat_database_numbackends / pg_settings_max_connections > 0.85`.

```bash
# 1. Check current connections
psql $DATABASE_URL -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# 2. Identify long-running queries
psql $DATABASE_URL -c "
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC LIMIT 20;
"

# 3. Kill idle-in-transaction connections older than 5 min
psql $DATABASE_URL -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle in transaction'
  AND query_start < now() - interval '5 minutes';
"

# 4. If persistent — scale up connection limit or add PgBouncer
```

---

### 3.6 RedisDown

**Symptom**: Redis scrape target unreachable. Rate limiting, caching, and token revocation impaired.

```bash
# 1. Check Redis pod
kubectl get pods -n docmind -l app=redis
kubectl logs -n docmind deployment/redis --tail=50

# 2. Attempt Redis CLI ping
kubectl exec -n docmind deployment/redis -- redis-cli ping

# 3. If AOF/RDB corruption
kubectl exec -n docmind deployment/redis -- redis-cli config set appendonly no
kubectl rollout restart deployment/redis -n docmind

# 4. If data loss is acceptable — flush and restart
# WARNING: clears rate-limit state and token revocation blacklist
kubectl exec -n docmind deployment/redis -- redis-cli FLUSHALL
```

---

### 3.7 CeleryNoWorkers

**Symptom**: `celery_workers == 0`. Document ingestion completely stopped.

```bash
kubectl get pods -n docmind -l app=celery-worker
kubectl logs -n docmind deployment/celery-worker --tail=50
kubectl rollout restart deployment/celery-worker -n docmind

# Check queue depth
kubectl exec -n docmind deployment/celery-worker -- celery -A app.worker.celery_app inspect active
```

---

### 3.8 DiskSpaceLow

**Symptom**: Available disk < 15% on any mount.

```bash
# Identify large consumers
kubectl exec -n docmind <pod> -- du -sh /data/* 2>/dev/null | sort -rh | head -20

# For ChromaDB volume — trigger compaction
kubectl exec -n docmind deployment/backend -- python -c "
from app.vectorstore.store_manager import VectorStoreManager
VectorStoreManager().chroma.compact()
"

# For log volumes — rotate
kubectl exec -n docmind <pod> -- find /var/log -name '*.log' -mtime +7 -delete
```

---

## 4. Backup & Restore Procedures

### PostgreSQL

**Backup** (runs nightly via CronJob):
```bash
pg_dump $DATABASE_URL --format=custom --compress=9 -f backup_$(date +%Y%m%d).dump
aws s3 cp backup_$(date +%Y%m%d).dump s3://docmind-backups/postgres/
```

**Restore**:
```bash
aws s3 cp s3://docmind-backups/postgres/backup_YYYYMMDD.dump .
pg_restore --clean --if-exists -d $DATABASE_URL backup_YYYYMMDD.dump
```

### ChromaDB / Vector Store

**Backup** (snapshot the persistent volume):
```bash
kubectl exec -n docmind deployment/backend -- tar -czf /tmp/chroma_backup.tar.gz /data/chroma
kubectl cp docmind/<pod>:/tmp/chroma_backup.tar.gz ./chroma_backup_$(date +%Y%m%d).tar.gz
aws s3 cp ./chroma_backup_$(date +%Y%m%d).tar.gz s3://docmind-backups/chroma/
```

**Restore**:
```bash
aws s3 cp s3://docmind-backups/chroma/chroma_backup_YYYYMMDD.tar.gz .
kubectl cp chroma_backup_YYYYMMDD.tar.gz docmind/<pod>:/tmp/
kubectl exec -n docmind <pod> -- tar -xzf /tmp/chroma_backup_YYYYMMDD.tar.gz -C /
```

### Redis

Redis data is **ephemeral cache** — no backup needed. On restart, rate-limit counters reset (acceptable). Token revocation blacklist clears (acceptable — tokens expire naturally within `jwt_access_token_expire_minutes`).

---

## 5. Disaster Recovery (DR) Scenarios

### Scenario A: Primary Database Failure

1. **Identify**: `ServiceDown` + `PostgresConnectionPoolExhausted` alerts fire simultaneously.
2. **Failover**: Promote read replica (if Patroni/Repmgr configured):
   ```bash
   patronictl -c /etc/patroni/config.yml failover docmind-cluster
   ```
3. **Update connection string**: Update `DATABASE_URL` secret in Kubernetes.
   ```bash
   kubectl patch secret docmind-secrets -n docmind --patch '{"data":{"DATABASE_URL":"<new-base64>"}}'
   kubectl rollout restart deployment/backend -n docmind
   ```
4. **Verify**: Run smoke tests (§7).
5. **RTO**: ~15 minutes with automated failover; ~30 minutes manual.

### Scenario B: Full Region Outage

1. **Redirect DNS** to standby region (Route 53 health check failover).
2. **Restore DB** from latest S3 snapshot (§4).
3. **Restore ChromaDB** from S3 snapshot.
4. **Re-seed environment secrets** (copy from secrets manager).
5. **Run smoke tests** (§7).
6. **RTO**: ~60 minutes. **RPO**: up to 1 hour (WAL archiving interval).

### Scenario C: Compromised JWT Secret / Private Key

1. **Rotate key immediately**:
   ```bash
   # Generate new RSA keypair
   openssl genrsa -out private.pem 4096
   openssl rsa -in private.pem -pubout -out public.pem
   
   # Update secret
   kubectl create secret generic docmind-jwt-keys \
     --from-file=private.pem \
     --from-file=public.pem \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
2. **Force re-login**: All existing tokens are immediately invalid after key rotation.
3. **Audit logs**: Check for unauthorized access in the past 24 hours.
4. **Notify affected users** if unauthorized access is confirmed.

---

## 6. Deployment Rollback

```bash
# View rollout history
kubectl rollout history deployment/backend -n docmind

# Rollback to previous version
kubectl rollout undo deployment/backend -n docmind

# Rollback to specific revision
kubectl rollout undo deployment/backend -n docmind --to-revision=3

# Verify rollback
kubectl rollout status deployment/backend -n docmind
```

For database migrations (Alembic):
```bash
# Check current revision
alembic current

# Rollback one migration
alembic downgrade -1

# Rollback to specific revision
alembic downgrade <revision_id>
```

---

## 7. Health Checks & Smoke Tests

```bash
BASE_URL=https://api.docmind.ai

# Health endpoint
curl -sf "$BASE_URL/health" | jq .status

# Auth smoke test
TOKEN=$(curl -sf -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@docmind.ai","password":"SmokeTest@2025!"}' | jq -r .access_token)

# RAG query smoke test (non-destructive)
curl -sf -X POST "$BASE_URL/api/v1/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"test","workspace_id":"default","top_k_retrieve":1,"top_k_rerank":1}' \
  | jq .answer
```

Expected: All return HTTP 200 within 10 seconds.

---

## 8. Scaling Procedures

### Horizontal Scaling (API)

```bash
kubectl scale deployment/backend -n docmind --replicas=5
```

### Horizontal Scaling (Celery Workers)

```bash
kubectl scale deployment/celery-worker -n docmind --replicas=3
```

### Vertical Scaling (PostgreSQL)

Update `resources.limits.memory` in the Helm values and `pg_settings.max_connections` accordingly. Restart requires a brief downtime window — schedule with the team first.

### Vector Store Scaling

ChromaDB in single-node mode does not scale horizontally. For high read load:
- Enable read-replica mode (ChromaDB Enterprise) or
- Shard by workspace ID across multiple ChromaDB instances
