// frontend/src/api/demo.js — Demo mode mock layer
// Activated when VITE_DEMO_MODE=true or backend is unreachable

export const DEMO_USER = {
  user_id: "demo-user-001",
  email: "demo@documind.ai",
  display_name: "Demo User",
  workspace_id: "demo-workspace",
  role: "owner",
  is_active: true,
  is_email_verified: true,
  is_superuser: true,
  workspaces: [
    { workspace_id: "demo-workspace", name: "Demo Workspace", role: "owner", is_default: true },
  ],
};

export const DEMO_DOCS = [
  {
    source_file: "uploads/Annual_Report_2024.pdf",
    page_count: 48,
    chunk_count: 214,
    mean_ocr_confidence: 0.97,
    document_type: "report",
    file_size: 3_240_000,
  },
  {
    source_file: "uploads/Service_Agreement_v3.docx",
    page_count: 12,
    chunk_count: 63,
    mean_ocr_confidence: 0.99,
    document_type: "contract",
    file_size: 128_000,
  },
  {
    source_file: "uploads/Q4_Financial_Data.xlsx",
    page_count: 4,
    chunk_count: 31,
    mean_ocr_confidence: 0.98,
    document_type: "other",
    file_size: 210_000,
  },
  {
    source_file: "uploads/Product_Overview.mp3",
    page_count: 1,
    chunk_count: 18,
    mean_ocr_confidence: 0.93,
    document_type: "other",
    file_size: 4_500_000,
  },
];

const DEMO_ANSWERS = {
  default: `Based on the documents in your workspace, here is what I found:\n\nThe **Annual Report 2024** shows strong revenue growth of **23% YoY**, driven primarily by expanded enterprise contracts and new product launches in Q3. Operating margins improved to **18.4%** compared to 15.1% in the prior year.\n\nThe **Service Agreement v3** outlines standard SLA terms with a 99.9% uptime guarantee and 4-hour response time for critical incidents. Section 7.2 covers liability limitations up to the total contract value.\n\nThe **Q4 Financial Data** spreadsheet records 847 transactions across 12 cost centers, with total quarterly expenditure of **$4.2M** against a budget of **$3.9M** (7.7% over budget).`,
  summary: `**Key Findings Summary:**\n\n• Revenue grew **23% YoY** to $142M in FY2024\n• Operating margin expanded from 15.1% → **18.4%**\n• Enterprise customer count increased by 41 accounts (net)\n• R&D investment was $18.7M (13.2% of revenue)\n• Cash position: $28.4M with no long-term debt\n\nThe company is in a strong financial position with healthy growth momentum heading into FY2025.`,
  terms: `**Payment Terms (Service Agreement v3, Section 4):**\n\n1. Net-30 payment terms from invoice date\n2. Late payments accrue interest at **1.5% per month**\n3. Invoices disputed within **10 business days** are held pending resolution\n4. Early payment discount: **2% if paid within 10 days**\n5. Currency: USD with FX conversion at daily rate for international clients\n\nAll payments must reference the Purchase Order number specified in Schedule A.`,
  liability: `**Liability Clause (Section 7.2 — Service Agreement v3):**\n\n> *"In no event shall either party be liable for indirect, incidental, special, or consequential damages. Total liability shall not exceed the total fees paid in the twelve (12) months preceding the claim."*\n\nAdditionally:\n- Force majeure events (Section 9.1) exclude liability for outages caused by acts of God, government actions, or third-party infrastructure failures\n- Indemnification obligations (Section 8) require 30 days written notice before initiating any claim`,
  data: `**Extracted Data Tables from Q4_Financial_Data.xlsx:**\n\n| Cost Center | Budget | Actual | Variance |\n|---|---|---|---|\n| Engineering | $1,200,000 | $1,340,000 | +$140K |\n| Marketing | $800,000 | $820,000 | +$20K |\n| Operations | $1,100,000 | $1,050,000 | -$50K |\n| Sales | $600,000 | $720,000 | +$120K |\n| G&A | $200,000 | $270,000 | +$70K |\n\n**Total: $3,900,000 budget vs $4,200,000 actual (+$300K, 7.7% over)**`,
};

function getDemoAnswer(question) {
  const q = question.toLowerCase();
  if (q.includes("summary") || q.includes("key findings") || q.includes("overview")) return DEMO_ANSWERS.summary;
  if (q.includes("payment") || q.includes("term") || q.includes("invoice")) return DEMO_ANSWERS.terms;
  if (q.includes("liabil") || q.includes("section") || q.includes("clause")) return DEMO_ANSWERS.liability;
  if (q.includes("table") || q.includes("data") || q.includes("extract") || q.includes("spreadsheet")) return DEMO_ANSWERS.data;
  return DEMO_ANSWERS.default;
}

