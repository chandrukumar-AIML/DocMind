// frontend/src/components/AnnotationPanel.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import { isDemoMode } from "../api/demo";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const ANN_TYPES = ["highlight", "comment", "tag", "risk_flag", "approval"];
const TYPE_COLORS = {
  highlight: "#F59E0B",
  comment: "#60A5FA",
  tag: "#34D399",
  risk_flag: "#F87171",
  approval: "#A78BFA",
};

function TypeBadge({ type }) {
  const color = TYPE_COLORS[type] || "#94A3B8";
  return (
    <span style={{
      background: `${color}22`, color, border: `1px solid ${color}55`,
      borderRadius: 4, padding: "1px 6px", fontSize: 10, fontWeight: 600,
    }}>
      {type.replace("_", " ").toUpperCase()}
    </span>
  );
}

export function AnnotationPanel({ sourceFile, workspaceId }) {
  const [annotations, setAnnotations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filterType, setFilterType] = useState("");
  const [form, setForm] = useState({ type: "comment", content: "", page_number: "" });
  const wsRef = useRef(null);

  const load = useCallback(async () => {
    if (!sourceFile) return;
    setLoading(true);
    try {
      const data = await api.listAnnotations(sourceFile, filterType || undefined);
      setAnnotations(data.annotations || []);
    } catch { toast.error("Failed to load annotations"); }
    finally { setLoading(false); }
  }, [sourceFile, filterType]);

  useEffect(() => { load(); }, [load]);

  // WebSocket for real-time sync (skipped in demo — no live backend)
  useEffect(() => {
    if (!sourceFile || !workspaceId || isDemoMode()) return;
    const BASE = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/^http/, "ws");
    const params = new URLSearchParams({ source_file: sourceFile });
    const ws = new WebSocket(`${BASE}/api/v1/annotations/ws/${workspaceId}?${params}`);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.event === "annotation_created") {
          setAnnotations(prev => [...prev, msg.annotation]);
        } else if (msg.event === "annotation_deleted") {
          setAnnotations(prev => prev.filter(a => a.id !== msg.annotation_id));
        } else if (msg.event === "annotation_resolved") {
          setAnnotations(prev => prev.map(a => a.id === msg.annotation_id ? { ...a, resolved: true } : a));
        }
      } catch { /* malformed WS message — skip */ }
    };
    return () => { ws.close(); };
  }, [sourceFile, workspaceId]);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.content.trim()) { toast.error("Content required"); return; }
    try {
      await api.createAnnotation(
        sourceFile, form.type, form.content,
        form.page_number ? parseInt(form.page_number) : null, null
      );
      setForm(f => ({ ...f, content: "", page_number: "" }));
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to add annotation");
    }
  };

  const handleResolve = async (id) => {
    try {
      await api.resolveAnnotation(id, sourceFile);
      setAnnotations(prev => prev.map(a => a.id === id ? { ...a, resolved: true } : a));
    } catch { toast.error("Could not resolve"); }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteAnnotation(id, sourceFile);
      setAnnotations(prev => prev.filter(a => a.id !== id));
    } catch { toast.error("Could not delete"); }
  };

  if (!sourceFile) {
    return <div className="panel-empty">Select a document to annotate</div>;
  }

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Annotations</span>
        <span style={{ fontSize: 10, color: "var(--text-4)" }}>● Live sync</span>
      </div>

      <form className="panel-form" onSubmit={handleCreate}>
        <div style={{ display: "flex", gap: 6 }}>
          <select className="input" value={form.type} onChange={e => setForm(f => ({ ...f, type: e.target.value }))} style={{ flex: "0 0 120px" }}>
            {ANN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <input className="input" style={{ flex: "0 0 60px" }} type="number" min="1" value={form.page_number} onChange={e => setForm(f => ({ ...f, page_number: e.target.value }))} placeholder="Page" />
        </div>
        <textarea
          className="input"
          style={{ marginTop: 6, minHeight: 60, resize: "vertical" }}
          value={form.content}
          onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
          placeholder="Add annotation…"
        />
        <button className="btn-primary" type="submit" style={{ marginTop: 6 }}>Add</button>
      </form>

      <div style={{ padding: "0 12px", display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        <button className={`mode-chip${!filterType ? " active" : ""}`} onClick={() => setFilterType("")}>All</button>
        {ANN_TYPES.map(t => (
          <button key={t} className={`mode-chip${filterType === t ? " active" : ""}`} onClick={() => setFilterType(t)}>{t}</button>
        ))}
      </div>

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : annotations.length === 0 ? (
        <div className="panel-empty">No annotations yet</div>
      ) : (
        <div className="panel-list">
          {annotations.map(ann => (
            <div key={ann.id} className={`panel-item${ann.resolved ? " resolved" : ""}`}>
              <div className="panel-item-row">
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
                    <TypeBadge type={ann.type} />
                    {ann.page_number && <span style={{ fontSize: 10, color: "var(--text-4)" }}>p.{ann.page_number}</span>}
                    {ann.resolved && <span style={{ fontSize: 10, color: "var(--green)" }}>✓ resolved</span>}
                    <span style={{ fontSize: 10, color: "var(--text-4)", marginLeft: "auto" }}>{ann.username || ann.user_id}</span>
                  </div>
                  <div style={{ fontSize: 12 }}>{ann.content}</div>
                </div>
                {!ann.resolved && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, marginLeft: 8 }}>
                    <button className="btn-sm" onClick={() => handleResolve(ann.id)}>✓</button>
                    <button className="btn-sm danger" onClick={() => handleDelete(ann.id)}>×</button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

AnnotationPanel.propTypes = {
  sourceFile: PropTypes.string,
  workspaceId: PropTypes.string,
};
