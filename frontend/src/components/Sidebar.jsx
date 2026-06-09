// frontend/src/components/Sidebar.jsx — DocuMind AI left-hand panel
// Manages its own tab/feature/stats state so App.jsx stays lean.
// All data dependencies arrive as grouped prop objects (see JSDoc below).
import { useState } from "react";
import { toast } from "react-hot-toast";
import { DropZone }             from "./DropZone";
import { DocumentList }         from "./DocumentList";
import { WorkspaceSwitcher }    from "./WorkspaceSwitcher";
import { DomainPanel }          from "./DomainPanel";
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
import { IconClose }            from "./Icons";
import { api }                  from "../api/client";

// ── Static lookup tables ─────────────────────────────────────────────────────

const NAV_TABS = [
  ["docs",     "Docs"],
  ["analyze",  "Analyze"],
  ["history",  "History"],
  ["finetune", "Train"],
  ["stats",    "Stats"],
  ["features", "Features"],
];

const FEATURE_TABS = [
  ["webhooks",   "Webhooks"],
  ["compare",    "Compare"],
  ["workflows",  "Workflows"],
  ["annotate",   "Annotate"],
  ["templates",  "Templates"],
  ["esign",      "E-Sign"],
  ["compliance", "Compliance"],
  ["admin",      "Admin"],
  ["onboard",    "Onboard"],
  ["regional",   "Regional"],
];

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
  const [sidebarTab, setSidebarTab] = useState("docs");
  const [featureTab, setFeatureTab] = useState(null);
  const [stats,      setStats]      = useState(null);   // fetched lazily on Stats tab open

  // ── Destructure prop groups ──────────────────────────────────────────────
  const { user, workspaces, current: currentWorkspace, id: workspaceId, onSwitch } = workspace;
  const { list: documents, loading: loadingDocs, error: loadError } = docs;
  const { file: selectedFile, shortName: shortFileName, onSelect: onSelectFile, onDelete: onDocumentDeleted } = selection;
  const { onUpload, uploading, progress, vision: visionEnabled, onVision: onVisionChange, batch: batchQueue, onRefresh: onRefreshDocuments } = upload;
  const { show: showPdfViewer, onToggle: onTogglePdf } = pdf;
  const { loading: extracting, results: extractionResults, onExtract } = extract;
  const { conversations, activeId: sessionId, onSelect: onSelectConv, onDelete: onDeleteConv, onClear: onClearHistory, onNew: onNewChat } = history;

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleStatsTabClick = () => {
    setSidebarTab("stats");
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
            onClick={id === "stats" ? handleStatsTabClick : () => setSidebarTab(id)}
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

        {/* ─ Features ─ */}
        {sidebarTab === "features" && (
          <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <div className="sidebar-feature-tabs">
              {FEATURE_TABS.map(([id, label]) => (
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
              {featureTab === "webhooks"   && <WebhookPanel />}
              {featureTab === "compare"    && <ComparisonPanel documents={documents} />}
              {featureTab === "workflows"  && <WorkflowPanel />}
              {featureTab === "annotate"   && <AnnotationPanel sourceFile={selectedFile} workspaceId={workspaceId} />}
              {featureTab === "templates"  && <TemplatePanel selectedFile={selectedFile} />}
              {featureTab === "esign"      && <ESignPanel selectedFile={selectedFile} />}
              {featureTab === "compliance" && <CompliancePanel selectedFile={selectedFile} />}
              {featureTab === "admin"      && <SuperAdminPanel user={user} />}
              {featureTab === "onboard"    && <OnboardingPanel />}
              {featureTab === "regional"   && <RegionalPanel />}
              {!featureTab && (
                <div className="panel-empty" style={{ padding: 20 }}>
                  Select a feature above to get started
                </div>
              )}
            </div>
          </div>
        )}

        {/* ─ Train / Fine-tune ─ */}
        {sidebarTab === "finetune" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%", overflow: "auto" }}>
            <FineTuningPanel workspaceId={workspaceId} />
            <div style={{ padding: "8px 4px 4px" }}>
              <div className="section-header"><span className="section-label">RAG Evaluation</span></div>
              <RAGAsDashboard />
            </div>
          </div>
        )}

        {/* ─ Analyze ─ */}
        {sidebarTab === "analyze" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%", overflow: "auto" }}>
            <DomainPanel selectedFile={selectedFile} documents={documents} workspaceId={workspaceId} />

            {selectedFile && (
              <div style={{ padding: "8px 4px 4px" }}>
                <div className="section-header" style={{ justifyContent: "space-between" }}>
                  <span className="section-label">Tables & Charts</span>
                  <button
                    className="sidebar-link-btn"
                    style={{ fontSize: 10, padding: "2px 8px" }}
                    onClick={onExtract}
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

            <div style={{ padding: "4px 0" }}>
              <div className="section-header"><span className="section-label">Graph Query</span></div>
              <GraphQueryPanel />
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

        {/* ─ Stats / Monitoring ─ */}
        {sidebarTab === "stats" && (
          <div style={{ padding: "4px 0", height: "100%", overflow: "auto" }}>
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

            <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">User</span></div>
            <div style={{ fontSize: 11, color: "var(--text-3)", padding: "6px 4px", lineHeight: 1.8 }}>
              <div>{user?.email}</div>
              <div style={{ color: "var(--text-4)" }}>
                {currentWorkspace?.name || currentWorkspace?.workspace_id?.slice(0, 8) || "Default"}
              </div>
            </div>

            <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Document Health</span></div>
            <button className="sidebar-link-btn" style={{ marginBottom: 4 }} onClick={handleFindDuplicates}>
              Find Duplicates
            </button>

            <div className="section-header" style={{ marginTop: 12 }}><span className="section-label">Audit Trail</span></div>
            <button className="sidebar-link-btn" style={{ marginBottom: 4 }} onClick={handleDownloadAudit}>
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
        )}

        {/* ─ Docs (default) ─ */}
        {sidebarTab === "docs" && (
          <>
            {user && workspaces?.length > 1 && (
              <div>
                <div className="section-header" style={{ marginBottom: 6 }}>
                  <span className="section-label">Workspace</span>
                </div>
                <WorkspaceSwitcher user={user} workspaces={workspaces} onSwitch={onSwitch} />
              </div>
            )}

            <div>
              <div className="section-header"><span className="section-label">Upload</span></div>
              <DropZone
                onDrop={onUpload}
                uploading={uploading}
                progress={progress}
                visionEnabled={visionEnabled}
                onVisionChange={onVisionChange}
                batchQueue={batchQueue}
              />
            </div>

            <div>
              <div className="section-header"><span className="section-label">Audio & Office Files</span></div>
              <AudioUploader onSuccess={onRefreshDocuments} />
            </div>

            <IngestProgressPanel />

            <div>
              <div className="section-header"><span className="section-label">Web URLs</span></div>
              <UrlWatcher workspaceId={workspaceId} onRefreshed={onRefreshDocuments} />
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

            <div style={{ flex: 1, minHeight: 0 }}>
              <div className="section-header">
                <span className="section-label">Library</span>
                {documents.length > 0 && (
                  <span className="section-count">{documents.length}</span>
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
              ) : (
                <DocumentList
                  documents={documents}
                  selectedFile={selectedFile}
                  onSelect={onSelectFile}
                  onDeleted={onDocumentDeleted}
                  workspaceId={workspaceId}
                />
              )}
            </div>
          </>
        )}

      </div>
    </aside>
  );
}
