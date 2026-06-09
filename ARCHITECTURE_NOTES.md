# DocuMind AI — Architecture Notes

Quick reference for system design interviews and code walkthroughs.
See [DECISIONS.md](DECISIONS.md) for in-depth rationale on each major technology choice.

---

## 1. System Overview

DocuMind AI is a full-stack AI document intelligence platform: a React 19 SPA talks to a
FastAPI backend over REST + Server-Sent Events (SSE). The backend orchestrates a hybrid
RAG pipeline (BM25 sparse + dense vector retrieval, RRF fusion, cross-encoder reranking),
a LangGraph agent loop, and a Neo4j knowledge graph — all backed by PostgreSQL (users,
workspaces, audit), ChromaDB + FAISS (vectors), and Redis (cache, Celery queue). Document
ingestion (OCR, audio transcription, chunking, embedding) runs in Celery workers so the
HTTP layer never blocks on CPU-intensive work.

---

## 2. High-Level Component Diagram

```
Browser (React 19 SPA)
    │
    │  REST / SSE (streaming tokens)
    ▼
FastAPI + Uvicorn (async, SSE)
    │
    ├─► Auth layer (JWT / API keys / RBAC / rate-limit via Redis)
    │
    ├─► Query router ──────────────────────────────────────────────┐
    │       ├── RAG mode   → Hybrid retriever → LLM (GPT-4o/Ollama)│
    │       ├── Agent mode → LangGraph loop → tools → LLM          │
    │       └── Graph mode → Neo4j Cypher → LLM                    │
    │                                                               │
    ├─► Ingest router → Celery task queue (Redis broker)            │
    │       └── workers: OCR (PaddleOCR), Whisper, chunking,        │
    │                     embedding → ChromaDB + FAISS + BM25        │
    │                                                               │
    ├─► Domain routes (legal, medical, compliance, comparison)      │
    ├─► Annotation WebSocket (real-time collaboration)              │
    └─► /health (DB + vector_store + rag_chain critical checks)     │
                                                                    │
PostgreSQL 16  ChromaDB  FAISS  BM25  Redis  Neo4j  MLflow  LangSmith
```

---

## 3. Request Lifecycle — RAG Query (end-to-end)

1. **Browser** sends `POST /api/v1/query/stream` with `{question, workspace_id, mode:"rag"}`.
2. **Auth middleware** verifies JWT, resolves workspace membership.
3. **Rate limiter** checks Redis (sliding-window Lua script); fail-open on Redis miss.
4. **Response cache** checks Redis for an identical (question × workspace) hash. Hit → stream
   cached answer immediately. Miss → proceed.
5. **Hybrid retriever**:
   - BM25 (`rank-bm25`) returns top-20 candidates by keyword score.
   - ChromaDB + FAISS dense search returns top-20 by cosine similarity
     (`text-embedding-3-large`, 3072-dim).
   - **RRF fusion** merges both ranked lists into a single top-20 (no alpha tuning needed).
   - **Cross-encoder** (`cross-encoder/ms-marco-MiniLM-L-6-v2`) re-scores the top-20
     and returns the top-6 chunks.
6. **LLM call** — `gpt-4o` with a system prompt that includes the 6 chunks + citation
   markers. Streamed via OpenAI SDK.
7. **FastAPI SSE** fans out tokens to the browser as `data:` events.
8. On stream completion, the full answer + citations are written to Redis cache (TTL 1 hour)
   and to the `query_logs` PostgreSQL table.
9. Browser `useStreamQuery` hook assembles tokens, renders citations via `CitationCardV2`.

**Measured latency (p50, warm cache miss):** ~2.4 s to first token, ~0.4 s for a cache hit.

---

## 4. Document Ingestion Pipeline

```
Upload (multipart)
    │
    ▼
Celery task dispatched to priority queue
    │
    ├── PDF/DOCX/XLSX → pypdfium2 / docx2txt / openpyxl → text pages
    ├── Image PDF / scans → PaddleOCR → text pages
    ├── Audio (MP3/WAV/MP4) → OpenAI Whisper → transcript
    └── Web URL → httpx scraper → markdown
    │
    ▼
Chunking (RecursiveCharacterTextSplitter, 512 tokens, 64 overlap)
    │
    ▼
Embedding batch (text-embedding-3-large, 3072-dim, batches of 100)
    │
    ├── ChromaDB (persistent cosine index)
    ├── FAISS (IVFFlat, in-memory, persisted to disk)
    └── BM25 index (rank-bm25, pickle-serialised per workspace)
    │
    ▼
PostgreSQL: document record + chunk metadata + status = "ready"
```

`max_tasks_per_child=50` on Celery workers prevents PaddleOCR inference-session memory
leaks from accumulating across restarts.

