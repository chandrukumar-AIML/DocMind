# DocMind AI — System Architecture

## C4 Model Overview

The diagrams below follow the [C4 model](https://c4model.com/): Context → Containers → Components.

---

## Level 1: System Context

```mermaid
C4Context
  title DocMind AI — System Context

  Person(user, "End User", "Uploads documents, asks questions, views citations")
  Person(admin, "Workspace Admin", "Manages members, API keys, billing settings")
  Person(oncall, "SRE / On-Call", "Monitors alerts, runs runbooks")

  System(docmind, "DocMind AI", "Enterprise document intelligence — multi-modal OCR, hybrid RAG, LangGraph agentic pipeline, multi-tenant SaaS")

  System_Ext(openai, "OpenAI / Groq API", "LLM inference (GPT-4o, Llama3)")
  System_Ext(stripe, "Stripe / Razorpay", "Subscription billing & invoicing")
  System_Ext(sso, "OIDC / OAuth2 IdP", "Google, Azure AD, Okta SSO")
  System_Ext(duckduckgo, "DuckDuckGo Search", "Web search for CRAG fallback")
  System_Ext(s3, "S3-compatible Storage", "Raw uploaded document files")
  System_Ext(pagerduty, "PagerDuty", "On-call alert routing")
  System_Ext(slack, "Slack", "Team alert notifications")

  Rel(user, docmind, "Uploads docs, queries, views answers", "HTTPS")
  Rel(admin, docmind, "Manages workspace, billing, API keys", "HTTPS")
  Rel(oncall, docmind, "Monitors metrics, triggers runbooks", "HTTPS / kubectl")

  Rel(docmind, openai, "LLM calls (generate, embed)", "HTTPS / OpenAI SDK")
  Rel(docmind, stripe, "Subscription management", "HTTPS / Stripe SDK")
  Rel(docmind, sso, "OIDC authorization code flow", "HTTPS / OAuth2")
  Rel(docmind, duckduckgo, "Web search (CRAG fallback)", "HTTPS")
  Rel(docmind, s3, "Store / retrieve raw files", "HTTPS / S3 API")
  Rel(docmind, pagerduty, "Critical alerts", "HTTPS")
  Rel(docmind, slack, "Alert notifications", "Webhooks / HTTPS")
```

---

## Level 2: Container Diagram

```mermaid
C4Container
  title DocMind AI — Container Diagram

  Person(user, "End User")
  Person(admin, "Admin")

  Container_Boundary(frontend, "Frontend") {
    Container(spa, "React SPA", "React 19, Vite, Zustand, TanStack Query", "Document upload, Q&A chat, citations, analytics dashboard")
  }

  Container_Boundary(backend, "Backend") {
    Container(api, "FastAPI API", "Python 3.11, FastAPI, SQLAlchemy async", "REST API — auth, ingest, query, billing, monitoring")
    Container(worker, "Celery Worker", "Celery + Redis broker", "Async document ingestion, OCR, chunking, embedding")
    Container(langgraph, "LangGraph Agent", "LangGraph 0.2, LangChain 0.3", "12-node CRAG state machine — grade → rewrite → web search → generate")
  }

  Container_Boundary(data, "Data Layer") {
    ContainerDb(postgres, "PostgreSQL 16", "PostgreSQL", "Users, workspaces, billing, document ACL, audit log")
    ContainerDb(redis, "Redis 7", "Redis", "Rate limiting, token revocation, Celery broker/backend")
    ContainerDb(chroma, "ChromaDB", "ChromaDB", "Dense vector embeddings (text-embedding-3-large)")
    ContainerDb(faiss, "FAISS", "FAISS (in-process)", "Secondary ANN index for ultra-low-latency retrieval")
    ContainerDb(neo4j, "Neo4j", "Neo4j Graph DB", "Entity knowledge graph (optional — relationship queries)")
    ContainerDb(s3_store, "S3 Storage", "S3-compatible", "Raw uploaded files (PDF, DOCX, images)")
  }

  Container_Boundary(observability, "Observability") {
    Container(prometheus, "Prometheus", "Prometheus", "Metrics scrape + alert rules")
    Container(alertmanager, "Alertmanager", "Alertmanager", "Alert routing → PagerDuty + Slack")
    Container(grafana, "Grafana", "Grafana", "Dashboards — latency, error rate, RAG quality, billing")
    Container(otel, "OpenTelemetry", "OTLP", "Distributed tracing — spans exported to Grafana Tempo")
  }

  Rel(user, spa, "Uses", "HTTPS")
  Rel(admin, spa, "Manages workspace", "HTTPS")
  Rel(spa, api, "API calls", "HTTPS / REST + SSE streaming")

  Rel(api, postgres, "Read / write", "asyncpg")
  Rel(api, redis, "Rate limit, token blacklist", "redis-py async")
  Rel(api, chroma, "Vector similarity search", "ChromaDB HTTP API")
  Rel(api, faiss, "ANN retrieval (in-process)", "FAISS Python")
  Rel(api, neo4j, "Graph traversal", "Neo4j Bolt")
  Rel(api, worker, "Enqueue ingestion tasks", "Redis / Celery")
  Rel(api, langgraph, "Invoke agentic pipeline", "Python in-process")

  Rel(worker, s3_store, "Download raw files", "S3 API")
  Rel(worker, chroma, "Index embeddings", "ChromaDB API")
  Rel(worker, faiss, "Update ANN index", "FAISS Python")
  Rel(worker, postgres, "Write job status", "asyncpg")

  Rel(langgraph, api, "LLM + retrieval via shared services", "In-process")

  Rel(prometheus, api, "Scrape /metrics", "HTTP")
  Rel(prometheus, alertmanager, "Fire alerts", "HTTP")
  Rel(api, otel, "Emit spans", "OTLP gRPC")
```

---

## Level 3: Component Diagram — API (FastAPI)

```mermaid
C4Component
  title DocMind AI — API Backend Components

  Container_Boundary(api_container, "FastAPI API") {

    Component(auth, "Auth Router\n/api/v1/auth", "FastAPI Router", "Login, register, OAuth2/SSO, MFA/TOTP, token refresh, logout")
    Component(mfa, "MFA Router\n/api/v1/mfa", "FastAPI Router", "TOTP setup, verify, disable — pyotp-based")
    Component(ingest, "Ingest Router\n/api/v1/ingest", "FastAPI Router", "File upload → Celery task → OCR → chunk → embed")
    Component(query, "Query Router\n/api/v1/query", "FastAPI Router", "Streaming RAG endpoint — SSE token-by-token delivery")
    Component(documents, "Documents Router\n/api/v1/documents", "FastAPI Router", "CRUD, listing, pagination, metadata")
    Component(billing, "Billing Router\n/api/v1/billing", "FastAPI Router", "Stripe + Razorpay subscription management")
    Component(monitoring, "Monitoring Router\n/api/v1/monitoring", "FastAPI Router", "Prometheus metrics, system health, usage stats")

    Component(jwt, "JWT Handler", "Python module", "RS256 (production) / HS256 (dev), JTI revocation, SSO state tokens")
    Component(rag, "AdvancedRAGChain", "Python class", "HyDE → hybrid search → rerank → LLM generate, circuit breaker")
    Component(hybrid, "HybridSearcher", "Python class", "ChromaDB + FAISS + BM25/Okapi RRF fusion, JSON cache (no pickle)")
    Component(crag, "CRAG Pipeline", "LangGraph", "Grade → decompose → web search → self-RAG → generate")
    Component(llm_pool, "LLM Pool", "Python module", "get_llm() — OpenAI / Groq / Ollama, FakeListChatModel blocked in prod")
    Component(circuit, "Circuit Breaker", "Python class", "Protects LLM + embedding calls, CLOSED/OPEN/HALF_OPEN FSM")
    Component(rate, "Rate Limiter", "Middleware", "Redis Lua sliding window, fail-closed in prod, Prometheus counter")
    Component(acl, "Document ACL", "Python module", "Per-document permission grants, workspace-role fallback")
    Component(apikey_mw, "ApiKeyAuthMiddleware", "FastAPI Middleware", "Authorization: ApiKey header only (query param disabled)")
  }

  Rel(auth, jwt, "Signs / verifies tokens")
  Rel(query, rag, "Invokes retrieval pipeline")
  Rel(rag, hybrid, "Hybrid BM25 + vector search")
  Rel(rag, llm_pool, "Requests LLM instance")
  Rel(rag, circuit, "Wraps LLM + embedding calls")
  Rel(rag, crag, "CRAG routing when relevance low")
  Rel(query, acl, "Filters results by document ACL")
  Rel(apikey_mw, rate, "Feeds workspace ID for rate limiting")
```

---

## Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| JWT algorithm | RS256 (prod), HS256 (dev) | Asymmetric — private key stays in auth service; downstream services verify with public key |
| BM25 cache | JSON (not pickle) | Pickle can execute arbitrary code on deserialization (RCE) — JSON is data-only |
| API key transport | `X-API-Key` header only | Query params are logged by every HTTP layer; headers are not |
| Rate limiter failure mode | Fail-closed in production | Redis outage must not disable security controls |
| LLM fallback | Hard error in production | Silent fake responses would corrupt user trust; fail fast is correct |
| Citation types | `Citation` (document) vs `WebCitation` (web) | Web results have no page/PDF anchors — separate type prevents broken UI highlights |
| Circuit breaker | Module-level, 5-failure threshold, 60 s reset | Prevent cascading failures on partial OpenAI outages |
| MFA | TOTP via pyotp | RFC 6238 standard, compatible with all authenticator apps, no SMS dependency |
| Document ACL | Per-file permission table with workspace-level fallback | Minimal configuration overhead while supporting fine-grained sharing |
