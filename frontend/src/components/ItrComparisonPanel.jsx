/**
 * ItrComparisonPanel — Feature #11 ITR Year-on-Year Comparison
 * Select two ITR documents and get an AI-driven field-by-field comparison.
 */
import { useState } from "react";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

function isItrFile(filename) {
  if (!filename) return false;
  const name = filename.toLowerCase();
  return /itr|income.?tax.?return|ay\s*20\d{2}|form.?16/.test(name);
}

const CHANGE_COLOR = {
  "↑": "var(--teal, #0d9488)",
  "↓": "var(--red, #ef4444)",
  "Same": "var(--text-3)",
  "New":  "var(--blue, #3b82f6)",
  "Removed": "var(--amber, #f59e0b)",
};

function changeColor(change = "") {
  for (const [k, v] of Object.entries(CHANGE_COLOR)) {
    if (change.startsWith(k)) return v;
  }
  return "var(--text-3)";
}

export function ItrComparisonPanel({ documents = [], workspaceId }) {
  const [docA,    setDocA]    = useState("");
  const [docB,    setDocB]    = useState("");
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);
  const [error,   setError]   = useState(null);
  const token = localStorage.getItem("documind_access_token") || "";

  const docOptions = documents.filter(d => d.source_file || d.filename);

  const handleCompare = async () => {
    if (!docA || !docB) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await fetch(`${BASE_URL}/api/v1/itr/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ doc_current: docA, doc_previous: docB, workspace_id: workspaceId }),
      });
      if (!res.ok) throw new Error(await res.text());
      setResult(await res.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const itrDocs = docOptions.filter(d => isItrFile(d.source_file || d.filename));
  const allDocs = docOptions;

  const renderSelect = (value, onChange, placeholder) => (
    <select className="itr-select" value={value} onChange={e => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {(itrDocs.length ? itrDocs : allDocs).map(d => {
        const id = d.source_file || d.filename;
        const label = id.split("/").pop().split("\\").pop();
        return <option key={id} value={id}>{label}</option>;
      })}
    </select>
  );

  return (
    <div className="itr-panel">
      <div className="itr-doc-row">
        <div className="itr-doc-col">
          <label className="itr-label">Current Year ITR</label>
          {renderSelect(docA, setDocA, "Select document…")}
        </div>
        <div className="itr-vs">vs</div>
        <div className="itr-doc-col">
          <label className="itr-label">Previous Year ITR</label>
          {renderSelect(docB, setDocB, "Select document…")}
        </div>
      </div>

      <button
        className="itr-compare-btn"
        onClick={handleCompare}
        disabled={loading || !docA || !docB || docA === docB}
      >
        {loading ? "Comparing…" : "Compare Year-on-Year"}
      </button>

      {error && <div className="itr-error">{error}</div>}

      {result && (
        <div className="itr-result">
          <div className="itr-summary">{result.summary}</div>

          {result.fields.length > 0 && (
            <div className="itr-table-wrap">
              <table className="itr-table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Current Year</th>
                    <th>Previous Year</th>
                    <th>Change</th>
                  </tr>
                </thead>
                <tbody>
                  {result.fields.map((f, i) => (
                    <tr key={i} className={`itr-row${f.change === "↑" ? " itr-row-up" : f.change?.startsWith("↓") ? " itr-row-down" : ""}`}>
                      <td className="itr-field-name">{f.field}</td>
                      <td className="itr-num">{f.current}</td>
                      <td className="itr-num itr-prev">{f.previous}</td>
                      <td style={{ color: changeColor(f.change), fontWeight: 600, fontSize: 11 }}>
                        {f.change}
                        {f.note && <div style={{ color: "var(--text-4)", fontWeight: 400, fontSize: 10 }}>{f.note}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {result.red_flags?.length > 0 && (
            <div className="itr-flags">
              <div className="itr-flags-title">Observations</div>
              {result.red_flags.map((f, i) => (
                <div key={i} className="itr-flag-item">⚠ {f}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {!result && !loading && (
        <div className="itr-hint">
          Select two ITR documents (current and previous year) to get a field-by-field AI comparison of income, deductions, and tax.
        </div>
      )}
    </div>
  );
}
