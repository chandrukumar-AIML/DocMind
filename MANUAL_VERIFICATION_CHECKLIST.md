# DocuMind AI — Pre-Deploy Manual Verification Checklist
**Version:** 2.0.0 · **Date:** 2026-05-27
> Run this checklist with backend running (`VITE_DEMO_MODE=false`) before deploying.
> Every item was visually verified in demo mode; items marked ⚠ need live backend confirmation.

---

## Setup
- [ ] `VITE_DEMO_MODE=false` in `frontend/.env.local`
- [ ] Backend running on `http://localhost:8000` (FastAPI + PostgreSQL + Redis + ChromaDB)
- [ ] Frontend running on `http://localhost:5175` (`npm run dev` in `/frontend`)
- [ ] Open app in browser, confirm title: **DocMind AI — Intelligent Document Intelligence**

---

## 1. Authentication
- [ ] Login page loads when not authenticated
- [ ] Login with test credentials (see `project_context.md`)
- [ ] "Demo Workspace" appears in workspace selector after login
- [ ] Logout works and redirects to login

---

## 2. DOCS Tab — Upload & Library

### Upload Zone
- [ ] Drag-and-drop PDF onto upload zone → shows upload progress
- [ ] Click upload zone → file picker opens, supports PDF/TXT/DOCX/Image
- [ ] **Vision OCR toggle** — flip ON for image-based PDFs
- [ ] **Audio & Office uploader** — drag MP3/DOCX/XLSX
- [ ] **Multi-file uploader** — drop 2+ files simultaneously → "processed in parallel" toast

### Web URLs Watcher
- [ ] Type a URL in "https://… to watch" field + click `+` → URL added to watch list

### Library
- [ ] Uploaded docs appear in LIBRARY with: filename, file type badge, page count, chunk count, file size, confidence %
- [ ] **Search filter** — type partial filename → list filters in real time
- [ ] Click a doc row → expands with action icons: 🔲 chunks · 📊 export tables · ⬇ download · ↻ re-index · 🗑 delete
- [ ] Click **🔲 View chunks** → "Indexed Chunks" panel shows text snippets
- [ ] Click **↻ Re-index** → spinner appears, success toast
- [ ] Click **🗑 Delete** → confirm prompt → doc removed from list
- [ ] Active filter badge appears in topbar: "Filter · [filename]" with × to clear

---

## 3. Main Chat Interface

### RAG Mode ✅ (verified in demo)
- [ ] Switch to **RAG** mode (topbar badge shows "⚡ RAG")
- [ ] Click a sample chip (e.g. "What are the key findings?") → response streams in
- [ ] Response shows: formatted text with **bold** key terms
- [ ] **SOURCES panel** appears below response: filename · page · type · confidence %
- [ ] Click ▼ on a source chip → expands to show exact excerpt
- [ ] **Response time** shown (e.g. 1.34s)
- [ ] **👍 👎 feedback** buttons clickable
- [ ] **Follow-up chips** appear: 3 contextual suggestions
- [ ] Topbar gains: **↓ MD** · **↓ PDF** · **Clear** buttons after first message
- [ ] Click **↓ MD** → downloads conversation as Markdown
- [ ] Click **↓ PDF** → downloads conversation as PDF
- [ ] Click **Clear** → conversation clears, welcome screen returns

### Agent Mode ✅ (verified in demo)
- [ ] Click **Agent** → badge switches to "⚡ AGENT", placeholder updates
- [ ] Send "Extract all financial tables and summarize the key risks"
- [ ] Response renders **markdown table** with columns + row data
- [ ] Table includes bold **Total** summary row
- [ ] Sources + follow-up chips appear as expected

### Graph Mode ✅ (verified in demo)
- [ ] Click **Graph** → badge switches to "⚡ GRAPH"
- [ ] Send "Show relationships between entities in the documents"
- [ ] Response summarises entities **across all documents** (cross-doc synthesis)
- [ ] Each document referenced by **bold name** with key facts

### Document Filter
- [ ] With a doc selected in DOCS tab, topbar shows "Querying: [filename]"
- [ ] RAG query only searches that document (response sources show only that file)
- [ ] Click × on active filter badge → reverts to "All documents"

### Keyboard Shortcuts
- [ ] `Ctrl+K` → focuses chat input from anywhere
- [ ] `Enter` → sends message
- [ ] `Shift+Enter` → inserts newline in input

---

## 4. ANALYZE Tab

### Domain Analysis ⚠ (requires backend)
- [ ] Select a doc in DOCS tab (e.g. Service_Agreement_v3.docx)
- [ ] Switch to ANALYZE tab → doc name + "Analyze →" button appear
- [ ] **Legal** domain — click Analyze → shows clauses, risk flags, obligations
- [ ] **Medical** domain — select medical doc → shows diagnoses, drugs, ICD codes
- [ ] **Invoices** domain — shows vendor, line items, totals
- [ ] **Bills** domain — shows utility breakdown (no doc required, uses all docs)
- [ ] **Forms** domain — shows extracted fields
- [ ] **Sign** domain — shows detected signature zones
- [ ] **📥 Download Report** button appears after analysis → opens print dialog

