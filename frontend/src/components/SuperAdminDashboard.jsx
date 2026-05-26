// frontend/src/components/SuperAdminDashboard.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({ label, value, color = "var(--accent)" }) {
  return (
    <div className="stat-card">
      <div className="stat-value" style={{ color }}>{value ?? "—"}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ServiceDot({ name, status }) {
  const ok = status === "ok";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 4 }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: ok ? "var(--green)" : "var(--red)",
        flexShrink: 0,
      }} />
      <span style={{ flex: 1 }}>{name}</span>
      <span style={{ color: ok ? "var(--green)" : "var(--red)", fontFamily: "monospace" }}>
        {status}
      </span>
    </div>
  );
}

function WorkspaceRow({ ws, onSuspend, onActivate, onImpersonate, onViewAudit, onEditLimits }) {
  const [showBilling, setShowBilling] = useState(false);
  const [billing, setBilling] = useState(null);

  const toggleBilling = async () => {
    if (showBilling) { setShowBilling(false); return; }
    try {
      const b = await api.adminGetBilling(ws.workspace_id);
      setBilling(b);
      setShowBilling(true);
    } catch { toast.error("Failed to load billing"); }
  };

  return (
    <div className="panel-item" style={{ marginBottom: 6 }}>
      <div className="panel-item-row">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="panel-item-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {ws.client_name || ws.name}
            <span className="mode-chip" style={{ fontSize: 10, padding: "1px 6px" }}>{ws.plan}</span>
          </div>
          <div className="panel-item-sub">
            {ws.client_email} · {ws.active_users ?? 0} users · {ws.doc_count ?? 0} docs
            · {((ws.storage_used_mb || 0) / 1024).toFixed(2)} GB
          </div>
          <div className="panel-item-sub" style={{ fontFamily: "monospace", fontSize: 10 }}>
            {ws.workspace_id?.slice(0, 8)}…
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <span className={`status-chip ${ws.is_active ? "green" : "grey"}`}>
            {ws.is_active ? "Active" : "Suspended"}
          </span>
          <button className="btn-sm" onClick={toggleBilling}>$</button>
          <button className="btn-sm" onClick={() => onEditLimits(ws)}>Limits</button>
          <button className="btn-sm" onClick={() => onImpersonate(ws.workspace_id)}>Login As</button>
          <button className="btn-sm" onClick={() => onViewAudit(ws.workspace_id)}>Audit</button>
          {ws.is_active
            ? <button className="btn-sm danger" onClick={() => onSuspend(ws)}>Suspend</button>
            : <button className="btn-sm" onClick={() => onActivate(ws.workspace_id)}>Activate</button>
          }
        </div>
      </div>
      {showBilling && billing && (
        <div className="delivery-log" style={{ marginTop: 8 }}>
          {Object.entries(billing)
            .filter(([k]) => !["workspace_id", "error"].includes(k))
            .map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                <span style={{ color: "var(--text-4)" }}>{k.replace(/_/g, " ")}</span>
                <span>{String(v)}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

// ── Create workspace modal ────────────────────────────────────────────────────

function CreateWorkspaceModal({ onClose, onCreated }) {
  const [form, setForm] = useState({
    client_name: "", client_email: "", plan: "starter",
    domain_type: "", max_docs: 100, max_queries_per_day: 500,
    max_storage_gb: 5, send_invite: true,
  });
  const [loading, setLoading] = useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const ws = await api.adminCreateWorkspace(form);
      toast.success(`Workspace "${ws.name}" created!`);
      onCreated(ws);
      onClose();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Create failed");
    } finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 16 }}>Onboard New Client</div>
        <form onSubmit={submit}>
          <div className="form-group">
            <label>Client Name</label>
            <input className="input" required value={form.client_name}
              onChange={e => set("client_name", e.target.value)} placeholder="Acme Law Firm" />
          </div>
          <div className="form-group">
            <label>Client Email</label>
            <input className="input" type="email" required value={form.client_email}
              onChange={e => set("client_email", e.target.value)} placeholder="contact@acme.com" />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Plan</label>
              <select className="input" value={form.plan} onChange={e => set("plan", e.target.value)}>
                <option value="starter">Starter</option>
                <option value="business">Business</option>
                <option value="enterprise">Enterprise</option>
              </select>
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Domain Type</label>
              <input className="input" value={form.domain_type}
                onChange={e => set("domain_type", e.target.value)} placeholder="legal / medical…" />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Max Docs</label>
              <input className="input" type="number" value={form.max_docs}
                onChange={e => set("max_docs", +e.target.value)} />
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Queries/Day</label>
              <input className="input" type="number" value={form.max_queries_per_day}
                onChange={e => set("max_queries_per_day", +e.target.value)} />
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label>Storage (GB)</label>
              <input className="input" type="number" step="0.5" value={form.max_storage_gb}
                onChange={e => set("max_storage_gb", +e.target.value)} />
            </div>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, marginBottom: 16 }}>
            <input type="checkbox" checked={form.send_invite}
              onChange={e => set("send_invite", e.target.checked)} />
            Send onboarding invite email to client
          </label>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn-sm" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Creating…" : "Create Workspace"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Edit limits modal ─────────────────────────────────────────────────────────

function EditLimitsModal({ ws, onClose, onSaved }) {
  const [form, setForm] = useState({
    max_docs: ws.max_docs, max_queries_per_day: ws.max_queries_per_day,
    max_storage_gb: ws.max_storage_gb, plan: ws.plan,
  });
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.adminUpdateWorkspaceLimits(ws.workspace_id, form);
      toast.success("Limits updated");
      onSaved();
      onClose();
    } catch { toast.error("Update failed"); }
    finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()} style={{ maxWidth: 380 }}>
        <div style={{ fontWeight: 700, marginBottom: 14 }}>
          Edit Limits — {ws.client_name || ws.name}
        </div>
        <form onSubmit={submit}>
          {[
            ["max_docs", "Max Documents", 1],
            ["max_queries_per_day", "Max Queries/Day", 1],
            ["max_storage_gb", "Max Storage (GB)", 0.1],
          ].map(([key, label, step]) => (
            <div className="form-group" key={key}>
              <label>{label}</label>
              <input className="input" type="number" step={step} value={form[key]}
                onChange={e => setForm(f => ({ ...f, [key]: +e.target.value }))} />
            </div>
          ))}
          <div className="form-group">
            <label>Plan</label>
            <select className="input" value={form.plan}
              onChange={e => setForm(f => ({ ...f, plan: e.target.value }))}>
              <option value="starter">Starter</option>
              <option value="business">Business</option>
              <option value="enterprise">Enterprise</option>
            </select>
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn-sm" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Saving…" : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main dashboard ────────────────────────────────────────────────────────────

