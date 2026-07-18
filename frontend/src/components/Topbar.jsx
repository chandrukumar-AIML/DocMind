// Chat topbar — sidebar toggle, query context, mode switcher, and actions.
// Extracted from App.jsx to keep the app shell focused.
import { useState } from "react";
import PropTypes from "prop-types";
import { IconMenu, IconClear, IconSpark } from "./Icons";

const MODES = [
  { id: "rag",   label: "RAG",   tip: "Cited answers — fastest, most accurate" },
  { id: "agent", label: "Agent", tip: "Multi-step reasoning over documents" },
  { id: "graph", label: "Graph", tip: "Entity relationship graph queries (Neo4j)" },
];

export function Topbar({
  sidebarOpen, onToggleSidebar,
  selectedFile, shortFileName, currentWorkspace, demoMode,
  queryMode, onModeChange,
  documents, messages,
  onCompare, onExportMarkdown, onExportPdf, onClear,
  theme, onToggleTheme,
}) {
  const [modeOpen, setModeOpen] = useState(false);
  const activeMode = MODES.find(m => m.id === queryMode) || MODES[0];

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
          {selectedFile ? shortFileName : "All documents"}
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

      {/* Mode pill — compact dropdown */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <button
          className="mode-pill"
          onClick={() => setModeOpen(o => !o)}
          title={activeMode.tip}
          aria-haspopup="listbox"
          aria-expanded={modeOpen}
        >
          <IconSpark />
          <span>{activeMode.label}</span>
          <span style={{ opacity: 0.5, fontSize: 9 }}>▾</span>
        </button>
        {modeOpen && (
          <div className="mode-dropdown" role="listbox">
            {MODES.map(m => (
              <button
                key={m.id}
                className={`mode-dropdown-item${queryMode === m.id ? " active" : ""}`}
                role="option"
                aria-selected={queryMode === m.id}
                onClick={() => { onModeChange(m.id); setModeOpen(false); }}
              >
                <span className="mode-dropdown-label">{m.label}</span>
                <span className="mode-dropdown-tip">{m.tip}</span>
              </button>
            ))}
          </div>
        )}
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
