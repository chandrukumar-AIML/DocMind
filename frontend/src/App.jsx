// frontend/src/App.jsx — DocuMind AI v2 — Nebula Dark Design
import { useState, useEffect, useCallback, useRef } from "react";
import { Toaster, toast } from "react-hot-toast";
import { useStreamQuery } from "./hooks/useStreamQuery";
import { useIngest } from "./hooks/useIngest";
import { useAuth } from "./hooks/useAuth";
import { DropZone } from "./components/DropZone";
import { DocumentList } from "./components/DocumentList";
import { ChatWindow } from "./components/ChatWindow";
import { ChatInput } from "./components/ChatInput";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { WorkspaceSwitcher } from "./components/WorkspaceSwitcher";
import { LoginForm } from "./components/LoginForm";
import { DomainPanel } from "./components/DomainPanel";
import { ConversationHistory } from "./components/ConversationHistory";
import { VersionTimeline } from "./components/VersionTimeline";
import { FineTuningPanel } from "./components/FineTuningPanel";
import { UrlWatcher } from "./components/UrlWatcher";
import { IngestProgressPanel } from "./components/IngestProgressPanel";
import { AgentStepsPanel } from "./components/AgentStepsPanel";
import { PDFViewer } from "./components/PDFViewer";
import { AudioUploader } from "./components/AudioUploader";
import { MonitoringDashboard } from "./components/MonitoringDashboard";
import { GraphQueryPanel } from "./components/GraphQueryPanel";
import { RAGAsDashboard } from "./components/RAGAsDashboard";
import { TableViewer } from "./components/TableViewer";
import { ChartViewer } from "./components/ChartViewer";
import { ApiKeyPanel } from "./components/ApiKeyPanel";
import { DocCompare } from "./components/DocCompare";
import { WebhookPanel } from "./components/WebhookPanel";
import { ComparisonPanel } from "./components/ComparisonPanel";
import { WorkflowPanel } from "./components/WorkflowPanel";
import { AnnotationPanel } from "./components/AnnotationPanel";
import { TemplatePanel } from "./components/TemplatePanel";
import { ESignPanel } from "./components/ESignPanel";
import { CompliancePanel } from "./components/CompliancePanel";
import { SuperAdminPanel } from "./components/SuperAdminPanel";
import { OnboardingPanel } from "./components/OnboardingPanel";
import { RegionalPanel } from "./components/RegionalPanel";
import { useConversationHistory } from "./hooks/useConversationHistory";
import { api } from "./api/client";
import "./App.css";

// ── Icons ─────────────────────────────────────────────────────
function IconMenu() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
    </svg>
  );
}
function IconClose() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  );
}
function IconClear() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>
    </svg>
  );
}
function IconSpark() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
    </svg>
  );
}

