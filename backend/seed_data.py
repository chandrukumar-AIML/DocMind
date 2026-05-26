"""
DocuMind AI - Database Seed Script
Creates sample users, workspaces, and uploads real sample documents.
Run from the backend directory.
"""
import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse
import tempfile

API = "http://localhost:8000/api/v1"

# ── helpers ──────────────────────────────────────────────────────────────────
def req(method, path, data=None, headers=None):
    url = f"{API}{path}"
    hdrs = headers or {}
    body = None
    if data:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    rq = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=30) as resp:
            raw = resp.read().decode(errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw

def upload_doc(filepath, workspace_id, auth_headers):
    boundary = "----DocuMindSeedBoundary"
    with open(filepath, "rb") as f:
        file_data = f.read()
    filename = os.path.basename(filepath)

    # build multipart body
    body_parts = []
    if workspace_id:
        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"workspace_id\"\r\n\r\n{workspace_id}\r\n".encode()
        )
    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: text/plain\r\n\r\n".encode()
        + file_data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    body = b"".join(body_parts)

    hdrs = {**auth_headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    rq = urllib.request.Request(f"{API}/ingest/document", data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(rq, timeout=60) as resp:
            raw = resp.read().decode(errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]

# ── Sample document content ───────────────────────────────────────────────────
SAMPLE_DOCS = {
    "quarterly_report_q1_2026.txt": """
DOCUMIND AI TECHNOLOGIES
Q1 2026 QUARTERLY FINANCIAL REPORT

Executive Summary
-----------------
DocuMind AI delivered exceptional performance in Q1 2026, with total revenue reaching $4.2 million,
representing a 38% year-over-year growth. Our AI-powered document processing platform onboarded
142 new enterprise clients, bringing total customers to 890.

Revenue Breakdown
-----------------
- SaaS Subscriptions: $2.8M (67%)
- Professional Services: $0.9M (21%)
- API Usage Fees: $0.5M (12%)

Key Metrics
-----------
- Documents Processed: 12.4 million
- Average Query Response: 1.2 seconds
- OCR Accuracy Rate: 98.7%
- Customer Satisfaction: 4.8/5.0
- Net Promoter Score: 72

Product Highlights
------------------
The new Hybrid RAG pipeline reduced hallucination by 45% compared to Q4 2025.
Multi-language support extended to 28 languages including Tamil, Telugu, and Hindi.
Enterprise security features including SOC 2 Type II compliance achieved.

Outlook Q2 2026
---------------
Projected revenue: $5.1M - $5.4M
Target new clients: 160+
Planned feature releases: GraphRAG v2.0, Real-time collaboration, Mobile app
""",
    "employee_handbook.txt": """
DOCUMIND AI - EMPLOYEE HANDBOOK 2026

Welcome to DocuMind AI!
-----------------------
We are an AI-powered document intelligence company headquartered in Chennai, India.
Our mission: Make every document instantly searchable, understandable, and actionable.

Core Values
-----------
1. Innovation First - We ship 2x per week and embrace rapid iteration
2. Customer Obsession - Every feature must solve a real customer problem
3. Radical Transparency - No hidden agendas, share data openly
4. Engineering Excellence - Code quality, test coverage, performance matter
5. Inclusivity - Diverse teams build better products

Leave Policy
------------
- Annual Leave: 18 days per year (accrued monthly)
- Sick Leave: 10 days per year
- Festival Holidays: 10 days (as per Tamil Nadu government calendar)
- Maternity Leave: 26 weeks (fully paid)
- Paternity Leave: 2 weeks (fully paid)
- Work from Home: Up to 3 days per week for non-production roles

Engineering Team Structure
--------------------------
Backend Team: FastAPI, PostgreSQL, Celery, Redis
Frontend Team: React, Vite, TypeScript
ML/AI Team: LangChain, LangGraph, PyTorch, HuggingFace
DevOps Team: Docker, Kubernetes, GitHub Actions, AWS

Benefits
--------
- Health Insurance: Employee + Spouse + 2 Children covered
- Stock Options: ESOP program for all full-time employees
- Learning Budget: $1,000 per year for courses, conferences, books
- Home Office Allowance: $500 one-time setup allowance
- Gym Membership: Company-subsidized ₹2,000/month reimbursement
""",
    "product_roadmap_2026.txt": """
DOCUMIND AI - PRODUCT ROADMAP 2026

Vision
------
By end of 2026, DocuMind AI will be the world's most trusted enterprise document intelligence platform,
processing 100 million documents monthly across 50+ languages and 10,000+ customers.

Q1 2026 (Completed)
-------------------
✅ Hybrid RAG Pipeline (BM25 + Dense Vector + Reranking)
✅ Multi-tenant Workspace Management
✅ JWT Authentication with httpOnly Cookies
✅ Document Versioning and Diff
✅ LangGraph-based Agent with CRAG + Self-RAG
✅ PDF, DOCX, XLSX, TXT ingestion
✅ ChromaDB + FAISS dual vector store

Q2 2026 (In Progress)
---------------------
🔄 GraphRAG v2.0 - Knowledge graph-enhanced retrieval
🔄 Real-time Collaboration - Multiple users on same workspace
🔄 Document Annotations - Highlight and comment on PDFs
🔄 E-Signature Integration - DocuSign API
🔄 Advanced Analytics Dashboard
🔄 Mobile App (React Native)

Q3 2026 (Planned)
-----------------
📋 Fine-tuning Pipeline - Custom embedding models per customer
📋 Video/Audio Transcription - Whisper integration
📋 Compliance Module - GDPR, HIPAA, SOC2 automated checks
📋 API Marketplace - Third-party integrations (Salesforce, Slack, Teams)
📋 On-premise Deployment - Self-hosted Docker/Kubernetes package

Q4 2026 (Planned)
-----------------
📋 Multilingual OCR - 50+ languages
📋 Table Understanding - Complex table extraction and reasoning
📋 Document Generation - AI-powered report creation
📋 Enterprise SSO - SAML 2.0, LDAP, Active Directory
📋 SOC 2 Type II Renewal + ISO 27001

Technical Debt Items
--------------------
- Migrate from synchronous ORM to fully async SQLAlchemy 2.0
- Replace Redis sessions with JWT-only approach
- Upgrade to FastAPI 0.115.x with Pydantic v2
- Implement database connection pooling with PgBouncer
""",
    "meeting_notes_may2026.txt": """
ENGINEERING TEAM MEETING NOTES
Date: May 15, 2026
Attendees: Chandru (Lead), Priya (Backend), Rahul (Frontend), Deepa (ML), Karthik (DevOps)

Agenda Items Discussed
----------------------

1. Sprint Review - Sprint 23
   - Completed: Document annotation system, WebSocket real-time updates
   - Velocity: 42 story points (target was 38) ✅
   - Bugs fixed: 17 (12 critical, 5 minor)

2. Production Incidents Review
   - May 10: Redis connection timeout causing 503s on query endpoint
     → Fix: Increased connection pool size from 10 to 50, added circuit breaker
   - May 13: FAISS index rebuild taking 45s on first request
     → Fix: Pre-warm FAISS on startup, lazy load only for new workspaces

3. Performance Review
   - P50 latency: 1.1s (target: <1.5s) ✅
   - P95 latency: 3.8s (target: <5s) ✅
   - P99 latency: 12s (needs improvement - target: <8s) ⚠️

4. Upcoming Sprint 24 Goals
   - Complete GraphRAG v2 integration
   - Mobile app beta release
   - Reduce P99 latency to <8s
   - Add document sharing feature

5. Architecture Decision: Vector Store Strategy
   Decision: Keep ChromaDB (primary) + FAISS (secondary for speed)
   Rationale: ChromaDB provides persistence and filtering, FAISS provides sub-millisecond search

6. Action Items
   - Chandru: Review security audit report by May 20
   - Priya: Fix document metadata endpoint returning 500 (JIRA-489)
   - Rahul: Complete login page UX redesign
   - Deepa: Benchmark new embedding model (text-embedding-3-large vs small)
   - Karthik: Set up staging environment on AWS ECS

Next Meeting: May 22, 2026 at 10:00 AM IST
""",
}

# ── Seed users ────────────────────────────────────────────────────────────────
SEED_USERS = [
    {"email": "admin@docmind.ai", "password": "AdminP@ssw0rd!2026", "display_name": "Admin User"},
    {"email": "demo@docmind.ai", "password": "DemoP@ssw0rd!2026", "display_name": "Demo User"},
    {"email": "chandru@docmind.ai", "password": "ChandruP@ss!2026", "display_name": "Chandru Kumar"},
]

print("=" * 60)
print("DocuMind AI - Database Seed Script")
print("=" * 60)

# Check server is up
try:
    r = urllib.request.urlopen("http://localhost:8000/health", timeout=30)
    print(f"✓ Backend server is running (HTTP {r.status})")
except Exception as e:
    print(f"✗ Backend not running: {e}")
    sys.exit(1)

# ── Step 1: Register / Login users ───────────────────────────────────────────
print("\n── Step 1: Creating users ──")
tokens = {}
workspace_ids = {}

for user in SEED_USERS:
    # Try register
    status, body = req("POST", "/auth/register", {
        "email": user["email"],
        "password": user["password"],
        "display_name": user["display_name"],
    })
    if status == 201:
        print(f"  ✓ Registered: {user['email']}")
    elif status in (400, 409, 422):
        detail = body.get("detail", "") if isinstance(body, dict) else str(body)[:80]
        if "already" in str(detail).lower() or "exists" in str(detail).lower():
            print(f"  ✓ Already exists: {user['email']}")
        else:
            print(f"  ✗ Register failed for {user['email']}: {status} - {detail}")
    else:
        print(f"  ✗ Register failed for {user['email']}: {status}")

    # Login
    status, body = req("POST", "/auth/login", {
        "email": user["email"],
        "password": user["password"],
    })
    if status == 200 and isinstance(body, dict) and "access_token" in body:
        tokens[user["email"]] = body["access_token"]
        workspace_ids[user["email"]] = body.get("workspace_id", "default")
        print(f"  ✓ Logged in: {user['email']} | workspace={workspace_ids[user['email']][:8]}...")
    else:
        print(f"  ✗ Login failed for {user['email']}: {status} - {str(body)[:100]}")

if not tokens:
    print("No users logged in. Exiting.")
    sys.exit(1)

# Use admin as primary user
primary_email = list(tokens.keys())[0]
primary_token = tokens[primary_email]
primary_workspace = workspace_ids[primary_email]
auth_headers = {"Authorization": f"Bearer {primary_token}"}

print(f"\n  Using primary user: {primary_email}")
print(f"  Workspace: {primary_workspace}")

# ── Step 2: Create sample documents ──────────────────────────────────────────
print("\n── Step 2: Creating sample documents ──")
doc_dir = tempfile.mkdtemp(prefix="docmind_seed_")

created_docs = []
for filename, content in SAMPLE_DOCS.items():
    filepath = os.path.join(doc_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    created_docs.append(filepath)
    print(f"  ✓ Created: {filename} ({len(content)} chars)")

# ── Step 3: Upload documents via API ─────────────────────────────────────────
print("\n── Step 3: Uploading documents to DocuMind ──")
uploaded = []
for filepath in created_docs:
    filename = os.path.basename(filepath)
    print(f"  Uploading {filename}...", end=" ", flush=True)
    status, body = upload_doc(filepath, primary_workspace, auth_headers)
    if status == 200 and isinstance(body, dict):
        chunks = body.get("child_chunks", 0)
        print(f"✓ {status} | {chunks} chunks")
        uploaded.append(filename)
    else:
        print(f"✗ {status} | {str(body)[:100]}")

# ── Step 4: Verify documents are listed ──────────────────────────────────────
print("\n── Step 4: Verifying document listing ──")
status, body = req("GET", f"/documents?workspace_id={primary_workspace}", headers=auth_headers)
if status == 200 and isinstance(body, dict):
    docs = body.get("documents", [])
    print(f"  ✓ {len(docs)} documents in workspace")
    for d in docs[:6]:
        print(f"    - {d.get('source_file', '?')} ({d.get('document_type', '?')})")
else:
    print(f"  ✗ List failed: {status}")

# ── Step 5: Test a query ──────────────────────────────────────────────────────
print("\n── Step 5: Test RAG query ──")
status, body = req("POST", "/query", {
    "question": "What was the revenue in Q1 2026?",
    "workspace_id": primary_workspace,
    "strategy": "hybrid",
    "top_k": 3,
}, headers=auth_headers)
if status == 200:
    print(f"  ✓ Query answered (status 200)")
    if isinstance(body, dict):
        answer = body.get("answer") or body.get("result") or body.get("response", "")
        if answer:
            print(f"  Answer preview: {str(answer)[:200]}...")
    else:
        print(f"  Response: {str(body)[:200]}")
else:
    print(f"  ✗ Query failed: {status} - {str(body)[:200]}")

# ── Step 6: Test agent query ──────────────────────────────────────────────────
print("\n── Step 6: Test Agent query ──")
import urllib.error as _ue
_agent_url = f"{API}/agent/query"
_agent_body = json.dumps({
    "question": "Summarize the key product features mentioned in the roadmap",
    "workspace_id": primary_workspace,
}).encode()
_agent_rq = urllib.request.Request(_agent_url, data=_agent_body,
    headers={**auth_headers, "Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(_agent_rq, timeout=120) as _resp:
        _raw = _resp.read().decode(errors="replace")
        status = _resp.status
        body = json.loads(_raw) if _raw.strip().startswith("{") else _raw
except _ue.HTTPError as _e:
    status, body = _e.code, _e.read().decode(errors="replace")[:300]
except Exception as _e:
    status, body = 0, str(_e)
if status == 200:
    print(f"  ✓ Agent query answered (status 200)")
    if isinstance(body, dict):
        answer = body.get("answer") or body.get("result") or body.get("response", "")
        if answer:
            print(f"  Answer preview: {str(answer)[:200]}...")
    else:
        print(f"  Response: {str(body)[:200]}")
else:
    print(f"  ✗ Agent query: {status} - {str(body)[:200]}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SEED COMPLETE")
print("=" * 60)
print(f"Users created: {len(tokens)}")
print(f"Documents uploaded: {len(uploaded)}/{len(SAMPLE_DOCS)}")
print()
print("Login credentials:")
for user in SEED_USERS:
    if user["email"] in tokens:
        print(f"  {user['email']} / {user['password']}")
print()
print(f"Frontend: http://localhost:5173")
print(f"Backend API: http://localhost:8000")
print(f"API Docs: http://localhost:8000/docs")
print("=" * 60)
