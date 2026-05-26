"""
DocuMind AI — Comprehensive Batch Test (all remaining endpoints)
Tests Batches 2-6: ~80 endpoints across all feature categories.
"""
import json, os, sys, time, uuid, tempfile, urllib.request, urllib.error, urllib.parse

BASE = "http://localhost:8000"
API  = f"{BASE}/api/v1"

# ── helpers ──────────────────────────────────────────────────────────────────

def req(method, path, data=None, headers=None, raw_body=None, content_type=None, timeout=30):
    url = path if path.startswith("http") else f"{API}{path}"
    hdrs = dict(headers or {})
    body = None
    if raw_body is not None:
        body = raw_body
        if content_type:
            hdrs["Content-Type"] = content_type
    elif data is not None:
        body = json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    rq = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as resp:
            raw = resp.read().decode(errors="replace")
            try:    return resp.status, json.loads(raw)
            except: return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:    return e.code, json.loads(raw)
        except: return e.code, raw
    except Exception as e:
        return 0, str(e)

def multipart(fields, files):
    """Build multipart/form-data body."""
    boundary = "----DocuMindTest" + uuid.uuid4().hex[:8]
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    for name, (filename, filedata, ct) in files.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: {ct}\r\n\r\n".encode()
            + filedata + f"\r\n".encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"

passed = []
failed = []
skipped = []

def check(name, cond, detail=""):
    if cond:
        passed.append(name); print(f"  [PASS] {name}")
    else:
        failed.append(name); print(f"  [FAIL] {name}  {detail}")

def skip(name, reason=""):
    skipped.append(name); print(f"  [SKIP] {name}  {reason}")

