// frontend/src/components/SuperAdminPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

export function SuperAdminPanel({ user }) {
  const [stats, setStats] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [activeTab, setActiveTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [billingWs, setBillingWs] = useState(null);
  const [billing, setBilling] = useState(null);
  const [health, setHealth] = useState(null);
  const [celery, setCelery] = useState(null);

  const isSuperAdmin = user?.is_superuser;

  const loadStats = useCallback(async () => {
    setLoading(true);
    try {
      const [s, w] = await Promise.all([api.adminGetStats(), api.adminListWorkspaces()]);
      setStats(s);
      setWorkspaces(w.workspaces || []);
    } catch (err) {
      if (err?.response?.status === 403) return;
      toast.error("Failed to load admin data");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (isSuperAdmin) loadStats();
  }, [isSuperAdmin, loadStats]);

  const loadBilling = async (wsId) => {
    if (billingWs === wsId) { setBillingWs(null); setBilling(null); return; }
    try {
      const b = await api.adminGetBilling(wsId);
      setBilling(b);
      setBillingWs(wsId);
    } catch { toast.error("Failed to load billing"); }
  };

  const suspendWs = async (wsId) => {
    if (!confirm(`Suspend workspace ${wsId}?`)) return;
    try {
      await api.adminSuspendWorkspace(wsId);
      toast.success("Workspace suspended");
      loadStats();
    } catch { toast.error("Suspend failed"); }
  };

  const activateWs = async (wsId) => {
    try {
      await api.adminActivateWorkspace(wsId);
      toast.success("Workspace activated");
      loadStats();
    } catch { toast.error("Activate failed"); }
  };

  const flushCache = async () => {
    if (!confirm("Flush Redis cache? This will clear all cached queries.")) return;
    try {
      const r = await api.adminFlushCache();
      toast.success(r.flushed ? "Cache flushed" : `Not flushed: ${r.reason || r.error}`);
    } catch { toast.error("Flush failed"); }
  };

  const loadHealth = async () => {
    try {
      const h = await api.adminSystemHealth();
      setHealth(h);
      const c = await api.adminCeleryStatus();
      setCelery(c);
    } catch { toast.error("Health check failed"); }
  };

  if (!isSuperAdmin) {
    return <div className="panel-empty" style={{ padding: 24 }}>Superadmin access required</div>;
  }

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Super Admin</span>
        <div className="tab-bar">
          {["overview", "workspaces", "system"].map(t => (
            <button key={t} className={`tab-btn${activeTab === t ? " active" : ""}`} onClick={() => setActiveTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {loading && activeTab !== "system" ? (
        <div className="panel-empty">Loading…</div>
      ) : (
        <>
          {activeTab === "overview" && stats && (
            <div style={{ padding: "8px 12px" }}>
              <div className="stats-grid">
                {[
                  ["Workspaces", stats.total_workspaces],
                  ["Users", stats.total_users],
                  ["Documents", stats.total_documents],
                  ["Webhook Calls", stats.total_webhook_deliveries],
                  ["Compliance Checks", stats.total_compliance_checks],
                  ["E-Signs", stats.total_esign_requests],
                ].map(([label, val]) => (
                  <div key={label} className="stat-card">
                    <div className="stat-value">{val ?? "—"}</div>
                    <div className="stat-label">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === "workspaces" && (
            <div className="panel-list">
              {workspaces.map(ws => (
                <div key={ws.workspace_id} className="panel-item">
                  <div className="panel-item-row">
                    <div>
                      <div className="panel-item-title">{ws.name || ws.workspace_id}</div>
                      <div className="panel-item-sub">{ws.user_count} users · {ws.doc_count} docs · {ws.plan || "free"}</div>
                    </div>
                    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <span className={`status-chip ${ws.is_active ? "green" : "grey"}`}>
                        {ws.is_active ? "Active" : "Suspended"}
                      </span>
                      <button className="btn-sm" onClick={() => loadBilling(ws.workspace_id)}>$</button>
                      {ws.is_active
                        ? <button className="btn-sm danger" onClick={() => suspendWs(ws.workspace_id)}>Suspend</button>
                        : <button className="btn-sm" onClick={() => activateWs(ws.workspace_id)}>Activate</button>
                      }
                    </div>
                  </div>
                  {billingWs === ws.workspace_id && billing && (
                    <div className="delivery-log">
                      {Object.entries(billing).filter(([k]) => k !== "workspace_id" && k !== "error").map(([k, v]) => (
                        <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                          <span style={{ color: "var(--text-4)" }}>{k.replace(/_/g, " ")}</span>
                          <span>{v}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {activeTab === "system" && (
            <div style={{ padding: "8px 12px" }}>
              <button className="btn-primary" onClick={loadHealth} style={{ marginBottom: 10 }}>
                Refresh Health
              </button>
              <button className="btn-sm danger" onClick={flushCache} style={{ marginLeft: 8, marginBottom: 10 }}>
                Flush Redis Cache
              </button>
              {health && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    System: <span style={{ color: health.status === "healthy" ? "var(--green)" : "var(--amber)" }}>{health.status}</span>
                  </div>
                  {Object.entries(health.services || {}).map(([svc, status]) => (
                    <div key={svc} style={{ display: "flex", gap: 8, fontSize: 11, marginBottom: 3 }}>
                      <span className={`status-dot ${status === "ok" ? "green" : "red"}`} />
                      <span>{svc}</span>
                      <span style={{ color: "var(--text-4)" }}>{status}</span>
                    </div>
                  ))}
                </div>
              )}
              {celery && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Celery</div>
                  <div style={{ fontSize: 11 }}>Workers: {celery.worker_count}</div>
                  <div style={{ fontSize: 11 }}>Active tasks: {celery.active_tasks}</div>
                  {celery.workers?.length > 0 && (
                    <div style={{ fontSize: 10, color: "var(--text-4)", marginTop: 4 }}>
                      {celery.workers.join(", ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

SuperAdminPanel.propTypes = { user: PropTypes.object };