---

## 5. Scale Bottleneck

**The embedding step.** `text-embedding-3-large` at 3072 dimensions is called once per
chunk during ingest. For a 200-page PDF (~800 chunks), this is 800 sequential API calls
(batched 100 at a time = 8 round trips). At $0.13/million tokens that's cheap, but the
wall-clock time is ~12–18 s on OpenAI's API.

**At higher scale, the fix is:**
- Increase Celery concurrency and add more workers (horizontal scale, no code change).
- Switch to a locally-hosted embedding model (e5-large-v2 on A10 GPU: 500 chunks/s vs.
  ~50 chunks/s via OpenAI API).
- For write-heavy workloads, move `query_logs` + `usage_logs` to TimescaleDB (time-series
  hypertables with automatic compression), keeping PostgreSQL for ACID-critical tables.

**Secondary bottleneck — FAISS is in-memory.** At >10M vectors, FAISS IVFFlat RAM usage
exceeds Render's free-tier 512 MB limit. Fix: shard per-workspace (already the case) +
add disk-backed FAISS-on-GPU or migrate to Qdrant/Milvus for the hot path.

---

## 6. Key Trade-offs

| Dimension | Choice made | What we gave up |
|---|---|---|
| **Retrieval** | Hybrid BM25 + dense + RRF + reranker | More moving parts; BM25 rebuilds on cold start |
| **Agent** | LangGraph (graph-based, resumable) | Pinned to langgraph 0.2–0.3; steep learning curve |
| **Streaming** | SSE (one-way, native browser support) | No bidirectional events (WebSocket used for annotations instead) |
| **ORM** | SQLAlchemy Core (no ORM relations) | No Django-style relationship traversal; raw SQL for joins |
| **Vector DB** | ChromaDB (dev simplicity) | Not as battle-tested under high write load as Qdrant/Weaviate |
| **Free-tier deploy** | Render.com + Supabase + Upstash | Cold-start latency (~30 s on Render free), 500 MB Postgres limit |
| **Demo mode** | All panels mocked client-side | Mocks can drift from real API contract |

---

## 7. v2 Improvements (prioritised)

1. **Streaming annotations** — replace the polling pattern in `AnnotationsPanel` with a
   WebSocket diff stream so multiple reviewers see highlights in real time without refresh.

2. **Structured output extraction** — replace regex-based legal clause extraction with
   GPT-4o function calling (`response_format: json_schema`) to get typed, validated
   JSON objects for each clause, risk score, and jurisdiction reference.

3. **Multi-modal ingest** — add vision-LLM (GPT-4o-vision) for diagram/chart extraction
   from PDFs (currently PaddleOCR handles text regions only).

4. **Evaluation loop** — RAGAs scores (faithfulness, answer relevance, context precision)
   are computed on demand. v2: run automatically on every query in the background and
   surface a rolling quality dashboard in the monitoring panel.

5. **Per-workspace fine-tuning** — after a workspace accumulates >1 k rated Q/A pairs
   (from thumbs-up/down feedback), fine-tune a small embedding adapter (LoRA on e5-large)
   for that domain to improve domain-specific retrieval precision.

---

## 8. Security Model (brief)

- **Auth:** JWT (HS256, 30-min access + 7-day refresh). Passwords: bcrypt with
  `rounds=12`. API keys: SHA-256 hashed, prefix-based lookup.
- **Rate limiting:** Sliding-window Lua script in Redis on `/auth/login` and `/auth/signup`.
  Fail-open by default (if Redis is down, auth proceeds — availability over lockout).
- **CORS:** Explicit origins list in `CORS_ORIGINS` env var; credentials allowed only for
  listed origins.
- **Workspace isolation:** Every query and ingest path filters by `workspace_id` derived
  from the authenticated user's JWT claims — no cross-workspace data leakage at the ORM
  layer.
- **SQL injection:** SQLAlchemy Core with bound parameters throughout; no raw string
  interpolation in queries.
- **Secrets:** Never logged. `DATABASE_URL` password is masked in engine logs. JWT secret
  enforces `min_length=64`.

---

## 9. Observability

| Signal | Tool | Where |
|---|---|---|
| LLM traces (inputs, outputs, latency per node) | LangSmith | Cloud dashboard |
| ML experiment metrics (RAGAs faithfulness, etc.) | MLflow | `/mlflow` container |
| API request latency + error rates | Custom `monitoring/` module | `/api/v1/monitoring/stats` |
| Celery task queue depth + worker health | Flower | `localhost:5555` |
| Health check (DB + vector + RAG + cache) | `/health` endpoint | Render uptime monitor |
| Frontend bundle analysis | `vite build --reporter=json` | CI artefact |