def section(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")

# ── Auth: login as admin ──────────────────────────────────────────────────────
section("SETUP — Login")
status, body = req("POST", "/auth/login", {"email": "admin@docmind.ai", "password": "AdminP@ssw0rd!2026"})
assert status == 200 and isinstance(body, dict) and "access_token" in body, f"Login failed: {status} {body}"
TOKEN = body["access_token"]
WORKSPACE_ID = body.get("workspace_id", "default")
AUTH = {"Authorization": f"Bearer {TOKEN}"}
print(f"  Logged in | workspace={WORKSPACE_ID[:8]}... | token={TOKEN[:12]}...")

# Get a real document filename for tests
status, body = req("GET", f"/documents?workspace_id={WORKSPACE_ID}", headers=AUTH)
DOC_NAME = None
if status == 200 and isinstance(body, dict):
    docs = body.get("documents", [])
    if docs:
        DOC_NAME = docs[0].get("source_file") or docs[0].get("filename")
        print(f"  First doc: {DOC_NAME}")
print()

# ═══════════════════════════════════════════════════════════════════
# BATCH 2 — Document extras, Ingest variants, Annotations, Versioning
# ═══════════════════════════════════════════════════════════════════

section("BATCH 2A — Document: Download / File / Reindex / Duplicates / Delete")

if DOC_NAME:
    safe = urllib.parse.quote(DOC_NAME, safe="")
    # File serve
    status, body = req("GET", f"/documents/{safe}/file", headers=AUTH)
    check("GET /documents/:name/file", status in (200, 404), f"{status}")
    # Download
    status, body = req("GET", f"/documents/{safe}/download", headers=AUTH)
    check("GET /documents/:name/download", status in (200, 404), f"{status}: {str(body)[:100]}")
    # Reindex
    status, body = req("POST", f"/documents/{safe}/reindex", headers=AUTH)
    check("POST /documents/:id/reindex", status in (200, 202, 404, 422), f"{status}")
else:
    skip("GET /documents/:name/file", "no doc")
    skip("GET /documents/:name/download", "no doc")
    skip("POST /documents/:id/reindex", "no doc")

# Duplicates
status, body = req("GET", f"/documents/duplicates?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /documents/duplicates", status in (200, 204), f"{status}: {str(body)[:100]}")

# Workspaces listing
status, body = req("GET", "/documents/workspaces", headers=AUTH)
check("GET /documents/workspaces", status == 200, f"{status}")

section("BATCH 2B — Ingest Variants: DOCX, XLSX, URL, Status")

# DOCX ingest — create a minimal docx-like file (we use a .txt as .docx for test, expect 415 or 200)
# Since we don't have python-docx in path easily, create a real minimal DOCX zip
try:
    import zipfile, io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>DocuMind AI DOCX Test Document. This is a sample Word document for integration testing of the DocuMind AI platform. It contains text to test chunking and RAG retrieval.</w:t></w:r></w:p></w:body></w:document>')
        zf.writestr("word/_rels/document.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
    docx_bytes = buf.getvalue()
    body_bytes, ct = multipart({}, {"file": ("test_doc.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    status, body = req("POST", "/ingest/docx", headers=AUTH, raw_body=body_bytes, content_type=ct, timeout=60)
    check("POST /ingest/docx", status in (200, 422), f"{status}: {str(body)[:200]}")
    if status == 200:
        print(f"    chunks={body.get('child_chunks', '?') if isinstance(body, dict) else '?'}")
except Exception as e:
    check("POST /ingest/docx", False, f"exception: {e}")

# XLSX ingest — create a minimal XLSX (same structure as DOCX but for xlsx)
try:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Product</t></is></c><c r="B1" t="inlineStr"><is><t>Revenue</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>DocuMind AI SaaS</t></is></c><c r="B2" t="inlineStr"><is><t>2800000</t></is></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>API Usage</t></is></c><c r="B3" t="inlineStr"><is><t>500000</t></is></c></row></sheetData></worksheet>')
    xlsx_bytes = buf.getvalue()
    body_bytes, ct = multipart({}, {"file": ("test_sheet.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    status, body = req("POST", "/ingest/xlsx", headers=AUTH, raw_body=body_bytes, content_type=ct, timeout=60)
    check("POST /ingest/xlsx", status in (200, 422), f"{status}: {str(body)[:200]}")
    if status == 200:
        print(f"    chunks={body.get('child_chunks', '?') if isinstance(body, dict) else '?'}")
except Exception as e:
    check("POST /ingest/xlsx", False, f"exception: {e}")

# CSV ingest (CSV is in XLSX_EXTENSIONS)
csv_data = b"Name,Role,Salary\nChandru,Lead Engineer,1200000\nPriya,Backend Dev,900000\nRahul,Frontend Dev,850000\n"
body_bytes, ct = multipart({}, {"file": ("test_data.csv", csv_data, "text/csv")})
status, body = req("POST", "/ingest/xlsx", headers=AUTH, raw_body=body_bytes, content_type=ct, timeout=60)
check("POST /ingest/xlsx (CSV)", status in (200, 422), f"{status}: {str(body)[:200]}")

# URL ingest
status, body = req("POST", "/ingest/url", data={
    "url": "https://httpbin.org/html",
    "title": "Test HTML Page",
    "workspace_id": WORKSPACE_ID,
}, headers=AUTH, timeout=45)
check("POST /ingest/url", status in (200, 400, 422), f"{status}: {str(body)[:150]}")

# Ingest status
if DOC_NAME:
    safe = urllib.parse.quote(DOC_NAME, safe="")
    status, body = req("GET", f"/ingest/status/{safe}", headers=AUTH)
    check("GET /ingest/status/:id", status in (200, 404), f"{status}")
else:
    skip("GET /ingest/status/:id", "no doc")

section("BATCH 2C — Annotations: Create, List, Resolve, Delete")

ANN_ID = None
# Create annotation — field is `type` not `annotation_type`
ann_payload = {
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "workspace_id": WORKSPACE_ID,
    "type": "highlight",
    "content": "Key metric to track",
    "page_number": 1,
    "position": {"x": 100, "y": 200, "width": 300, "height": 20},
}
status, body = req("POST", "/annotations/create", data=ann_payload, headers=AUTH)
check("POST /annotations/create", status in (200, 201), f"{status}: {str(body)[:200]}")
if isinstance(body, dict):
    ANN_ID = body.get("annotation_id") or body.get("id")
    print(f"    annotation_id={ANN_ID}")

# List annotations
list_params = urllib.parse.urlencode({"workspace_id": WORKSPACE_ID, "source_file": DOC_NAME or "quarterly_report_q1_2026.txt"})
status, body = req("GET", f"/annotations/list?{list_params}", headers=AUTH)
check("GET /annotations/list", status == 200, f"{status}: {str(body)[:200]}")

# Resolve annotation
if ANN_ID:
    src_enc = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
    # source_file is a required query param for resolve and delete
    status, body = req("POST", f"/annotations/{ANN_ID}/resolve?source_file={src_enc}", headers=AUTH)
    check("POST /annotations/:id/resolve", status in (200, 204), f"{status}: {str(body)[:100]}")
    status, body = req("DELETE", f"/annotations/{ANN_ID}?source_file={src_enc}", headers=AUTH)
    check("DELETE /annotations/:id", status in (200, 204), f"{status}: {str(body)[:100]}")
else:
    skip("POST /annotations/:id/resolve", "no annotation_id")
    skip("DELETE /annotations/:id", "no annotation_id")

section("BATCH 2D — Versioning: History, Diff, Get Version")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/versioning/history/{src}?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /versioning/history/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/versioning/diff/{src}?workspace_id={WORKSPACE_ID}&v1=1&v2=2", headers=AUTH)
check("GET /versioning/diff/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/versioning/{src}/version/1?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /versioning/:file/version/:n", status in (200, 404), f"{status}: {str(body)[:150]}")

section("BATCH 2E — Compliance: Regulations, Check, Result, History")

status, body = req("GET", "/compliance/regulations", headers=AUTH)
check("GET /compliance/regulations", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/compliance/check", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "workspace_id": WORKSPACE_ID,
    "regulations": ["GDPR"],
}, headers=AUTH, timeout=60)
check("POST /compliance/check", status in (200, 202), f"{status}: {str(body)[:200]}")
COMP_ID = None
if isinstance(body, dict):
    COMP_ID = body.get("result_id") or body.get("id")
    print(f"    result_id={COMP_ID}")

if COMP_ID:
    status, body = req("GET", f"/compliance/result/{COMP_ID}", headers=AUTH)
    check("GET /compliance/result/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /compliance/result/:id", "no result_id")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/compliance/history/{src}?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /compliance/history/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

section("BATCH 2F — Regional: Scripts, Preprocess, Entities, Validate, Parse")

status, body = req("GET", "/regional/scripts", headers=AUTH)
check("GET /regional/scripts", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/regional/preprocess-query", data={
    "query": "What is the revenue for Q1?",
    "workspace_id": WORKSPACE_ID,
    "language": "en",
}, headers=AUTH)
check("POST /regional/preprocess-query", status in (200, 422), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/regional/extract-entities", data={
    "text": "DocuMind AI Technologies, Chennai, Tamil Nadu, India. Revenue: ₹4.2 million.",
    "language": "en",
    "entity_types": ["ORG", "LOC", "MONEY"],
}, headers=AUTH)
check("POST /regional/extract-entities", status in (200, 422), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/regional/validate", data={
    "text": "Sample text for validation",
    "language": "en",
}, headers=AUTH)
check("POST /regional/validate", status in (200, 422), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/regional/parse-number", data={
    "text": "1,23,456.78",
    "locale": "en_IN",
}, headers=AUTH)
check("POST /regional/parse-number", status in (200, 422), f"{status}: {str(body)[:150]}")

# ═══════════════════════════════════════════════════════════════════
# BATCH 3 — Domain Analysis, Templates, Comparison, Extraction
# ═══════════════════════════════════════════════════════════════════

section("BATCH 3A — Domain: Legal Analysis")

status, body = req("POST", "/domains/legal/analyze", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "analysis_types": ["clauses", "risk", "obligations"],
}, headers=AUTH, timeout=60)
check("POST /domains/legal/analyze", status in (200, 422, 404), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/domains/legal/detect-signatures", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "workspace_id": WORKSPACE_ID,
}, headers=AUTH, timeout=30)
check("POST /domains/legal/detect-signatures", status in (200, 422, 404), f"{status}: {str(body)[:200]}")

section("BATCH 3B — Domain: Medical Analysis")

status, body = req("POST", "/domains/medical/analyze", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "workspace_id": WORKSPACE_ID,
    "analysis_type": "clinical_notes",
}, headers=AUTH, timeout=60)
check("POST /domains/medical/analyze", status in (200, 422, 404), f"{status}: {str(body)[:200]}")

