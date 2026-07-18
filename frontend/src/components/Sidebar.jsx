// frontend/src/components/Sidebar.jsx — DocuMind AI left-hand panel
// Manages its own tab/feature/stats state so App.jsx stays lean.
// All data dependencies arrive as grouped prop objects (see JSDoc below).
import { useState, useEffect } from "react";
import { toast } from "react-hot-toast";
import { DropZone }             from "./DropZone";
import { DocumentList }         from "./DocumentList";
import { WorkspaceSwitcher }    from "./WorkspaceSwitcher";
import { DomainPanel, detectDomain } from "./DomainPanel";
import { ConversationHistory }  from "./ConversationHistory";
import { VersionTimeline }      from "./VersionTimeline";
import { FineTuningPanel }      from "./FineTuningPanel";
import { UrlWatcher }           from "./UrlWatcher";
import { IngestProgressPanel }  from "./IngestProgressPanel";
import { AudioUploader }        from "./AudioUploader";
import { MonitoringDashboard }  from "./MonitoringDashboard";
import { GraphQueryPanel }      from "./GraphQueryPanel";
import { RAGAsDashboard }       from "./RAGAsDashboard";
import { TableViewer }          from "./TableViewer";
import { ChartViewer }          from "./ChartViewer";
import { ApiKeyPanel }          from "./ApiKeyPanel";
import { WebhookPanel }         from "./WebhookPanel";
import { ComparisonPanel }      from "./ComparisonPanel";
import { WorkflowPanel }        from "./WorkflowPanel";
import { AnnotationPanel }      from "./AnnotationPanel";
import { TemplatePanel }        from "./TemplatePanel";
import { ESignPanel }           from "./ESignPanel";
import { CompliancePanel }      from "./CompliancePanel";
import { SuperAdminPanel }      from "./SuperAdminPanel";
import { OnboardingPanel }      from "./OnboardingPanel";
import { RegionalPanel }        from "./RegionalPanel";
import { LlmSettingsPanel }     from "./LlmSettingsPanel";
import { BillingPanel }         from "./BillingPanel";
import { SsoSettingsPanel }     from "./SsoSettingsPanel";
import { IconClose }            from "./Icons";
import { ClientPanel }          from "./ClientPanel";
import { DraftReplyPanel, isNoticeFile } from "./DraftReplyPanel";
import { DeadlineDashboard }    from "./DeadlineDashboard";
import { DiscrepancyPanel }     from "./DiscrepancyPanel";
import { WhatsAppUploader }     from "./WhatsAppUploader";
import { RegulatoryPanel }      from "./RegulatoryPanel";
import { GstinLookupPanel }     from "./GstinLookupPanel";
import { ItrComparisonPanel }   from "./ItrComparisonPanel";
import { RegulatoryFeedPanel }  from "./RegulatoryFeedPanel";
import { HindiDocPanel }        from "./HindiDocPanel";
import { AuditTrailPanel }      from "./AuditTrailPanel";
import { api }                  from "../api/client";

// ── Static lookup tables ─────────────────────────────────────────────────────

const NAV_TABS = [
  ["docs",     "Docs"],
  ["history",  "History"],
  ["settings", "⚙ Settings"],
];

// Settings sub-tabs (power-user / admin features)
const SETTINGS_TABS = [
  ["analysis", "Analysis"],
  ["train",    "Train & Eval"],
  ["monitor",  "Monitor"],
  ["advanced", "Advanced"],
];

// ── UsageMeter ───────────────────────────────────────────────────────────────