### Tables & Charts Extractor ⚠ (requires backend)
- [ ] Click **⚡ Extract** with a doc selected → tables and charts rendered
- [ ] Table viewer shows column headers + rows
- [ ] Chart viewer shows bar/line chart

### Graph Query ⚠ (requires backend)
- [ ] Type "Which companies are involved in contracts?" in Graph Query textarea
- [ ] Select routing mode: **Auto** / **Hybrid** / **Graph (Neo4j)** / **Vector (ChromaDB)**
- [ ] Click **Run Graph Query** → entity relationship results appear

---

## 5. HISTORY Tab ✅ (verified in demo)
- [ ] Conversations list shows previous sessions with: first query text, message count, relative time
- [ ] Click a conversation → loads it in main chat area
- [ ] Click **+ New Chat** → clears chat, starts fresh session
- [ ] **Clear all** button → removes all history (confirm prompt)
- [ ] Notification badge on HISTORY tab clears after viewing

---

## 6. TRAIN Tab ⚠ (requires backend)

### Training Dataset
- [ ] Click **Generate Dataset** → triggers triplet generation from indexed docs
- [ ] Progress indicator appears, download link when complete

### Re-embed Workspace
- [ ] Click **Re-embed All Docs** → re-processes all documents with current embedding model
- [ ] Progress toast + completion notification

### Pull Ollama Model
- [ ] Type "llama3.2:7b" in model name field → click **Pull**
- [ ] Download progress shown

### RAG Evaluation
- [ ] **Single Eval** tab: enter question + generated answer → click Evaluate → scores appear (faithfulness, relevancy, etc.)
- [ ] **Pipeline** tab: batch evaluation across multiple Q&A pairs

---

## 7. STATS Tab ⚠ (monitoring requires backend)

### Monitoring Dashboard
- [ ] Stat cards show live values: Documents · Chunks · Messages (⚠ from backend, not demo)
- [ ] Time range selector (24h / 7d / 30d) updates charts
- [ ] Click **↻ Refresh** → reloads metrics
- [ ] Click **Run pipeline** → triggers evaluation pipeline

### User & Workspace Info
- [ ] Shows logged-in user email + workspace name

### Document Health
- [ ] Click **Find Duplicates** → lists duplicate documents ⚠

### Audit Trail
- [ ] Click **↓ Download Audit CSV** → downloads CSV of all actions ⚠

### API Keys
- [ ] Enter key name (e.g. "my-app") → click **Generate** → API key shown once
- [ ] Generated key appears in list with name + created date
- [ ] Delete button removes key

---

## 8. FEATURES Tab

### Webhooks ✅
- [ ] Click **+ Register** → form opens: URL, event type, secret
- [ ] Fill form + submit → webhook appears in list
- [ ] Webhook list shows: URL, event, status
- [ ] Delete webhook → removed from list ⚠

### Compare ✅ (UI verified in demo)
- [ ] Select 2+ documents (checkboxes)
- [ ] Choose mode: **Similarity** / **Difference** / **Pattern** / **Summary**
- [ ] Click **Compare** → comparison job starts ⚠
- [ ] Result shows: similarity/divergence score, themed tabs (Themes/Entities/Differences/Patterns/Insights)
- [ ] Recent Jobs list shows past comparisons with status chips

### Workflows ✅
- [ ] Click **+ New Rule** → form opens: trigger event, actions ⚠
- [ ] Create rule "on document upload → send webhook" ⚠
- [ ] Workflow appears in list with toggle to enable/disable ⚠

### Annotate ✅ (UI verified in demo)
- [ ] Select annotation type: highlight / comment / tag / risk_flag / approval
- [ ] Enter page number (optional)
- [ ] Type annotation text → click **Add**
- [ ] Annotation appears in list with type badge + page number
- [ ] **● Live sync** — open same doc in 2 browser tabs → annotations sync in real-time ⚠
- [ ] ✓ Resolve button marks annotation resolved (greyed out)
- [ ] × Delete button removes annotation
- [ ] Filter chips narrow list by type

### Templates ✅
- [ ] **Built-in Templates** section shows preset extraction templates
- [ ] Click **+ Custom** → template name + field definitions form ⚠
- [ ] Apply template to document → fields extracted ⚠

### E-Sign ✅
- [ ] Click **+ Request** → form: recipient email + document selection ⚠
- [ ] Signature request appears in list with status (pending/signed/expired) ⚠

### Compliance ✅ (selected file auto-populates)
- [ ] With doc selected, "Document: [filename]" shown automatically
- [ ] **Select Regulations** — choose: GDPR / HIPAA / SOX / PCI-DSS etc. ⚠
- [ ] Click **Run Compliance Check** → results appear in Result tab ⚠
- [ ] Result tab: compliance score + flagged clauses + recommendations ⚠
- [ ] History tab: past compliance checks with timestamps ⚠

### Admin ✅ (access gated)
- [ ] With superadmin account: workspace management, user roles, system config visible
- [ ] Demo user correctly sees "Superadmin access required" gate

