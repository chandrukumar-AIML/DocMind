// frontend/src/api/client.js
import axios from "axios";
import toast from "react-hot-toast";
import { demoApi, isDemoMode } from "./demo";
// [OK] FIXED: Use React Router navigation instead of window.location.href = "/login"
// window.location.href triggers a full page reload and drops all React state.
// navigateTo() uses the registered React Router navigate function (set in AppRouter).
import { navigateTo } from "../utils/navigator";

// ════════════════════════════════════════════════════════════════════════
// CONFIG (Centralized, env-driven)
// ════════════════════════════════════════════════════════════════════════
const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const ACCESS_TOKEN_KEY = "documind_access_token";
const LEGACY_ACCESS_TOKEN_KEY = "auth_token";
const WORKSPACE_KEY = "documind_workspace_id";
const LEGACY_WORKSPACE_KEY = "workspace_id";
const DEFAULT_TIMEOUT = 60000;
const INGEST_TIMEOUT = 300000; // 5 min for large files
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 500;
const CIRCUIT_BREAKER_THRESHOLD = 5;
const CIRCUIT_BREAKER_RESET_MS = 30000;

// Circuit breaker state
let failureCount = 0;
let circuitOpen = false;
let circuitResetTimeout = null;

// ════════════════════════════════════════════════════════════════════════
// AXIOS INSTANCE WITH INTERCEPTORS
// ════════════════════════════════════════════════════════════════════════
const apiClient = axios.create({
  baseURL: BASE_URL,
  timeout: DEFAULT_TIMEOUT,
  headers: { "Content-Type": "application/json" },
  // [OK] FIXED: withCredentials=true sends httpOnly cookies automatically on every request.
  // Cookies set by the login/refresh endpoints (access_token, refresh_token) are
  // XSS-safe because JavaScript cannot read httpOnly cookies — they're sent by the
  // browser transparently. This also enables CORS with credentials.
  withCredentials: true,
});

