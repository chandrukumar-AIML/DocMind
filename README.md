# DocuMind AI — Intelligent Document Intelligence Platform

<p align="center">
  <img src="frontend/public/logo.png" alt="DocuMind AI" width="120" height="120" onerror="this.style.display='none'"/>
</p>

<p align="center">
  <strong>Upload documents → Ask questions → Get AI-powered answers with sources</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi" />
  <img src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql" />
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python" />
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker" />
  <img src="https://img.shields.io/badge/tests-142%20passing-brightgreen?style=flat-square" />
</p>

---

## What is DocuMind AI?

DocuMind AI is a full-stack AI-powered document intelligence platform. Upload PDFs, Word docs, spreadsheets, or plain text — then query them in natural language, extract legal clauses, run compliance checks, annotate, compare documents, and automate workflows.

Built for companies, legal teams, freelancers, and anyone who works with large volumes of documents.

---

## Features

### Core
| Feature | Description |
|---|---|
| **Hybrid RAG** | BM25 + Vector search with RRF fusion for best-in-class retrieval |
| **Multi-format Ingest** | PDF, DOCX, XLSX, TXT, images (OCR), web URLs |
| **Streaming Answers** | Real-time SSE streaming with sources cited per chunk |
| **3 Query Modes** | RAG, Agent (multi-step), Graph (entity relationships) |
| **Parent-Child Chunking** | Hierarchical chunking for better context preservation |

### AI & Legal
| Feature | Description |
|---|---|
| **Legal Analysis** | Clause extraction, risk scoring (1–10), obligation parsing |
| **Compliance Check** | GDPR, HIPAA, RBI, SEBI regulation scanning |
| **Extractive + LLM** | Works without LLM (extractive fallback); enhanced with Ollama/OpenAI |

### Collaboration
| Feature | Description |
|---|---|
| **Annotations** | Real-time WebSocket sync, highlight/comment/tag/risk_flag/approval |
| **E-Signatures** | Request and track document signatures |
| **Comparison** | Cross-document similarity, difference, pattern, summary modes |
| **Workflows** | Automation rules triggered on document events |
| **Webhooks** | Real-time event push to external systems |
| **Templates** | Built-in + custom extraction templates (Invoice, Contract, Medical, etc.) |

### Platform
| Feature | Description |
|---|---|
| **Multi-workspace** | Workspace isolation, role-based access (viewer/editor/admin) |
| **API Keys** | Per-user API keys for programmatic access |
| **Onboarding** | Email invite system with role assignment |
| **Regional** | Indian language tools — Tanglish normalization, multilingual queries |
| **Monitoring** | Metrics dashboard, usage stats, evaluation pipeline (RAGAs + MLflow) |
| **Admin** | Superadmin panel for workspace and user management |

---

## Tech Stack

```
Backend:   FastAPI + Uvicorn + asyncpg + SQLAlchemy
Frontend:  React 19 + Vite + Axios
Database:  PostgreSQL 16 (users, workspaces, annotations, webhooks)
Vectors:   ChromaDB + FAISS (hybrid dual-store)
Search:    BM25 (sparse) + text-embedding-3-large (dense) + RRF fusion
Reranker:  cross-encoder/ms-marco-MiniLM-L-6-v2 (local)
LLM:       Ollama llama3.2 (local) / OpenAI GPT-4o (cloud)
OCR:       PaddleOCR + pypdfium2
Cache:     Redis
Queue:     Celery + Redis (background ingest jobs)
Graph:     Neo4j (entity extraction + relationships)
Tracking:  MLflow experiments + LangSmith traces
Deploy:    Docker Compose / Railway / Render
CI/CD:     GitHub Actions
```

---

## Quick Start

### Option A — Docker Compose (Recommended)

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/docmind-ai.git
cd docmind-ai

# 2. Configure environment
cp .env.example .env
# Edit .env and set:
#   OPENAI_API_KEY=sk-...
#   POSTGRES_PASSWORD=your-secure-password
#   DOCUMIND_JWT_SECRET_KEY=your-64-char-secret

# 3. Start all services
docker compose up -d

# 4. Seed demo data (optional)
docker compose exec backend python seed_data.py

# 5. Open browser
open http://localhost:3000
```

### Option B — Local Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # edit with your values
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (new terminal)
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
# Open http://localhost:5173
```

---

## API Documentation

Once running, visit:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

---

## Test Credentials (after seeding)

| Email | Password | Role |
|---|---|---|
| admin@docmind.ai | AdminP@ssw0rd!2026 | Admin |
| demo@docmind.ai | DemoP@ssw0rd!2026 | Editor |

---

## Deploy to Railway (One Click)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/docmind-ai)

Or manually:
```bash
railway login
railway up
```

---

## Project Structure

```
docmind-ai/
├── backend/
│   ├── app/
│   │   ├── api/routes/      # 60+ FastAPI endpoints
│   │   ├── auth/            # JWT, RBAC, rate limiting
│   │   ├── core/            # RAG pipeline, chunking, prompts
│   │   ├── domains/legal/   # Clause extraction, risk scoring
│   │   ├── retrieval/       # Hybrid BM25 + vector retrieval
│   │   ├── vectorstore/     # ChromaDB + FAISS managers
│   │   └── main.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── seed_data.py
├── frontend/
│   ├── src/
│   │   ├── components/      # 40+ React components
│   │   ├── hooks/           # useStreamQuery, useAuth, useIngest
│   │   └── api/client.js    # Axios client + interceptors
│   ├── Dockerfile
│   └── nginx.conf
├── docker-compose.yml       # Full stack: postgres, redis, chromadb, neo4j, mlflow
├── .github/workflows/       # CI/CD: lint, test, build, push
└── .env.example             # Sanitized environment template
```

---

## Test Results

```
Integration tests:  24/24 passing ✅
Batch API tests:   118/126 passing ✅ (8 skipped — data-dependent)
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests welcome. Please open an issue first to discuss major changes.

---

<p align="center">Built with ❤️ for document intelligence</p>
