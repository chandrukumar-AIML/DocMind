// frontend/src/components/CompliancePanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const SEVERITY_COLORS = { critical: "#EF4444", high: "#F87171", medium: "#F59E0B", low: "#10B981" };

function ScoreGauge({ score }) {
  const color = score >= 80 ? "var(--green)" : score >= 60 ? "var(--amber)" : "var(--red)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 6, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${score}%`, height: "100%", background: color, borderRadius: 3, transition: "width 0.5s" }} />
      </div>
      <span style={{ fontSize: 12, fontWeight: 700, color, minWidth: 32 }}>{Math.round(score)}</span>
    </div>
  );
}

export function CompliancePanel({ selectedFile }) {
  const [regulations, setRegulations] = useState({});
  const [selected, setSelected] = useState(["GDPR", "INDIAN_CONTRACT"]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [activeTab, setActiveTab] = useState("check");

  useEffect(() => {
    api.listRegulations().then(d => setRegulations(d.regulations || {})).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedFile) return;
    api.getComplianceHistory(selectedFile)
      .then(d => setHistory(d.history || []))
      .catch(() => {});
  }, [selectedFile]);

  const toggleReg = (code) => {
    setSelected(s => s.includes(code) ? s.filter(c => c !== code) : [...s, code]);
  };

  const runCheck = async () => {
    if (!selectedFile) { toast.error("Select a document first"); return; }
    if (selected.length === 0) { toast.error("Select at least one regulation"); return; }
    setLoading(true);
    try {
      const r = await api.checkCompliance(selectedFile, selected);
      setResult(r);
      setActiveTab("result");
      toast.success("Compliance check complete");
      api.getComplianceHistory(selectedFile).then(d => setHistory(d.history || {})).catch(() => {});
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Check failed");
    } finally { setLoading(false); }
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Compliance Checker</span>
        <div className="tab-bar">
          {["check", "result", "history"].map(t => (
            <button key={t} className={`tab-btn${activeTab === t ? " active" : ""}`} onClick={() => setActiveTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "check" && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>
            Document: {selectedFile?.split("/").pop().split("\\").pop() || "—none selected—"}
          </div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>Select Regulations:</div>
          {Object.entries(regulations).map(([code, name]) => (
            <label key={code} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, marginBottom: 4, cursor: "pointer" }}>
              <input type="checkbox" checked={selected.includes(code)} onChange={() => toggleReg(code)} />
              <span style={{ fontWeight: 600 }}>{code}</span>
              <span style={{ color: "var(--text-4)" }}>— {name}</span>
            </label>
          ))}
          <button
            className="btn-primary"
            onClick={runCheck}
            disabled={loading || !selectedFile}
            style={{ marginTop: 10, width: "100%" }}
          >
            {loading ? "Checking…" : "Run Compliance Check"}
          </button>
        </div>
      )}

      {activeTab === "result" && result && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>Overall Score</div>
            <ScoreGauge score={result.overall_score || 0} />
          </div>

          {Object.entries(result.scores || {}).map(([reg, score]) => (
            <div key={reg} style={{ marginBottom: 6 }}>
              <div style={{ fontSize: 11, marginBottom: 2 }}>{reg}</div>
              <ScoreGauge score={score} />
            </div>
          ))}

          {result.violations?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                Violations ({result.violations.length})
              </div>
              {result.violations.map((v, i) => {
                const color = SEVERITY_COLORS[v.severity] || "var(--text-4)";
                return (
                  <div key={i} style={{ background: `${color}11`, border: `1px solid ${color}33`, borderRadius: 6, padding: 8, marginBottom: 6 }}>
                    <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
                      <span style={{ fontSize: 10, background: `${color}22`, color, padding: "1px 6px", borderRadius: 3, fontWeight: 700 }}>
                        {v.severity?.toUpperCase()}
                      </span>
                      <span style={{ fontSize: 11, fontWeight: 600 }}>{v.regulation}</span>
                    </div>
                    <div style={{ fontSize: 11 }}>{v.description}</div>
                  </div>
                );
              })}
            </div>
          )}

          {result.recommendations?.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Recommendations</div>
              {result.recommendations.map((r, i) => (
                <div key={i} style={{ fontSize: 11, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ fontWeight: 600 }}>{r.regulation}:</span> {r.action}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "history" && (
        <div style={{ padding: "8px 12px" }}>
          {history.length === 0 ? (
            <div className="panel-empty">No compliance checks yet</div>
          ) : history.map(h => (
            <div key={h.result_id} className="panel-item" style={{ marginBottom: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontSize: 11 }}>{(h.regulations || []).join(", ")}</div>
                  <div style={{ fontSize: 10, color: "var(--text-4)" }}>{h.created_at?.slice(0, 16)}</div>
                </div>
                <ScoreGauge score={h.overall_score || 0} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

CompliancePanel.propTypes = { selectedFile: PropTypes.string };
