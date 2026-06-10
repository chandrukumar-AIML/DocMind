// frontend/src/components/DocCompare.jsx
import { useState, useCallback, useEffect, memo } from "react";
import { api } from "../api/client";
import PropTypes from "prop-types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function shortName(f) {
  return (f || "").split("/").pop().split("\\").pop();
}

function ComparePanel({ label, doc, workspaceId, query }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [queried, setQueried] = useState(null);

  const run = useCallback(async (q, docFile) => {
    if (!docFile || !q) return;
    setLoading(true);
    try {
      const r = await api.query({
        question: q,
        filter_source_file: docFile,
        workspace_id: workspaceId,
        top_k_retrieve: 8,
        top_k_rerank: 3,
        stream: false,
      });
      setResult(r.answer || r.content || "No answer");
      setQueried(q);
    } catch {
      setResult("Failed to query this document.");
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  const runQuery = useCallback(() => run(query, doc), [run, query, doc]);

  // Auto-run when the user hits "Compare" (the panel is remounted with the
  // submitted query) or picks a different document while a query is active —
  // without this the Compare button only cleared the panes and users had to
  // click "Run →" in each pane separately.
  useEffect(() => {
    if (query && doc) run(query, doc);
  }, [query, doc, run]);

  return (
    <div className="compare-panel">
      <div className="compare-panel-header">
        <div className="compare-panel-label">{label}</div>
        <div className="compare-panel-doc" title={doc}>{doc ? shortName(doc) : "—"}</div>
      </div>
      {!doc ? (
        <div className="compare-empty">Select a document below to compare</div>
      ) : !result && !loading ? (
        <div className="compare-empty">Click "Compare" to see the answer for this document</div>
      ) : loading ? (
        <div className="compare-loading">
          <span className="compare-dot" /><span className="compare-dot" /><span className="compare-dot" />
        </div>
      ) : (
        <div className="compare-answer">
          {queried && <div className="compare-queried">Q: {queried}</div>}
          <div className="compare-text">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
          </div>
        </div>
      )}
      {doc && query && !loading && (
        <button className="compare-run-btn" onClick={runQuery}>
          {result ? "Re-run" : "Run"} →
        </button>
      )}
    </div>
  );
}

export const DocCompare = memo(function DocCompare({ documents, workspaceId, onClose }) {
  const [docA, setDocA] = useState(null);
  const [docB, setDocB] = useState(null);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [keyA, setKeyA] = useState(0);
  const [keyB, setKeyB] = useState(0);

  const compare = () => {
    if (!query.trim() || !docA || !docB) return;
    setSubmitted(query.trim());
    setKeyA(k => k + 1);
    setKeyB(k => k + 1);
  };

  return (
    <div className="doc-compare-overlay">
      <div className="doc-compare-modal">
        <div className="doc-compare-header">
          <span className="doc-compare-title">Document Comparison</span>
          <button className="doc-compare-close" onClick={onClose} aria-label="Close comparison">✕</button>
        </div>

        {/* Doc selectors */}
        <div className="doc-compare-selectors">
          <div className="doc-compare-selector">
            <label className="compare-sel-label">Document A</label>
            <select className="compare-sel" value={docA || ""} onChange={e => setDocA(e.target.value || null)}>
              <option value="">Select…</option>
              {documents.map(d => (
                <option key={d.source_file} value={d.source_file}>{shortName(d.source_file)}</option>
              ))}
            </select>
          </div>
          <div className="compare-vs">VS</div>
          <div className="doc-compare-selector">
            <label className="compare-sel-label">Document B</label>
            <select className="compare-sel" value={docB || ""} onChange={e => setDocB(e.target.value || null)}>
              <option value="">Select…</option>
              {documents.map(d => (
                <option key={d.source_file} value={d.source_file}>{shortName(d.source_file)}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Query input */}
        <div className="doc-compare-query-row">
          <input
            className="doc-compare-query"
            placeholder="Ask a question to compare across both documents…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === "Enter" && compare()}
          />
          <button
            className="doc-compare-btn"
            onClick={compare}
            disabled={!query.trim() || !docA || !docB}
          >
            Compare
          </button>
        </div>

        {/* Side-by-side panels */}
        <div className="doc-compare-panels">
          <ComparePanel key={`a-${keyA}`} label="A" doc={docA} workspaceId={workspaceId} query={submitted} />
          <ComparePanel key={`b-${keyB}`} label="B" doc={docB} workspaceId={workspaceId} query={submitted} />
        </div>
      </div>
    </div>
  );
});

DocCompare.propTypes = {
  documents: PropTypes.array.isRequired,
  workspaceId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};
