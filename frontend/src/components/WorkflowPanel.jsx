// frontend/src/components/WorkflowPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

const TRIGGERS = ["document_ingested", "query_answered", "extraction_complete", "alert_triggered", "manual"];
const OPERATORS = ["eq", "neq", "gt", "lt", "gte", "lte", "contains", "not_contains", "in", "regex"];
const ACTION_TYPES = ["webhook", "email", "tag", "domain_analysis"];

const emptyCondition = () => ({ field: "doc_type", operator: "eq", value: "" });
const emptyAction = () => ({ type: "tag", tag_value: "", recipient: "", subject: "", body_template: "" });

export function WorkflowPanel() {
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [runs, setRuns] = useState([]);

  const [form, setForm] = useState({
    name: "",
    trigger_event: "document_ingested",
    conditions: [emptyCondition()],
    actions: [emptyAction()],
    is_active: true,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listWorkflows();
      setWorkflows(data.workflows || []);
    } catch { toast.error("Failed to load workflows"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const addCondition = () => setForm(f => ({ ...f, conditions: [...f.conditions, emptyCondition()] }));
  const addAction = () => setForm(f => ({ ...f, actions: [...f.actions, emptyAction()] }));
  const removeCondition = (i) => setForm(f => ({ ...f, conditions: f.conditions.filter((_, j) => j !== i) }));
  const removeAction = (i) => setForm(f => ({ ...f, actions: f.actions.filter((_, j) => j !== i) }));

  const updateCondition = (i, key, val) => {
    setForm(f => {
      const c = [...f.conditions];
      c[i] = { ...c[i], [key]: val };
      return { ...f, conditions: c };
    });
  };
  const updateAction = (i, key, val) => {
    setForm(f => {
      const a = [...f.actions];
      a[i] = { ...a[i], [key]: val };
      return { ...f, actions: a };
    });
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.name) { toast.error("Name required"); return; }
    try {
      await api.createWorkflow(form);
      toast.success("Workflow created");
      setShowForm(false);
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Create failed");
    }
  };

  const toggleActive = async (wf) => {
    try {
      await api.updateWorkflow(wf.workflow_id, { is_active: !wf.is_active });
      load();
    } catch { toast.error("Update failed"); }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteWorkflow(id);
      toast.success("Workflow disabled");
      load();
    } catch { toast.error("Delete failed"); }
  };

  const viewRuns = async (id) => {
    if (expandedId === id) { setExpandedId(null); return; }
    try {
      const data = await api.getWorkflowRuns(id);
      setRuns(data.runs || []);
      setExpandedId(id);
    } catch { toast.error("Could not load runs"); }
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Workflow Automation</span>
        <button className="btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? "Cancel" : "+ New Rule"}
        </button>
      </div>

      {showForm && (
        <form className="panel-form" onSubmit={handleCreate}>
          <div className="form-group">
            <label>Workflow Name</label>
            <input className="input" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Tag High-Risk Invoices" />
          </div>
          <div className="form-group">
            <label>Trigger Event</label>
            <select className="input" value={form.trigger_event} onChange={e => setForm(f => ({ ...f, trigger_event: e.target.value }))}>
              {TRIGGERS.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div className="form-group">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label>IF Conditions</label>
              <button type="button" className="btn-sm" onClick={addCondition}>+ Add</button>
            </div>
            {form.conditions.map((c, i) => (
              <div key={i} style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
                <input className="input" style={{ flex: 1 }} value={c.field} onChange={e => updateCondition(i, "field", e.target.value)} placeholder="field" />
                <select className="input" style={{ flex: 1 }} value={c.operator} onChange={e => updateCondition(i, "operator", e.target.value)}>
                  {OPERATORS.map(op => <option key={op} value={op}>{op}</option>)}
                </select>
                <input className="input" style={{ flex: 1 }} value={c.value} onChange={e => updateCondition(i, "value", e.target.value)} placeholder="value" />
                <button type="button" className="btn-sm danger" onClick={() => removeCondition(i)}>×</button>
              </div>
            ))}
          </div>

          <div className="form-group">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label>THEN Actions</label>
              <button type="button" className="btn-sm" onClick={addAction}>+ Add</button>
            </div>
            {form.actions.map((a, i) => (
              <div key={i} style={{ marginTop: 6, background: "var(--surface-2)", borderRadius: 6, padding: 8 }}>
                <div style={{ display: "flex", gap: 4, alignItems: "center", marginBottom: 4 }}>
                  <select className="input" value={a.type} onChange={e => updateAction(i, "type", e.target.value)}>
                    {ACTION_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <button type="button" className="btn-sm danger" onClick={() => removeAction(i)}>×</button>
                </div>
                {a.type === "tag" && (
                  <input className="input" value={a.tag_value} onChange={e => updateAction(i, "tag_value", e.target.value)} placeholder="Tag value" />
                )}
                {a.type === "email" && (
                  <>
                    <input className="input" value={a.recipient} onChange={e => updateAction(i, "recipient", e.target.value)} placeholder="Recipient email" style={{ marginBottom: 4 }} />
                    <input className="input" value={a.subject} onChange={e => updateAction(i, "subject", e.target.value)} placeholder="Subject" />
                  </>
                )}
                {a.type === "domain_analysis" && (
                  <input className="input" value={a.domain || ""} onChange={e => updateAction(i, "domain", e.target.value)} placeholder="Domain (legal, medical…)" />
                )}
              </div>
            ))}
          </div>

          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, marginTop: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={form.is_active} onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))} />
            Active immediately
          </label>
          <button className="btn-primary" type="submit" style={{ marginTop: 10, width: "100%" }}>Save Workflow</button>
        </form>
      )}

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : workflows.length === 0 ? (
        <div className="panel-empty">No workflows yet. Create one to automate post-ingest actions.</div>
      ) : (
        <div className="panel-list">
          {workflows.map(wf => (
            <div key={wf.workflow_id} className="panel-item">
              <div className="panel-item-row">
                <div>
                  <div className="panel-item-title">{wf.name}</div>
                  <div className="panel-item-sub">On: {wf.trigger_event} · {wf.condition_count} conditions · {wf.action_count} actions</div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span className={`status-chip ${wf.is_active ? "green" : "grey"}`} style={{ cursor: "pointer" }} onClick={() => toggleActive(wf)}>
                    {wf.is_active ? "Active" : "Off"}
                  </span>
                  <button className="btn-sm" onClick={() => viewRuns(wf.workflow_id)}>Runs</button>
                  <button className="btn-sm danger" onClick={() => handleDelete(wf.workflow_id)}>Del</button>
                </div>
              </div>
              {expandedId === wf.workflow_id && (
                <div className="delivery-log">
                  {runs.length === 0 ? (
                    <div style={{ fontSize: 11, color: "var(--text-4)" }}>No runs yet</div>
                  ) : runs.map(r => (
                    <div key={r.run_id} className="delivery-item">
                      <span className={`status-dot ${r.status === "completed" ? "green" : "red"}`} />
                      <span style={{ fontSize: 11 }}>{r.status}</span>
                      <span style={{ fontSize: 10, color: "var(--text-4)" }}>{r.created_at?.slice(0, 16)}</span>
                      {r.error_msg && <span style={{ fontSize: 10, color: "var(--red)" }}>{r.error_msg}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