export function SuperAdminDashboard() {
  const { isSuperAdmin } = usePermissions();

  const [activeTab, setActiveTab] = useState("overview");
  const [stats, setStats] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [health, setHealth] = useState(null);
  const [celery, setCelery] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [editLimitsWs, setEditLimitsWs] = useState(null);
  const [auditWs, setAuditWs] = useState(null);
  const [auditLogs, setAuditLogs] = useState([]);
  const [suspendModal, setSuspendModal] = useState(null);
  const [suspendReason, setSuspendReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, wList] = await Promise.all([
        api.adminOverview(),
        api.adminListWorkspaces(),
      ]);
      setStats(o);
      setWorkspaces(wList.workspaces || []);
    } catch { toast.error("Failed to load admin data"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { if (isSuperAdmin) load(); }, [isSuperAdmin, load]);

  const loadHealth = async () => {
    try {
      const [h, c] = await Promise.all([api.adminSystemHealth(), api.adminCeleryStatus()]);
      setHealth(h); setCelery(c);
    } catch { toast.error("Health check failed"); }
  };

  const handleImpersonate = async (wsId) => {
    try {
      const r = await api.adminImpersonate(wsId);
      await navigator.clipboard.writeText(r.token);
      toast.success(`Token copied — logged in as ${r.target_email}. Expires in 1h.`);
    } catch { toast.error("Impersonation failed"); }
  };

  const handleSuspend = async () => {
    if (!suspendModal || !suspendReason.trim()) return;
    try {
      await api.adminSuspendWorkspace(suspendModal.workspace_id, suspendReason);
      toast.success("Workspace suspended");
      setSuspendModal(null); setSuspendReason("");
      load();
    } catch { toast.error("Suspend failed"); }
  };

  const handleActivate = async (wsId) => {
    try {
      await api.adminActivateWorkspace(wsId);
      toast.success("Workspace activated");
      load();
    } catch { toast.error("Activate failed"); }
  };

  const viewAudit = async (wsId) => {
    setAuditWs(wsId);
    setActiveTab("audit");
    try {
      const r = await api.adminGetAuditLog(wsId, 100);
      setAuditLogs(r.logs || []);
    } catch { toast.error("Failed to load audit log"); }
  };

  const filtered = workspaces.filter(ws =>
    !search || [ws.client_name, ws.name, ws.client_email]
      .some(f => f?.toLowerCase().includes(search.toLowerCase()))
  );

  if (!isSuperAdmin) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Superadmin access required.
      </div>
    );
  }

  const TABS = ["overview", "workspaces", "system", "audit"];

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-1)", padding: "24px 32px" }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: -0.5 }}>
          Super Admin
          <span style={{ marginLeft: 10, fontSize: 11, fontWeight: 400,
            color: "var(--amber)", background: "var(--amber)22",
            padding: "2px 8px", borderRadius: 4 }}>
            PLATFORM CONTROL
          </span>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 4 }}>
          Full visibility across all workspaces, users, and system services.
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24, borderBottom: "1px solid var(--border)" }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setActiveTab(t)}
            className={`nav-tab${activeTab === t ? " active" : ""}`}
            style={{ fontSize: 12, padding: "6px 14px" }}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn-primary" style={{ fontSize: 12 }}
            onClick={() => setShowCreate(true)}>
            + New Client
          </button>
          <button className="btn-sm" onClick={() => api.adminExportBilling()
            .then(csv => {
              const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
              a.download = "billing.csv"; a.click();
            }).catch(() => toast.error("Export failed"))
          }>
            Export Billing
          </button>
        </div>
      </div>

      {/* Overview */}
      {activeTab === "overview" && (
        <div>
          {loading ? <div className="panel-empty">Loading…</div> : stats && (
            <>
              <div className="stats-grid" style={{ marginBottom: 24 }}>
                <StatCard label="Total Workspaces" value={stats.total_workspaces} />
                <StatCard label="Active Workspaces" value={stats.active_workspaces} color="var(--green)" />
                <StatCard label="Total Users" value={stats.total_users} />
                <StatCard label="Total Documents" value={stats.total_documents?.toLocaleString()} />
                <StatCard label="Queries Today" value={stats.total_queries_today?.toLocaleString()} color="var(--amber)" />
                <StatCard label="Active API Keys" value={stats.active_api_keys} />
                <StatCard label="Pending Invites" value={stats.pending_invites} />
                <StatCard label="Audit Events (24h)" value={stats.audit_events_24h?.toLocaleString()} />
              </div>

              {stats.top_workspaces_by_usage?.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10 }}>
                    Top Workspaces by Usage Today
                  </div>
                  {stats.top_workspaces_by_usage.map(ws => (
                    <div key={ws.workspace_id} className="panel-item"
                      style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>{ws.client_name || ws.name}</span>
                      <span style={{ color: "var(--accent)", fontWeight: 700 }}>
                        {ws.query_count_today} queries
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Workspaces */}
      {activeTab === "workspaces" && (
        <div>
          <input className="input" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search by name, email…"
            style={{ marginBottom: 12, maxWidth: 320 }} />
          {loading ? <div className="panel-empty">Loading…</div>
            : filtered.length === 0 ? <div className="panel-empty">No workspaces found</div>
            : filtered.map(ws => (
              <WorkspaceRow key={ws.workspace_id} ws={ws}
                onSuspend={w => { setSuspendModal(w); setSuspendReason(""); }}
                onActivate={handleActivate}
                onImpersonate={handleImpersonate}
                onViewAudit={viewAudit}
                onEditLimits={setEditLimitsWs}
              />
            ))
          }
        </div>
      )}

      {/* System */}
      {activeTab === "system" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <button className="btn-primary" onClick={loadHealth}>Refresh Health</button>
            <button className="btn-sm danger" onClick={async () => {
              if (!confirm("Flush Redis cache?")) return;
              const r = await api.adminFlushCache().catch(() => null);
              toast[r?.flushed ? "success" : "error"](r?.flushed ? "Cache flushed" : "Flush failed");
            }}>
              Flush Redis Cache
            </button>
          </div>

          {health && (
            <div style={{ marginBottom: 20, padding: 16, background: "var(--surface-2)",
              borderRadius: 8, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
                System Status:
                <span style={{ marginLeft: 8, color: health.status === "healthy" ? "var(--green)" : "var(--amber)" }}>
                  {health.status?.toUpperCase()}
                </span>
              </div>
              {Object.entries(health.services || {}).map(([svc, status]) => (
                <ServiceDot key={svc} name={svc} status={status} />
              ))}
            </div>
          )}

          {celery && (
            <div style={{ padding: 16, background: "var(--surface-2)",
              borderRadius: 8, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Celery Workers</div>
              <div style={{ fontSize: 12 }}>Workers: {celery.worker_count}</div>
              <div style={{ fontSize: 12 }}>Active tasks: {celery.active_tasks}</div>
              {celery.workers?.length > 0 && (
                <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 6 }}>
                  {celery.workers.join(", ")}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Audit */}
      {activeTab === "audit" && (
        <div>
          <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 12 }}>
            {auditWs ? `Showing audit log for workspace: ${auditWs.slice(0, 8)}…` : "Select a workspace to view its audit log"}
          </div>
          {auditLogs.length === 0
            ? <div className="panel-empty">No audit events</div>
            : auditLogs.map(log => (
              <div key={log.id} className="panel-item" style={{ fontSize: 11 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <span style={{ fontWeight: 600, color: "var(--accent)" }}>{log.action}</span>
                  <span style={{ color: "var(--text-4)" }}>
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>
                <div style={{ color: "var(--text-3)", marginTop: 2 }}>
                  {log.user_email || "system"} · {log.resource_type || "–"}
                  {log.ip_address && ` · ${log.ip_address}`}
                </div>
              </div>
            ))
          }
        </div>
      )}

      {/* Modals */}
      {showCreate && (
        <CreateWorkspaceModal
          onClose={() => setShowCreate(false)}
          onCreated={() => load()}
        />
      )}

      {editLimitsWs && (
        <EditLimitsModal
          ws={editLimitsWs}
          onClose={() => setEditLimitsWs(null)}
          onSaved={load}
        />
      )}

      {suspendModal && (
        <div className="modal-overlay" onClick={() => setSuspendModal(null)}>
          <div className="modal-box" onClick={e => e.stopPropagation()} style={{ maxWidth: 380 }}>
            <div style={{ fontWeight: 700, marginBottom: 12 }}>
              Suspend "{suspendModal.client_name || suspendModal.name}"?
            </div>
            <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 12 }}>
              All API calls from this workspace will be blocked immediately.
            </div>
            <div className="form-group">
              <label>Reason</label>
              <input className="input" value={suspendReason}
                onChange={e => setSuspendReason(e.target.value)}
                placeholder="Non-payment, abuse…" />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn-sm" onClick={() => setSuspendModal(null)}>Cancel</button>
              <button className="btn-sm danger" onClick={handleSuspend}
                disabled={!suspendReason.trim()}>
                Suspend Workspace
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
