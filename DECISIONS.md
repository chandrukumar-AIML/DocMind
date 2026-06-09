# Architecture Decision Records — DocuMind AI

Five key technology choices and the specific reasoning behind each.

---

## 1. Hybrid Retrieval: BM25 sparse + dense vectors fused with RRF

**Decision:** Use both BM25 (keyword) and `text-embedding-3-large` (semantic) retrieval, 
then fuse the result lists using Reciprocal Rank Fusion (RRF) before re-ranking with a 
local cross-encoder.

**Why:** Neither approach alone is sufficient. Dense-only retrieval misses exact keyword
matches (e.g., legal clause identifiers like "Section 12.4(b)", GSTIN numbers, drug codes).
BM25-only retrieval misses semantic paraphrases. RRF is parameter-free — it does not
require tuning alpha weights per domain, unlike linear interpolation. The cross-encoder
reranker then re-scores the top-20 candidates at inference time, recovering from any
ranking errors in the fused list. Measured +14% retrieval accuracy vs. dense-only on the
internal legal QA eval set.

**Trade-off:** Two indices to maintain (ChromaDB + FAISS + BM25 pickle). On a fresh Render
free-tier instance with no persistent disk, the BM25 index rebuilds from vectors on each
cold start (~8s at 500 documents).

---

## 2. FastAPI + asyncpg over Django/Flask

**Decision:** FastAPI with asyncpg (PostgreSQL async driver) for the REST + SSE API.

**Why:** DocuMind streams LLM tokens to the browser via Server-Sent Events (SSE). A
synchronous WSGI framework (Flask, Django) would hold a worker thread open for the entire
stream duration — severely limiting concurrency under Uvicorn. FastAPI's async-native model
lets a single worker handle hundreds of open SSE streams concurrently without threading
overhead. asyncpg is 3–5× faster than psycopg2 for async workloads because it speaks the
PostgreSQL wire protocol directly without the libpq C library.

**Trade-off:** The async ecosystem is less mature than Django's. We use SQLAlchemy Core
(not ORM relationships) to avoid the "greenlet" footgun of mixing async sessions with lazy
loading.

---

## 3. Celery + Redis for document ingestion (not FastAPI BackgroundTasks)

**Decision:** PDF/audio ingestion runs in Celery workers, not in FastAPI's
`BackgroundTasks`.

**Why:** `BackgroundTasks` ties task lifecycle to the HTTP worker process. If the server
restarts during a 2-minute OCR job, the task is silently lost with no retry. Celery
persists task state in Redis, supports priority queues (`high_priority`, `default`,
`bulk`), enforces `max_tasks_per_child=50` to prevent memory leaks from PaddlePaddle's
inference session, and exposes Flower for live monitoring. This is the same architecture
used by production ML pipelines at scale.

**Trade-off:** Adds Redis as a required dependency. On the free tier (no Redis), ingestion
falls back to synchronous processing inside the request handler with a 300s timeout.

---

## 4. LangGraph for the Agent reasoning loop (not a custom state machine)

**Decision:** Use LangGraph's compiled graph for the multi-step agent, rather than writing
a custom while-loop reasoning engine.

**Why:** The agent needs conditional branching (grade retrieved docs → decide whether to
re-query or web-search → decide whether to generate or escalate). LangGraph models this
as a directed graph with typed state, making the branching logic explicit, testable per
node, and resumable with persistent checkpointing. The alternative (a custom while-loop)
is untestable at the node level and accumulates hidden state. LangGraph also integrates
directly with LangSmith for tracing each node's inputs/outputs — essential for debugging
hallucination in production.

**Trade-off:** LangGraph's API changed significantly between 0.1 and 0.3. We pin to
`langgraph>=0.2,<0.4` to avoid breaking changes. The learning curve is steeper than a
simple chain.

---

## 5. PostgreSQL for auth/workspace/audit (not a NoSQL store)

**Decision:** Use PostgreSQL with SQLAlchemy for users, workspaces, annotations,
e-signatures, audit trail — not MongoDB or DynamoDB.

**Why:** The data is inherently relational. A user belongs to one or more workspaces
with a specific role. An annotation links to a specific chunk of a specific document
version. An audit log entry references a user, a workspace, and an action with a
timestamp. These cross-entity constraints are enforced at the DB level with foreign keys
and CHECK constraints, not just at the application level. PostgreSQL's JSONB column type
handles unstructured metadata (embedding configs, extraction results) without sacrificing
ACID guarantees. Supabase provides PostgreSQL-as-a-service with a free tier, eliminating
ops overhead for early deployments.

**Trade-off:** PostgreSQL cannot trivially scale horizontally for writes. At >10k
concurrent users, we would introduce read replicas (asyncpg supports this via
`asyncpg.Pool` with read-replica routing) and evaluate moving high-churn tables
(usage_logs, query_logs) to a time-series store (TimescaleDB).
