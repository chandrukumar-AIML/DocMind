// frontend/src/components/AuditLogViewer.jsx
import { useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

const ACTION_COLORS = {
  user_login: "var(--green)",
  login_failed: "var(--red)",
  document_uploaded: "var(--accent)",
  document_deleted: "var(--red)",
  api_key_created: "var(--green)",
  api_key_revoked: "var(--amber)",
  workspace_suspended: "var(--red)",
  workspace_activated: "var(--green)",
  impersonation_started: "var(--amber)",
  compliance_check_run: "var(--accent)",
};

const SEVERITY_COLORS = {
  info: "var(--text-4)",
  warn: "var(--amber)",
  error: "var(--red)",
  critical: "var(--red)",
};

export function AuditLogViewer() {
  const { user } = useAuth();
  const { isSuperAdmin, isWorkspaceAdmin } = usePermissions();

  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    action: "", severity: "", from_dt: "", to_dt: "", limit: 100,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        workspace_id: isSuperAdmin ? undefined : user?.workspace_id,
        action: filters.action || undefined,
        severity: filters.severity || undefined,
        from_dt: filters.from_dt || undefined,
        to_dt: filters.to_dt || undefined,
        limit: filters.limit,
      };
      const r = await api.getAuditLogs(params);
      setLogs(r.logs || []);
    } catch { toast.error("Failed to load audit log"); }
    finally { setLoading(false); }
  }, [user, isSuperAdmin, filters]);

  const exportCsv = async () => {
    try {
      const wsId = user?.workspace_id;
      if (!wsId) return;
      const csv = await api.exportAuditLog(wsId);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = `audit_${wsId.slice(0, 8)}.csv`;
      a.click();
    } catch { toast.error("Export failed"); }
  };

  const setF = (k, v) => setFilters(f => ({ ...f, [k]: v }));

  if (!isWorkspaceAdmin) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Workspace admin access required.
      </div>
    );
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1000 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start",
        marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>Audit Log</div>
          <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
            Track all actions across your workspace.
          </div>
        </div>
        <button className="btn-sm" onClick={exportCsv}>Export CSV</button>
      </div>

      {/* Filters */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <input className="input" style={{ flex: "1 0 160px", maxWidth: 200 }}
          value={filters.action} onChange={e => setF("action", e.target.value)}
          placeholder="Filter by action…" />
        <select className="input" style={{ flex: "0 0 120px" }}
          value={filters.severity} onChange={e => setF("severity", e.target.value)}>
          <option value="">All severity</option>
          <option value="info">Info</option>
          <option value="warn">Warn</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
        </select>
        <input className="input" type="date" style={{ flex: "0 0 140px" }}
          value={filters.from_dt} onChange={e => setF("from_dt", e.target.value)} />
        <input className="input" type="date" style={{ flex: "0 0 140px" }}
          value={filters.to_dt} onChange={e => setF("to_dt", e.target.value)} />
        <select className="input" style={{ flex: "0 0 90px" }}
          value={filters.limit} onChange={e => setF("limit", +e.target.value)}>
          <option value={50}>50</option>
          <option value={100}>100</option>
          <option value={500}>500</option>
          <option value={1000}>1000</option>
        </select>
        <button className="btn-primary" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Search"}
        </button>
      </div>

      {/* Log table */}
      {logs.length === 0 ? (
        <div className="panel-empty">
          {loading ? "Loading…" : "No audit events. Press Search to load."}
        </div>
      ) : (
        <div>
          <div style={{ fontSize: 11, color: "var(--text-4)", marginBottom: 8 }}>
            {logs.length} events
          </div>
          <div className="panel-list">
            {logs.map(log => (
              <div key={log.id} className="panel-item" style={{ padding: "8px 12px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                    background: SEVERITY_COLORS[log.severity] || "var(--text-4)",
                  }} />
                  <span style={{
                    fontWeight: 600, fontSize: 12,
                    color: ACTION_COLORS[log.action] || "var(--text-2)",
                    flex: 1,
                  }}>
                    {log.action}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-4)", flexShrink: 0 }}>
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 3,
                  paddingLeft: 16, display: "flex", gap: 12 }}>
                  {log.user_email && <span>👤 {log.user_email}</span>}
                  {log.resource_type && (
                    <span>📄 {log.resource_type}{log.resource_id ? ` (${log.resource_id.slice(0, 8)}…)` : ""}</span>
                  )}
                  {log.ip_address && <span>🌐 {log.ip_address}</span>}
                  {log.response_status && (
                    <span style={{ color: log.response_status >= 400 ? "var(--red)" : undefined }}>
                      HTTP {log.response_status}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
