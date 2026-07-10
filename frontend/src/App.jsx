import { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { Toaster, toast } from "react-hot-toast";
import { useStreamQuery } from "./hooks/useStreamQuery";
import { useIngest } from "./hooks/useIngest";
import { useAuth } from "./hooks/useAuth";
import { useDocuments } from "./hooks/useDocuments";
import { useUIPrefs } from "./hooks/useUIPrefs";
import { useAgentSteps } from "./hooks/useAgentSteps";
import { useDocBrief } from "./hooks/useDocBrief";
import { useExtraction } from "./hooks/useExtraction";
import { ChatWindow } from "./components/ChatWindow";
import { ChatInput } from "./components/ChatInput";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoginForm } from "./components/LoginForm";
import { AgentStepsPanel } from "./components/AgentStepsPanel";
const PDFViewer = lazy(() => import("./components/PDFViewer").then(m => ({ default: m.PDFViewer })));
import { DocCompare } from "./components/DocCompare";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { useConversationHistory } from "./hooks/useConversationHistory";
import { isDemoMode } from "./api/demo";
import { downloadConversationMarkdown, printConversationPdf } from "./utils/conversationExport";
import "./App.css";

export default function App() {
  const { user, workspaces, loading: authLoading, login, register, getCurrentWorkspace } = useAuth();
  const { messages, isStreaming, submit, cancel, clear, newConversation, loadSession, sessionId } = useStreamQuery();
  const { conversations, addOrUpdate: addConvHistory, remove: removeConv, clearAll: clearConvHistory } = useConversationHistory();

  const { theme, toggleTheme, visionEnabled, setVisionEnabled, sidebarOpen, setSidebarOpen, toggleSidebar } = useUIPrefs();

  const { documents, loadingDocs, loadError, refresh: refreshDocuments, retryRef } = useDocuments({ getCurrentWorkspace, user });

  const [selectedFile, setSelectedFile] = useState(null);
  const [queryMode,    setQueryMode]    = useState("rag");
  const [showCompare,  setShowCompare]  = useState(false);
  const [showPdfViewer, setShowPdfViewer] = useState(false);

  const abortRef = useRef(null);

  const { docBrief, triggerDocBrief, dismissDocBrief } = useDocBrief();
  const { extractionResults, extracting, handleExtract } = useExtraction({ selectedFile, getCurrentWorkspace });

  const lastAssistantMsg = messages.filter(m => m.role === "assistant").pop();
  const { agentSteps } = useAgentSteps({
    queryMode,
    isStreaming,
    lastStatusStep: lastAssistantMsg?.statusStep,
    sessionId,
  });

  // Initial document load with exponential-backoff retry
  useEffect(() => {
    if (!user) return;
    let retries = 0;
    const tryLoad = async () => {
      const ok = await refreshDocuments();
      if (!ok && retries < 3) {
        retries++;
        retryRef.current = setTimeout(tryLoad, 1000 * Math.pow(2, retries - 1));
      }
    };
    tryLoad();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [refreshDocuments, user]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Track conversation in history whenever messages change
  useEffect(() => {
    if (messages.length < 2) return;
    const firstUser = messages.find(m => m.role === "human");
    if (!firstUser?.content) return;
    const msgCount = messages.filter(m => !m.streaming).length;
    addConvHistory(sessionId, firstUser.content, msgCount);
  }, [messages, sessionId, addConvHistory]);

  const handleWorkspaceSwitch = useCallback((wsId) => {
    setSelectedFile(null);
    clear();
    refreshDocuments(wsId);
  }, [clear, refreshDocuments]);

  const { upload, uploadBatch, uploading, progress, batchQueue } = useIngest(() => refreshDocuments());

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
    if (selectedFile === file) setSelectedFile(null);
    refreshDocuments();
  }, [selectedFile, refreshDocuments]);

  const handleExportConversation = useCallback(() => downloadConversationMarkdown(messages), [messages]);
  const handleExportPDF          = useCallback(() => printConversationPdf(messages),         [messages]);

  const shortFileName    = selectedFile
    ? selectedFile.split("/").pop().split("\\").pop()
    : null;
  const currentWorkspace = getCurrentWorkspace();
  const demoMode         = isDemoMode();

  if (authLoading) {
    return (
      <div className="loading-screen">
        <div className="loading-logo">D</div>
        <div className="loading-spinner" />
        <div className="loading-text">Loading DocuMind AI…</div>
      </div>
    );
  }

  if (!user) {
    return <LoginForm onLogin={login} onRegister={register} />;
  }

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

      {sidebarOpen && (
        <div
          className="sidebar-backdrop"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      <main className="chat-main" role="main" aria-label="Chat interface">
        <Topbar
          sidebarOpen={sidebarOpen}
          onToggleSidebar={toggleSidebar}
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
          onToggleTheme={toggleTheme}
        />

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
            <button className="doc-brief-close" onClick={dismissDocBrief} aria-label="Dismiss brief">✕</button>
          </div>
        )}

        {queryMode === "agent" && agentSteps.length > 0 && (
          <AgentStepsPanel steps={agentSteps} isStreaming={isStreaming} />
        )}

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

        <ErrorBoundary>
          <ChatWindow messages={messages} isStreaming={isStreaming} onSuggestion={handleSubmit} />
        </ErrorBoundary>

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

      {showCompare && (
        <DocCompare
          documents={documents}
          workspaceId={currentWorkspace?.workspace_id}
          onClose={() => setShowCompare(false)}
        />
      )}

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
