# DocuMind AI — Intelligent Document Intelligence Platform

<p align="center">
  <img src="frontend/public/logo.png" alt="DocuMind AI" width="120" height="120"/>
</p>

<p align="center">
  <strong>Upload documents → Ask in natural language → Get AI-powered answers with cited sources</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python"/>
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi"/>
  <img src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react"/>
  <img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql"/>
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker"/>
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/tests-passing-brightgreen?style=flat-square"/>
  <img src="https://img.shields.io/github/last-commit/chandrukumar-AIML/DocMind?style=flat-square"/>
</p>

<p align="center">
  <a href="https://doc-mind-peach.vercel.app"><strong>🚀 Live Demo</strong></a> ·
  <a href="DEPLOY.md">Deploy Guide</a> ·
  <a href="#-quick-start">Quick Start</a>
</p>

---

## 🎬 Demo

**▶️ Live:** https://doc-mind-peach.vercel.app

The live demo runs in **demo mode** with realistic sample data (no API key
required) so you can explore every feature instantly — RAG/Agent/Graph chat,
document library, domain analysis, and all collaboration panels. To run with a
real LLM backend, set `VITE_DEMO_MODE=false` and add an `OPENAI_API_KEY`
(see [DEPLOY.md](DEPLOY.md)).

> _Tip: add a GIF/screenshot here — record the walkthrough with OBS or Loom._

---

## What is DocuMind AI?

DocuMind AI is a full-stack AI document intelligence platform — **29 API route modules,
156 REST endpoints, ~69k lines of backend Python, and a 59-file React 19 SPA** with a
built-in demo mode. Upload PDFs, Word docs, Excel sheets, audio files, or web URLs — then
query them in natural language, extract legal clauses, run compliance checks, annotate
collaboratively, compare documents, and automate document workflows.

Built for legal teams, enterprises, freelancers, and anyone who works with large volumes of documents.