function UsageMeter({ label, used, limit, style }) {
  const unlimited = limit === null || limit === undefined;
  const pct = unlimited ? 0 : Math.min(100, Math.round((used / limit) * 100));
  const warn = !unlimited && pct >= 80;
  const color = pct >= 100 ? "var(--red, #ef4444)" : warn ? "var(--yellow, #f59e0b)" : "var(--teal, #0d9488)";
  return (
    <div style={{ fontSize: 10, color: "var(--tx-2)", ...style }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
        <span>{label}</span>
        <span style={{ color: warn ? color : undefined }}>
          {unlimited ? `${used} / ∞` : `${used} / ${limit}`}
        </span>
      </div>
      {!unlimited && (
        <div style={{ height: 3, background: "var(--bg-3)", borderRadius: 2, overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2, transition: "width .4s" }} />
        </div>
      )}
    </div>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * Sidebar
 *
 * Props (grouped objects keep the call-site tidy):
 *   sidebarOpen  boolean
 *   workspace    { user, workspaces, current, id, onSwitch }
 *   docs         { list, loading, error }
 *   selection    { file, shortName, onSelect, onDelete }
 *   upload       { onUpload, uploading, progress, vision, onVision, batch, onRefresh }
 *   pdf          { show, onToggle }
 *   extract      { loading, results, onExtract }
 *   messages     Message[]
 *   history      { conversations, activeId, onSelect, onDelete, onClear, onNew }
 */
export function Sidebar({
  sidebarOpen,
  workspace,
  docs,
  selection,
  upload,
  pdf,
  extract,
  messages,
  history,
}) {
  // ── Internal state (sidebar-only; no need to hoist to App) ───────────────
  const [sidebarTab,       setSidebarTab]       = useState("docs");
  const [featureTab,       setFeatureTab]       = useState(null);
  const [stats,            setStats]            = useState(null);
  const [settingsTab,      setSettingsTab]      = useState("analysis");
  const [uploadOpen,       setUploadOpen]       = useState(false);
  const [selectedClientId, setSelectedClientId] = useState(null);
  const [docMap,           setDocMap]           = useState({});
  const [statusMap,        setStatusMap]        = useState({});

  // ── Destructure prop groups ──────────────────────────────────────────────
  const { user, workspaces, current: currentWorkspace, id: workspaceId, onSwitch } = workspace;
  const { list: documents, loading: loadingDocs, error: loadError } = docs;
  const { file: selectedFile, shortName: shortFileName, onSelect: onSelectFile, onDelete: onDocumentDeleted } = selection;
  const { onUpload, uploading, progress, vision: visionEnabled, onVision: onVisionChange, batch: batchQueue, onRefresh: onRefreshDocuments } = upload;
  const { show: showPdfViewer, onToggle: onTogglePdf } = pdf;
  const { loading: extracting, results: extractionResults, onExtract } = extract;
  const { conversations, activeId: sessionId, onSelect: onSelectConv, onDelete: onDeleteConv, onClear: onClearHistory, onNew: onNewChat } = history;

  // ── Handlers ─────────────────────────────────────────────────────────────

  // ── Usage limits ─────────────────────────────────────────────────────────
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    if (!workspaceId) return;
    api.getBillingUsage().then(setUsage).catch(() => {});
    api.getDocStatusMap(workspaceId).then(setStatusMap).catch(() => {});
  }, [workspaceId]);

  const handleStatsTabClick = () => {
    setSidebarTab("settings");
    setSettingsTab("monitor");
    if (!stats) {
      api.getMonitoringStats(workspaceId).then(setStats).catch(() => setStats({}));
    }
  };

  /** Load a conversation then auto-switch to the Docs tab. */
  const handleHistorySelect = async (sid) => {
    await onSelectConv(sid);
    setSidebarTab("docs");
  };

  const handleFindDuplicates = async () => {
    try {
      const data = await api.findDuplicates(workspaceId);
      if (data.exact_duplicate_groups === 0) {
        toast.success(`No duplicates found across ${data.documents_scanned} documents`);
      } else {
        toast(`⚠ Found ${data.exact_duplicate_groups} duplicate group(s) in ${data.documents_scanned} documents`, {
          icon: "⚠️", duration: 6000,
        });
      }
    } catch { toast.error("Duplicate check failed"); }
  };

  const handleDownloadAudit = () => {
    api.getMonitoringStats(workspaceId, 720).then(data => {
      const rows = [["metric", "value", "workspace", "timestamp"]];
      const s    = data.stats || data || {};
      rows.push(["query_count",    s.query_count    ?? 0, workspaceId || "", new Date().toISOString()]);
      rows.push(["avg_latency_ms", s.avg_latency_ms ?? 0, workspaceId || "", new Date().toISOString()]);
      rows.push(["total_documents", documents.length, workspaceId || "", new Date().toISOString()]);
      rows.push(["total_chunks", documents.reduce((a, d) => a + (d.chunk_count || 0), 0), workspaceId || "", new Date().toISOString()]);
      const csv  = rows.map(r => r.map(v => `"${v}"`).join(",")).join("\n");
      const blob = new Blob([csv], { type: "text/csv" });
      const a    = document.createElement("a");
      a.href     = URL.createObjectURL(blob);
      a.download = `documind-audit-${Date.now()}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    }).catch(() => toast.error("Could not fetch audit data"));
  };

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <aside
      id="sidebar"
      className={`sidebar${sidebarOpen ? "" : " collapsed"}`}
      aria-label="Document library"
    >

      {/* ── Header ───────────────────────────────────────────────────────── */}
      <div className="sidebar-header">
        <div className="logo-mark" aria-hidden="true">D</div>
        <div className="logo-text">
          <div className="app-name">DocuMind AI</div>
          <div className="app-tagline">Intelligent Document AI</div>
        </div>
      </div>

      {/* ── Nav Tabs ─────────────────────────────────────────────────────── */}
      <div className="sidebar-nav" role="tablist">
        {NAV_TABS.map(([id, label]) => (
          <button
            key={id}
            className={`nav-tab${sidebarTab === id ? " active" : ""}`}
            role="tab"
            aria-selected={sidebarTab === id}
            onClick={() => setSidebarTab(id)}
          >
            {label}
            {id === "history" && conversations.length > 0 && (
              <span className="nav-tab-badge">{conversations.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="sidebar-body">

        {/* ─ Settings ─ */}
        {sidebarTab === "settings" && (
          <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div className="sidebar-feature-tabs">
              {SETTINGS_TABS.map(([id, label]) => (
                <button
                  key={id}
                  className={`sidebar-feature-tab${settingsTab === id ? " active" : ""}`}
                  onClick={() => setSettingsTab(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "4px 0" }}>

              {/* Analysis sub-tab */}
              {settingsTab === "analysis" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                  <DomainPanel selectedFile={selectedFile} documents={documents} workspaceId={workspaceId} />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">Discrepancy Scanner</span>
                  </div>
                  <DiscrepancyPanel documents={documents} workspaceId={workspaceId} />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">GSTIN Lookup</span>
                  </div>
                  <GstinLookupPanel />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">ITR Year-on-Year</span>
                  </div>
                  <ItrComparisonPanel documents={documents} workspaceId={workspaceId} />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">Regulatory Reference</span>
                  </div>
                  <RegulatoryPanel />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">Regulatory Updates Feed</span>
                  </div>
                  <RegulatoryFeedPanel />
                  <div className="section-header" style={{ marginTop: 12 }}>
                    <span className="section-label">Vernacular / Hindi</span>
                  </div>
                  <HindiDocPanel selectedFile={selectedFile} workspaceId={workspaceId} />
                </div>
              )}

              {/* Train & Eval sub-tab */}
              {settingsTab === "train" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                  <FineTuningPanel workspaceId={workspaceId} />
                  <div style={{ padding: "8px 4px 4px" }}>
                    <div className="section-header"><span className="section-label">RAG Evaluation</span></div>
                    <RAGAsDashboard />
                  </div>
                </div>
              )}

              {/* Monitor sub-tab */}
              {settingsTab === "monitor" && (
                <div>
                  <div className="section-header"><span className="section-label">Monitoring</span></div>
                  <MonitoringDashboard />
                  <div className="stats-grid" style={{ marginTop: 8 }}>
                    <div className="stat-card">
                      <div className="stat-value">{documents.length}</div>
                      <div className="stat-label">Documents</div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-value">{documents.reduce((s, d) => s + (d.chunk_count || 0), 0)}</div>
                      <div className="stat-label">Chunks</div>
                    </div>
                    <div className="stat-card">
                      <div className="stat-value">{messages.length}</div>
                      <div className="stat-label">Messages</div>
                    </div>
                  </div>
                  <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Document Health</span></div>
                  <button className="sidebar-link-btn" style={{ marginBottom: 4 }} onClick={handleFindDuplicates}>
                    Find Duplicates
                  </button>
                  <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Audit Trail</span></div>
                  <AuditTrailPanel workspaceId={workspaceId} selectedFile={selectedFile} />
                </div>
              )}

              {/* Advanced sub-tab */}
              {settingsTab === "advanced" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                  <div className="section-header"><span className="section-label">User</span></div>
                  <div style={{ fontSize: 11, color: "var(--text-3)", padding: "6px 4px", lineHeight: 1.8 }}>
                    <div>{user?.email}</div>
                    <div style={{ color: "var(--text-4)" }}>
                      {currentWorkspace?.name || currentWorkspace?.workspace_id?.slice(0, 8) || "Default"}
                    </div>
                  </div>

                  <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">API Keys</span></div>
                  <ApiKeyPanel />

                  <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">LLM Settings</span></div>
                  <LlmSettingsPanel />

                  <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">Billing</span></div>
                  <BillingPanel />

                  <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">Web URL Watcher</span></div>
                  <UrlWatcher workspaceId={workspaceId} onRefreshed={onRefreshDocuments} />

                  <div className="section-header" style={{ marginTop: 8 }}><span className="section-label">Webhooks</span></div>
                  <WebhookPanel />

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
              )}
            </div>
          </div>
        )}

        {/* ─ Conversation History ─ */}
        {sidebarTab === "history" && (
          <ConversationHistory
            conversations={conversations}
            activeSessionId={sessionId}
            onSelect={handleHistorySelect}
            onDelete={onDeleteConv}
            onClearAll={onClearHistory}
            onNewChat={onNewChat}
          />
        )}



        {/* ─ Docs (default) ─ */}
        {sidebarTab === "docs" && (
          <>
            {/* CA Deadline Dashboard */}
            <div>
              <div className="section-header">
                <span className="section-label">Upcoming Deadlines</span>
              </div>
              <DeadlineDashboard />
            </div>

            {/* Client / Matter folder navigation */}
            <ClientPanel
              workspaceId={workspaceId}
              selectedClientId={selectedClientId}
              onSelectClient={setSelectedClientId}
              onDocumentMapChange={setDocMap}
            />

            {user && workspaces?.length > 1 && (
              <div>
                <div className="section-header" style={{ marginBottom: 6 }}>
                  <span className="section-label">Workspace</span>
                </div>
                <WorkspaceSwitcher user={user} workspaces={workspaces} onSwitch={onSwitch} />
              </div>
            )}

            {/* Compact upload row — expands on click */}
            <div>
              <div className="section-header" style={{ justifyContent: "space-between" }}>
                <span className="section-label">Upload</span>
                <button
                  className="sidebar-link-btn"
                  style={{ fontSize: 10, padding: "2px 8px" }}
                  onClick={() => setUploadOpen(o => !o)}
                >
                  {uploadOpen ? "✕ Close" : "+ Add files"}
                </button>
              </div>
              {uploadOpen && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <DropZone
                    onDrop={onUpload}
                    uploading={uploading}
                    progress={progress}
                    visionEnabled={visionEnabled}
                    onVisionChange={onVisionChange}
                    batchQueue={batchQueue}
                  />
                  <AudioUploader onSuccess={onRefreshDocuments} />
                  <WhatsAppUploader workspaceId={workspaceId} onIngested={onRefreshDocuments} />
                </div>
              )}
              {(uploading || batchQueue?.length > 0) && <IngestProgressPanel />}
            </div>

            {selectedFile && (
              <div className="active-filter">
                <span className="active-filter-label">Filter</span>
                <span className="active-filter-name" title={selectedFile}>{shortFileName}</span>
                <button
                  className="active-filter-close"
                  onClick={() => onSelectFile(null)}
                  aria-label={`Remove filter: ${shortFileName}`}
                >
                  <IconClose />
                </button>
              </div>
            )}

            {selectedFile && (
              <div>
                <div className="section-header" style={{ justifyContent: "space-between" }}>
                  <span className="section-label">Versions</span>
                  <button
                    className="sidebar-link-btn"
                    style={{ fontSize: 10, padding: "2px 8px" }}
                    onClick={onTogglePdf}
                    title={showPdfViewer ? "Hide PDF viewer" : "View PDF"}
                  >
                    {showPdfViewer ? "✕ PDF" : "📄 PDF"}
                  </button>
                </div>
                <VersionTimeline sourceFile={selectedFile} />
              </div>
            )}

            {/* Draft Reply card — auto-appears for GST/tax notice files */}
            {selectedFile && isNoticeFile(selectedFile) && (
              <div className="smart-analysis-card">
                <div className="section-header">
                  <span className="section-label">Draft Reply Letter</span>
                  <span style={{ fontSize: 10, color: "var(--teal, #0d9488)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                    AI Draft
                  </span>
                </div>
                <DraftReplyPanel
                  noticeFile={selectedFile}
                  documents={documents}
                  workspaceId={workspaceId}
                />
              </div>
            )}

            {/* Smart Analysis card — auto-runs when a recognized document is selected */}
            {selectedFile && detectDomain(selectedFile) && (
              <div className="smart-analysis-card">
                <div className="section-header">
                  <span className="section-label">Smart Analysis</span>
                  <span style={{ fontSize: 10, color: "var(--teal, #0d9488)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                    {detectDomain(selectedFile)}
                  </span>
                </div>
                <DomainPanel
                  selectedFile={selectedFile}
                  documents={documents}
                  workspaceId={workspaceId}
                  autoRun
                  compact
                />
              </div>
            )}

            {(() => {
              const filteredDocs = selectedClientId
                ? documents.filter(d => docMap[d.source_file || d.filename] === selectedClientId)
                : documents;
              return (
                <div style={{ flex: 1, minHeight: 0 }}>
                  <div className="section-header">
                    <span className="section-label">Library</span>
                    {filteredDocs.length > 0 && (
                      <span className="section-count">{filteredDocs.length}</span>
                    )}
                  </div>

                  {loadingDocs ? (
                    <div style={{ padding: "20px 0", display: "flex", flexDirection: "column", gap: 6 }}>
                      {[1, 2, 3].map(i => (
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
                  ) : filteredDocs.length === 0 ? (
                    <div style={{
                      padding: "28px 12px",
                      textAlign: "center",
                      color: "var(--text-4)",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 8,
                    }}>
                      <div style={{ fontSize: 32 }}>{selectedClientId ? "📂" : "📄"}</div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-2)" }}>
                        {selectedClientId ? "No documents in this client folder" : "No documents yet"}
                      </div>
                      <div style={{ fontSize: 11, lineHeight: 1.6 }}>
                        {selectedClientId
                          ? "Upload a document above and assign it to this client."
                          : "Upload your first document above to get started. GST notices, ITR, contracts — any PDF or text file works."}
                      </div>
                    </div>
                  ) : (
                    <DocumentList
                      documents={filteredDocs}
                      selectedFile={selectedFile}
                      onSelect={onSelectFile}
                      onDeleted={onDocumentDeleted}
                      workspaceId={workspaceId}
                      clients={Object.entries(docMap).reduce((acc, [docId, cid]) => {
                        acc[docId] = cid; return acc;
                      }, {})}
                      onAssignClient={async (docId, clientId) => {
                        await api.assignDocument(docId, clientId, workspaceId);
                        const newMap = { ...docMap };
                        if (clientId) newMap[docId] = clientId;
                        else delete newMap[docId];
                        setDocMap(newMap);
                      }}
                      statusMap={statusMap}
                      onStatusUpdated={(docId, updated) =>
                        setStatusMap(prev => ({ ...prev, [docId]: updated }))
                      }
                    />
                  )}
                </div>
              );
            })()}
          </>
        )}

      </div>

      {/* ── Usage Bar Footer ─────────────────────────────────────────────── */}
      {usage && (
        <div style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--bg-3)",
          background: "var(--bg-1)",
          flexShrink: 0,
        }}>
          <UsageMeter
            label="Docs"
            used={usage.docs?.used ?? 0}
            limit={usage.docs?.limit}
          />
          <UsageMeter
            label="Queries"
            used={usage.queries_today?.used ?? 0}
            limit={usage.queries_today?.limit}
            style={{ marginTop: 5 }}
          />
        </div>
      )}
    </aside>
  );
}
