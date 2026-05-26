// frontend/src/components/VersionPanel.jsx
import { useState, useEffect } from "react";
import { api } from "../api/client";
import PropTypes from "prop-types";

function formatDate(ts) {
  if (!ts) return "—";
  try {
    const d = typeof ts === "string" ? new Date(ts) : new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return String(ts).slice(0, 16);
  }
}

export function VersionPanel({ sourceFile }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [diff, setDiff] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [compareV1, setCompareV1] = useState(null);
  const [compareV2, setCompareV2] = useState(null);

  useEffect(() => {
    if (!sourceFile) { setVersions([]); return; }
    setLoading(true);
    setDiff(null);
    api.getVersionHistory(sourceFile)
      .then(d => setVersions(d.versions || []))
      .catch(() => setVersions([]))
      .finally(() => setLoading(false));
  }, [sourceFile]);

  const runDiff = async () => {
    if (compareV1 == null || compareV2 == null || compareV1 === compareV2) return;
    setDiffLoading(true);
    try {
      const result = await api.getVersionDiff(sourceFile, compareV1, compareV2);
      setDiff(result);
    } catch {
      setDiff({ error: true });
    } finally {
      setDiffLoading(false);
    }
  };

  if (!sourceFile) {
    return <div className="version-empty">Select a document to view its version history.</div>;
  }

  return (
    <div className="version-panel">
      <div className="version-panel-title">
        {sourceFile.split("/").pop().split("\\").pop()}
      </div>

      {loading ? (
        <div className="version-loading">Loading versions…</div>
      ) : versions.length === 0 ? (
        <div className="version-empty">No version history found. Re-index the document to create a version.</div>
      ) : (
        <>
          <div className="version-list">
            {versions.map((v, i) => (
              <div key={i} className="version-item">
                <div className="version-num">v{v.version_number ?? i + 1}</div>
                <div className="version-meta">
                  <div className="version-date">{formatDate(v.created_at || v.timestamp)}</div>
                  {v.chunk_count > 0 && <div className="version-chunks">{v.chunk_count} chunks</div>}
                  {v.note && <div className="version-note">{v.note}</div>}
                </div>
                <input
                  type="radio"
                  name="v1"
                  className="version-radio"
                  title="Compare from"
                  onChange={() => setCompareV1(v.version_number ?? i + 1)}
                  checked={compareV1 === (v.version_number ?? i + 1)}
                />
                <input
                  type="radio"
                  name="v2"
                  className="version-radio"
                  title="Compare to"
                  onChange={() => setCompareV2(v.version_number ?? i + 1)}
                  checked={compareV2 === (v.version_number ?? i + 1)}
                />
              </div>
            ))}
          </div>

          {versions.length > 1 && (
            <div className="version-diff-controls">
              <span className="version-diff-hint">
                {compareV1 && compareV2 ? `v${compareV1} → v${compareV2}` : "Select 2 versions to compare"}
              </span>
              <button
                className="version-diff-btn"
                onClick={runDiff}
                disabled={compareV1 == null || compareV2 == null || compareV1 === compareV2 || diffLoading}
              >
                {diffLoading ? "…" : "Diff →"}
              </button>
            </div>
          )}

          {diff && (
            <div className="version-diff-result">
              {diff.error ? (
                <div style={{ color: "var(--red)", fontSize: 11 }}>Could not load diff</div>
              ) : (
                <>
                  {diff.diff_summary && <div className="version-diff-summary">{diff.diff_summary}</div>}
                  {(diff.changes || []).slice(0, 6).map((c, i) => (
                    <div key={i} className={`version-change ${c.change_type || "modified"}`}>
                      <span className="change-type">{c.change_type || "~"}</span>
                      <span className="change-text">{(c.text || c.content || "").slice(0, 140)}</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

VersionPanel.propTypes = {
  sourceFile: PropTypes.string,
};
