// Chat topbar — sidebar toggle, query context, mode switcher, and actions.
// Extracted from App.jsx to keep the app shell focused.
import PropTypes from "prop-types";
import { IconMenu, IconClear, IconSpark } from "./Icons";

const MODES = [
  { id: "rag", label: "RAG" },
  { id: "agent", label: "Agent" },
  { id: "graph", label: "Graph" },
];

export function Topbar({
  sidebarOpen, onToggleSidebar,
  selectedFile, shortFileName, currentWorkspace, demoMode,
  queryMode, onModeChange,
  documents, messages,
  onCompare, onExportMarkdown, onExportPdf, onClear,
  theme, onToggleTheme,
}) {
  return (
    <div className="topbar">
      <button
        className="topbar-btn"
        onClick={onToggleSidebar}
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
        {demoMode && (
          <span className="demo-badge" title="Running with realistic sample data — no backend required">
            ● DEMO
          </span>
        )}
      </div>

      {/* Mode switcher */}
      <div className="topbar-mode-switcher" role="group" aria-label="Query mode">
        {MODES.map(m => (
          <button
            key={m.id}
            className={`mode-btn${queryMode === m.id ? " active" : ""}`}
            onClick={() => onModeChange(m.id)}
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
            onClick={onCompare}
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
              onClick={onExportMarkdown}
              aria-label="Export conversation as Markdown"
              title="Export as Markdown"
            >
              ↓ MD
            </button>
            <button
              className="topbar-action-btn"
              onClick={onExportPdf}
              aria-label="Export conversation as PDF"
              title="Export as PDF Report"
            >
              ↓ PDF
            </button>
            <button className="topbar-action-btn danger" onClick={onClear} aria-label="Clear conversation">
              <IconClear /> Clear
            </button>
          </>
        )}
        <button
          className="topbar-btn"
          onClick={onToggleTheme}
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
  );
}

Topbar.propTypes = {
  sidebarOpen: PropTypes.bool,
  onToggleSidebar: PropTypes.func.isRequired,
  selectedFile: PropTypes.string,
  shortFileName: PropTypes.string,
  currentWorkspace: PropTypes.object,
  demoMode: PropTypes.bool,
  queryMode: PropTypes.string.isRequired,
  onModeChange: PropTypes.func.isRequired,
  documents: PropTypes.array.isRequired,
  messages: PropTypes.array.isRequired,
  onCompare: PropTypes.func.isRequired,
  onExportMarkdown: PropTypes.func.isRequired,
  onExportPdf: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  theme: PropTypes.string,
  onToggleTheme: PropTypes.func.isRequired,
};