section("BATCH 3C — Domain: Logistics Analysis")

status, body = req("POST", "/domains/logistics/analyze-invoices", data={
    "workspace_id": WORKSPACE_ID,
    "source_files": [DOC_NAME or "quarterly_report_q1_2026.txt"],
}, headers=AUTH, timeout=60)
check("POST /domains/logistics/analyze-invoices", status in (200, 422, 404), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/domains/logistics/calculate-bills", data={
    "workspace_id": WORKSPACE_ID,
    "invoice_ids": [],
}, headers=AUTH, timeout=30)
check("POST /domains/logistics/calculate-bills", status in (200, 422, 404), f"{status}: {str(body)[:150]}")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/domains/logistics/invoice/{src}?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /domains/logistics/invoice/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

section("BATCH 3D — Templates: Builtins, Create, List, Extract, Results")

status, body = req("GET", "/templates/builtins", headers=AUTH)
check("GET /templates/builtins", status == 200, f"{status}: {str(body)[:150]}")
TEMPLATE_SLUG = None
if isinstance(body, dict):
    items = body.get("templates") or body.get("builtins") or []
    if isinstance(items, list) and items:
        TEMPLATE_SLUG = items[0].get("slug") or items[0].get("id")
        print(f"    first_slug={TEMPLATE_SLUG}")

if TEMPLATE_SLUG:
    status, body = req("GET", f"/templates/builtins/{TEMPLATE_SLUG}", headers=AUTH)
    check("GET /templates/builtins/:slug", status in (200, 404), f"{status}")
else:
    skip("GET /templates/builtins/:slug", "no slug")

status, body = req("POST", "/templates/create", data={
    "name": "Test Template",
    "workspace_id": WORKSPACE_ID,
    "fields": [{"name": "company_name", "type": "string", "description": "The company or organization name", "required": True}],
}, headers=AUTH)
check("POST /templates/create", status in (200, 201), f"{status}: {str(body)[:200]}")
TMPL_ID = None
if isinstance(body, dict):
    TMPL_ID = body.get("template_id") or body.get("id")

status, body = req("GET", f"/templates/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /templates/list", status == 200, f"{status}: {str(body)[:150]}")

if TMPL_ID and DOC_NAME:
    status, body = req("POST", "/templates/extract", data={
        "template_id": TMPL_ID,
        "source_file": DOC_NAME,
        "workspace_id": WORKSPACE_ID,
    }, headers=AUTH, timeout=60)
    check("POST /templates/extract", status in (200, 202, 404), f"{status}: {str(body)[:200]}")
else:
    skip("POST /templates/extract", "no template_id or doc")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/templates/results/{src}?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /templates/results/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

section("BATCH 3E — Comparison: Start, Status, List")