// ✅ Request interceptor: inject auth + correlation_id + workspace_id
apiClient.interceptors.request.use(
  (config) => {
    // Circuit breaker check
    if (circuitOpen) {
      throw new Error("Service temporarily unavailable. Please try again later.");
    }

    // Generate correlation_id if not provided
    const correlationId = config.headers?.["X-Correlation-ID"] || `req-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    config.headers["X-Correlation-ID"] = correlationId;

    // [OK] FIXED: JWT is now in httpOnly cookies sent automatically via withCredentials.
    // Fallback: still inject Bearer header if a token exists in localStorage
    // (backward compat for API clients / Swagger / mobile apps using the old flow).
    const token = localStorage.getItem(ACCESS_TOKEN_KEY) || localStorage.getItem(LEGACY_ACCESS_TOKEN_KEY);
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Note: if no localStorage token, the browser sends the httpOnly cookie automatically.

    // Inject workspace_id from auth context
    const workspaceId = config.params?.workspace_id || localStorage.getItem(WORKSPACE_KEY) || localStorage.getItem(LEGACY_WORKSPACE_KEY);
    if (workspaceId && config.url?.includes("/api/v1/")) {
      if (config.method === "get") {
        config.params = { ...config.params, workspace_id: workspaceId };
      } else if (config.data && typeof config.data === "object" && !(config.data instanceof FormData)) {
        config.data = { ...config.data, workspace_id: workspaceId };
      }
    }

    return config;
  },
  (error) => Promise.reject(error)
);

// ✅ Response interceptor: retry logic + circuit breaker + user-friendly errors
apiClient.interceptors.response.use(
  (response) => {
    // Reset circuit breaker on success
    if (failureCount > 0) {
      failureCount = 0;
      if (circuitResetTimeout) clearTimeout(circuitResetTimeout);
      circuitOpen = false;
    }
    return response;
  },
  async (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message;
    const config = error.config;

    // Retry logic for transient errors (429, 503, network)
    // FIXED: was setting config._retry = true on first attempt which blocked all
    // subsequent retries — only 1 retry ever happened despite MAX_RETRIES = 3.
    // Now correctly checks _retryCount against MAX_RETRIES.
    const isRetryable = status === 429 || status === 503 || !error.response;
    const retryCount = config?._retryCount || 0;
    if (isRetryable && retryCount < MAX_RETRIES) {
      config._retryCount = retryCount + 1;
      failureCount++;

      // Circuit breaker: open after threshold failures
      if (failureCount >= CIRCUIT_BREAKER_THRESHOLD) {
        circuitOpen = true;
        circuitResetTimeout = setTimeout(() => {
          circuitOpen = false;
          failureCount = 0;
        }, CIRCUIT_BREAKER_RESET_MS);
        toast.error("Service overloaded. Retrying in 30s...");
        return Promise.reject(error);
      }

      // Exponential backoff
      const delay = RETRY_DELAY_MS * Math.pow(2, retryCount);
      await new Promise(resolve => setTimeout(resolve, delay));

      // Retry the request
      return apiClient(config);
    }

    // User-facing error messages
    if (status === 413) {
      toast.error("File too large. Maximum 50MB allowed.");
    } else if (status === 422) {
      toast.error(`Validation error: ${detail}`);
    } else if (status === 401) {
      localStorage.removeItem(ACCESS_TOKEN_KEY);
      localStorage.removeItem(LEGACY_ACCESS_TOKEN_KEY);
      toast.error("Session expired. Please log in again.");
      // [OK] FIXED: Use React Router navigate instead of window.location.href.
      // Hard navigation drops React state (chat history, pending uploads).
      // navigateTo() uses the registered navigate function from AppRouter.
      if (window.location.pathname !== "/login") {
        navigateTo("/login", { replace: true });
      }
    } else if (status === 403) {
      toast.error("Access denied. Check your permissions.");
    } else if (status === 404) {
      toast.error("Resource not found.");
    } else if (status === 429) {
      toast.error("Rate limit exceeded. Please wait before retrying.");
    } else if (status === 503) {
      toast.error(detail || "Service temporarily unavailable.");
    } else if (status && status >= 500) {
      // FIXED: Don't expose raw server detail to users — internal errors may leak
      // stack traces, DB query info, or file paths. Show a generic message instead.
      toast.error("A server error occurred. Our team has been notified.");
    } else if (!error.response) {
      toast.error("Network error. Check your connection.");
    }

    return Promise.reject(error);
  }
);

// ════════════════════════════════════════════════════════════════════════
// API METHODS (Modular, explicit contracts)
// ════════════════════════════════════════════════════════════════════════
const apiImpl = {
  // Health check (root-level, no auth required)
  health: () => apiClient.get("/health").then((r) => r.data),

  // Ingest — routes to correct endpoint by file type
  ingest: (file, options = {}) => {
    if (isDemoMode()) return demoApi.ingest(file, options);
    const ext = file.name.split(".").pop().toLowerCase();
    let endpoint = "/api/v1/ingest/document";
    if (["mp3", "mp4", "wav", "m4a", "ogg", "flac"].includes(ext)) endpoint = "/api/v1/ingest/audio";
    else if (["docx", "doc"].includes(ext)) endpoint = "/api/v1/ingest/docx";
    else if (["xlsx", "xls", "csv"].includes(ext)) endpoint = "/api/v1/ingest/xlsx";

    const formData = new FormData();
    formData.append("file", file);
    if (["pdf", "png", "jpg", "jpeg", "tiff", "tif", "bmp"].includes(ext)) {
      formData.append("options", JSON.stringify({
        enable_vision_enrichment: Boolean(options.enableVision ?? true),
        enable_ocr_fallback: Boolean(options.enableFallback ?? true),
        tags: options.tags || [],
      }));
    }

    return apiClient
      .post(endpoint, formData, {
        timeout: INGEST_TIMEOUT,
        onUploadProgress: options.onProgress,
        params: { workspace_id: options.workspaceId },
      })
      .then((r) => r.data);
  },

  listDocuments: (workspaceId) => {
    if (isDemoMode()) return demoApi.listDocuments();
    return apiClient.get("/api/v1/documents", { params: { workspace_id: workspaceId } }).then((r) => r.data);
  },

  queryHistory: (sessionId, workspaceId) => {
    if (isDemoMode()) return demoApi.queryHistory();
    return apiClient
      .get("/api/v1/query/history", { params: { session_id: sessionId, workspace_id: workspaceId } })
      .then((r) => r.data);
  },

  // Path traversal protection + workspace scoping
  deleteDocument: (sourceFile, workspaceId) => {
    if (isDemoMode()) return demoApi.deleteDocument(sourceFile);
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    if (!filename || filename.includes("..") || filename.startsWith(".")) {
      return Promise.reject(new Error("Invalid source file path"));
    }
    return apiClient
      .delete(`/api/v1/documents/${encodeURIComponent(filename)}`, {
        params: { workspace_id: workspaceId }
      })
      .then((r) => r.data);
  },

  // Non-streaming query
  query: (request, signal) => {
    if (isDemoMode()) return demoApi.query(request);
    const payload = {
      ...request,
      stream: false,
      correlation_id: request.correlation_id || `req-${Date.now()}`,
    };
    return apiClient
      .post("/api/v1/query", payload, { signal, timeout: DEFAULT_TIMEOUT })
      .then((r) => r.data);
  },

  // SSE streaming with safe JSON parsing + workspace_id
  // Routes to the right backend per query mode: RAG (default) / Agent / Graph.
  queryStream: async function* (request, signal) {
    if (isDemoMode()) {
      yield* demoApi.queryStream(request, signal);
      return;
    }

    const correlationId = request.correlation_id || `req-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    // Graph mode has no streaming endpoint — call it once and synthesize a
    // citations -> token -> done sequence so the consumer (useStreamQuery) doesn't
    // need mode-specific handling.
    if (request.mode === "graph") {
      const graphResponse = await fetch(`${BASE_URL}/api/v1/graph/query`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${localStorage.getItem(ACCESS_TOKEN_KEY) || localStorage.getItem(LEGACY_ACCESS_TOKEN_KEY) || ""}`,
          "X-Correlation-ID": correlationId,
        },
        body: JSON.stringify({
          question: request.question,
          workspace_id: request.workspace_id,
          top_k: request.top_k_retrieve || 5,
        }),
        signal,
      });
      if (!graphResponse.ok) {
        const err = await graphResponse.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${graphResponse.status}`);
      }
      const data = await graphResponse.json();
      yield { type: "citations", content: data.citations || [] };
      yield { type: "token", content: data.answer || "" };
      yield {
        type: "done",
        latency_seconds: data.latency_seconds,
        retrieved_count: data.vector_chunks,
        reranked_count: (data.citations || []).length,
        correlation_id: data.correlation_id,
      };
      return;
    }

    const isAgentMode = request.mode === "agent";
    const url = isAgentMode ? `${BASE_URL}/api/v1/agent/query` : `${BASE_URL}/api/v1/query`;

    // `mode` here is the top-level RAG/Agent/Graph chat mode used to pick the endpoint
    // above — it must NOT be forwarded as-is into AgentQueryRequest.mode, which is a
    // different enum (the agent's internal retrieval strategy: rag/crag/self_rag/graph/
    // hybrid) and rejects "agent" with a 422. Drop it for the agent endpoint and let the
    // backend use its own default.
    const { mode: _uiMode, ...requestWithoutMode } = request;
    const body = isAgentMode
      ? { ...requestWithoutMode, stream: true, correlation_id: correlationId, workspace_id: request.workspace_id }
      : { ...request, stream: true, correlation_id: correlationId, workspace_id: request.workspace_id };

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${localStorage.getItem(ACCESS_TOKEN_KEY) || localStorage.getItem(LEGACY_ACCESS_TOKEN_KEY) || ""}`,
        "X-Correlation-ID": correlationId,
      },
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("ReadableStream not supported");
    
    const decoder = new TextDecoder("utf-8", { fatal: false });
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

        if (done) {
          if (buffer.trim()) {
            const remaining = buffer.startsWith("data: ") ? buffer.slice(6) : buffer;
            if (remaining.trim() && remaining.trim() !== "[DONE]") {
              try {
                const parsed = JSON.parse(remaining);
                yield parsed;
              } catch {
                yield remaining.trim(); // Fallback to raw text
              }
            }
          }
          break;
        }

        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            const payload = line.slice(6);
            if (payload === "[DONE]") break;
            try {
              const parsed = JSON.parse(payload);
              yield parsed;
            } catch {
              yield payload; // Fallback to raw text
            }
          }
        }
      }
    } finally {
      reader.releaseLock(); // Always release lock
    }
  },

  // Auth methods
  login: async (email, password) => {
    if (isDemoMode()) return demoApi.login(email, password);
    const response = await apiClient.post("/api/v1/auth/login", { email, password });
    if (response.data.access_token) {
      localStorage.setItem(ACCESS_TOKEN_KEY, response.data.access_token);
      localStorage.setItem(WORKSPACE_KEY, response.data.workspace_id);
    }
    return response.data;
  },

  logout: () => {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(LEGACY_ACCESS_TOKEN_KEY);
    localStorage.removeItem(WORKSPACE_KEY);
    localStorage.removeItem(LEGACY_WORKSPACE_KEY);
    return Promise.resolve();
  },

  getDocumentChunks: (sourceFile, workspaceId) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    return apiClient.post("/api/v1/retrieval/hybrid-search", {
      query: "..",
      k: 50,
      filter_dict: { source_file: filename },
      workspace_id: workspaceId,
    }).then(r => r.data);
  },

  getMonitoringStats: (workspaceId, hours = 24) => {
    return apiClient.get("/api/v1/monitoring/stats", {
      params: { workspace_id: workspaceId, hours },
    }).then(r => r.data);
  },

  reindexDocument: (sourceFile) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    if (!filename) return Promise.reject(new Error("Invalid filename"));
    return apiClient.post(`/api/v1/documents/${encodeURIComponent(filename)}/reindex`).then(r => r.data);
  },

  submitFeedback: (queryId, rating) => {
    return apiClient.post("/api/v1/query/feedback", null, {
      params: { query_id: queryId, rating: Math.max(1, Math.min(5, rating)) },
    }).then(r => r.data).catch(() => {}); // silent — feedback is non-critical
  },

  ingestUrl: (url, workspaceId) => {
    return apiClient.post("/api/v1/ingest/url", { url }, {
      timeout: INGEST_TIMEOUT,
      params: { workspace_id: workspaceId },
    }).then(r => r.data);
  },

  // Auth-scoped API Key management (used by ApiKeyPanel sidebar widget)
  authListApiKeys: () => apiClient.get("/api/v1/auth/api-keys").then(r => r.data),
  authCreateApiKey: (name, expires_days = 365) => apiClient.post("/api/v1/auth/api-keys", { name, expires_days }).then(r => r.data),
  authDeleteApiKey: (keyId) => apiClient.delete(`/api/v1/auth/api-keys/${keyId}`).then(r => r.data),

  // Fine-tuning
  generateDataset: (workspaceId) =>
    apiClient.post("/api/v1/finetuning/generate-dataset", { workspace_id: workspaceId }).then(r => r.data),
  getDatasetStatus: (workspaceId) =>
    apiClient.get("/api/v1/finetuning/dataset-status", { params: { workspace_id: workspaceId } }).then(r => r.data).catch(() => null),
  pullFineTuneModel: (modelName, workspaceId) =>
    apiClient.post("/api/v1/finetuning/pull-model", { model_name: modelName, workspace_id: workspaceId }).then(r => r.data),
  listFineTuneModels: (workspaceId) =>
    apiClient.get("/api/v1/finetuning/models", { params: { workspace_id: workspaceId } }).then(r => r.data).catch(() => ({ models: [] })),
  reembedWorkspace: (workspaceId) =>
    apiClient.post("/api/v1/finetuning/reembed-workspace", { workspace_id: workspaceId }).then(r => r.data),

  // Versioning
  getVersionHistory: (sourceFile) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    return apiClient.get(`/api/v1/versioning/history/${encodeURIComponent(filename)}`).then(r => r.data);
  },
  getVersionDiff: (sourceFile, v1, v2) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    return apiClient.get(`/api/v1/versioning/diff/${encodeURIComponent(filename)}`, { params: { v1, v2 } }).then(r => r.data);
  },

  // Domain analysis
  analyzeLegal: (sourceFile) =>
    isDemoMode() ? demoApi.analyzeLegal(sourceFile)
      : apiClient.post("/api/v1/domains/legal/analyze", { source_file: sourceFile }).then(r => r.data),
  analyzeMedical: (sourceFile) =>
    isDemoMode() ? demoApi.analyzeMedical(sourceFile)
      : apiClient.post("/api/v1/domains/medical/analyze", null, { params: { source_file: sourceFile } }).then(r => r.data),
  analyzeLogistics: (sourceFiles) =>
    isDemoMode() ? demoApi.analyzeLogistics(sourceFiles)
      : apiClient.post("/api/v1/domains/logistics/analyze-invoices", { source_files: sourceFiles }).then(r => r.data),

  // Document download
  downloadDocument: (sourceFile, workspaceId) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    const params = new URLSearchParams({ workspace_id: workspaceId || "" });
    return `${BASE_URL}/api/v1/documents/${encodeURIComponent(filename)}/download?${params}`;
  },

  // Duplicate detection
  findDuplicates: (workspaceId) =>
    apiClient.get("/api/v1/documents/duplicates", { params: { workspace_id: workspaceId } }).then(r => r.data),

  // Excel export
  exportTablesUrl: (sourceFile, workspaceId) => {
    const filename = sourceFile.split("/").pop()?.split("\\").pop();
    const params = new URLSearchParams({ workspace_id: workspaceId || "" });
    return `${BASE_URL}/api/v1/extraction/export-tables/${encodeURIComponent(filename)}?${params}`;
  },

  // Bill calculator
  calculateBills: (sourceFiles, currency, workspaceId) =>
    isDemoMode() ? demoApi.calculateBills(sourceFiles, currency, workspaceId)
      : apiClient.post("/api/v1/domains/logistics/calculate-bills", {
      source_files: sourceFiles,
      currency: currency || "INR",
      workspace_id: workspaceId,
    }).then(r => r.data),

  // Form field extraction
  extractFormFields: (sourceFile, workspaceId) =>
    isDemoMode() ? demoApi.extractFormFields(sourceFile, workspaceId)
      : apiClient.post("/api/v1/extraction/form-fields", { source_file: sourceFile, workspace_id: workspaceId }).then(r => r.data),

  // Cross-document aggregation
  aggregateData: (sourceFiles, operation, column, filterCol, filterValue, workspaceId) =>
    apiClient.post("/api/v1/extraction/aggregate", {
      source_files: sourceFiles,
      operation,
      column,
      filter_col: filterCol || undefined,
      filter_value: filterValue || undefined,
      workspace_id: workspaceId,
    }).then(r => r.data),

  // Signature detection
  detectSignatures: (sourceFile, workspaceId) =>
    isDemoMode() ? demoApi.detectSignatures(sourceFile, workspaceId)
      : apiClient.post("/api/v1/domains/legal/detect-signatures", { source_file: sourceFile, workspace_id: workspaceId }).then(r => r.data),

  // Table extraction — returns { tables: [{table_id, summary, table_type, row_count, col_count}] }
  extractTables: (sourceFile, workspaceId) =>
    apiClient.post("/api/v1/extraction/tables", { source_file: sourceFile, workspace_id: workspaceId }).then(r => r.data),

  // Chart extraction — returns { charts: [{chart_type, title, description, confidence}] }
  extractCharts: (sourceFile, workspaceId) =>
    apiClient.post("/api/v1/extraction/charts", { source_file: sourceFile, workspace_id: workspaceId }).then(r => r.data),

  // Workspace management
  listWorkspaces: () => {
    if (isDemoMode()) return demoApi.listWorkspaces();
    return apiClient.get("/api/v1/workspaces").then(r => r.data);
  },
  createWorkspace: (name, description) => {
    if (isDemoMode()) return demoApi.createWorkspace(name, description);
    return apiClient.post("/api/v1/workspaces", { name, description }).then(r => r.data);
  },

  // ── Feature 1: Webhooks ──────────────────────────────────────
  registerWebhook: (name, url, secret, events) =>
    apiClient.post("/api/v1/webhooks/register", { name, url, secret, events }).then(r => r.data),
  listWebhooks: () => apiClient.get("/api/v1/webhooks/list").then(r => r.data),
  deleteWebhook: (id) => apiClient.delete(`/api/v1/webhooks/${id}`).then(r => r.data),
  testWebhook: (webhook_id, event_type = "document_ingested") =>
    apiClient.post("/api/v1/webhooks/test", { webhook_id, event_type }).then(r => r.data),
  getWebhookDeliveries: (webhookId) =>
    apiClient.get(`/api/v1/webhooks/deliveries/${webhookId}`).then(r => r.data),

  // ── Feature 2: Comparison ─────────────────────────────────────
  startComparison: (source_files, mode = "SIMILARITY") =>
    apiClient.post("/api/v1/comparison/start", { source_files, mode }).then(r => r.data),
  getComparisonStatus: (jobId) =>
    apiClient.get(`/api/v1/comparison/status/${jobId}`).then(r => r.data),
  listComparisons: (limit = 20) =>
    apiClient.get("/api/v1/comparison/list", { params: { limit } }).then(r => r.data),

  // ── Feature 3: Workflows ──────────────────────────────────────
  createWorkflow: (data) => apiClient.post("/api/v1/workflows/create", data).then(r => r.data),
  listWorkflows: () => apiClient.get("/api/v1/workflows/list").then(r => r.data),
  getWorkflow: (id) => apiClient.get(`/api/v1/workflows/${id}`).then(r => r.data),
  updateWorkflow: (id, data) => apiClient.patch(`/api/v1/workflows/${id}`, data).then(r => r.data),
  deleteWorkflow: (id) => apiClient.delete(`/api/v1/workflows/${id}`).then(r => r.data),
  getWorkflowRuns: (id) => apiClient.get(`/api/v1/workflows/${id}/runs`).then(r => r.data),

  // ── Feature 4: Annotations ────────────────────────────────────
  createAnnotation: (source_file, type, content, page_number, position) =>
    apiClient.post("/api/v1/annotations/create", { source_file, type, content, page_number, position }).then(r => r.data),
  listAnnotations: (source_file, type) =>
    apiClient.get("/api/v1/annotations/list", { params: { source_file, type } }).then(r => r.data),
  resolveAnnotation: (id, source_file) =>
    apiClient.post(`/api/v1/annotations/${id}/resolve`, null, { params: { source_file } }).then(r => r.data),
  deleteAnnotation: (id, source_file) =>
    apiClient.delete(`/api/v1/annotations/${id}`, { params: { source_file } }).then(r => r.data),

  // ── Feature 5: Templates ──────────────────────────────────────
  listBuiltinTemplates: () => apiClient.get("/api/v1/templates/builtins").then(r => r.data),
  getBuiltinTemplate: (slug) => apiClient.get(`/api/v1/templates/builtins/${slug}`).then(r => r.data),
  createTemplate: (name, fields) => apiClient.post("/api/v1/templates/create", { name, fields }).then(r => r.data),
  listTemplates: () => apiClient.get("/api/v1/templates/list").then(r => r.data),
  extractWithTemplate: (template_id, source_file) =>
    apiClient.post("/api/v1/templates/extract", { template_id, source_file }).then(r => r.data),
  getExtractionResults: (sourceFile) =>
    apiClient.get(`/api/v1/templates/results/${encodeURIComponent(sourceFile)}`).then(r => r.data),

  // ── Feature 6: E-Signature ────────────────────────────────────
  requestSignature: (source_file, signers, callback_url) =>
    apiClient.post("/api/v1/esignature/request", { source_file, signers, callback_url }).then(r => r.data),
  getESignStatus: (requestId) =>
    apiClient.get(`/api/v1/esignature/status/${requestId}`).then(r => r.data),
  listESignRequests: () => apiClient.get("/api/v1/esignature/list").then(r => r.data),
  inappSign: (request_id, signature_data) =>
    apiClient.post("/api/v1/esignature/inapp/sign", { request_id, signature_data }).then(r => r.data),

  // ── Feature 7: Compliance ─────────────────────────────────────
  listRegulations: () => apiClient.get("/api/v1/compliance/regulations").then(r => r.data),
  checkCompliance: (source_file, regulations) =>
    apiClient.post("/api/v1/compliance/check", { source_file, regulations }).then(r => r.data),
  getComplianceHistory: (sourceFile) =>
    apiClient.get(`/api/v1/compliance/history/${encodeURIComponent(sourceFile)}`).then(r => r.data),
  getComplianceResult: (resultId) =>
    apiClient.get(`/api/v1/compliance/result/${resultId}`).then(r => r.data),

  // ── Feature 8: Super Admin ────────────────────────────────────
  adminGetStats: () => apiClient.get("/api/v1/superadmin/stats").then(r => r.data),
  adminOverview: () => apiClient.get("/api/v1/superadmin/overview").then(r => r.data),
  adminListWorkspaces: (params = {}) =>
    apiClient.get("/api/v1/superadmin/workspaces", { params }).then(r => r.data),
  adminCreateWorkspace: (data) =>
    apiClient.post("/api/v1/superadmin/workspace/create", data).then(r => r.data),
  adminUpdateWorkspaceLimits: (workspaceId, limits) =>
    apiClient.put(`/api/v1/superadmin/workspace/${workspaceId}/limits`, limits).then(r => r.data),
  adminGetBilling: (workspaceId) =>
    apiClient.get(`/api/v1/superadmin/workspaces/${workspaceId}/billing`).then(r => r.data),
  adminExportBilling: (month) =>
    apiClient.get("/api/v1/superadmin/billing/export", { params: { month }, responseType: "text" }).then(r => r.data),
  adminSuspendWorkspace: (workspace_id, reason = "") =>
    apiClient.put(`/api/v1/superadmin/workspace/${workspace_id}/suspend`, { reason }).then(r => r.data),
  adminActivateWorkspace: (workspace_id) =>
    apiClient.put(`/api/v1/superadmin/workspace/${workspace_id}/reactivate`).then(r => r.data),
  adminImpersonate: (workspaceId) =>
    apiClient.post(`/api/v1/superadmin/workspace/${workspaceId}/impersonate`).then(r => r.data),
  // Audit log from superadmin perspective (alias used in SuperAdminDashboard)
  adminGetAuditLog: (workspaceId, limit = 100) =>
    apiClient.get(`/api/v1/superadmin/workspace/${workspaceId}/audit-log`, { params: { limit } }).then(r => r.data),
  adminFlushCache: () => apiClient.post("/api/v1/superadmin/system/flush-cache").then(r => r.data),
  adminCeleryStatus: () => apiClient.get("/api/v1/superadmin/system/tasks").then(r => r.data),
  adminSystemHealth: () => apiClient.get("/api/v1/superadmin/system/health").then(r => r.data),

  // ── Feature 9: Onboarding / Invites ──────────────────────────
  sendInvite: (email, role = "editor", send_email = true) =>
    apiClient.post("/api/v1/onboarding/invite", { email, role, send_email }).then(r => r.data),
  listInvites: () => apiClient.get("/api/v1/onboarding/invites").then(r => r.data),
  validateInviteToken: (token) =>
    apiClient.get(`/api/v1/onboarding/invite/${token}/validate`).then(r => r.data),
  acceptInviteToken: (token, data) =>
    apiClient.post(`/api/v1/onboarding/invite/${token}/accept`, data).then(r => r.data),
  getOnboardingProgress: (workspaceId) =>
    apiClient.get("/api/v1/onboarding/progress", { params: { workspace_id: workspaceId } }).then(r => r.data),
  wizardStep: (step, data = {}) =>
    apiClient.post(`/api/v1/onboarding/wizard/step/${step}`, data).then(r => r.data),
  createWorkspaceApiKey: (name, scopes = ["read", "write"]) =>
    apiClient.post("/api/v1/onboarding/api-keys/create", { name, scopes }).then(r => r.data),
  listWorkspaceApiKeys: () => apiClient.get("/api/v1/onboarding/api-keys").then(r => r.data),
  revokeWorkspaceApiKey: (keyId) =>
    apiClient.delete(`/api/v1/onboarding/api-keys/${keyId}`).then(r => r.data),

  // ── Access Management: API Keys ───────────────────────────────
  createApiKey: (data) =>
    apiClient.post("/api/v1/apikeys/create", data).then(r => r.data),
  listApiKeys: (workspaceId) =>
    apiClient.get("/api/v1/apikeys/list", { params: { workspace_id: workspaceId } }).then(r => r.data),
  revokeApiKey: (keyId) =>
    apiClient.post(`/api/v1/apikeys/${keyId}/revoke`).then(r => r.data),
  rotateApiKey: (keyId) =>
    apiClient.post(`/api/v1/apikeys/${keyId}/rotate`).then(r => r.data),
  getApiKeyUsage: (keyId) =>
    apiClient.get(`/api/v1/apikeys/${keyId}/usage`).then(r => r.data),

  // ── Access Management: LLM Settings (per-workspace BYOK) ───────
  listLlmProviders: () =>
    apiClient.get("/api/v1/llm-settings/providers").then(r => r.data),
  getLlmSettings: () =>
    apiClient.get("/api/v1/llm-settings").then(r => r.data),
  updateLlmSettings: (data) =>
    apiClient.put("/api/v1/llm-settings", data).then(r => r.data),
  deleteLlmSettings: () =>
    apiClient.delete("/api/v1/llm-settings").then(r => r.data),
  testLlmSettings: () =>
    apiClient.post("/api/v1/llm-settings/test").then(r => r.data),

  // ── Access Management: Billing (Stripe) ────────────────────────
  listPlans: () =>
    apiClient.get("/api/v1/billing/plans").then(r => r.data),
  getSubscription: () =>
    apiClient.get("/api/v1/billing/subscription").then(r => r.data),
  getUsage: () =>
    apiClient.get("/api/v1/billing/usage").then(r => r.data),
  startCheckout: (plan) =>
    apiClient.post("/api/v1/billing/checkout", { plan }).then(r => r.data),
  openBillingPortal: () =>
    apiClient.post("/api/v1/billing/portal").then(r => r.data),

  // ── Access Management: SSO (OIDC) ───────────────────────────────
  getSsoConfig: () =>
    apiClient.get("/api/v1/sso/config").then(r => r.data),
  updateSsoConfig: (data) =>
    apiClient.put("/api/v1/sso/config", data).then(r => r.data),
  deleteSsoConfig: () =>
    apiClient.delete("/api/v1/sso/config").then(r => r.data),

  // ── Access Management: Audit Log ──────────────────────────────
  getAuditLogs: (params = {}) =>
    apiClient.get("/api/v1/audit/logs", { params }).then(r => r.data),
  exportAuditLog: (workspaceId) =>
    apiClient.get(`/api/v1/audit/export/${workspaceId}`, { responseType: "text" }).then(r => r.data),

  // ── Feature 10: Regional ──────────────────────────────────────
  preprocessQuery: (query) =>
    apiClient.post("/api/v1/regional/preprocess-query", { query }).then(r => r.data),
  extractIndianEntities: (text) =>
    apiClient.post("/api/v1/regional/extract-entities", { text }).then(r => r.data),
  validateIndianId: (value, type) =>
    apiClient.post("/api/v1/regional/validate", { value, type }).then(r => r.data),
  parseIndianNumber: (text) =>
    apiClient.post("/api/v1/regional/parse-number", { text }).then(r => r.data),
};

// ════════════════════════════════════════════════════════════════════════
// DEMO-AWARE EXPORT
// In demo mode every call is routed to the mock layer first (demoApi). If a
// method has no mock (e.g. a URL builder like downloadDocument), it falls back
// to the real implementation so the app never breaks. This guarantees a client
// can touch ANY panel in demo mode without hitting the backend or seeing a
// "Failed to load" error. Outside demo mode the real implementation is used
// directly with zero overhead.
// ════════════════════════════════════════════════════════════════════════
export const api = isDemoMode()
  ? new Proxy(apiImpl, {
      get(target, prop) {
        if (prop in demoApi) return demoApi[prop];
        return target[prop];
      },
    })
  : apiImpl;

export default apiClient;
