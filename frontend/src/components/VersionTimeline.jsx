// frontend/src/components/VersionTimeline.jsx
// DVMELTSS-FIX: M - Modular, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useEffect, useCallback } from "react";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const MAGNITUDE_STYLES = {
  none:     { badge: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",     dot: "bg-gray-400",   label: "No change" },
  minor:    { badge: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",  dot: "bg-blue-400",   label: "Minor" },
  moderate: { badge: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300", dot: "bg-amber-400", label: "Moderate" },
  major:    { badge: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",       dot: "bg-red-400",    label: "Major" },
};

function DiffViewer({ diff, onClose }) {
  if (!diff) return null;

  const magnitude = MAGNITUDE_STYLES[diff.change_magnitude] || MAGNITUDE_STYLES.minor;

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="diff-title"
    >
      <div className="bg-white dark:bg-gray-900 rounded-2xl border border-gray-200 dark:border-gray-700 w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div>
            <h2 id="diff-title" className="text-sm font-semibold text-gray-800 dark:text-gray-200">
              v{diff.version_1} → v{diff.version_2}
            </h2>
            <div className="flex items-center gap-2 mt-1">
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${magnitude.badge}`}>
                {magnitude.label} change
              </span>
              <span className="text-xs text-gray-400">
                {(diff.overall_similarity * 100).toFixed(0)}% similar
              </span>
            </div>
          </div>
          <button 
            onClick={onClose} 
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
            aria-label="Close diff viewer"
          >
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Stats row */}
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "Added",     count: diff.chunks_added,     color: "text-green-600 dark:text-green-400" },
              { label: "Removed",   count: diff.chunks_removed,   color: "text-red-600 dark:text-red-400" },
              { label: "Modified",  count: diff.chunks_modified,  color: "text-amber-600 dark:text-amber-400" },
              { label: "Unchanged", count: diff.chunks_unchanged, color: "text-gray-500" },
            ].map(s => (
              <div key={s.label} className="text-center p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
                <div className={`text-lg font-bold ${s.color}`}>{s.count}</div>
                <div className="text-xs text-gray-400">{s.label}</div>
              </div>
            ))}
          </div>

          {/* Summary */}
          {diff.summary && (
            <div className="p-3 rounded-xl bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800">
              <p className="text-xs font-medium text-blue-600 dark:text-blue-400 mb-1">Summary</p>
              <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-wrap">
                {diff.summary}
              </p>
            </div>
          )}

          {/* Modified sections */}
          {diff.modified_sections?.length > 0 && (
            <div>
              <p className="text-xs font-medium text-amber-600 dark:text-amber-400 mb-2 uppercase tracking-wide">
                Modified ({diff.modified_sections.length})
              </p>
              <div className="space-y-2">
                {diff.modified_sections.slice(0, 5).map((s, i) => (
                  <div key={i} className="text-xs rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                    <div className="px-3 py-1.5 bg-red-50 dark:bg-red-950/20 border-b border-gray-200 dark:border-gray-700">
                      <span className="text-red-500" aria-hidden="true">−</span>
                      <span className="text-gray-600 dark:text-gray-400 ml-2 line-clamp-2">{s.v1_text}</span>
                    </div>
                    <div className="px-3 py-1.5 bg-green-50 dark:bg-green-950/20">
                      <span className="text-green-500" aria-hidden="true">+</span>
                      <span className="text-gray-700 dark:text-gray-300 ml-2 line-clamp-2">{s.v2_text}</span>
                    </div>
                    <div className="px-3 py-1 bg-gray-50 dark:bg-gray-800/50 text-gray-400">
                      {(s.similarity * 100).toFixed(0)}% similar
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Added sections */}
          {diff.added_sections?.length > 0 && (
            <div>
              <p className="text-xs font-medium text-green-600 dark:text-green-400 mb-2 uppercase tracking-wide">
                Added ({diff.added_sections.length})
              </p>
              {diff.added_sections.slice(0, 3).map((s, i) => (
                <div key={i} className="text-xs p-2.5 rounded-lg bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-800 mb-2">
                  <span className="text-green-500 mr-2" aria-hidden="true">+</span>
                  <span className="text-gray-700 dark:text-gray-300 line-clamp-2">{s.text}</span>
                </div>
              ))}
            </div>
          )}

          {/* Removed sections */}
          {diff.removed_sections?.length > 0 && (
            <div>
              <p className="text-xs font-medium text-red-600 dark:text-red-400 mb-2 uppercase tracking-wide">
                Removed ({diff.removed_sections.length})
              </p>
              {diff.removed_sections.slice(0, 3).map((s, i) => (
                <div key={i} className="text-xs p-2.5 rounded-lg bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 mb-2">
                  <span className="text-red-500 mr-2" aria-hidden="true">−</span>
                  <span className="text-gray-600 dark:text-gray-400 line-clamp-2">{s.text}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function VersionTimeline({ sourceFile, API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeDiff, setActiveDiff] = useState(null);
  const [loadingDiff, setLoadingDiff] = useState(null);

  useEffect(() => {
    if (!sourceFile) return;
    setLoading(true);
    const token = localStorage.getItem("documind_access_token");
    fetch(
      `${API_URL}/api/v1/versioning/history/${encodeURIComponent(sourceFile)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} }
    )
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setVersions)
      .catch(() => setVersions([]))
      .finally(() => setLoading(false));
  }, [sourceFile, API_URL]);

  const loadDiff = useCallback(async (v1, v2) => {
    const key = `${v1}-${v2}`;
    setLoadingDiff(key);
    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(
        `${API_URL}/api/v1/versioning/diff/${encodeURIComponent(sourceFile)}?v1=${v1}&v2=${v2}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setActiveDiff(await res.json());
    } catch (err) {
      toast.error(`Failed to load diff: ${err.message}`);
    } finally {
      setLoadingDiff(null);
    }
  }, [sourceFile, API_URL]);

  if (!sourceFile) return null;

  if (loading) {
    return (
      <div className="text-xs text-gray-400 py-4 text-center animate-pulse" role="status" aria-live="polite">
        Loading version history...
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="text-xs text-gray-400 py-4 text-center">
        No version history found
      </div>
    );
  }

  return (
    <>
      {activeDiff && (
        <DiffViewer diff={activeDiff} onClose={() => setActiveDiff(null)} />
      )}

      <div className="space-y-1">
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-3">
          Version history · {versions.length} versions
        </p>

        {/* Timeline */}
        <div className="relative">
          {/* Vertical line */}
          <div className="absolute left-3 top-0 bottom-0 w-px bg-gray-200 dark:bg-gray-700" aria-hidden="true" />

          <div className="space-y-4">
            {versions.map((v, idx) => {
              const magnitude = MAGNITUDE_STYLES[v.change_type || "none"] || MAGNITUDE_STYLES.minor;
              const prevV = versions[idx + 1];
              const diffKey = prevV ? `${prevV.version_number}-${v.version_number}` : null;
              const isLoadingThisDiff = loadingDiff === diffKey;

              return (
                <div key={v.version_id} className="flex gap-3 pl-1">
                  {/* Timeline dot */}
                  <div className="relative z-10 flex-shrink-0">
                    <div className={`
                      w-5 h-5 rounded-full border-2 border-white dark:border-gray-900
                      flex items-center justify-center
                      ${v.is_current
                        ? "bg-blue-500"
                        : `${magnitude.dot} opacity-70`
                      }
                    `} aria-hidden="true">
                      {v.is_current && (
                        <div className="w-2 h-2 rounded-full bg-white" />
                      )}
                    </div>
                  </div>

                  {/* Content */}
                  <div className="flex-1 pb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                        {v.version_label || `v${v.version_number}`}
                      </span>
                      {v.is_current && (
                        <span className="text-xs px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300 font-medium">
                          current
                        </span>
                      )}
                      {v.change_type && v.change_type !== "none" && (
                        <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${magnitude.badge}`}>
                          {magnitude.label}
                        </span>
                      )}
                    </div>

                    <div className="text-xs text-gray-400 mt-0.5 flex items-center gap-2">
                      <span>{new Date(v.uploaded_at).toLocaleDateString()}</span>
                      <span aria-hidden="true">·</span>
                      <span>{v.chunk_count} chunks</span>
                      <span aria-hidden="true">·</span>
                      <span>{v.page_count} pages</span>
                    </div>

                    {v.change_summary && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
                        {v.change_summary}
                      </p>
                    )}

                    {/* Diff button — only between consecutive versions */}
                    {prevV && (
                      <button
                        onClick={() => loadDiff(prevV.version_number, v.version_number)}
                        disabled={isLoadingThisDiff}
                        className="mt-1.5 text-xs text-blue-500 dark:text-blue-400 hover:underline disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
                        aria-label={`View diff between v${prevV.version_number} and v${v.version_number}`}
                      >
                        {isLoadingThisDiff
                          ? "Computing diff..."
                          : `↕ View changes from v${prevV.version_number}`
                        }
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

VersionTimeline.propTypes = {
  sourceFile: PropTypes.string.isRequired,
  API_URL: PropTypes.string,
};
