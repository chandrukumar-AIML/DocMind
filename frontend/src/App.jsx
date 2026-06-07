// frontend/src/App.jsx — DocuMind AI v2 — Nebula Dark Design
import { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { Toaster, toast } from "react-hot-toast";
import { useStreamQuery } from "./hooks/useStreamQuery";
import { useIngest } from "./hooks/useIngest";
import { useAuth } from "./hooks/useAuth";
import { ChatWindow } from "./components/ChatWindow";
import { ChatInput } from "./components/ChatInput";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoginForm } from "./components/LoginForm";
import { AgentStepsPanel } from "./components/AgentStepsPanel";
// Lazy-loaded: pulls in the heavy pdf.js vendor chunk (~400KB) only when the
// user actually opens the PDF side-panel, keeping the initial bundle lean.
const PDFViewer = lazy(() => import("./components/PDFViewer").then(m => ({ default: m.PDFViewer })));
import { DocCompare } from "./components/DocCompare";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { useConversationHistory } from "./hooks/useConversationHistory";
import { api } from "./api/client";
import { isDemoMode } from "./api/demo";
import { downloadConversationMarkdown, printConversationPdf } from "./utils/conversationExport";
import "./App.css";

// ── Main App ──────────────────────────────────────────────────
export default function App() {
  const [documents,         setDocuments]         = useState([]);
  const [selectedFile,      setSelectedFile]      = useState(null);
  const [queryMode,         setQueryMode]         = useState("rag"); // rag | agent | graph
  const [visionEnabled,     setVisionEnabled]     = useState(() => {
    try { return localStorage.getItem("dm_vision") === "true"; } catch { return false; }
  });
  const [sidebarOpen,       setSidebarOpen]       = useState(() => {
    try { return window.innerWidth > 900; } catch { return true; }
  });
  const [loadingDocs,       setLoadingDocs]       = useState(true);
  const [loadError,         setLoadError]         = useState(null);
  const [docBrief,          setDocBrief]          = useState(null); // { file, summary, loading }
  const [showCompare,       setShowCompare]       = useState(false);
  const [theme,             setTheme]             = useState(() => {
    try { return localStorage.getItem("dm_theme") || "dark"; } catch { return "dark"; }
  });
  const [agentSteps,        setAgentSteps]        = useState([]);
  const [showPdfViewer,     setShowPdfViewer]     = useState(false);
  const [extractionResults, setExtractionResults] = useState(null); // { tables, charts }
  const [extracting,        setExtracting]        = useState(false);

  const { user, workspaces, loading: authLoading, login, register, getCurrentWorkspace } = useAuth();
  const { messages, isStreaming, submit, cancel, clear, newConversation, loadSession, sessionId } = useStreamQuery();
  const { conversations, addOrUpdate: addConvHistory, remove: removeConv, clearAll: clearConvHistory } = useConversationHistory();
  const retryTimeoutRef = useRef(null);
  const abortRef        = useRef(null);

  useEffect(() => {
    try { localStorage.setItem("dm_vision", String(visionEnabled)); } catch { /* storage unavailable */ }
  }, [visionEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem("dm_theme", theme);
      // "theme-light" drives the custom-CSS light overrides; "dark" drives Tailwind's
      // class-based dark: variant (see tailwind.config.js darkMode: "class").
      document.documentElement.className = theme === "light" ? "theme-light" : "dark";
    } catch { /* storage unavailable */ }
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
    const wsId    = getCurrentWorkspace()?.workspace_id;
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

  const handleExportConversation = useCallback(() => downloadConversationMarkdown(messages), [messages]);
  const handleExportPDF          = useCallback(() => printConversationPdf(messages),         [messages]);

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
  const lastStatusStep   = lastAssistantMsg?.statusStep;

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

  // ── Table / Chart extraction ───────────────────────────────
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
        tables: tabRes.status  === "fulfilled" ? (tabRes.value?.tables   || []) : [],
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

  const shortFileName    = selectedFile
    ? selectedFile.split("/").pop().split("\\").pop()
    : null;
  const currentWorkspace = getCurrentWorkspace();
  const demoMode         = isDemoMode();

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
            background: "#1A2235", color: "#F1F5F9",
            border: "1px solid rgba(148,163,184,0.18)",
            borderRadius: "10px", fontSize: "13px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
          },
          success: { duration: 4000, iconTheme: { primary: "#10B981", secondary: "#fff" } },
          error:   { duration: 6000, iconTheme: { primary: "#EF4444", secondary: "#fff" } },
        }}
      />

      {/* ── Sidebar ──────────────────────────────────────── */}
      <Sidebar
        sidebarOpen={sidebarOpen}
        workspace={{
          user,
          workspaces,
          current:  currentWorkspace,
          id:       currentWorkspace?.workspace_id,
          onSwitch: handleWorkspaceSwitch,
        }}
        docs={{
          list:    documents,
          loading: loadingDocs,
          error:   loadError,
        }}
        selection={{
          file:      selectedFile,
          shortName: shortFileName,
          onSelect:  setSelectedFile,
          onDelete:  handleDocumentDeleted,
        }}
        upload={{
          onUpload:  handleUpload,
          uploading,
          progress,
          vision:    visionEnabled,
          onVision:  setVisionEnabled,
          batch:     batchQueue,
          onRefresh: refreshDocuments,
        }}
        pdf={{
          show:     showPdfViewer,
          onToggle: () => setShowPdfViewer(v => !v),
        }}
        extract={{
          loading:   extracting,
          results:   extractionResults,
          onExtract: handleExtract,
        }}
        messages={messages}
        history={{
          conversations,
          activeId:  sessionId,
          onSelect:  loadSession,
          onDelete:  removeConv,
          onClear:   clearConvHistory,
          onNew:     newConversation,
        }}
      />

      {/* ── Chat Main ────────────────────────────────────── */}
      <main className="chat-main" role="main" aria-label="Chat interface">
        <Topbar
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(v => !v)}
          selectedFile={selectedFile}
          shortFileName={shortFileName}
          currentWorkspace={currentWorkspace}
          demoMode={demoMode}
          queryMode={queryMode}
          onModeChange={setQueryMode}
          documents={documents}
          messages={messages}
          onCompare={() => setShowCompare(true)}
          onExportMarkdown={handleExportConversation}
          onExportPdf={handleExportPDF}
          onClear={clear}
          theme={theme}
          onToggleTheme={() => setTheme(t => t === "dark" ? "light" : "dark")}
        />

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

        {/* Agent reasoning steps */}
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
              <Suspense fallback={<div className="panel-empty" style={{ padding: 24 }}>Loading PDF viewer…</div>}>
                <PDFViewer
                  sourceFile={selectedFile}
                  citations={messages.flatMap(m => m.citations || [])}
                />
              </Suspense>
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
          workspaceId={currentWorkspace?.workspace_id}
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
