/**
 * AuditTrailPanel — Feature #14/#15 Audit Trail per Client File
 * View and download the full audit log for the workspace or a specific document.
 */
import { useState, useEffect } from "react";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

const ACTION_COLOR = {
  upload:        "var(--teal, #0d9488)",
  query:         "var(--blue, #3b82f6)",
  status_change: "var(--violet, #8b5cf6)",
  draft_reply:   "var(--amber, #f59e0b)",
  discrepancy:   "var(--pink, #ec4899)",
  export_pdf:    "var(--teal, #0d9488)",
  delete:        "var(--red, #ef4444)",
};

function actionColor(action = "") {
  for (const [k, v] of Object.entries(ACTION_COLOR)) {
    if (action.includes(k)) return v;
  }
  return "var(--text-3)";
}

function formatTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleDateString("en-IN") + " " + d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  } catch { return ts; }
}

function downloadCsv(events) {
  const header = ["id", "timestamp", "actor", "action", "document", "detail"];
  const rows = events.map(e => [
    e.id, e.created_at, e.actor_email, e.action, e.document_id || "", e.detail || "",
  ]);
  const csv = [header, ...rows].map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `audit-trail-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function AuditTrailPanel({ workspaceId, selectedFile }) {
  const [events,  setEvents]  = useState([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const [filter,  setFilter]  = useState("all");  // "all" | "document"
  const token = localStorage.getItem("documind_access_token") || "";

  const load = async () => {
    setLoading(true); setError(null);
    try {
      let url = `${BASE_URL}/api/v1/audit/list?workspace_id=${workspaceId}&limit=100`;
      if (filter === "document" && selectedFile) {
        url += `&document_id=${encodeURIComponent(selectedFile)}`;
      }
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      setEvents(d.events || []);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (workspaceId) load();
  }, [workspaceId, filter, selectedFile]);

  return (
    <div className="audit-panel">
      <div className="audit-toolbar">
        <div className="audit-filter-row">
          <button
            className={`audit-filter-btn${filter === "all" ? " active" : ""}`}
            onClick={() => setFilter("all")}
          >
            All Events
          </button>
          <button
            className={`audit-filter-btn${filter === "document" ? " active" : ""}`}
            onClick={() => setFilter("document")}
            disabled={!selectedFile}
            title={!selectedFile ? "Select a document first" : undefined}
          >
            This Document
          </button>
        </div>
        {events.length > 0 && (
          <button className="audit-download-btn" onClick={() => downloadCsv(events)}>
            ↓ CSV
          </button>
        )}
      </div>

      {error && <div className="audit-error">{error}</div>}
      {loading && <div className="audit-loading">Loading audit log…</div>}

      {!loading && events.length === 0 && !error && (
        <div className="audit-empty">
          No audit events recorded yet. Events are logged automatically as your team uses DocuMind.
        </div>
      )}

      {!loading && events.length > 0 && (
        <div className="audit-list">
          {events.map(e => (
            <div key={e.id} className="audit-event">
              <div
                className="audit-action-dot"
                style={{ background: actionColor(e.action) }}
              />
              <div className="audit-event-body">
                <div className="audit-event-top">
                  <span className="audit-action" style={{ color: actionColor(e.action) }}>
                    {e.action.replace(/_/g, " ")}
                  </span>
                  <span className="audit-ts">{formatTs(e.created_at)}</span>
                </div>
                <div className="audit-actor">{e.actor_email}</div>
                {e.document_id && (
                  <div className="audit-doc">{e.document_id.split("/").pop().split("\\").pop()}</div>
                )}
                {e.detail && <div className="audit-detail">{e.detail}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