// Per-document answers used when a query is scoped to a single file
// (filter_source_file) — e.g. the side-by-side Document Comparison panel.
// Each document must answer from its own perspective, otherwise both
// compare panes show identical text and the feature looks broken.
const DOC_SCOPED_ANSWERS = {
  "uploads/Annual_Report_2024.pdf": `**From Annual_Report_2024.pdf (p. 5, 12):**\n\nRevenue for FY2024 reached **$142M, up 23% YoY**, driven by enterprise expansion (41 net-new accounts). Revenue is recognised on delivery per ASC 606, with deferred revenue of $19.8M carried into FY2025.\n\nNo customer payment terms are defined here — this report only discloses *recognised* revenue and collection performance (DSO improved from 48 to 41 days).`,
  "uploads/Service_Agreement_v3.docx": `**From Service_Agreement_v3.docx (Section 4 — Payment Terms):**\n\n1. **Net-30** payment terms from invoice date\n2. Late payments accrue interest at **1.5% per month**\n3. Early payment discount: **2% if paid within 10 days**\n4. Currency: USD with FX conversion at the daily rate\n\nAll payments must reference the Purchase Order number in Schedule A.`,
  "uploads/Q4_Financial_Data.xlsx": `**From Q4_Financial_Data.xlsx (Sheet: Cost Centers):**\n\nQ4 recorded **847 transactions** across 12 cost centers totalling **$4.2M actual vs $3.9M budget** (+7.7%). The largest variances: Engineering **+$140K** and Sales **+$120K**.\n\nThis workbook tracks expenditure only — no revenue or payment-term data is present.`,
  "uploads/Product_Overview.mp3": `**From Product_Overview.mp3 (audio transcript, 18:42):**\n\nThe recording covers product positioning and pricing: three tiers are mentioned (Starter, Pro, Enterprise) with **annual billing discounted 15%** (at 14:32). The speaker notes enterprise deals are invoiced "on standard Net-30 terms" (at 16:05).\n\nNo contractual terms are defined in this recording — it references the Service Agreement for specifics.`,
};

const DEMO_CITATIONS = [
  { source_file: "uploads/Annual_Report_2024.pdf", page_number: 4, page_display: 5, block_type: "text", chunk_text: "Revenue for fiscal year 2024 increased by 23% year-over-year, reaching $142 million driven by enterprise expansion...", rerank_score: 0.94 },
  { source_file: "uploads/Service_Agreement_v3.docx", page_number: 6, page_display: 7, block_type: "text", chunk_text: "Section 7.2 — Limitation of Liability: Total liability shall not exceed the total fees paid in the twelve months preceding the claim...", rerank_score: 0.87 },
  { source_file: "uploads/Q4_Financial_Data.xlsx", page_number: 0, page_display: 1, block_type: "table", chunk_text: "Cost Center: Engineering | Budget: 1200000 | Actual: 1340000 | Variance: +140000", rerank_score: 0.81 },
];

// Simulate server-sent streaming
async function* streamText(text, delayMs = 18) {
  const words = text.split(" ");
  for (let i = 0; i < words.length; i++) {
    yield { type: "token", content: (i === 0 ? "" : " ") + words[i] };
    await new Promise(r => setTimeout(r, delayMs + Math.random() * 10));
  }
  yield { type: "citations", content: DEMO_CITATIONS };
  yield { type: "done", latency_seconds: 1.2 + Math.random() * 0.8 };
}

// Small latency simulator so the demo feels like a real network round-trip.
// Collapsed to 0ms under Vitest so the test suite is fast and never flakes on
// timers under CPU load (the simulated latency only matters in the browser demo).
// Use import.meta.env.VITEST (Vite-native) rather than process.env which is not
// available in the browser runtime and triggers a no-undef ESLint error.
const _IN_TEST = Boolean(import.meta.env.VITEST);
const delay = (ms = 300) => new Promise(r => setTimeout(r, _IN_TEST ? 0 : ms));