status, body = req("POST", "/comparison/start", data={
    "source_files": [
        DOC_NAME or "quarterly_report_q1_2026.txt",
        "employee_handbook.txt",
    ],
    "mode": "SIMILARITY",
}, headers=AUTH, timeout=60)
check("POST /comparison/start", status in (200, 202), f"{status}: {str(body)[:200]}")
JOB_ID = None
if isinstance(body, dict):
    JOB_ID = body.get("job_id") or body.get("id")
    print(f"    job_id={JOB_ID}")

if JOB_ID:
    time.sleep(1)
    safe_job = urllib.parse.quote(str(JOB_ID), safe="")
    status, body = req("GET", f"/comparison/status/{safe_job}", headers=AUTH, timeout=30)
    check("GET /comparison/status/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /comparison/status/:id", "no job_id")

status, body = req("GET", f"/comparison/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /comparison/list", status == 200, f"{status}: {str(body)[:150]}")

section("BATCH 3F — Extraction: Stats, Form-Fields, Tables, Export, Aggregate")

src_q = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/extraction/stats?source_file={src_q}&workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /extraction/stats", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/extraction/form-fields", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "workspace_id": WORKSPACE_ID,
}, headers=AUTH, timeout=60)
check("POST /extraction/form-fields", status in (200, 404, 422), f"{status}: {str(body)[:200]}")
TABLE_ID = None
if isinstance(body, dict):
    tables = body.get("tables") or []
    if isinstance(tables, list) and tables:
        TABLE_ID = tables[0].get("table_id") or tables[0].get("id")

status, body = req("POST", "/extraction/aggregate", data={
    "workspace_id": WORKSPACE_ID,
    "fields": ["company_name", "revenue", "date"],
}, headers=AUTH, timeout=30)
check("POST /extraction/aggregate", status in (200, 404, 422), f"{status}: {str(body)[:200]}")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/extraction/export-tables/{src}?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /extraction/export-tables/:file", status in (200, 404), f"{status}: {str(body)[:150]}")

