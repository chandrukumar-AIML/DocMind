// frontend/src/components/SsoSettingsPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

export function SsoSettingsPanel() {
  const { canManageSso } = usePermissions();

  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showSecret, setShowSecret] = useState(false);

  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [issuer, setIssuer] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.getSsoConfig();
      if (r.configured) {
        setCurrent(r);
        setClientId(r.client_id || "");
        setIssuer(r.issuer || "");
      } else {
        setCurrent(null);
      }
    } catch {
      toast.error("Failed to load SSO settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async (e) => {
    e.preventDefault();
    if (!clientId.trim() || !clientSecret.trim() || !issuer.trim()) {
      toast.error("Client ID, client secret, and issuer are all required");
      return;
    }
    setSaving(true);
    try {
      const r = await api.updateSsoConfig({ client_id: clientId, client_secret: clientSecret, issuer });
      setCurrent(r);
      setClientSecret("");
      toast.success("SSO settings saved");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to save SSO settings");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!confirm("Remove SSO for this workspace? Members will only be able to sign in with a password.")) return;
    try {
      await api.deleteSsoConfig();
      toast.success("SSO removed");
      setCurrent(null);
      setClientId("");
      setClientSecret("");
      setIssuer("");
      load();
    } catch {
      toast.error("Failed to remove SSO");
    }
  };

  if (!canManageSso) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Workspace admin access required.
      </div>
    );
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 640 }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>SSO</div>
        <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
          Let this workspace's members sign in via your company's identity provider
          (Okta, Azure AD/Entra ID, Google Workspace, or any OIDC-compliant IdP) —
          alongside password login, not instead of it.
        </div>
      </div>

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : (
        <>
          {current && (
            <div className="panel-item" style={{ marginBottom: 20 }}>
              <div className="panel-item-row">
                <div style={{ flex: 1 }}>
                  <div className="panel-item-title">SSO configured</div>
                  <div className="panel-item-sub">
                    {current.issuer} · client {current.client_id} · secret {current.client_secret_masked}
                  </div>
                </div>
                <span className="status-chip green">Active</span>
              </div>
            </div>
          )}

          <form onSubmit={save}>
            <div className="form-group">
              <label>Issuer URL</label>
              <input className="input" value={issuer} onChange={e => setIssuer(e.target.value)}
                placeholder="e.g. https://your-domain.okta.com" />
            </div>

            <div className="form-group">
              <label>Client ID</label>
              <input className="input" value={clientId} onChange={e => setClientId(e.target.value)}
                placeholder="From your IdP's application settings" />
            </div>

            <div className="form-group">
              <label>Client Secret</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input className="input" type={showSecret ? "text" : "password"} value={clientSecret}
                  onChange={e => setClientSecret(e.target.value)}
                  placeholder={current ? "Enter a new secret to replace the saved one" : "From your IdP's application settings"} />
                <button type="button" className="btn-sm" onClick={() => setShowSecret(s => !s)}>
                  {showSecret ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              {current && (
                <button type="button" className="btn-sm" onClick={remove}>
                  Remove SSO
                </button>
              )}
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
}
