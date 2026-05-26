// frontend/src/components/TemplatePanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const FIELD_TYPES = ["string", "number", "date", "boolean", "list", "email", "phone"];

const BUILTIN_ICONS = {
  invoice: "🧾", contract: "📋", medical: "🏥",
  purchase_order: "📦", resume: "👤", bank_statement: "🏦",
};

export function TemplatePanel({ selectedFile }) {
  const [builtins, setBuiltins] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [extracting, setExtracting] = useState(null);
  const [results, setResults] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newTemplate, setNewTemplate] = useState({ name: "", fields: [{ name: "", type: "string", description: "", required: false }] });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [b, t] = await Promise.all([api.listBuiltinTemplates(), api.listTemplates()]);
      setBuiltins(b.templates || []);
      setTemplates(t.templates || []);
    } catch { toast.error("Failed to load templates"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const extract = async (templateId) => {
    if (!selectedFile) { toast.error("Select a document first"); return; }
    setExtracting(templateId);
    try {
      const result = await api.extractWithTemplate(templateId, selectedFile);
      setResults(result);
      toast.success("Extraction complete");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Extraction failed");
    } finally { setExtracting(null); }
  };

  const addField = () => setNewTemplate(t => ({
    ...t,
    fields: [...t.fields, { name: "", type: "string", description: "", required: false }],
  }));

  const updateField = (i, key, val) => {
    setNewTemplate(t => {
      const f = [...t.fields];
      f[i] = { ...f[i], [key]: val };
      return { ...t, fields: f };
    });
  };

  const removeField = (i) => setNewTemplate(t => ({
    ...t, fields: t.fields.filter((_, j) => j !== i),
  }));

  const handleCreateTemplate = async (e) => {
    e.preventDefault();
    if (!newTemplate.name || newTemplate.fields.some(f => !f.name || !f.description)) {
      toast.error("Fill all field names and descriptions"); return;
    }
    try {
      await api.createTemplate(newTemplate.name, newTemplate.fields);
      toast.success("Template created");
      setShowCreate(false);
      setNewTemplate({ name: "", fields: [{ name: "", type: "string", description: "", required: false }] });
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Create failed");
    }
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Extraction Templates</span>
        <button className="btn-primary" onClick={() => setShowCreate(s => !s)}>
          {showCreate ? "Cancel" : "+ Custom"}
        </button>
      </div>

      {showCreate && (
        <form className="panel-form" onSubmit={handleCreateTemplate}>
          <div className="form-group">
            <label>Template Name</label>
            <input className="input" value={newTemplate.name} onChange={e => setNewTemplate(t => ({ ...t, name: e.target.value }))} placeholder="My Template" />
          </div>
          <div className="form-group">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <label>Fields</label>
              <button type="button" className="btn-sm" onClick={addField}>+ Field</button>
            </div>
            {newTemplate.fields.map((f, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 90px 1fr auto auto", gap: 4, marginTop: 4, alignItems: "center" }}>
                <input className="input" value={f.name} onChange={e => updateField(i, "name", e.target.value)} placeholder="field_name" style={{ fontFamily: "monospace", fontSize: 11 }} />
                <select className="input" value={f.type} onChange={e => updateField(i, "type", e.target.value)} style={{ fontSize: 11 }}>
                  {FIELD_TYPES.map(t => <option key={t}>{t}</option>)}
                </select>
                <input className="input" value={f.description} onChange={e => updateField(i, "description", e.target.value)} placeholder="Description" style={{ fontSize: 11 }} />
                <label style={{ fontSize: 11, whiteSpace: "nowrap" }}>
                  <input type="checkbox" checked={f.required} onChange={e => updateField(i, "required", e.target.checked)} /> Req
                </label>
                <button type="button" className="btn-sm danger" onClick={() => removeField(i)}>×</button>
              </div>
            ))}
          </div>
          <button className="btn-primary" type="submit" style={{ marginTop: 8, width: "100%" }}>Create Template</button>
        </form>
      )}

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : (
        <>
          <div style={{ padding: "4px 12px 0" }}>
            <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>Built-in Templates</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {builtins.map(b => (
                <button
                  key={b.slug}
                  className={`mode-chip${extracting === b.slug ? " active" : ""}`}
                  onClick={() => extract(b.slug)}
                  disabled={extracting === b.slug}
                  title={`${b.field_count} fields`}
                >
                  {BUILTIN_ICONS[b.slug] || "📄"} {b.name}
                  {extracting === b.slug && " …"}
                </button>
              ))}
            </div>
          </div>

          {templates.length > 0 && (
            <div style={{ padding: "8px 12px 0" }}>
              <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>Custom Templates</div>
              {templates.map(t => (
                <div key={t.template_id} className="panel-item" style={{ marginBottom: 4 }}>
                  <div className="panel-item-row">
                    <div>
                      <div className="panel-item-title">{t.name}</div>
                      <div className="panel-item-sub">{t.field_count} fields</div>
                    </div>
                    <button
                      className="btn-sm"
                      onClick={() => extract(t.template_id)}
                      disabled={extracting === t.template_id}
                    >
                      {extracting === t.template_id ? "…" : "Extract"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {results && (
            <div className="extraction-result">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontWeight: 600, fontSize: 12 }}>{results.template_name} — Extraction</span>
                <button className="btn-sm" onClick={() => setResults(null)}>×</button>
              </div>
              <div className="extraction-grid">
                {Object.entries(results.fields || {}).map(([field, value]) => {
                  const conf = results.confidence?.[field];
                  const confColor = conf >= 0.9 ? "var(--green)" : conf >= 0.7 ? "var(--amber)" : "var(--red)";
                  return (
                    <div key={field} className="extraction-row">
                      <span className="extraction-key">{field}</span>
                      <span className="extraction-val">{value == null ? "—" : String(value)}</span>
                      {conf != null && (
                        <span style={{ fontSize: 10, color: confColor }}>{Math.round(conf * 100)}%</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

TemplatePanel.propTypes = { selectedFile: PropTypes.string };
