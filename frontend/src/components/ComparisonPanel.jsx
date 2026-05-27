// frontend/src/components/ComparisonPanel.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const MODES = [
  { id: "SIMILARITY", label: "Similarity", desc: "Common themes & entities" },
  { id: "DIFFERENCE", label: "Difference", desc: "Key divergences" },
  { id: "PATTERN", label: "Pattern", desc: "Recurring clauses & structures" },
  { id: "SUMMARY", label: "Summary", desc: "Executive overview" },
];

function ResultViewer({ result }) {
  const [tab, setTab] = useState(0);
  if (!result) return null;
  const mode = result.mode || "SUMMARY";
  const tabs = [];

  if (mode === "SIMILARITY") {
    if (result.common_themes?.length) tabs.push({ label: "Themes", content: result.common_themes });
    if (result.shared_entities?.length) tabs.push({ label: "Entities", content: result.shared_entities });
  } else if (mode === "DIFFERENCE") {
    if (result.differences?.length) tabs.push({ label: "Differences", content: result.differences });
  } else if (mode === "PATTERN") {
    if (result.patterns?.length) tabs.push({ label: "Patterns", content: result.patterns });
  } else if (mode === "SUMMARY") {
    if (result.cross_doc_insights?.length) tabs.push({ label: "Insights", content: result.cross_doc_insights });
  }

  return (
    <div className="comparison-result">
      {result.similarity_score != null && (
        <div className="comp-score">Similarity: <strong>{result.similarity_score}%</strong></div>
      )}
      {result.divergence_score != null && (
        <div className="comp-score">Divergence: <strong>{result.divergence_score}%</strong></div>
      )}
      {result.summary && <p className="comp-summary">{result.summary}</p>}
      {result.recommendation && <p className="comp-summary" style={{ fontStyle: "italic" }}>{result.recommendation}</p>}
      {tabs.length > 0 && (
        <>
          <div className="tab-bar" style={{ marginTop: 8 }}>
            {tabs.map((t, i) => (
              <button key={t.label} className={`tab-btn${tab === i ? " active" : ""}`} onClick={() => setTab(i)}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="comp-list">
            {(Array.isArray(tabs[tab]?.content) ? tabs[tab].content : []).map((item, i) => (
              <div key={i} className="comp-list-item">
                {typeof item === "string" ? item : (
                  <div>
                    {item.aspect && <strong>{item.aspect}: </strong>}
                    {item.doc1_value && <span style={{ color: "var(--amber)" }}>{item.doc1_value}</span>}
                    {item.doc2_value && <> → <span style={{ color: "var(--accent)" }}>{item.doc2_value}</span></>}
                    {item.description && <span>{item.description}</span>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function ComparisonPanel({ documents }) {
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState("SIMILARITY");
  const [running, setRunning] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [activeJob, setActiveJob] = useState(null);
  const pollRef = useRef(null);

  const loadJobs = useCallback(async () => {
    try {
      const data = await api.listComparisons();
      setJobs(data.jobs || []);
    } catch { /* comparison history unavailable */ }
  }, []);

  useEffect(() => { loadJobs(); }, [loadJobs]);

  const toggleDoc = (sf) => {
    setSelected(s => s.includes(sf) ? s.filter(x => x !== sf) : [...s, sf]);
  };

  const startComparison = async () => {
    if (selected.length < 2) { toast.error("Select at least 2 documents"); return; }
    setRunning(true);
    try {
      const job = await api.startComparison(selected, mode);
      toast.success("Comparison started");
      setActiveJob(job);
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getComparisonStatus(job.job_id);
          if (status.status === "done" || status.status === "failed") {
            clearInterval(pollRef.current);
            setActiveJob(status);
            loadJobs();
          }
        } catch { clearInterval(pollRef.current); }
      }, 2000);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to start comparison");
    } finally { setRunning(false); }
  };

  useEffect(() => () => clearInterval(pollRef.current), []);

  const docs = documents || [];

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Cross-Document Comparison</span>
      </div>

      <div style={{ padding: "8px 12px" }}>
        <div className="form-group">
          <label>Mode</label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
            {MODES.map(m => (
              <button
                key={m.id}
                className={`mode-chip${mode === m.id ? " active" : ""}`}
                onClick={() => setMode(m.id)}
                title={m.desc}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        <div className="form-group" style={{ marginTop: 8 }}>
          <label>Select Documents ({selected.length}/50)</label>
          {docs.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 4 }}>Upload documents first</div>
          ) : (
            <div style={{ maxHeight: 140, overflowY: "auto", marginTop: 4 }}>
              {docs.map(doc => {
                const name = doc.source_file.split("/").pop().split("\\").pop();
                return (
                  <label key={doc.source_file} style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0", fontSize: 12, cursor: "pointer" }}>
                    <input type="checkbox" checked={selected.includes(doc.source_file)} onChange={() => toggleDoc(doc.source_file)} />
                    {name}
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <button
          className="btn-primary"
          onClick={startComparison}
          disabled={running || selected.length < 2}
          style={{ marginTop: 8, width: "100%" }}
        >
          {running ? "Starting…" : "Compare"}
        </button>
      </div>

      {activeJob && (
        <div className="comparison-job-status">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{activeJob.mode} — {activeJob.status}</span>
            {activeJob.status === "running" && <span className="spinner-dot" />}
          </div>
          {activeJob.status === "done" && activeJob.result && (
            <ResultViewer result={activeJob.result} />
          )}
          {activeJob.status === "failed" && (
            <div style={{ fontSize: 11, color: "var(--red)", marginTop: 4 }}>{activeJob.error_msg}</div>
          )}
        </div>
      )}

      {jobs.length > 0 && (
        <div style={{ padding: "0 12px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>Recent Jobs</div>
          {jobs.slice(0, 5).map(j => (
            <div key={j.job_id} className="job-row" onClick={async () => {
              const full = await api.getComparisonStatus(j.job_id);
              setActiveJob(full);
            }}>
              <span style={{ fontSize: 11 }}>{j.mode}</span>
              <span style={{ fontSize: 11, color: "var(--text-4)" }}>{j.doc_count} docs</span>
              <span className={`status-chip ${j.status}`}>{j.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

ComparisonPanel.propTypes = { documents: PropTypes.array };