// ── Demo API surface (matches api/client.js api object shape) ──
export const demoApi = {
  health: async () => ({ status: "demo", version: "2.0.0-demo" }),

  login: async (email) => {
    await new Promise(r => setTimeout(r, 600));
    if (!email.includes("@")) throw new Error("Invalid email");
    return {
      access_token: "demo-access-token-" + Date.now(),
      refresh_token: "demo-refresh-token",
      expires_in: 3600,
      workspace_id: "demo-workspace",
      user: DEMO_USER,
    };
  },

  register: async (email, _password, displayName) => {
    await new Promise(r => setTimeout(r, 800));
    return {
      access_token: "demo-access-token-" + Date.now(),
      refresh_token: "demo-refresh-token",
      expires_in: 3600,
      workspace_id: "demo-workspace",
      user: { ...DEMO_USER, email, display_name: displayName || email.split("@")[0] },
    };
  },

  me: async () => {
    await new Promise(r => setTimeout(r, 200));
    return DEMO_USER;
  },

  listDocuments: async () => {
    await new Promise(r => setTimeout(r, 300));
    return { documents: DEMO_DOCS };
  },

  deleteDocument: async (sourceFile) => {
    await new Promise(r => setTimeout(r, 400));
    return { deleted: true, source_file: sourceFile };
  },

  ingest: async (_file, options = {}) => {
    // Simulate upload progress via onProgress
    if (options.onProgress) {
      for (let p = 0; p <= 100; p += 10) {
        options.onProgress({ loaded: p, total: 100 });
        await new Promise(r => setTimeout(r, 120));
      }
    }
    return {
      source_file: `uploads/${_file.name}`,
      page_count: Math.floor(Math.random() * 40) + 1,
      child_chunks: Math.floor(Math.random() * 180) + 20,
      mean_ocr_confidence: 0.92 + Math.random() * 0.07,
      document_type: "other",
    };
  },

  query: async (request) => {
    await new Promise(r => setTimeout(r, 900));
    const scoped = request.filter_source_file && DOC_SCOPED_ANSWERS[request.filter_source_file];
    return {
      answer: scoped || getDemoAnswer(request.question),
      citations: DEMO_CITATIONS,
      latency_seconds: 0.9,
    };
  },

  queryStream: async function* (request) {
    const answer = getDemoAnswer(typeof request === "string" ? request : request.question);
    yield* streamText(answer);
  },

  queryHistory: async () => ({ history: [] }),

  listWorkspaces: async () => ({
    workspaces: DEMO_USER.workspaces,
  }),

  createWorkspace: async (name) => ({
    workspace_id: `ws-${Date.now()}`,
    name,
    role: "owner",
    is_default: false,
  }),

  logout: async () => {},

  // ── Domain analysis (shapes match DomainPanel render) ─────
  analyzeLegal: async () => {
    await delay(900);
    return {
      analysis: {
        risk: {
          overall_score: 6.4,
          risk_level: "medium",
          executive_summary:
            "Standard B2B service agreement with balanced terms. Liability is capped and termination is mutual, but the indemnification clause (Section 8) is broad and should be reviewed before signing.",
        },
        clauses: {
          count: 5,
          missing: ["Force Majeure carve-out", "Data Processing Addendum"],
          items: [
            { type: "Limitation of Liability", text: "Total liability shall not exceed the total fees paid in the twelve (12) months preceding the claim (Section 7.2).", risk: 5 },
            { type: "Indemnification", text: "Vendor shall indemnify and hold harmless the Client against any and all third-party claims, including IP infringement, without a stated cap.", risk: 8 },
            { type: "Termination", text: "Either party may terminate this agreement for convenience with thirty (30) days written notice.", risk: 2 },
            { type: "Confidentiality", text: "Mutual non-disclosure covering all shared materials for a period of three (3) years post-termination.", risk: 3 },
            { type: "Auto-Renewal", text: "This agreement renews automatically for successive 12-month terms unless cancelled 60 days prior.", risk: 6 },
          ],
        },
        obligations: [
          { party: "Vendor", obligation: "Maintain 99.9% uptime SLA measured monthly", deadline: "Ongoing" },
          { party: "Vendor", obligation: "Respond to critical incidents within 4 hours", deadline: "Per incident" },
          { party: "Client", obligation: "Pay all invoices within Net-30 of receipt", deadline: "Net-30" },
          { party: "Both", obligation: "Conduct quarterly business reviews", deadline: "Quarterly" },
        ],
      },
    };
  },
  analyzeMedical: async () => {
    await delay(900);
    return {
      analysis: {
        icd10_codes: [
          { code: "E11.9", description: "Type 2 diabetes mellitus without complications", is_primary: true, confidence: 0.96 },
          { code: "I10", description: "Essential (primary) hypertension", is_primary: false, confidence: 0.91 },
          { code: "E78.5", description: "Hyperlipidemia, unspecified", is_primary: false, confidence: 0.88 },
        ],
        interactions: [
          { drug_1: "Metformin", drug_2: "Lisinopril", severity: "low", description: "No clinically significant interaction; routine monitoring of renal function advised." },
          { drug_1: "Atorvastatin", drug_2: "Amlodipine", severity: "medium", description: "Amlodipine may increase atorvastatin exposure; consider dose limit of 20mg." },
        ],
        interaction_summary: { total_medications: 4, high_risk: 0, requires_attention: true },
      },
    };
  },
  analyzeLogistics: async (sourceFiles) => {
    await delay(900);
    const sf = (Array.isArray(sourceFiles) && sourceFiles[0]) || "uploads/Service_Agreement_v3.docx";
    return {
      total_anomalies: 2,
      requires_review: true,
      results: [
        {
          source_file: sf,
          confidence: 0.95,
          invoice: { invoice_number: "INV-2024-0042", vendor_name: "Acme Logistics Pvt Ltd", invoice_date: "2024-11-18", total_amount: 12400, currency: "USD" },
          anomalies: [
            { severity: "medium", description: "Line-item total ($12,400) is 8% above the 90-day vendor average for comparable shipments." },
            { severity: "low", description: "Tax rate (18%) applied but GSTIN not present on invoice header." },
          ],
        },
      ],
    };
  },
  calculateBills: async (_files, currency = "INR") => {
    await delay(900);
    return {
      currency,
      summary: { subtotal: 41250, tax: 7500, grand_total: 48750, invoice_count: 3, line_item_count: 14 },
      invoices: [
        { source_file: "uploads/Freight_Nov.pdf", invoice_number: "FRT-9912", vendor: "BlueDart", date: "2024-11-02", total: 32000, currency },
        { source_file: "uploads/Handling_Nov.pdf", invoice_number: "HND-2231", vendor: "Acme Logistics", date: "2024-11-10", total: 9750, currency },
        { source_file: "uploads/Misc_Nov.pdf", invoice_number: "MSC-0098", vendor: "CityCargo", date: "2024-11-21", total: 7000, currency },
      ],
    };
  },
  extractFormFields: async () => {
    await delay(800);
    return {
      field_count: 6,
      fields: [
        { field: "full_name", field_type: "string", value: "Chandru Kumar" },
        { field: "date", field_type: "date", value: "2026-06-05" },
        { field: "invoice_no", field_type: "string", value: "INV-2024-0042" },
        { field: "amount", field_type: "number", value: 12400 },
        { field: "gstin", field_type: "string", value: "27AAPFU0939F1ZV" },
        { field: "signature", field_type: "boolean", value: null },
      ],
    };
  },
  detectSignatures: async () => {
    await delay(800);
    return {
      signatures_detected: 2,
      signatures: [
        { page: 12, confidence: 0.94, description: "Handwritten signature detected bottom-right, above 'Authorized Signatory' line." },
        { page: 12, confidence: 0.71, description: "Partial ink mark bottom-left near 'Witness' field — likely incomplete signature." },
      ],
    };
  },
  graphQuery: async (request) => {
    await delay(900);
    const question = typeof request === "string" ? request : request?.question || "";
    return {
      retrieval_mode: "hybrid",
      vector_chunks: 8,
      graph_records: 3,
      latency_seconds: 1.1,
      answer: `Based on the knowledge graph: Acme Corp is a party to "Service Agreement v3", which references the "Q4 Financial Data" workbook. The agreement contains a liability clause (Section 7.2) capping exposure at the trailing 12-month fees.${question ? "" : ""}`,
      graph_context: "Acme Corp -[party_to]-> Service Agreement v3 -[references]-> Q4 Financial Data",
      visualization: {
        nodes: [
          { id: "acme", label: "Acme Corp", type: "Organization" },
          { id: "sa3", label: "Service Agreement v3", type: "Document" },
          { id: "q4", label: "Q4 Financial Data", type: "Document" },
          { id: "clause", label: "Liability §7.2", type: "Clause" },
        ],
        edges: [
          { source: "acme", target: "sa3", label: "party_to" },
          { source: "sa3", target: "q4", label: "references" },
          { source: "sa3", target: "clause", label: "contains" },
        ],
      },
      citations: DEMO_CITATIONS,
    };
  },

  // ════════════════════════════════════════════════════════
  // FEATURE PANELS — sample data so demo never hits backend
  // ════════════════════════════════════════════════════════

  // ── Documents / misc ──────────────────────────────────────
  getMonitoringStats: async () => {
    await delay(300);
    return {
      query_count: 142,
      avg_latency_ms: 1840,
      confidence_mean: 0.81,
      faithfulness_mean: 0.86,
      context_precision_mean: 0.74,
      latency_ms_p95: 3120,
    };
  },
  findDuplicates: async () => {
    await delay(400);
    return { exact_duplicate_groups: 0, documents_scanned: DEMO_DOCS.length };
  },
  reindexDocument: async (sourceFile) => {
    await delay(700);
    return { status: "reindexed", source_file: sourceFile, chunks: 96 };
  },
  getDocumentChunks: async () => {
    await delay(400);
    return { results: DEMO_CITATIONS.map((c, i) => ({ ...c, id: `chunk-${i}` })) };
  },
  ingestUrl: async () => {
    await delay(1200);
    return { source_file: "uploads/web_article.html", child_chunks: 27, page_count: 1 };
  },
  extractTables: async () => {
    await delay(700);
    return {
      tables: [
        { table_id: "tbl-1", summary: "Cost-center budget vs actual (5 rows)", table_type: "financial", row_count: 5, col_count: 4 },
        { table_id: "tbl-2", summary: "Quarterly revenue by region (4 rows)", table_type: "financial", row_count: 4, col_count: 3 },
      ],
    };
  },
  extractCharts: async () => {
    await delay(700);
    return {
      charts: [
        { chart_type: "bar", title: "Revenue by Quarter", description: "Q1–Q4 FY2024 revenue trend showing 23% YoY growth.", confidence: 0.92 },
      ],
    };
  },

  // ── Webhooks ──────────────────────────────────────────────
  listWebhooks: async () => {
    await delay(300);
    return {
      webhooks: [
        { id: "wh-1", name: "Slack Notifier", url: "https://hooks.slack.com/services/T00/B00/xxx", events: ["document_ingested", "compliance_checked"] },
        { id: "wh-2", name: "Ops Pipeline", url: "https://ops.acme.com/ingest-hook", events: ["workflow_triggered"] },
      ],
    };
  },
  registerWebhook: async (name, url, _secret, events) => { await delay(500); return { id: `wh-${Date.now()}`, name, url, events }; },
  deleteWebhook: async () => { await delay(300); return { deleted: true }; },
  testWebhook: async () => { await delay(600); return { success: true, http_status: 200 }; },
  getWebhookDeliveries: async () => {
    await delay(300);
    return {
      deliveries: [
        { id: "d-1", event_type: "document_ingested", status: "delivered", attempt: 1, http_status: 200 },
        { id: "d-2", event_type: "compliance_checked", status: "delivered", attempt: 1, http_status: 200 },
        { id: "d-3", event_type: "document_ingested", status: "failed", attempt: 2, http_status: 503, error_msg: "Timeout" },
      ],
    };
  },

  // ── LLM Settings (per-workspace BYOK) ──────────────────────
  listLlmProviders: async () => {
    await delay(200);
    return {
      providers: [
        { id: "openai", label: "OpenAI", default_model: "gpt-4o", base_url: null },
        { id: "groq", label: "Groq (Free)", default_model: "llama-3.3-70b-versatile", base_url: "https://api.groq.com/openai/v1" },
        { id: "gemini", label: "Google Gemini", default_model: "gemini-2.0-flash", base_url: "https://generativelanguage.googleapis.com/v1beta/openai/" },
        { id: "ollama", label: "Ollama (Local)", default_model: "llama3.2:7b", base_url: "http://localhost:11434" },
      ],
    };
  },
  getLlmSettings: async () => {
    await delay(300);
    return {
      configured: true,
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      base_url: "https://api.groq.com/openai/v1",
      api_key_masked: "****demo",
      is_active: true,
      updated_at: new Date().toISOString(),
    };
  },
  updateLlmSettings: async (data) => {
    await delay(500);
    return {
      configured: true,
      provider: data.provider,
      model: data.model || "llama-3.3-70b-versatile",
      base_url: data.base_url || null,
      api_key_masked: `****${(data.api_key || "demo").slice(-4)}`,
      is_active: true,
      updated_at: new Date().toISOString(),
    };
  },
  deleteLlmSettings: async () => { await delay(300); return { deleted: true }; },
  testLlmSettings: async () => {
    await delay(700);
    return { success: true, provider: "groq", model: "llama-3.3-70b-versatile", latency_ms: 340, sample_response: "OK" };
  },

  // ── Billing (Stripe) ────────────────────────────────────────
  listPlans: async () => {
    await delay(200);
    return {
      plans: [
        { id: "starter", label: "Starter", price_display: "Free", self_serve: false, max_docs: 100, max_queries_per_day: 500, max_storage_gb: 5.0 },
        { id: "business", label: "Business", price_display: "$49/mo", self_serve: true, max_docs: 1000, max_queries_per_day: 5000, max_storage_gb: 50.0 },
        { id: "enterprise", label: "Enterprise", price_display: "Contact us", self_serve: false, max_docs: null, max_queries_per_day: null, max_storage_gb: null },
      ],
    };
  },
  getSubscription: async () => {
    await delay(300);
    return { plan: "starter", subscription_status: "none", has_stripe_customer: false };
  },
  getUsage: async () => {
    await delay(250);
    return {
      docs: { used: 20, limit: 100 },
      queries_today: { used: 12, limit: 500 },
      storage_mb: { used: 340, limit_mb: 5120 },
    };
  },
  startCheckout: async () => {
    await delay(500);
    return { checkout_url: "#demo-checkout-not-available" };
  },
  openBillingPortal: async () => {
    await delay(400);
    return { portal_url: "#demo-portal-not-available" };
  },

  // ── SSO (OIDC) ────────────────────────────────────────────
  getSsoConfig: async () => {
    await delay(300);
    return { configured: false };
  },
  updateSsoConfig: async (data) => {
    await delay(500);
    return {
      configured: true,
      client_id: data.client_id,
      client_secret_masked: `****${(data.client_secret || "demo").slice(-4)}`,
      issuer: data.issuer,
      is_active: true,
      updated_at: new Date().toISOString(),
    };
  },
  deleteSsoConfig: async () => { await delay(300); return { deleted: true }; },

  // ── Workflows ─────────────────────────────────────────────
  listWorkflows: async () => {
    await delay(300);
    return {
      workflows: [
        { workflow_id: "wf-1", name: "Tag High-Risk Invoices", trigger_event: "document_ingested", condition_count: 2, action_count: 1, is_active: true },
        { workflow_id: "wf-2", name: "Notify Legal on Contracts", trigger_event: "extraction_complete", condition_count: 1, action_count: 2, is_active: false },
      ],
    };
  },
  createWorkflow: async (data) => { await delay(500); return { workflow_id: `wf-${Date.now()}`, ...data }; },
  updateWorkflow: async () => { await delay(300); return { updated: true }; },
  deleteWorkflow: async () => { await delay(300); return { deleted: true }; },
  getWorkflowRuns: async () => {
    await delay(300);
    return {
      runs: [
        { run_id: "r-1", status: "completed", created_at: "2026-06-05T10:24:00" },
        { run_id: "r-2", status: "completed", created_at: "2026-06-04T16:02:00" },
        { run_id: "r-3", status: "failed", created_at: "2026-06-03T09:11:00", error_msg: "Webhook endpoint unreachable" },
      ],
    };
  },

  // ── Annotations ───────────────────────────────────────────
  listAnnotations: async (_sf, filterType) => {
    await delay(300);
    const all = [
      { id: "an-1", type: "comment", content: "Confirm the auto-renewal window with legal before counter-signing.", page_number: 7, username: "Priya (Legal)", resolved: false },
      { id: "an-2", type: "risk_flag", content: "Indemnification clause has no liability cap — high exposure.", page_number: 8, username: "Demo User", resolved: false },
      { id: "an-3", type: "approval", content: "Pricing schedule approved by Finance.", page_number: 3, username: "Ravi (Finance)", resolved: true },
    ];
    return { annotations: filterType ? all.filter(a => a.type === filterType) : all };
  },
  createAnnotation: async (_sf, type, content, page_number) => {
    await delay(300);
    return { id: `an-${Date.now()}`, type, content, page_number, username: "Demo User", resolved: false };
  },
  resolveAnnotation: async () => { await delay(200); return { resolved: true }; },
  deleteAnnotation: async () => { await delay(200); return { deleted: true }; },

  // ── Templates ─────────────────────────────────────────────
  listBuiltinTemplates: async () => {
    await delay(300);
    return {
      templates: [
        { slug: "invoice", name: "Invoice", field_count: 8 },
        { slug: "contract", name: "Contract", field_count: 10 },
        { slug: "purchase_order", name: "Purchase Order", field_count: 7 },
        { slug: "resume", name: "Resume", field_count: 9 },
        { slug: "bank_statement", name: "Bank Statement", field_count: 6 },
      ],
    };
  },
  listTemplates: async () => {
    await delay(300);
    return { templates: [{ template_id: "t-1", name: "NDA Extractor", field_count: 5 }] };
  },
  createTemplate: async (name) => { await delay(500); return { template_id: `t-${Date.now()}`, name }; },
  extractWithTemplate: async (templateId) => {
    await delay(900);
    return {
      template_name: templateId === "invoice" ? "Invoice" : "Extraction",
      fields: { invoice_number: "INV-2024-0042", vendor: "Acme Logistics", invoice_date: "2024-11-18", subtotal: 10508.47, tax: 1891.53, total: 12400.0, currency: "USD", due_date: "2024-12-18" },
      confidence: { invoice_number: 0.98, vendor: 0.93, invoice_date: 0.95, subtotal: 0.9, tax: 0.88, total: 0.97, currency: 0.99, due_date: 0.84 },
    };
  },

  // ── Comparison ────────────────────────────────────────────
  listComparisons: async () => {
    await delay(300);
    return { jobs: [{ job_id: "cmp-1", mode: "SIMILARITY", doc_count: 2, status: "done" }] };
  },
  startComparison: async (_files, mode = "SIMILARITY") => {
    await delay(500);
    return { job_id: `cmp-${Date.now()}`, mode, status: "running" };
  },
  getComparisonStatus: async (jobId) => {
    await delay(500);
    return {
      job_id: jobId, mode: "SIMILARITY", status: "done",
      result: {
        mode: "SIMILARITY",
        similarity_score: 78,
        summary: "Both documents share a common SLA framework and confidentiality language, but differ in liability caps and renewal terms.",
        common_themes: ["99.9% uptime SLA", "Mutual confidentiality (3 years)", "Net-30 payment terms", "Quarterly business reviews"],
        shared_entities: ["Acme Corp", "Q4 Financial Data", "Section 7.2"],
      },
    };
  },

  // ── Compliance ────────────────────────────────────────────
  listRegulations: async () => {
    await delay(300);
    return {
      regulations: {
        GDPR: "EU General Data Protection Regulation",
        INDIAN_CONTRACT: "Indian Contract Act, 1872",
        HIPAA: "US Health Insurance Portability & Accountability Act",
        SOC2: "SOC 2 Trust Services Criteria",
        COMPANIES_ACT: "Companies Act, 2013 (India)",
      },
    };
  },
  getComplianceHistory: async () => {
    await delay(300);
    return { history: [{ result_id: "cc-1", regulations: ["GDPR", "INDIAN_CONTRACT"], overall_score: 82, created_at: "2026-06-04T11:30:00" }] };
  },
  checkCompliance: async (_sf, regulations = ["GDPR"]) => {
    await delay(1200);
    return {
      overall_score: 82,
      scores: regulations.reduce((acc, r, i) => ({ ...acc, [r]: [88, 76, 84, 79][i % 4] }), {}),
      violations: [
        { severity: "high", regulation: "GDPR", description: "No explicit data-retention period stated for personal data (Art. 5(1)(e))." },
        { severity: "medium", regulation: "GDPR", description: "Cross-border transfer mechanism (SCCs) not referenced." },
        { severity: "low", regulation: "INDIAN_CONTRACT", description: "Stamp-duty clause missing for execution in Maharashtra." },
      ],
      recommendations: [
        { regulation: "GDPR", action: "Add a defined retention schedule and deletion process." },
        { regulation: "GDPR", action: "Reference Standard Contractual Clauses for EU↔India transfers." },
      ],
    };
  },

  // ── E-Signature ───────────────────────────────────────────
  listESignRequests: async () => {
    await delay(300);
    return {
      requests: [
        { request_id: "es-1", source_file: "uploads/Service_Agreement_v3.docx", provider: "in_app", status: "pending", created_at: "2026-06-05" },
        { request_id: "es-2", source_file: "uploads/Annual_Report_2024.pdf", provider: "docusign", status: "completed", created_at: "2026-05-29" },
      ],
    };
  },
  requestSignature: async () => { await delay(600); return { request_id: `es-${Date.now()}`, status: "pending" }; },
  inappSign: async () => { await delay(600); return { signed: true }; },

  // ── Onboarding / Invites ──────────────────────────────────
  listInvites: async () => {
    await delay(300);
    return {
      invites: [
        { invite_id: "inv-1", email: "priya@acme.com", role: "editor", status: "accepted", expires_at: "2026-06-30" },
        { invite_id: "inv-2", email: "ravi@acme.com", role: "viewer", status: "pending", expires_at: "2026-06-20" },
      ],
    };
  },
  sendInvite: async (email, role) => { await delay(500); return { invite_id: `inv-${Date.now()}`, email, role, status: "pending" }; },
  listWorkspaceApiKeys: async () => {
    await delay(300);
    return { api_keys: [{ key_id: "wk-1", name: "CI/CD Pipeline", key_prefix: "dm_live_a1b2", scopes: ["read", "write"], is_active: true }] };
  },
  createWorkspaceApiKey: async () => { await delay(500); return { api_key: "dm_live_" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2) }; },
  revokeWorkspaceApiKey: async () => { await delay(300); return { revoked: true }; },

  // ── Auth-scoped API keys (ApiKeyPanel) ────────────────────
  authListApiKeys: async () => {
    await delay(300);
    return { keys: [{ key_id: "ak-1", name: "my-app", expires_days: 365, created_at: "2026-05-01T00:00:00Z" }] };
  },
  authCreateApiKey: async (name) => { await delay(500); return { key_id: `ak-${Date.now()}`, name, token: "dm_" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2) }; },
  authDeleteApiKey: async () => { await delay(300); return { deleted: true }; },

  // ── Fine-tuning ───────────────────────────────────────────
  listFineTuneModels: async () => { await delay(300); return { models: ["all-MiniLM-L6-v2 (active)", "bge-small-en-v1.5"] }; },
  getDatasetStatus: async () => { await delay(300); return { triplet_count: 1240, status: "ready" }; },
  generateDataset: async () => { await delay(1500); return { triplet_count: 1240, status: "ready" }; },
  pullFineTuneModel: async () => { await delay(1500); return { pulled: true }; },
  reembedWorkspace: async () => { await delay(1500); return { chunks_reembedded: 326 }; },

  // ── Regional (Indian language tools) ──────────────────────
  preprocessQuery: async (query) => {
    await delay(500);
    return {
      original_query: query,
      normalized_query: "income statement search",
      detected_script: "tanglish",
      extracted_amounts: [520000, 1800000],
      extracted_entities: { amounts: ["5.2 lakhs", "18 lakhs"] },
    };
  },
  extractIndianEntities: async () => {
    await delay(500);
    return {
      entities: { pan: ["ABCDE1234F"], gstin: ["27AAPFU0939F1ZV"], aadhaar: ["2345 6789 0123"], mobile: ["+91 98400 12345"], pincode: ["600028"] },
      detected_script: "latin",
      total_entities: 5,
    };
  },
  validateIndianId: async (value, type) => {
    await delay(400);
    return { is_valid: true, type, value };
  },
  parseIndianNumber: async (text) => {
    await delay(400);
    return { input: text, parsed_value: 5200000, formatted: "₹52,00,000 (52 lakhs)" };
  },

  // ── Super Admin ───────────────────────────────────────────
  adminGetStats: async () => {
    await delay(400);
    return { total_workspaces: 12, total_users: 47, total_documents: 1840, total_webhook_deliveries: 3120, total_compliance_checks: 218, total_esign_requests: 64 };
  },
  adminListWorkspaces: async () => {
    await delay(400);
    return {
      workspaces: [
        { workspace_id: "ws-acme", name: "Acme Corp", user_count: 14, doc_count: 642, plan: "enterprise", is_active: true },
        { workspace_id: "ws-globex", name: "Globex Legal", user_count: 8, doc_count: 311, plan: "pro", is_active: true },
        { workspace_id: "ws-initech", name: "Initech", user_count: 3, doc_count: 57, plan: "free", is_active: false },
      ],
    };
  },
  adminGetBilling: async (wsId) => {
    await delay(400);
    return { workspace_id: wsId, plan: "enterprise", monthly_queries: 8420, storage_gb: 12.4, amount_due_usd: 499 };
  },
  adminSuspendWorkspace: async () => { await delay(400); return { suspended: true }; },
  adminActivateWorkspace: async () => { await delay(400); return { activated: true }; },
  adminFlushCache: async () => { await delay(400); return { flushed: true }; },
  adminSystemHealth: async () => {
    await delay(400);
    return { status: "healthy", services: { postgres: "ok", redis: "ok", chromadb: "ok", neo4j: "ok", ollama: "ok" } };
  },
  adminCeleryStatus: async () => {
    await delay(400);
    return { worker_count: 2, active_tasks: 1, workers: ["celery@worker-1", "celery@worker-2"] };
  },
};

// Check if demo mode should be active
export function isDemoMode() {
  if (import.meta.env?.VITE_DEMO_MODE === "true") return true;
  if (typeof window !== "undefined" && window.location.search.includes("demo=true")) return true;
  return false;
}
