// frontend/src/components/WebhookPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

const VALID_EVENTS = [
  "document_ingested", "query_answered", "extraction_complete",
  "alert_triggered", "workflow_triggered", "annotation_created", "compliance_checked",
];

export function WebhookPanel() {
  const [webhooks, setWebhooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [testingId, setTestingId] = useState(null);
  const [deliveriesFor, setDeliveriesFor] = useState(null);
  const [deliveries, setDeliveries] = useState([]);
  const [form, setForm] = useState({
    name: "", url: "", secret: "", events: ["document_ingested"],
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listWebhooks();
      setWebhooks(data.webhooks || []);
    } catch { toast.error("Failed to load webhooks"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.name || !form.url || !form.secret || form.events.length === 0) {
      toast.error("All fields required"); return;
    }
    try {
      await api.registerWebhook(form.name, form.url, form.secret, form.events);
      toast.success("Webhook registered");
      setShowForm(false);
      setForm({ name: "", url: "", secret: "", events: ["document_ingested"] });
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Registration failed");
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteWebhook(id);
      toast.success("Webhook deleted");
      load();
    } catch { toast.error("Delete failed"); }
  };

  const handleTest = async (id) => {
    setTestingId(id);
    try {
      const r = await api.testWebhook(id);
      r.success ? toast.success(`Test OK (HTTP ${r.http_status})`) : toast.error(`Test failed: ${r.error}`);
    } catch { toast.error("Test failed"); }
    finally { setTestingId(null); }
  };

  const loadDeliveries = async (id) => {
    if (deliveriesFor === id) { setDeliveriesFor(null); return; }
    try {
      const data = await api.getWebhookDeliveries(id);
      setDeliveries(data.deliveries || []);
      setDeliveriesFor(id);
    } catch { toast.error("Could not load deliveries"); }
  };

  const toggleEvent = (ev) => {
    setForm(f => ({
      ...f,
      events: f.events.includes(ev) ? f.events.filter(e => e !== ev) : [...f.events, ev],
    }));
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Webhooks</span>
        <button className="btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? "Cancel" : "+ Register"}
        </button>
      </div>

      {showForm && (
        <form className="panel-form" onSubmit={handleCreate}>
          <div className="form-group">
            <label>Name</label>
            <input className="input" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="My Webhook" />
          </div>
          <div className="form-group">
            <label>URL</label>
            <input className="input" value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))} placeholder="https://your-endpoint.com/hook" />
          </div>
          <div className="form-group">
            <label>Secret</label>
            <input className="input" type="password" value={form.secret} onChange={e => setForm(f => ({ ...f, secret: e.target.value }))} placeholder="Min 8 chars" />
          </div>
          <div className="form-group">
            <label>Events</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {VALID_EVENTS.map(ev => (
                <label key={ev} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
                  <input type="checkbox" checked={form.events.includes(ev)} onChange={() => toggleEvent(ev)} />
                  {ev}
                </label>
              ))}
            </div>
          </div>
          <button className="btn-primary" type="submit" style={{ marginTop: 8 }}>Register Webhook</button>
        </form>
      )}

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : webhooks.length === 0 ? (
        <div className="panel-empty">No webhooks registered yet</div>
      ) : (
        <div className="panel-list">
          {webhooks.map(wh => (
            <div key={wh.id} className="panel-item">
              <div className="panel-item-row">
                <div>
                  <div className="panel-item-title">{wh.name}</div>
                  <div className="panel-item-sub">{wh.url}</div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                    {(wh.events || []).map(ev => (
                      <span key={ev} className="tag-chip">{ev}</span>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                  <button className="btn-sm" onClick={() => handleTest(wh.id)} disabled={testingId === wh.id}>
                    {testingId === wh.id ? "…" : "Test"}
                  </button>
                  <button className="btn-sm" onClick={() => loadDeliveries(wh.id)}>Logs</button>
                  <button className="btn-sm danger" onClick={() => handleDelete(wh.id)}>Del</button>
                </div>
              </div>
              {deliveriesFor === wh.id && (
                <div className="delivery-log">
                  {deliveries.length === 0 ? (
                    <div style={{ fontSize: 11, color: "var(--text-4)" }}>No deliveries yet</div>
                  ) : deliveries.map(d => (
                    <div key={d.id} className={`delivery-item ${d.status}`}>
                      <span className={`status-dot ${d.status === "delivered" ? "green" : "red"}`} />
                      <span style={{ fontSize: 11 }}>{d.event_type}</span>
                      <span style={{ fontSize: 10, color: "var(--text-4)" }}>#{d.attempt}</span>
                      {d.http_status && <span style={{ fontSize: 10 }}>HTTP {d.http_status}</span>}
                      {d.error_msg && <span style={{ fontSize: 10, color: "var(--red)" }}>{d.error_msg}</span>}
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