// ── Main App ──────────────────────────────────────────────────
export default function App() {
  const [documents, setDocuments] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [queryMode, setQueryMode] = useState("rag"); // rag | agent | graph
  const [visionEnabled, setVisionEnabled] = useState(() => {
    try { return localStorage.getItem("dm_vision") === "true"; } catch { return false; }
  });
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try { return window.innerWidth > 900; } catch { return true; }
  });
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [sidebarTab, setSidebarTab] = useState("docs"); // docs | stats
  const [stats, setStats] = useState(null);
  const [docBrief, setDocBrief] = useState(null); // { file, summary, loading }
  const [showCompare, setShowCompare] = useState(false);
  const [featureTab, setFeatureTab] = useState(null); // null = no feature panel open
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("dm_theme") || "dark"; } catch { return "dark"; }
  });
  const [agentSteps, setAgentSteps] = useState([]); // agent reasoning steps
  const [showPdfViewer, setShowPdfViewer] = useState(false); // PDF side panel
  const [extractionResults, setExtractionResults] = useState(null); // { tables, charts }
  const [extracting, setExtracting] = useState(false);

  const { user, workspaces, loading: authLoading, login, register, getCurrentWorkspace } = useAuth();
  const { messages, isStreaming, submit, cancel, clear, newConversation, loadSession, sessionId } = useStreamQuery();
  const { conversations, addOrUpdate: addConvHistory, remove: removeConv, clearAll: clearConvHistory } = useConversationHistory();
  const retryTimeoutRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    try { localStorage.setItem("dm_vision", String(visionEnabled)); } catch {}
  }, [visionEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem("dm_theme", theme);
      document.documentElement.className = theme === "light" ? "theme-light" : "";
    } catch {}
  }, [theme]);

  const refreshDocuments = useCallback(async (workspaceId = null) => {
    setLoadingDocs(true);
    setLoadError(null);
    try {
      const wsId = workspaceId || getCurrentWorkspace()?.workspace_id;
      if (!user || !wsId) { setDocuments([]); return true; }
      const data = await api.listDocuments(wsId);
      setDocuments(data.documents || []);
      return true;
    } catch (err) {
      setLoadError(err.message || "Failed to load documents");
      return false;
    } finally {
      setLoadingDocs(false);
    }
  }, [getCurrentWorkspace, user]);

  useEffect(() => {
    if (!user) { setLoadingDocs(false); return; }
    let retries = 0;
    const tryLoad = async () => {
      const ok = await refreshDocuments();
      if (!ok && retries < 3) {
        retries++;
        retryTimeoutRef.current = setTimeout(tryLoad, 1000 * Math.pow(2, retries - 1));
      }
    };
    tryLoad();
    return () => {
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [refreshDocuments, user]);

  const handleWorkspaceSwitch = useCallback((wsId) => {
    setSelectedFile(null);
    clear();
    refreshDocuments(wsId);
  }, [clear, refreshDocuments]);

  const { upload, uploadBatch, uploading, progress, batchQueue } = useIngest(() => refreshDocuments());

  const triggerDocBrief = useCallback(async (sourceFile, workspaceId) => {
    if (!sourceFile) return;
    setDocBrief({ file: sourceFile, summary: null, loading: true });
    try {
      const result = await api.query({
        question: "Give me a 2-3 sentence brief summary of this document. What is it about and what are the main topics?",
        filter_source_file: sourceFile,
        workspace_id: workspaceId,
        top_k_retrieve: 5,
        top_k_rerank: 2,
        stream: false,
      });
      const summary = result.answer || result.content || "";
      setDocBrief({ file: sourceFile, summary: summary.replace(/^(Extractive answer|OpenAI unavailable)[^:]*:\s*/i, ""), loading: false });
    } catch {
      setDocBrief(null);
    }
  }, []);

  const handleUpload = useCallback((fileOrFiles, opts = {}) => {
    const wsId = getCurrentWorkspace()?.workspace_id;
    const options = { ...opts, enableVision: visionEnabled, enableFallback: visionEnabled, workspaceId: wsId };
    const afterUpload = (result) => {
      const src = result?.source_file || (fileOrFiles instanceof File ? fileOrFiles.name : null);
      if (src) triggerDocBrief(src, wsId);
    };
    if (fileOrFiles instanceof File) return upload(fileOrFiles, options).then(afterUpload).catch(() => {});
    return uploadBatch(fileOrFiles, options);
  }, [upload, uploadBatch, visionEnabled, getCurrentWorkspace, triggerDocBrief]);


  const handleSubmit = useCallback((question) => {
    submit({
      question,
      filterSourceFile: selectedFile,
      workspaceId: getCurrentWorkspace()?.workspace_id,
      correlation_id: `app_${Date.now()}`,
      mode: queryMode,
    });
  }, [submit, selectedFile, getCurrentWorkspace, queryMode]);

  const handleDocumentDeleted = useCallback((file) => {
    setDocuments(prev => prev.filter(d => d.source_file !== file));
    if (selectedFile === file) setSelectedFile(null);
  }, [selectedFile]);

  const handleExportConversation = useCallback(() => {
    if (messages.length === 0) return;
    const lines = messages.map(m => {
      const role = m.role === "human" ? "**You**" : "**DocuMind AI**";
      const content = m.content || "";
      const citations = (m.citations || []).length > 0
        ? "\n\n_Sources: " + m.citations.map(c =>
            `${(c.source_file || "").split("/").pop().split("\\").pop()} p.${c.page_number ?? "?"}`)
          .join(", ") + "_"
        : "";
      return `${role}\n\n${content}${citations}`;
    });
    const md = `# DocuMind AI Conversation\n_Exported ${new Date().toLocaleString()}_\n\n---\n\n${lines.join("\n\n---\n\n")}`;
    const blob = new Blob([md], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `docmind-chat-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [messages]);

  const handleExportPDF = useCallback(() => {
    if (messages.length === 0) return;
    const rows = messages.map(m => {
      const role = m.role === "human" ? "You" : "DocuMind AI";
      const content = (m.content || "").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
      const cites = (m.citations || []).map(c =>
        `<small>[${(c.source_file || "").split("/").pop().split("\\").pop()} p.${c.page_number ?? "?"}]</small>`
      ).join(" ");
      return `<div class="msg-block ${m.role}"><div class="msg-role">${role}</div><div class="msg-body">${content}${cites ? `<div class="cites">${cites}</div>` : ""}</div></div>`;
    }).join("");
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>DocuMind AI Report</title>
<style>body{font-family:Georgia,serif;max-width:720px;margin:40px auto;color:#1e293b;line-height:1.6}
h1{font-size:22px;color:#0f172a;margin-bottom:4px}.meta{font-size:12px;color:#64748b;margin-bottom:28px}
.msg-block{margin-bottom:20px;padding:14px 18px;border-radius:8px}
.msg-block.human{background:#f1f5f9;border-left:3px solid #7c3aed}
.msg-block.assistant{background:#f8fafc;border-left:3px solid #0ea5e9}
.msg-role{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;color:#64748b}
.msg-body{font-size:14px}
.cites{margin-top:8px;font-size:11px;color:#94a3b8}
small{margin-right:4px}</style></head>
<body><h1>DocuMind AI — Conversation Report</h1>
<div class="meta">Exported ${new Date().toLocaleString()} · ${messages.length} messages</div>
${rows}</body></html>`;
    const w = window.open("", "_blank");
    if (w) {
      w.document.write(html);
      w.document.close();
      w.onload = () => { w.print(); };
    }
  }, [messages]);

  // Track conversation in history whenever messages change
  useEffect(() => {
    if (messages.length < 2) return;
    const firstUser = messages.find(m => m.role === "human");
    if (!firstUser?.content) return;
    const msgCount = messages.filter(m => !m.streaming).length;
    addConvHistory(sessionId, firstUser.content, msgCount);
  }, [messages, sessionId, addConvHistory]);

  // ── Agent steps accumulator ────────────────────────────────
  const lastAssistantMsg = messages.filter(m => m.role === "assistant").pop();
  const lastStatusStep = lastAssistantMsg?.statusStep;

  useEffect(() => {
    if (queryMode !== "agent" || !lastStatusStep || !isStreaming) return;
    setAgentSteps(prev => {
      if (prev[prev.length - 1]?.node === lastStatusStep) return prev;
      return [...prev, { node: lastStatusStep, status: "running" }];
    });
  }, [lastStatusStep, isStreaming, queryMode]);

  useEffect(() => {
    if (!isStreaming && agentSteps.length > 0) {
      setAgentSteps(prev => prev.map(s => ({ ...s, status: "done" })));
    }
  }, [isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setAgentSteps([]); }, [sessionId]);

  // ── Table / Chart extraction handler ──────────────────────
  const handleExtract = useCallback(async () => {
    const wsId = getCurrentWorkspace()?.workspace_id;
    if (!selectedFile || extracting) return;
    setExtracting(true);
    setExtractionResults(null);
    try {
      const [tabRes, chartRes] = await Promise.allSettled([
        api.extractTables(selectedFile, wsId),
        api.extractCharts(selectedFile, wsId),
      ]);
      setExtractionResults({
        tables: tabRes.status === "fulfilled" ? (tabRes.value?.tables || []) : [],
        charts: chartRes.status === "fulfilled" ? (chartRes.value?.charts || []) : [],
      });
    } catch {
      toast.error("Extraction failed");
    } finally {
      setExtracting(false);
    }
  }, [selectedFile, getCurrentWorkspace, extracting]);

  // Keyboard shortcut: Ctrl+K → focus chat
  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        document.querySelector(".chat-textarea")?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const shortFileName = selectedFile
    ? selectedFile.split("/").pop().split("\\").pop()
    : null;

  const currentWorkspace = getCurrentWorkspace();

  // ── Loading ────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-logo">D</div>
        <div className="loading-spinner" />
        <div className="loading-text">Loading DocuMind AI…</div>
      </div>
    );
  }

  // ── Auth Gate ──────────────────────────────────────────────
  if (!user) {
    return <LoginForm onLogin={login} onRegister={register} />;
  }

  // ── Main App Shell ─────────────────────────────────────────
  return (
    <div className="app-shell" role="application" aria-label="DocuMind AI">
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: "#1A2235",
            color: "#F1F5F9",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: "10px",
            fontSize: "13px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
          },
          success: { duration: 4000, iconTheme: { primary: "#10B981", secondary: "#fff" } },
          error:   { duration: 6000, iconTheme: { primary: "#EF4444", secondary: "#fff" } },
        }}
      />

      {/* ── Sidebar ─────────────────────────────────────── */}
      <aside
        id="sidebar"
        className={`sidebar${sidebarOpen ? "" : " collapsed"}`}
        aria-label="Document library"
      >
        {/* Header */}
        <div className="sidebar-header">
          <div className="logo-mark" aria-hidden="true">D</div>
          <div className="logo-text">
            <div className="app-name">DocuMind AI</div>
            <div className="app-tagline">v2 · Neural RAG</div>
          </div>
        </div>

        {/* Nav Tabs */}
        <div className="sidebar-nav" role="tablist">
          <button
            className={`nav-tab${sidebarTab === "docs" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "docs"}
            onClick={() => setSidebarTab("docs")}
          >Docs</button>
          <button
            className={`nav-tab${sidebarTab === "analyze" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "analyze"}
            onClick={() => setSidebarTab("analyze")}
          >Analyze</button>
          <button
            className={`nav-tab${sidebarTab === "history" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "history"}
            onClick={() => setSidebarTab("history")}
          >
            History
            {conversations.length > 0 && (
              <span className="nav-tab-badge">{conversations.length}</span>
            )}
          </button>
          <button
            className={`nav-tab${sidebarTab === "finetune" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "finetune"}
            onClick={() => setSidebarTab("finetune")}
          >Train</button>
          <button
            className={`nav-tab${sidebarTab === "stats" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "stats"}
            onClick={() => {
              setSidebarTab("stats");
              if (!stats) {
                const wsId = getCurrentWorkspace()?.workspace_id;
                api.getMonitoringStats(wsId).then(setStats).catch(() => setStats({}));
              }
            }}
          >Stats</button>
          <button
            className={`nav-tab${sidebarTab === "features" ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === "features"}
            onClick={() => setSidebarTab("features")}
          >Features</button>
        </div>

        {/* Body */}
        <div className="sidebar-body">
          {sidebarTab === "features" ? (
            <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div className="sidebar-feature-tabs">
                {[
                  ["webhooks", "Webhooks"],
                  ["compare", "Compare"],
                  ["workflows", "Workflows"],
                  ["annotate", "Annotate"],
                  ["templates", "Templates"],
                  ["esign", "E-Sign"],
                  ["compliance", "Compliance"],
                  ["admin", "Admin"],
                  ["onboard", "Onboard"],
                  ["regional", "Regional"],
                ].map(([id, label]) => (
                  <button
                    key={id}
                    className={`sidebar-feature-tab${featureTab === id ? " active" : ""}`}
                    onClick={() => setFeatureTab(f => f === id ? null : id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div style={{ flex: 1, overflow: "hidden" }}>
                {featureTab === "webhooks" && <WebhookPanel />}
                {featureTab === "compare" && <ComparisonPanel documents={documents} />}
                {featureTab === "workflows" && <WorkflowPanel />}
                {featureTab === "annotate" && (
                  <AnnotationPanel
                    sourceFile={selectedFile}
                    workspaceId={getCurrentWorkspace()?.workspace_id}
                  />
                )}
                {featureTab === "templates" && <TemplatePanel selectedFile={selectedFile} />}
                {featureTab === "esign" && <ESignPanel selectedFile={selectedFile} />}
                {featureTab === "compliance" && <CompliancePanel selectedFile={selectedFile} />}
                {featureTab === "admin" && <SuperAdminPanel user={user} />}
                {featureTab === "onboard" && <OnboardingPanel />}
                {featureTab === "regional" && <RegionalPanel />}
                {!featureTab && (
                  <div className="panel-empty" style={{ padding: 20 }}>
                    Select a feature above to get started
                  </div>
                )}
              </div>
            </div>
          ) : sidebarTab === "finetune" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%", overflow: "auto" }}>
              <FineTuningPanel workspaceId={getCurrentWorkspace()?.workspace_id} />
              <div style={{ padding: "8px 4px 4px" }}>
                <div className="section-header"><span className="section-label">RAG Evaluation</span></div>
                <RAGAsDashboard />
              </div>
            </div>
          ) : sidebarTab === "analyze" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%", overflow: "auto" }}>
              <DomainPanel selectedFile={selectedFile} documents={documents} workspaceId={getCurrentWorkspace()?.workspace_id} />

              {/* Table & Chart extraction */}
              {selectedFile && (
                <div style={{ padding: "8px 4px 4px" }}>
                  <div className="section-header" style={{ justifyContent: "space-between" }}>
                    <span className="section-label">Tables & Charts</span>
                    <button
                      className="sidebar-link-btn"
                      style={{ fontSize: 10, padding: "2px 8px" }}
                      onClick={handleExtract}
                      disabled={extracting}
                    >
                      {extracting ? "Extracting…" : "⚡ Extract"}
                    </button>
                  </div>
                  {extractionResults && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {extractionResults.tables?.length > 0 ? (
                        extractionResults.tables.map((t, i) => (
                          <TableViewer
                            key={t.table_id || i}
                            tableId={t.table_id}
                            summary={t.summary}
                            tableType={t.table_type}
                            rowCount={t.row_count}
                            colCount={t.col_count}
                          />
                        ))
                      ) : (
                        <div style={{ fontSize: 11, color: "var(--text-4)", padding: "4px 0" }}>No tables found</div>
                      )}
                      {extractionResults.charts?.map((c, i) => (
                        <ChartViewer key={i} chart={c} />
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Graph query */}
              <div style={{ padding: "4px 0" }}>
                <div className="section-header"><span className="section-label">Graph Query</span></div>
                <GraphQueryPanel />
              </div>
            </div>
          ) : sidebarTab === "history" ? (
            <ConversationHistory
              conversations={conversations}
              activeSessionId={sessionId}
              onSelect={async (sid) => { await loadSession(sid); setSidebarTab("docs"); }}
              onDelete={removeConv}
              onClearAll={clearConvHistory}
              onNewChat={() => { newConversation(); setSidebarTab("docs"); }}
            />
          ) : sidebarTab === "stats" ? (
            <div style={{ padding: "4px 0", height: "100%", overflow: "auto" }}>
              {/* Full monitoring dashboard */}
              <div className="section-header"><span className="section-label">Monitoring</span></div>
              <MonitoringDashboard />

              {/* Quick counts */}
              <div className="stats-grid" style={{ marginTop: 8 }}>
                <div className="stat-card">
                  <div className="stat-value">{documents.length}</div>
                  <div className="stat-label">Documents</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">
                    {documents.reduce((s, d) => s + (d.chunk_count || 0), 0)}
                  </div>
                  <div className="stat-label">Chunks</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{messages.length}</div>
                  <div className="stat-label">Messages</div>
                </div>
              </div>

              <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">User</span></div>
              <div style={{ fontSize: 11, color: "var(--text-3)", padding: "6px 4px", lineHeight: 1.8 }}>
                <div>{user?.email}</div>
                <div style={{ color: "var(--text-4)" }}>
                  {currentWorkspace?.name || currentWorkspace?.workspace_id?.slice(0,8) || "Default"}
                </div>
              </div>
              <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Document Health</span></div>
              <button
                className="sidebar-link-btn"
                style={{ marginBottom: 4 }}
                onClick={async () => {
                  const wsId = getCurrentWorkspace()?.workspace_id;
                  try {
                    const data = await api.findDuplicates(wsId);
                    if (data.exact_duplicate_groups === 0) {
                      toast.success(`No duplicates found across ${data.documents_scanned} documents`);
                    } else {
                      toast(`⚠ Found ${data.exact_duplicate_groups} duplicate group(s) in ${data.documents_scanned} documents`, {
                        icon: "⚠️",
                        duration: 6000,
                      });
                    }
                  } catch { toast.error("Duplicate check failed"); }
                }}
              >
                Find Duplicates
              </button>

              <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Audit Trail</span></div>
              <button
                className="sidebar-link-btn"
                style={{ marginBottom: 4 }}
                onClick={() => {
                  const wsId = getCurrentWorkspace()?.workspace_id;
                  api.getMonitoringStats(wsId, 720).then(data => {
                    const rows = [["metric", "value", "workspace", "timestamp"]];
                    const s = data.stats || data || {};
                    rows.push(["query_count", s.query_count ?? 0, wsId || "", new Date().toISOString()]);
                    rows.push(["avg_latency_ms", s.avg_latency_ms ?? 0, wsId || "", new Date().toISOString()]);
                    rows.push(["total_documents", documents.length, wsId || "", new Date().toISOString()]);
                    rows.push(["total_chunks", documents.reduce((a, d) => a + (d.chunk_count || 0), 0), wsId || "", new Date().toISOString()]);
                    const csv = rows.map(r => r.map(v => `"${v}"`).join(",")).join("\n");
                    const blob = new Blob([csv], { type: "text/csv" });
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(blob);
                    a.download = `documind-audit-${Date.now()}.csv`;
                    a.click();
                    URL.revokeObjectURL(a.href);
                  }).catch(() => toast.error("Could not fetch audit data"));
                }}
              >
                ↓ Download Audit CSV
              </button>

              <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">API Keys</span></div>
              <ApiKeyPanel />

              <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">Links</span></div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: "4px 0" }}>
                <button className="sidebar-link-btn" onClick={() => window.open(`${import.meta.env.VITE_API_URL || "http://localhost:8000"}/docs`, "_blank")}>
                  API Docs ↗
                </button>
                <button className="sidebar-link-btn" onClick={() => window.open(`${import.meta.env.VITE_API_URL || "http://localhost:8000"}/health`, "_blank")}>
                  Health ↗
                </button>
              </div>
            </div>
          ) : (
          <>
          {/* Workspace switcher */}
          {user && workspaces?.length > 1 && (
            <div>
              <div className="section-header" style={{ marginBottom: 6 }}>
                <span className="section-label">Workspace</span>
              </div>
              <WorkspaceSwitcher user={user} workspaces={workspaces} onSwitch={handleWorkspaceSwitch} />
            </div>
          )}

          {/* Upload */}
          <div>
            <div className="section-header">
              <span className="section-label">Upload</span>
            </div>
            <DropZone
              onDrop={handleUpload}
              uploading={uploading}
              progress={progress}
              visionEnabled={visionEnabled}
              onVisionChange={setVisionEnabled}
              batchQueue={batchQueue}
            />
          </div>

          {/* Audio / Office file uploader */}
          <div>
            <div className="section-header">
              <span className="section-label">Audio & Office Files</span>
            </div>
            <AudioUploader onSuccess={() => refreshDocuments()} />
          </div>

          {/* Live ingest progress */}
          <IngestProgressPanel />

          {/* URL Watcher (ingest + watch) */}
          <div>
            <div className="section-header">
              <span className="section-label">Web URLs</span>
            </div>
            <UrlWatcher
              workspaceId={getCurrentWorkspace()?.workspace_id}
              onRefreshed={refreshDocuments}
            />
          </div>

          {/* Active filter badge */}
          {selectedFile && (
            <div className="active-filter">
              <span className="active-filter-label">Filter</span>
              <span className="active-filter-name" title={selectedFile}>{shortFileName}</span>
              <button
                className="active-filter-close"
                onClick={() => setSelectedFile(null)}
                aria-label={`Remove filter: ${shortFileName}`}
              >
                <IconClose />
              </button>
            </div>
          )}

          {/* Version timeline — shown when a doc is selected */}
          {selectedFile && (
            <div>
              <div className="section-header" style={{ justifyContent: "space-between" }}>
                <span className="section-label">Versions</span>
                <button
                  className="sidebar-link-btn"
                  style={{ fontSize: 10, padding: "2px 8px" }}
                  onClick={() => setShowPdfViewer(v => !v)}
                  title={showPdfViewer ? "Hide PDF viewer" : "View PDF"}
                >
                  {showPdfViewer ? "✕ PDF" : "📄 PDF"}
                </button>
              </div>
              <VersionTimeline sourceFile={selectedFile} />
            </div>
          )}

          <div style={{ flex: 1, minHeight: 0 }}>
            <div className="section-header">
              <span className="section-label">Library</span>
              {documents.length > 0 && (
                <span className="section-count">{documents.length}</span>
              )}
            </div>

            {loadingDocs ? (
              <div style={{ padding: "20px 0", display: "flex", flexDirection: "column", gap: 6 }}>
                {[1,2,3].map(i => (
                  <div key={i} style={{
                    height: 48, borderRadius: "var(--r)",
                    background: "var(--bg-3)",
                    animation: "pulse 1.5s ease infinite",
                    animationDelay: `${i * 0.15}s`,
                  }} />
                ))}
              </div>
            ) : loadError ? (
              <div style={{ padding: "16px 8px", textAlign: "center", color: "var(--red)", fontSize: 12 }} role="alert">
                {loadError}
              </div>
            ) : (
              <DocumentList
                documents={documents}
                selectedFile={selectedFile}
                onSelect={setSelectedFile}
                onDeleted={handleDocumentDeleted}
                workspaceId={getCurrentWorkspace()?.workspace_id}
              />
            )}
          </div>
          </>
          )}
        </div>
      </aside>

      {/* ── Chat Main ───────────────────────────────────── */}
      <main className="chat-main" role="main" aria-label="Chat interface">
        {/* Topbar */}
        <div className="topbar">
          <button
            className="topbar-btn"
            onClick={() => setSidebarOpen(v => !v)}
            aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
            aria-expanded={sidebarOpen}
            aria-controls="sidebar"
          >
            <IconMenu />
          </button>

          {/* Context */}
          <div className="topbar-context">
            <div className="topbar-dot" aria-hidden="true" />
            <span className="topbar-text">
              {selectedFile ? `Querying: ${shortFileName}` : "All documents"}
            </span>
            {currentWorkspace && (
              <span className="topbar-workspace" aria-hidden="true">
                · {currentWorkspace.name || currentWorkspace.workspace_id}
              </span>
            )}
          </div>

          {/* Mode switcher */}
          <div className="topbar-mode-switcher" role="group" aria-label="Query mode">
            {[
              { id: "rag",   label: "RAG" },
              { id: "agent", label: "Agent" },
              { id: "graph", label: "Graph" },
            ].map(m => (
              <button
                key={m.id}
                className={`mode-btn${queryMode === m.id ? " active" : ""}`}
                onClick={() => setQueryMode(m.id)}
                aria-pressed={queryMode === m.id}
              >
                {m.label}
              </button>
            ))}
          </div>

          {/* Actions */}
          <div className="topbar-actions">
            {documents.length >= 2 && (
              <button
                className="topbar-action-btn"
                onClick={() => setShowCompare(true)}
                aria-label="Compare documents"
                title="Compare 2 documents"
              >
                ⇔ Compare
              </button>
            )}
            {messages.length > 0 && (
              <>
                <button
                  className="topbar-action-btn"
                  onClick={handleExportConversation}
                  aria-label="Export conversation as Markdown"
                  title="Export as Markdown"
                >
                  ↓ MD
                </button>
                <button
                  className="topbar-action-btn"
                  onClick={handleExportPDF}
                  aria-label="Export conversation as PDF"
                  title="Export as PDF Report"
                >
                  ↓ PDF
                </button>
                <button className="topbar-action-btn danger" onClick={clear} aria-label="Clear conversation">
                  <IconClear /> Clear
                </button>
              </>
            )}
            <button
              className="topbar-btn"
              onClick={() => setTheme(t => t === "dark" ? "light" : "dark")}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light mode" : "Dark mode"}
              style={{ fontSize: 14 }}
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
            <span className="badge badge-violet" style={{ fontSize: 10, padding: "3px 8px" }}>
              <IconSpark /> {queryMode.toUpperCase()}
            </span>
          </div>
        </div>

        {/* Doc Brief Banner */}
        {docBrief && (
          <div className="doc-brief-banner anim-fade-in">
            <div className="doc-brief-icon">📄</div>
            <div className="doc-brief-body">
              <div className="doc-brief-title">
                {docBrief.file?.split("/").pop()?.split("\\").pop()}
              </div>
              {docBrief.loading ? (
                <div className="doc-brief-loading">Generating brief…</div>
              ) : (
                <div className="doc-brief-text">{docBrief.summary}</div>
              )}
            </div>
            <button className="doc-brief-close" onClick={() => setDocBrief(null)} aria-label="Dismiss brief">✕</button>
          </div>
        )}

        {/* Agent reasoning steps — visible when Agent mode is active */}
        {queryMode === "agent" && agentSteps.length > 0 && (
          <AgentStepsPanel steps={agentSteps} isStreaming={isStreaming} />
        )}

        {/* PDF side-panel — slides in when user clicks 📄 PDF in sidebar */}
        {showPdfViewer && selectedFile && (
          <div className="pdf-panel anim-fade-in">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px", borderBottom: "1px solid var(--border)" }}>
              <span style={{ fontSize: 12, color: "var(--text-3)", fontWeight: 600 }}>
                📄 {selectedFile.split("/").pop().split("\\").pop()}
              </span>
              <button className="topbar-btn" onClick={() => setShowPdfViewer(false)} aria-label="Close PDF viewer">✕</button>
            </div>
            <ErrorBoundary>
              <PDFViewer
                sourceFile={selectedFile}
                citations={messages.flatMap(m => m.citations || [])}
              />
            </ErrorBoundary>
          </div>
        )}

        {/* Chat */}
        <ErrorBoundary>
          <ChatWindow messages={messages} isStreaming={isStreaming} onSuggestion={handleSubmit} />
        </ErrorBoundary>

        {/* Input */}
        <ChatInput
          onSubmit={handleSubmit}
          onCancel={cancel}
          isStreaming={isStreaming}
          disabled={(documents.length === 0 && !isStreaming) || loadingDocs}
          placeholder={
            loadingDocs
              ? "Loading documents…"
              : documents.length === 0
              ? "Upload a document to get started…"
              : selectedFile
              ? `Ask anything about ${shortFileName}…`
              : `Ask anything · ${queryMode.toUpperCase()} mode · Ctrl+K`
          }
        />
      </main>

      {/* Document Comparison Modal */}
      {showCompare && (
        <DocCompare
          documents={documents}
          workspaceId={getCurrentWorkspace()?.workspace_id}
          onClose={() => setShowCompare(false)}
        />
      )}

      {/* Mobile overlay */}
      {sidebarOpen && typeof window !== "undefined" && window.innerWidth <= 900 && (
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
    </div>
  );
}
