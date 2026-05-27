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
    return {
      answer: getDemoAnswer(request.question),
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
};

// Check if demo mode should be active
export function isDemoMode() {
  if (import.meta.env?.VITE_DEMO_MODE === "true") return true;
  if (typeof window !== "undefined" && window.location.search.includes("demo=true")) return true;
  return false;
}