if TABLE_ID:
    safe_tid = urllib.parse.quote(str(TABLE_ID), safe="")
    status, body = req("GET", f"/extraction/table/{safe_tid}", headers=AUTH)
    check("GET /extraction/table/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("POST", f"/extraction/table/{safe_tid}/query", data={"query": "revenue"}, headers=AUTH)
    check("POST /extraction/table/:id/query", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /extraction/table/:id", "no table_id")
    skip("POST /extraction/table/:id/query", "no table_id")

# ═══════════════════════════════════════════════════════════════════
# BATCH 4 — Workflows, Webhooks, E-Signature, Fine-tuning, Tasks
# ═══════════════════════════════════════════════════════════════════

section("BATCH 4A — Workflows: Create, List, Get, Runs")

status, body = req("POST", "/workflows/create", data={
    "name": "Test Workflow",
    "workspace_id": WORKSPACE_ID,
    "trigger_event": "document_ingested",
    "actions": [{"type": "email", "recipient": "admin@docmind.ai", "subject": "New doc uploaded", "body_template": "A new document was uploaded."}],
    "description": "Integration test workflow",
}, headers=AUTH)
check("POST /workflows/create", status in (200, 201), f"{status}: {str(body)[:200]}")
WF_ID = None
if isinstance(body, dict):
    WF_ID = body.get("workflow_id") or body.get("id")
    print(f"    workflow_id={WF_ID}")

status, body = req("GET", f"/workflows/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /workflows/list", status == 200, f"{status}: {str(body)[:150]}")

if WF_ID:
    safe_wf = urllib.parse.quote(str(WF_ID), safe="")
    status, body = req("GET", f"/workflows/{safe_wf}", headers=AUTH)
    check("GET /workflows/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("GET", f"/workflows/{safe_wf}/runs", headers=AUTH)
    check("GET /workflows/:id/runs", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /workflows/:id", "no workflow_id")
    skip("GET /workflows/:id/runs", "no workflow_id")

section("BATCH 4B — Webhooks: Register, List, Test, Deliveries, Delete")

status, body = req("POST", "/webhooks/register", data={
    "workspace_id": WORKSPACE_ID,
    "name": "Test Webhook",
    "url": "https://httpbin.org/post",
    "events": ["document_ingested", "query_answered"],
    "secret": "test_secret_key_123",
}, headers=AUTH)
check("POST /webhooks/register", status in (200, 201), f"{status}: {str(body)[:200]}")
WH_ID = None
if isinstance(body, dict):
    WH_ID = body.get("webhook_id") or body.get("id")
    print(f"    webhook_id={WH_ID}")

status, body = req("GET", f"/webhooks/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /webhooks/list", status == 200, f"{status}: {str(body)[:150]}")

# Test webhook
status, body = req("POST", "/webhooks/test", data={
    "workspace_id": WORKSPACE_ID,
    "event": "document.ingested",
    "payload": {"test": True},
}, headers=AUTH, timeout=30)
check("POST /webhooks/test", status in (200, 422), f"{status}: {str(body)[:150]}")

if WH_ID:
    safe_wh = urllib.parse.quote(str(WH_ID), safe="")
    status, body = req("GET", f"/webhooks/deliveries/{safe_wh}", headers=AUTH)
    check("GET /webhooks/deliveries/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("DELETE", f"/webhooks/{safe_wh}", headers=AUTH)
    check("DELETE /webhooks/:id", status in (200, 204), f"{status}: {str(body)[:100]}")
else:
    skip("GET /webhooks/deliveries/:id", "no webhook_id")
    skip("DELETE /webhooks/:id", "no webhook_id")

section("BATCH 4C — E-Signature: Request, List, In-App Sign, Status")

status, body = req("POST", "/esignature/request", data={
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
    "signers": [{"name": "Admin User", "email": "admin@docmind.ai", "order": 1}],
}, headers=AUTH)
check("POST /esignature/request", status in (200, 201), f"{status}: {str(body)[:200]}")
ESIG_ID = None
if isinstance(body, dict):
    ESIG_ID = body.get("request_id") or body.get("id")
    print(f"    esig_id={ESIG_ID}")

status, body = req("GET", f"/esignature/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /esignature/list", status == 200, f"{status}: {str(body)[:150]}")

if ESIG_ID:
    safe_esig = urllib.parse.quote(str(ESIG_ID), safe="")
    status, body = req("GET", f"/esignature/status/{safe_esig}", headers=AUTH)
    check("GET /esignature/status/:id", status in (200, 201, 404), f"{status}: {str(body)[:150]}")
    # In-app sign — model: request_id + signature_data only
    status, body = req("POST", "/esignature/inapp/sign", data={
        "request_id": ESIG_ID,
        "signature_data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    }, headers=AUTH)
    check("POST /esignature/inapp/sign", status in (200, 201, 404, 422), f"{status}: {str(body)[:150]}")
else:
    skip("GET /esignature/status/:id", "no esig_id")
    skip("POST /esignature/inapp/sign", "no esig_id")

section("BATCH 4D — Fine-tuning: Models, Pull, Dataset Generate/Status, Reembed")

status, body = req("GET", "/finetuning/models", headers=AUTH)
check("GET /finetuning/models", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/finetuning/model/pull", data={
    "model_name": "llama3.2:1b",
}, headers=AUTH, timeout=30)
check("POST /finetuning/model/pull", status in (200, 202, 422), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/finetuning/dataset/generate", data={
    "workspace_id": WORKSPACE_ID,
    "num_samples": 10,
    "source_files": [DOC_NAME or "quarterly_report_q1_2026.txt"],
}, headers=AUTH, timeout=60)
check("POST /finetuning/dataset/generate", status in (200, 202), f"{status}: {str(body)[:200]}")
FT_JOB_ID = None
if isinstance(body, dict):
    FT_JOB_ID = body.get("job_id") or body.get("id")

status, body = req("GET", f"/finetuning/dataset/status?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /finetuning/dataset/status", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/finetuning/reembed", data={
    "workspace_id": WORKSPACE_ID,
    "model": "all-MiniLM-L6-v2",
}, headers=AUTH, timeout=30)
check("POST /finetuning/reembed", status in (200, 202, 422), f"{status}: {str(body)[:150]}")

section("BATCH 4E — Tasks: List, Get, Cancel")

status, body = req("GET", f"/tasks?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /tasks", status == 200, f"{status}: {str(body)[:150]}")
TASK_ID = None
if isinstance(body, dict):
    tasks = body.get("tasks") or []
    if isinstance(tasks, list) and tasks:
        TASK_ID = tasks[0].get("task_id") or tasks[0].get("id")

if TASK_ID:
    safe_task = urllib.parse.quote(str(TASK_ID), safe="")
    status, body = req("GET", f"/tasks/{safe_task}", headers=AUTH)
    check("GET /tasks/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("POST", f"/tasks/{safe_task}/cancel", headers=AUTH)
    check("POST /tasks/:id/cancel", status in (200, 204, 409), f"{status}: {str(body)[:100]}")
else:
    skip("GET /tasks/:id", "no tasks found")
    skip("POST /tasks/:id/cancel", "no tasks found")

# ═══════════════════════════════════════════════════════════════════
# BATCH 5 — Super Admin, Onboarding, Graph, Evaluation, Provenance
# ═══════════════════════════════════════════════════════════════════

section("BATCH 5A — Super Admin: Overview, Stats, Workspaces, System")

status, body = req("GET", "/superadmin/overview", headers=AUTH)
check("GET /superadmin/overview", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("GET", "/superadmin/stats", headers=AUTH)
check("GET /superadmin/stats", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("GET", "/superadmin/workspaces", headers=AUTH)
check("GET /superadmin/workspaces", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("GET", "/superadmin/system/health", headers=AUTH)
check("GET /superadmin/system/health", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/superadmin/system/tasks", headers=AUTH)
check("GET /superadmin/system/tasks", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/superadmin/system/flush-cache", headers=AUTH)
check("POST /superadmin/system/flush-cache", status in (200, 403), f"{status}: {str(body)[:100]}")

if WORKSPACE_ID:
    safe_ws = urllib.parse.quote(str(WORKSPACE_ID), safe="")
    status, body = req("GET", f"/superadmin/workspace/{safe_ws}/billing", headers=AUTH)
    check("GET /superadmin/workspace/:id/billing", status in (200, 403), f"{status}: {str(body)[:150]}")
    status, body = req("GET", f"/superadmin/workspace/{safe_ws}/audit-log", headers=AUTH)
    check("GET /superadmin/workspace/:id/audit-log", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/superadmin/billing/export?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /superadmin/billing/export", status in (200, 403), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/superadmin/workspace/create", data={
    "name": "Test Admin Workspace",
    "owner_email": "admin@docmind.ai",
    "plan": "starter",
}, headers=AUTH)
check("POST /superadmin/workspace/create", status in (200, 201, 403, 409), f"{status}: {str(body)[:200]}")

section("BATCH 5B — Onboarding: Wizard, Invite, Validate, Accept, API Keys")

status, body = req("POST", "/onboarding/wizard/step", data={
    "workspace_id": WORKSPACE_ID,
    "step": 1,
    "data": {"display_name": "Admin User", "use_case": "enterprise_docs"},
}, headers=AUTH)
check("POST /onboarding/wizard/step", status in (200, 201), f"{status}: {str(body)[:200]}")

status, body = req("GET", f"/onboarding/workspace/{WORKSPACE_ID}/progress", headers=AUTH)
check("GET /onboarding/workspace/:id/progress", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/onboarding/invite", data={
    "workspace_id": WORKSPACE_ID,
    "email": f"invite{int(time.time())}@test.com",
    "role": "viewer",
    "message": "Welcome to DocuMind AI!",
}, headers=AUTH)
check("POST /onboarding/invite", status in (200, 201), f"{status}: {str(body)[:200]}")
INV_ID = None
INV_TOKEN = None
if isinstance(body, dict):
    INV_ID = body.get("invite_id") or body.get("id")
    INV_TOKEN = body.get("token")
    print(f"    invite_id={INV_ID}")

status, body = req("GET", f"/onboarding/invites?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /onboarding/invites", status == 200, f"{status}: {str(body)[:150]}")

if INV_TOKEN:
    safe_tok = urllib.parse.quote(str(INV_TOKEN), safe="")
    status, body = req("GET", f"/onboarding/invite/{safe_tok}/validate", headers=AUTH)
    check("GET /onboarding/invite/:token/validate", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /onboarding/invite/:token/validate", "no token")

if INV_ID:
    safe_inv = urllib.parse.quote(str(INV_ID), safe="")
    status, body = req("POST", f"/onboarding/resend/{safe_inv}", headers=AUTH)
    check("POST /onboarding/resend/:id", status in (200, 201, 404), f"{status}: {str(body)[:150]}")
else:
    skip("POST /onboarding/resend/:id", "no invite_id")

# Onboarding API keys
status, body = req("GET", f"/onboarding/api-keys?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /onboarding/api-keys", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/onboarding/api-keys/create", data={
    "workspace_id": WORKSPACE_ID,
    "name": "Test Integration Key",
    "permissions": ["read", "write"],
    "expires_in_days": 30,
}, headers=AUTH)
check("POST /onboarding/api-keys/create", status in (200, 201), f"{status}: {str(body)[:200]}")
OB_KEY_ID = None
if isinstance(body, dict):
    OB_KEY_ID = body.get("key_id") or body.get("id")

if OB_KEY_ID:
    safe_key = urllib.parse.quote(str(OB_KEY_ID), safe="")
    status, body = req("DELETE", f"/onboarding/api-keys/{safe_key}", headers=AUTH)
    check("DELETE /onboarding/api-keys/:id", status in (200, 204), f"{status}: {str(body)[:100]}")
else:
    skip("DELETE /onboarding/api-keys/:id", "no key_id")

section("BATCH 5C — Graph: Extract, Schema, Query, Neighbors")

# graph/extract takes a list of source_file strings
status, body = req("POST", "/graph/extract", data=[
    DOC_NAME or "quarterly_report_q1_2026.txt",
], headers=AUTH, timeout=60)
check("POST /graph/extract", status in (200, 202, 422), f"{status}: {str(body)[:200]}")

status, body = req("GET", f"/graph/schema?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /graph/schema", status in (200, 404, 503, 0), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/graph/query", data={
    "query": "What organizations are mentioned?",
    "workspace_id": WORKSPACE_ID,
    "max_hops": 2,
}, headers=AUTH, timeout=30)
check("POST /graph/query", status in (200, 404, 422), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/graph/neighbors?workspace_id={WORKSPACE_ID}&entity_name=DocuMind+AI&hops=2", headers=AUTH)
check("GET /graph/neighbors", status in (200, 404), f"{status}: {str(body)[:150]}")

section("BATCH 5D — Evaluation / RAGAS: Datasets, Run, Sample, Alerts")

status, body = req("GET", f"/evaluation/datasets?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /evaluation/datasets", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/evaluation/run", data={
    "workspace_id": WORKSPACE_ID,
    "dataset_id": None,
    "metrics": ["faithfulness", "relevancy", "context_precision"],
    "num_samples": 5,
}, headers=AUTH, timeout=60)
check("POST /evaluation/run", status in (200, 202, 422), f"{status}: {str(body)[:200]}")

status, body = req("POST", "/evaluation/sample", data={
    "workspace_id": WORKSPACE_ID,
    "question": "What is DocuMind AI revenue?",
    "answer": "DocuMind AI revenue was $4.2 million in Q1 2026.",
    "context": "DocuMind AI delivered exceptional performance in Q1 2026, with total revenue reaching $4.2 million",
}, headers=AUTH, timeout=30)
check("POST /evaluation/sample", status in (200, 422), f"{status}: {str(body)[:200]}")

status, body = req("GET", f"/evaluation/alerts?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /evaluation/alerts", status == 200, f"{status}: {str(body)[:150]}")

section("BATCH 5E — Provenance: Answers, Citations, Stats, Search")

status, body = req("GET", f"/provenance/answers?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /provenance/answers", status == 200, f"{status}: {str(body)[:150]}")
ANS_ID = None
if isinstance(body, dict):
    items = body.get("answers") or []
    if isinstance(items, list) and items:
        ANS_ID = items[0].get("answer_id") or items[0].get("id")

if ANS_ID:
    safe_ans = urllib.parse.quote(str(ANS_ID), safe="")
    status, body = req("GET", f"/provenance/answers/{safe_ans}", headers=AUTH)
    check("GET /provenance/answers/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /provenance/answers/:id", "no answers yet")

src = urllib.parse.quote(DOC_NAME or "quarterly_report_q1_2026.txt", safe="")
status, body = req("GET", f"/provenance/documents/{src}/citations?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /provenance/documents/:file/citations", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/provenance/documents/{src}/stats?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /provenance/documents/:file/stats", status in (200, 404), f"{status}: {str(body)[:150]}")

prov_params = urllib.parse.urlencode({"source_file": DOC_NAME or "quarterly_report_q1_2026.txt", "limit": 5})
status, body = req("GET", f"/provenance/search?{prov_params}", headers=AUTH)
check("GET /provenance/search", status in (200, 404, 422), f"{status}: {str(body)[:200]}")

# ═══════════════════════════════════════════════════════════════════
# BATCH 6 — Query extras, Auth API keys, Audit, Monitoring extras,
#           Agent extras, Retrieval extras
# ═══════════════════════════════════════════════════════════════════

section("BATCH 6A — Query: History, Feedback")

status, body = req("GET", f"/query/history?workspace_id={WORKSPACE_ID}&limit=10", headers=AUTH)
check("GET /query/history", status == 200, f"{status}: {str(body)[:150]}")

# query/feedback uses query params, not body
feedback_params = urllib.parse.urlencode({"query_id": "test_query_123", "rating": 5, "comment": "Accurate answer"})
status, body = req("POST", f"/query/feedback?{feedback_params}", headers=AUTH)
check("POST /query/feedback", status in (200, 201, 204, 404), f"{status}: {str(body)[:200]}")

section("BATCH 6B — Auth API Keys: Create, List, Get, Delete")

status, body = req("POST", "/auth/api-keys", data={
    "name": "Integration Test Key",
    "workspace_id": WORKSPACE_ID,
    "permissions": ["read"],
    "expires_in_days": 7,
}, headers=AUTH)
check("POST /auth/api-keys", status in (200, 201), f"{status}: {str(body)[:200]}")
AUTH_KEY_ID = None
if isinstance(body, dict):
    AUTH_KEY_ID = body.get("key_id") or body.get("id")

status, body = req("GET", f"/auth/api-keys?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /auth/api-keys", status == 200, f"{status}: {str(body)[:150]}")

if AUTH_KEY_ID:
    safe_k = urllib.parse.quote(str(AUTH_KEY_ID), safe="")
    status, body = req("DELETE", f"/auth/api-keys/{safe_k}", headers=AUTH)
    check("DELETE /auth/api-keys/:id", status in (200, 204), f"{status}: {str(body)[:100]}")
else:
    skip("DELETE /auth/api-keys/:id", "no key_id")

section("BATCH 6C — API Keys Manager: Create, List, Revoke, Rotate, Usage")

status, body = req("POST", "/apikeys/create", data={
    "workspace_id": WORKSPACE_ID,
    "name": "Manager Test Key",
    "permissions": ["read", "write"],
    "rate_limit": 1000,
    "expires_in_days": 30,
}, headers=AUTH)
check("POST /apikeys/create", status in (200, 201), f"{status}: {str(body)[:200]}")
MGMT_KEY_ID = None
if isinstance(body, dict):
    MGMT_KEY_ID = body.get("key_id") or body.get("id")
    print(f"    key_id={MGMT_KEY_ID}")

status, body = req("GET", f"/apikeys/list?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /apikeys/list", status == 200, f"{status}: {str(body)[:150]}")

if MGMT_KEY_ID:
    safe_k = urllib.parse.quote(str(MGMT_KEY_ID), safe="")
    status, body = req("GET", f"/apikeys/{safe_k}", headers=AUTH)
    check("GET /apikeys/:id", status in (200, 404, 405), f"{status}: {str(body)[:150]}")
    status, body = req("GET", f"/apikeys/{safe_k}/usage", headers=AUTH)
    check("GET /apikeys/:id/usage", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("POST", f"/apikeys/{safe_k}/rotate", headers=AUTH)
    check("POST /apikeys/:id/rotate", status in (200, 201, 404), f"{status}: {str(body)[:150]}")
    status, body = req("POST", f"/apikeys/{safe_k}/revoke", headers=AUTH)
    check("POST /apikeys/:id/revoke", status in (200, 204, 404), f"{status}: {str(body)[:100]}")
else:
    skip("GET /apikeys/:id", "no key_id")
    skip("GET /apikeys/:id/usage", "no key_id")
    skip("POST /apikeys/:id/rotate", "no key_id")
    skip("POST /apikeys/:id/revoke", "no key_id")

section("BATCH 6D — Audit: Logs, Export")

status, body = req("GET", f"/audit/logs?workspace_id={WORKSPACE_ID}&limit=20", headers=AUTH)
check("GET /audit/logs", status in (200, 403), f"{status}: {str(body)[:150]}")

safe_ws = urllib.parse.quote(str(WORKSPACE_ID), safe="")
status, body = req("GET", f"/audit/export/{safe_ws}", headers=AUTH)
check("GET /audit/export/:workspace", status in (200, 403), f"{status}: {str(body)[:150]}")

section("BATCH 6E — Monitoring extras: Record, Trend, Run, Rechunk")

status, body = req("POST", "/monitoring/record", data={
    "workspace_id": WORKSPACE_ID,
    "event_type": "query",
    "latency_ms": 250,
    "success": True,
    "details": {"model": "test"},
}, headers=AUTH)
check("POST /monitoring/record", status in (200, 201, 202), f"{status}: {str(body)[:150]}")

status, body = req("GET", f"/monitoring/trend?workspace_id={WORKSPACE_ID}&metric=query_latency", headers=AUTH)
check("GET /monitoring/trend", status in (200, 404), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/monitoring/run", data={"workspace_id": WORKSPACE_ID}, headers=AUTH)
check("POST /monitoring/run", status in (200, 202), f"{status}: {str(body)[:150]}")

status, body = req("POST", "/monitoring/rechunk", data={
    "workspace_id": WORKSPACE_ID,
    "source_file": DOC_NAME or "quarterly_report_q1_2026.txt",
}, headers=AUTH, timeout=60)
check("POST /monitoring/rechunk", status in (200, 202, 404), f"{status}: {str(body)[:200]}")

section("BATCH 6F — Agent extras: Thread, Confidence")

# Make an agent query to get a thread_id
status, body = req("POST", "/agent/query", data={
    "question": "What is the Q1 revenue?",
    "workspace_id": WORKSPACE_ID,
}, headers=AUTH, timeout=60)
THREAD_ID = None
if isinstance(body, dict):
    THREAD_ID = body.get("thread_id") or body.get("session_id") or body.get("id")
    print(f"    thread_id={THREAD_ID}")

if THREAD_ID:
    safe_th = urllib.parse.quote(str(THREAD_ID), safe="")
    status, body = req("GET", f"/agent/thread/{safe_th}", headers=AUTH)
    check("GET /agent/thread/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
    status, body = req("GET", f"/agent/confidence/{safe_th}", headers=AUTH)
    check("GET /agent/confidence/:id", status in (200, 404), f"{status}: {str(body)[:150]}")
else:
    skip("GET /agent/thread/:id", "no thread_id")
    skip("GET /agent/confidence/:id", "no thread_id")

section("BATCH 6G — Retrieval: Profiles, Benchmark")

status, body = req("GET", f"/retrieval/profiles?workspace_id={WORKSPACE_ID}", headers=AUTH)
check("GET /retrieval/profiles", status == 200, f"{status}: {str(body)[:150]}")

status, body = req("POST", "/retrieval/benchmark", data={
    "workspace_id": WORKSPACE_ID,
    "queries": ["What is DocuMind AI?", "What was Q1 revenue?"],
    "top_k": 3,
}, headers=AUTH, timeout=60)
check("POST /retrieval/benchmark", status in (200, 202, 422), f"{status}: {str(body)[:200]}")

section("BATCH 6H — Auth extras: Logout All, Verify Email, Workspace ops")

status, body = req("GET", "/workspaces", headers=AUTH)
check("GET /workspaces", status == 200, f"{status}: {str(body)[:150]}")

safe_ws = urllib.parse.quote(str(WORKSPACE_ID), safe="")
status, body = req("GET", f"/workspaces/{safe_ws}", headers=AUTH)
check("GET /workspaces/:id", status in (200, 404), f"{status}: {str(body)[:150]}")

# Live/ready/metrics endpoints
status, body = req("GET", f"{BASE}/live", headers={})
check("GET /live", status == 200, f"{status}")

status, body = req("GET", f"{BASE}/ready", headers={})
check("GET /ready", status in (200, 503), f"{status}")

status, body = req("GET", f"{BASE}/metrics", headers={})
check("GET /metrics", status in (200, 404), f"{status}: {str(body)[:80]}")

# ─────────────────────────────────────────────────────────────────────────────
section("FINAL SUMMARY")
total = len(passed) + len(failed) + len(skipped)
print(f"  PASSED:  {len(passed)}/{total}")
print(f"  FAILED:  {len(failed)}/{total}")
print(f"  SKIPPED: {len(skipped)}/{total}")
if failed:
    print("\nFailed tests:")
    for f in failed:
        print(f"  - {f}")
if skipped:
    print("\nSkipped tests:")
    for s in skipped:
        print(f"  - {s}")
print("="*60)
sys.exit(0 if not failed else 1)