### Onboard ✅
- [ ] **Invites tab**: type colleague name + select role (viewer/editor/admin) + click **Invite** ⚠
- [ ] **API Keys tab**: shows workspace API keys + generate new key ⚠

### Regional ✅
- [ ] **Query tab**: type Tanglish/Tamil text (e.g. "aadayam kanam theriyuma") → click **Process** → normalized English output ⚠
- [ ] **Entities tab**: extract Indian entity types (person names, locations, dates in regional formats) ⚠
- [ ] **Validate tab**: validate Indian-format dates/phone numbers/PAN/Aadhaar ⚠
- [ ] **Numerals tab**: convert Indian number formats (lakhs/crores) ⚠

---

## 9. UI / UX

### Theme Toggle ✅ (verified in demo)
- [ ] Click ☀️ → switches to **light theme** (pale lavender bg, dark text)
- [ ] Click 🌙 → switches back to **dark nebula theme**
- [ ] Theme persists on page reload (saved to localStorage)

### Sidebar
- [ ] Click hamburger ≡ → sidebar collapses, main area expands
- [ ] Click ≡ again → sidebar reopens
- [ ] All 6 tabs accessible: DOCS / ANALYZE / HISTORY / TRAIN / STATS / FEATURES

### Topbar Compare Button
- [ ] Click **⇔ Compare** → opens ComparisonPanel in sidebar with doc checkboxes

### PDF Side Panel ⚠
- [ ] Click **↓ PDF** button in topbar (when conversation has citations)
- [ ] PDF viewer slides in on the right showing source document
- [ ] Cited pages highlighted
- [ ] Close × button dismisses viewer

### Version Timeline ✅
- [ ] Select a document → Version Timeline section appears below library
- [ ] Shows version history with timestamps + diff viewer ⚠

---

## 10. Error States & Edge Cases
- [ ] Upload unsupported file type → friendly error toast
- [ ] Send query with no documents uploaded → helpful "Upload a document first" message
- [ ] Network disconnect during streaming → graceful error, not blank screen
- [ ] Very long document name → truncated with ellipsis, full name in tooltip
- [ ] Concurrent uploads → all show individual progress bars

---

## Pre-Deploy Final Checks
- [ ] `npm run build` completes with no errors (5 eslint warnings OK, 0 errors)
- [ ] Bundle sizes within limits (no chunk > 800KB warning)
- [ ] `VITE_DEMO_MODE=false` confirmed before building
- [ ] Backend `.env` has all required keys: `OPENAI_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `NEO4J_URI`
- [ ] CORS origins in backend include production frontend URL
- [ ] Vercel environment variables set (see `DEPLOY.md`)

---

## Items That Require Live Backend (Summary)
All items marked ⚠ above — the following features showed "Network Error" / "Failed to fetch" in demo mode, which is **expected**. They must be verified with the full stack running:

| Feature | Why demo can't test it |
|---------|------------------------|
| Domain Analysis (Legal/Medical/etc.) | `apiClient` call to `/api/v1/domains/*` |
| Graph Query (Run) | `apiClient` call to `/api/v1/graph/query` |
| Tables & Charts Extractor | `apiClient` call to `/api/v1/tables/extract` |
| Webhooks (create/delete) | `apiClient` to `/api/v1/webhooks` |
| Comparison jobs | `apiClient` to `/api/v1/compare` |
| Compliance Check | `apiClient` to `/api/v1/compliance` |
| Monitoring stats | `apiClient` to `/api/v1/monitoring` |
| Audit CSV download | `apiClient` to `/api/v1/audit` |
| RAG Evaluation | `apiClient` to `/api/v1/evaluation` |
| E-Sign requests | `apiClient` to `/api/v1/esign` |
| Regional NLP | `apiClient` to `/api/v1/regional` |
| Annotation real-time sync | WebSocket to `ws://localhost:8000/api/v1/annotations/ws/` |

---

## ✅ Features Verified in Demo Mode (no backend needed)

| Feature | Status | Notes |
|---------|--------|-------|
| RAG Chat (3 modes) | ✅ PASS | Streams, sources, follow-ups, 1.3s avg |
| Document Library UI | ✅ PASS | 4 demo docs, all metadata correct |
| Upload zones (3 types) | ✅ PASS | All render correctly |
| History tab | ✅ PASS | Session stored, + New Chat works |
| All 10 FEATURES chips | ✅ PASS | All render, all panels load |
| Light / Dark theme toggle | ✅ PASS | Persists via localStorage |
| Sidebar tabs (6) | ✅ PASS | All navigate correctly |
| ↓ MD / ↓ PDF export buttons | ✅ PASS | Appear after first message |
| Topbar document filter | ✅ PASS | Badge shows selected file |
| Keyboard shortcut Ctrl+K | ✅ PASS | Focuses chat input |
| Admin access gate | ✅ PASS | Correctly blocked for demo user |
| Compliance auto-populates doc | ✅ PASS | Selected file shown automatically |

---

*Generated by automated browser walkthrough — 2026-05-27*