> **Project status:** feature-complete and demo-ready. Try every feature instantly via the
> [live demo](https://doc-mind-peach.vercel.app) (no API key needed). Backend services
> (Postgres, Redis, ChromaDB, Neo4j) and an `OPENAI_API_KEY` are required for the full,
> non-mocked stack — see [DEPLOY.md](DEPLOY.md).

---

## ✨ Key Features

| # | Feature | Description |
|---|---|---|
| 1 | **Hybrid RAG** | BM25 sparse + `text-embedding-3-large` dense retrieval fused with RRF + local cross-encoder reranking |
| 2 | **3 Query Modes** | RAG (cited answers), Agent (multi-step with table extraction), Graph (entity relationships via Neo4j) |
| 3 | **Multi-format Ingest** | PDF, DOCX, XLSX, TXT, images (PaddleOCR), MP3/MP4/WAV (Whisper), web URLs |
| 4 | **Domain AI** | Legal clause extraction + risk scoring (1–10), Medical, Logistics, Bills, Forms, Signature detection |
| 5 | **Compliance Scanner** | GDPR, HIPAA, RBI, SEBI regulation scanning with flagged clauses |
| 6 | **Real-time Annotations** | WebSocket sync — highlight / comment / tag / risk_flag / approval |
| 7 | **Cross-doc Comparison** | Similarity, difference, pattern, summary modes across up to 50 documents |
| 8 | **Workflow Automation** | Event-triggered rules for post-ingest actions + outbound webhooks |
| 9 | **Knowledge Graph** | Neo4j entity extraction + relationship visualisation |
| 10 | **Multi-workspace RBAC** | Workspace isolation, role-based access (viewer / editor / admin / superadmin) |

---

## 🛠 Tech Stack

| Category | Technology | Purpose |
|---|---|---|
| Backend | FastAPI 0.111 + Uvicorn | Async REST API + SSE streaming |
| Frontend | React 19 + Vite | SPA — dark nebula design system, 44 components |
| Database | PostgreSQL 16 + asyncpg | Users, workspaces, annotations, audit trail |
| Vector DB | ChromaDB + FAISS | Dual hybrid vector store |
| Sparse Search | rank-bm25 | Keyword retrieval with RRF fusion |
| Reranker | cross-encoder/ms-marco-MiniLM | Local cross-encoder reranking |
| LLM | OpenAI GPT-4o / Ollama llama3.2 | Answering, extraction, summarisation |
| Embeddings | text-embedding-3-large | 3072-dim dense vectors |
| OCR | PaddleOCR + pypdfium2 | Image PDF + handwriting extraction |
| Audio | OpenAI Whisper | MP3 / MP4 / WAV transcription |
| Graph | Neo4j + LangChain-Neo4j | Entity relationships + Cypher queries |
| Orchestration | LangGraph | Agentic multi-step reasoning |
| Cache | Redis | Response cache + session storage |
| Queue | Celery + Redis | Background ingest jobs |
| Tracking | MLflow + LangSmith | Experiment tracking + LLM traces |
| Deploy | Docker Compose / Render / Vercel | Full-stack containerised deployment |
| CI/CD | GitHub Actions | Lint → test → build pipeline |

---

## 📁 Project Structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # 29 FastAPI route modules (query, ingest, domains, auth…)
│   │   ├── auth/             # JWT + RBAC + rate limiting + API keys
│   │   ├── core/             # RAG chain, chunking, prompts, exceptions
│   │   ├── domains/          # Legal, medical, logistics, bills, forms, signature
│   │   ├── retrieval/        # Hybrid BM25 + vector retrieval + RRF fusion
│   │   ├── vectorstore/      # ChromaDB + FAISS managers
│   │   ├── graph/            # Neo4j entity extraction + graph queries
│   │   ├── evaluation/       # RAGAs pipeline + MLflow metrics
│   │   ├── monitoring/       # Usage metrics + query latency collector
│   │   └── main.py           # FastAPI app + lifespan + middleware
│   ├── Dockerfile
│   ├── requirements.txt
│   └── seed_data.py
├── frontend/
│   ├── src/
│   │   ├── components/       # 44 React components (chat, sidebar, panels, viewers)
│   │   ├── hooks/            # useStreamQuery, useAuth, useConversationHistory
│   │   └── api/              # Axios client + demo mock layer
│   ├── vite.config.js
│   └── nginx.conf            # Production nginx config (serves on port 80 inside container)
├── docker-compose.yml        # postgres + redis + chromadb + neo4j + mlflow + frontend
├── .github/workflows/        # CI: lint → test → build → push
├── DEPLOY.md                 # Free-tier deploy guide (Vercel + Render + Supabase + Upstash)
└── .env.example              # All 50+ environment variables documented
```

---

## 🚀 Quick Start

### Option A — Docker Compose (Full Stack)

> Runs backend + frontend + PostgreSQL + Redis + ChromaDB + Neo4j + MLflow in one command.

```bash
# 1. Clone
git clone https://github.com/chandrukumar-AIML/DocMind.git
cd DocMind

# 2. Configure environment
cp .env.example .env
# Open .env and set:
#   OPENAI_API_KEY=sk-...
#   POSTGRES_PASSWORD=your-secure-password
#   JWT_SECRET_KEY=your-64-char-random-secret

# 3. Start all services
docker compose up -d

# 4. (Optional) Seed demo data
docker compose exec backend python seed_data.py

# 5. Open browser
# Frontend: http://localhost:3000
# Backend API docs: http://localhost:8000/docs
```

### Option B — Local Development

```bash
# ── Backend ──────────────────────────────────────────
cd backend
python -m venv .venv

# Linux/Mac:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
cp ../.env.example ../.env   # edit with your values
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# ── Frontend (new terminal) ───────────────────────────
cd frontend
npm install
npm run dev
# Open http://localhost:5175
```

---

## 🔐 Test Credentials (after seeding)

| Email | Password | Role |
|---|---|---|
| `admin@docmind.ai` | `AdminP@ssw0rd!2026` | Admin |
| `demo@docmind.ai` | `DemoP@ssw0rd!2026` | Editor |

---

## 📡 Key API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/query/stream` | RAG / Agent / Graph streaming query (SSE) |
| `POST` | `/api/v1/ingest/upload` | Upload documents (PDF / DOCX / XLSX / image / audio) |
| `POST` | `/api/v1/domains/legal/analyze` | Legal clause extraction + risk scoring |
| `POST` | `/api/v1/compliance/check` | GDPR / HIPAA / RBI / SEBI compliance scan |
| `POST` | `/api/v1/compare/start` | Start cross-document comparison job |
| `GET`  | `/api/v1/annotations/` | List / filter annotations for a document |
| `POST` | `/api/v1/annotations/ws/{workspace_id}` | WebSocket real-time annotation sync |
| `GET`  | `/api/v1/monitoring/stats` | Usage metrics dashboard |
| `POST` | `/api/v1/auth/login` | JWT authentication |
| `GET`  | `/health` | Health check |
| `GET`  | `/docs` | Interactive Swagger UI (all 29 route modules, 156 endpoints) |

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | OpenAI key for GPT-4o + text-embedding-3-large |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (asyncpg) |
| `REDIS_URL` | ✅ | Redis URL for cache + Celery broker |
| `JWT_SECRET_KEY` | ✅ | 64+ character random secret |
| `CORS_ORIGINS` | ✅ | Frontend URL(s) as JSON array |
| `NEO4J_URI` | optional | Graph queries — `bolt://localhost:7687` |
| `LANGCHAIN_API_KEY` | optional | LangSmith trace observability |
| `MLFLOW_TRACKING_URI` | optional | MLflow experiment tracking |
| `HUGGINGFACE_TOKEN` | optional | HuggingFace model downloads |

See [.env.example](.env.example) for the complete list of 50+ variables.

---

## 🧪 Test Results

```
Backend  · pytest suite (app/tests):   67 test fns / 14 modules ✅   (regional + validators: 57 cases, pure-unit)
Backend  · E2E scripts (live API):     integration_test.py · batch_test.py · test_endpoints.py
Frontend · Vitest suite:               24 / 24 passing ✅   (demo proxy, isDemoMode, panels, components)
ESLint:                                0 errors ✅          (5 warnings — intentional ref patterns)
Production build:                      ✅ zero errors
CI/CD (GitHub Actions):                ruff + pytest + eslint + vitest + build ✅
```

> Run locally: `cd backend && pytest app/tests/` · `cd frontend && npm test`
>
> _Test coverage is actively being expanded; the `app/tests` unit suite and frontend
> Vitest suite are the source of truth for CI. The root `*_test.py` files are standalone
> end-to-end scripts that exercise a running backend._

---

## ☁️ Deploy (Free Tier)

See **[DEPLOY.md](DEPLOY.md)** for the complete step-by-step guide using:

| Service | What | Free Tier |
|---|---|---|
| **Vercel** | Frontend | Unlimited static + 100 GB bandwidth |
| **Render** | Backend (FastAPI) | 750 hrs/month |
| **Supabase** | PostgreSQL 16 | 500 MB database |
| **Upstash** | Redis | 10,000 commands/day |

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

<p align="center">
  Built with ❤️ for document intelligence
  <br/>
  <a href="https://github.com/chandrukumar-AIML/DocMind">GitHub</a> ·
  <a href="https://github.com/chandrukumar-AIML/DocMind/issues">Issues</a> ·
  <a href="DEPLOY.md">Deploy Guide</a>
</p>
