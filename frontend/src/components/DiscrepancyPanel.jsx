/**
 * DiscrepancyPanel — Discrepancy Auto-Detection (Feature #5)
 *
 * User picks two docs; panel auto-scans and renders a severity-coded
 * table of mismatches — no question needed.
 */
import { useState } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

// ── Icons ─────────────────────────────────────────────────────────────────

function ScanIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 7V5a2 2 0 012-2h2"/><path d="M17 3h2a2 2 0 012 2v2"/>
      <path d="M21 17v2a2 2 0 01-2 2h-2"/><path d="M7 21H5a2 2 0 01-2-2v-2"/>
      <circle cx="12" cy="12" r="3"/>
      <path d="M12 5v2M12 17v2M5 12H7M17 12h2"/>
    </svg>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────

function shortName(f) {
  return (f || "").split("/").pop().split("\\").pop();
}

const SEV_COLOR = {
  high:   { color: "var(--red, #ef4444)",   bg: "rgba(239,68,68,0.08)",   border: "rgba(239,68,68,0.25)"   },
  medium: { color: "var(--amber, #f59e0b)", bg: "rgba(245,158,11,0.08)",  border: "rgba(245,158,11,0.25)"  },
  low:    { color: "var(--text-4)",          bg: "var(--bg-2)",             border: "var(--border)"           },
};

function SeverityBadge({ severity }) {
  const s = SEV_COLOR[severity] || SEV_COLOR.low;
  return (
    <span style={{
      padding: "2px 7px", borderRadius: 4, fontSize: 10, fontWeight: 700,
      letterSpacing: "0.04em", textTransform: "uppercase",
      background: s.bg, color: s.color, border: `1px solid ${s.border}`,
      flexShrink: 0,
    }}>
      {severity}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────

export function DiscrepancyPanel({ documents, workspaceId }) {
  const [docA,    setDocA]    = useState("");
  const [docB,    setDocB]    = useState("");
  const [result,  setResult]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter,  setFilter]  = useState("all");

  const canScan = docA && docB && docA !== docB;

  const scan = async () => {
    if (!canScan) return;
    setLoading(true);
    setResult(null);
    try {
      const data = await api.detectDiscrepancies(docA, docB, workspaceId);
      setResult(data);
      if (data.total === 0) toast.success("No discrepancies found — documents match.");
      else if (data.high_count > 0) toast.error(`${data.high_count} high-severity mismatch${data.high_count > 1 ? "es" : ""} found!`);
      else toast(`${data.total} discrepancy found`, { icon: "⚠️" });
      api.logAudit("discrepancy_scan", docA,
        `vs ${shortName(docB)} — ${data.total} mismatch(es)`, workspaceId);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Scan failed");
    } finally {
      setLoading(false);
    }
  };

  const filtered = result
    ? (filter === "all" ? result.discrepancies : result.discrepancies.filter(d => d.severity === filter))
    : [];

  return (
    <div className="disc-panel">
      {/* Doc selectors */}
      <div className="disc-selectors">
        <div className="disc-selector-col">
          <div className="disc-selector-label">Document A</div>
          <select className="disc-select" value={docA} onChange={e => setDocA(e.target.value)}>
            <option value="">— pick document —</option>
            {documents.map(d => (
              <option key={d.source_file} value={d.source_file}>{shortName(d.source_file)}</option>
            ))}
          </select>
        </div>
        <div className="disc-vs">vs</div>
        <div className="disc-selector-col">
          <div className="disc-selector-label">Document B</div>
          <select className="disc-select" value={docB} onChange={e => setDocB(e.target.value)}>
            <option value="">— pick document —</option>
            {documents.map(d => (
              <option key={d.source_file} value={d.source_file} disabled={d.source_file === docA}>
                {shortName(d.source_file)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Scan button */}
      <button className="disc-scan-btn" onClick={scan} disabled={!canScan || loading}>
        {loading ? (
          <><span className="draft-spinner" /> Scanning…</>
        ) : (
          <><ScanIcon /> Auto-Detect Discrepancies</>
        )}
      </button>

      {/* Results */}
      {result && (
        <div className="disc-results">
          {/* Summary row */}
          <div className="disc-summary">
            <span className="disc-summary-total">{result.total} mismatch{result.total !== 1 ? "es" : ""}</span>
            {result.high_count > 0 && (
              <span className="disc-summary-high">{result.high_count} high severity</span>
            )}
          </div>

          {/* Filter tabs */}
          {result.total > 0 && (
            <div className="disc-filter-row">
              {["all", "high", "medium", "low"].map(f => (
                <button
                  key={f}
                  className={`disc-filter-btn${filter === f ? " active" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f === "all" ? `All (${result.total})` :
                   f === "high" ? `High (${result.discrepancies.filter(d => d.severity === "high").length})` :
                   f === "medium" ? `Medium (${result.discrepancies.filter(d => d.severity === "medium").length})` :
                   `Low (${result.discrepancies.filter(d => d.severity === "low").length})`}
                </button>
              ))}
            </div>
          )}

          {/* Discrepancy cards */}
          {filtered.length === 0 ? (
            <div className="disc-empty">
              {result.total === 0
                ? "✓ No discrepancies detected between these documents."
                : `No ${filter}-severity items.`}
            </div>
          ) : (
            <div className="disc-list">
              {filtered.map((item, i) => (
                <div key={i} className="disc-item" style={{
                  borderLeftColor: SEV_COLOR[item.severity]?.color || "var(--border)",
                }}>
                  <div className="disc-item-header">
                    <span className="disc-item-field">{item.field}</span>
                    <SeverityBadge severity={item.severity} />
                  </div>
                  <div className="disc-item-values">
                    <div className="disc-val-col">
                      <div className="disc-val-label">{shortName(docA)}</div>
                      <div className="disc-val">{item.doc_a_value}</div>
                    </div>
                    <div className="disc-val-arrow">→</div>
                    <div className="disc-val-col">
                      <div className="disc-val-label">{shortName(docB)}</div>
                      <div className="disc-val">{item.doc_b_value}</div>
                    </div>
                  </div>
                  {item.note && <div className="disc-item-note">{item.note}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

DiscrepancyPanel.propTypes = {
  documents:   PropTypes.array.isRequired,
  workspaceId: PropTypes.string,
};
