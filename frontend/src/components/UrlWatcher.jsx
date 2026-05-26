// frontend/src/components/UrlWatcher.jsx
import { useState, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const STORAGE_KEY = "documind_watched_urls";

function loadWatched() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
}
function saveWatched(list) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(list)); } catch {}
}
function timeAgo(ts) {
  if (!ts) return "never";
  const m = Math.floor((Date.now() - ts) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

export function UrlWatcher({ workspaceId, onRefreshed }) {
  const [watched, setWatched] = useState(loadWatched);
  const [input, setInput] = useState("");
  const [refreshing, setRefreshing] = useState({});

  const add = useCallback(() => {
    const url = input.trim();
    if (!url || watched.some(w => w.url === url)) return;
    const next = [...watched, { url, addedAt: Date.now(), lastChecked: null, chunks: null }];
    setWatched(next);
    saveWatched(next);
    setInput("");
  }, [input, watched]);

  const remove = useCallback((url) => {
    const next = watched.filter(w => w.url !== url);
    setWatched(next);
    saveWatched(next);
  }, [watched]);

  const refresh = useCallback(async (url) => {
    setRefreshing(prev => ({ ...prev, [url]: true }));
    const toastId = toast.loading("Re-indexing URL…");
    try {
      const result = await api.ingestUrl(url, workspaceId);
      const chunks = result.child_chunks ?? 0;
      toast.success(`Re-indexed: ${chunks} chunks`, { id: toastId });
      setWatched(prev => {
        const next = prev.map(w => w.url === url ? { ...w, lastChecked: Date.now(), chunks } : w);
        saveWatched(next);
        return next;
      });
      onRefreshed?.();
    } catch {
      toast.error("Re-index failed", { id: toastId });
    } finally {
      setRefreshing(prev => ({ ...prev, [url]: false }));
    }
  }, [workspaceId, onRefreshed]);

  return (
    <div className="url-watcher">
      <div className="url-watcher-add">
        <input
          className="url-ingest-input"
          type="url"
          placeholder="https://… to watch"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && add()}
        />
        <button
          className="url-ingest-btn"
          onClick={add}
          disabled={!input.trim()}
          title="Watch this URL"
        >+</button>
      </div>

      {watched.length > 0 && (
        <div className="url-watcher-list">
          {watched.map(w => (
            <div key={w.url} className="url-watch-item">
              <div className="url-watch-body">
                <div className="url-watch-url" title={w.url}>
                  {w.url.replace(/^https?:\/\//, "").slice(0, 32)}{w.url.length > 40 ? "…" : ""}
                </div>
                <div className="url-watch-meta">
                  {w.chunks != null && <span>{w.chunks} chunks · </span>}
                  <span>Checked {timeAgo(w.lastChecked)}</span>
                </div>
              </div>
              <button
                className="url-watch-refresh"
                onClick={() => refresh(w.url)}
                disabled={refreshing[w.url]}
                title="Re-ingest now"
              >
                {refreshing[w.url]
                  ? <span style={{ animation: "spin 0.8s linear infinite", display: "inline-block" }}>↻</span>
                  : "↻"}
              </button>
              <button
                className="url-watch-remove"
                onClick={() => remove(w.url)}
                title="Remove"
              >✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

UrlWatcher.propTypes = {
  workspaceId: PropTypes.string,
  onRefreshed: PropTypes.func,
};
