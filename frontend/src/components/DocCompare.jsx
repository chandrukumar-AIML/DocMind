// frontend/src/components/DocCompare.jsx
import { useState, useCallback, useEffect, memo } from "react";
import { api } from "../api/client";
import PropTypes from "prop-types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// ── CA quick-template questions ──────────────────────────────────────────

const CA_TEMPLATES = [
  { label: "GST Notice vs Filing", q: "What does the demand notice claim and what was actually filed? Identify specific discrepancies in amounts, periods, and section references." },
  { label: "ITR vs 26AS", q: "Compare the income reported in the ITR with the TDS/income figures in 26AS. List any mismatches in amounts, TDS deducted, or unreported income." },
  { label: "GSTR-3B vs GSTR-1", q: "Compare the tax liability declared in GSTR-3B with the outward supplies reported in GSTR-1. Identify differences in taxable value, tax amounts, or ITC." },
  { label: "Notice vs Reply", q: "Compare the original notice with the reply filed. Did the reply address all the objections raised? What points remain unanswered?" },
  { label: "Contract vs Amendment", q: "What terms were changed between the original contract and the amendment? List all differences in rates, dates, obligations, and parties." },
  { label: "Two Agreements", q: "Compare the key commercial terms of these two agreements — payment terms, liability, termination clauses, and governing law. List differences." },
];

function shortName(f) {
  return (f || "").split("/").pop().split("\\").pop();
}

// ── Side-by-side panel ───────────────────────────────────────────────────

function ComparePanel({ label, doc, workspaceId, query }) {
  const [result, setResult]   = useState(null);
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
        <div className="compare-empty">Select a document below</div>
      ) : !result && !loading ? (
        <div className="compare-empty">Click "Compare" to run</div>
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
    </div>
  );
}

// ── Synthesis panel ──────────────────────────────────────────────────────

function SynthesisPanel({ docA, docB, question, workspaceId }) {
  const [result,  setResult]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    if (!docA || !docB || !question) return;
    setLoading(true);
    setError(null);
    setResult(null);
    api.crossDocSynthesize(docA, docB, question, workspaceId)
      .then(data => setResult(data.answer))
      .catch(() => setError("Synthesis failed — check both documents are indexed."))
      .finally(() => setLoading(false));
  }, [docA, docB, question, workspaceId]);

  if (!docA || !docB) {
    return <div className="compare-empty">Select both documents to run synthesis</div>;
  }
  if (!question) {
    return <div className="compare-empty">Enter a question above and click Compare</div>;
  }
  if (loading) {
    return (
      <div style={{ padding: "32px 0", textAlign: "center" }}>
        <div className="compare-loading" style={{ justifyContent: "center", marginBottom: 10 }}>
          <span className="compare-dot" /><span className="compare-dot" /><span className="compare-dot" />
        </div>
        <div style={{ fontSize: 11, color: "var(--tx-3)" }}>
          Analysing both documents together…
        </div>
      </div>
    );
  }
  if (error) {
    return <div style={{ padding: 16, color: "var(--red)", fontSize: 12 }}>{error}</div>;
  }
  if (!result) return null;

  return (
    <div className="synthesis-panel">
      <div className="synthesis-docs-row">
        <span className="synthesis-doc-badge">A: {shortName(docA)}</span>
        <span style={{ color: "var(--tx-3)", fontSize: 11 }}>×</span>
        <span className="synthesis-doc-badge">B: {shortName(docB)}</span>
      </div>
      <div className="synthesis-answer">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────

export const DocCompare = memo(function DocCompare({ documents, workspaceId, onClose }) {
  const [docA,      setDocA]      = useState(null);
  const [docB,      setDocB]      = useState(null);
  const [query,     setQuery]     = useState("");
  const [submitted, setSubmitted] = useState("");
  const [mode,      setMode]      = useState("synthesis"); // "synthesis" | "sidebyside"
  const [keyA,      setKeyA]      = useState(0);
  const [keyB,      setKeyB]      = useState(0);

  const compare = () => {
    if (!query.trim() || !docA || !docB) return;
    setSubmitted(query.trim());
    setKeyA(k => k + 1);
    setKeyB(k => k + 1);
  };

  const applyTemplate = (q) => {
    setQuery(q);
    setSubmitted("");
  };

  return (
    <div className="doc-compare-overlay">
      <div className="doc-compare-modal">
        {/* Header */}
        <div className="doc-compare-header">
          <span className="doc-compare-title">Cross-Document Analysis</span>
          <button className="doc-compare-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* CA Quick Templates */}
        <div className="compare-templates">
          <span className="compare-templates-label">Quick templates:</span>
          <div className="compare-template-chips">
            {CA_TEMPLATES.map(t => (
              <button
                key={t.label}
                className={`compare-template-chip${query === t.q ? " active" : ""}`}
                onClick={() => applyTemplate(t.q)}
                title={t.q}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Doc selectors */}
        <div className="doc-compare-selectors">
          <div className="doc-compare-selector">
            <label className="compare-sel-label">Document A</label>
            <select className="compare-sel" value={docA || ""} onChange={e => { setDocA(e.target.value || null); setSubmitted(""); }}>
              <option value="">Select…</option>
              {documents.map(d => (
                <option key={d.source_file} value={d.source_file}>{shortName(d.source_file)}</option>
              ))}
            </select>
          </div>
          <div className="compare-vs">VS</div>
          <div className="doc-compare-selector">
            <label className="compare-sel-label">Document B</label>
            <select className="compare-sel" value={docB || ""} onChange={e => { setDocB(e.target.value || null); setSubmitted(""); }}>
              <option value="">Select…</option>
              {documents.map(d => (
                <option key={d.source_file} value={d.source_file}>{shortName(d.source_file)}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Query row */}
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
            Analyse →
          </button>
        </div>

        {/* Mode tabs */}
        <div className="compare-mode-tabs">
          <button
            className={`compare-mode-tab${mode === "synthesis" ? " active" : ""}`}
            onClick={() => setMode("synthesis")}
          >
            ✦ Synthesis
          </button>
          <button
            className={`compare-mode-tab${mode === "sidebyside" ? " active" : ""}`}
            onClick={() => setMode("sidebyside")}
          >
            ⇔ Side-by-side
          </button>
        </div>

        {/* Results */}
        <div className="doc-compare-results">
          {mode === "synthesis" ? (
            <SynthesisPanel
              key={`synth-${submitted}`}
              docA={docA}
              docB={docB}
              question={submitted}
              workspaceId={workspaceId}
            />
          ) : (
            <div className="doc-compare-panels">
              <ComparePanel key={`a-${keyA}`} label="A" doc={docA} workspaceId={workspaceId} query={submitted} />
              <ComparePanel key={`b-${keyB}`} label="B" doc={docB} workspaceId={workspaceId} query={submitted} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

DocCompare.propTypes = {
  documents:   PropTypes.array.isRequired,
  workspaceId: PropTypes.string,
  onClose:     PropTypes.func.isRequired,
};
