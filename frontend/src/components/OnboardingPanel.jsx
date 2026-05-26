// frontend/src/components/OnboardingPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

const SCOPES = ["read", "write", "ingest", "query", "admin"];

export function OnboardingPanel() {
  const [activeTab, setActiveTab] = useState("invites");
  const [invites, setInvites] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("editor");
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyScopes, setNewKeyScopes] = useState(["read", "write"]);
  const [generatedKey, setGeneratedKey] = useState(null);
  const [copyDone, setCopyDone] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [inv, keys] = await Promise.all([
        api.listInvites().catch(() => ({ invites: [] })),
        api.listWorkspaceApiKeys().catch(() => ({ api_keys: [] })),
      ]);
      setInvites(inv.invites || []);
      setApiKeys(keys.api_keys || []);
    } catch { toast.error("Failed to load"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const sendInvite = async (e) => {
    e.preventDefault();
    if (!inviteEmail) { toast.error("Email required"); return; }
    try {
      const r = await api.sendInvite(inviteEmail, inviteRole, true);
      toast.success(`Invite sent to ${r.email}`);
      setInviteEmail("");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to send invite");
    }
  };

  const createKey = async (e) => {
    e.preventDefault();
    if (!newKeyName) { toast.error("Key name required"); return; }
    try {
      const r = await api.createWorkspaceApiKey(newKeyName, newKeyScopes);
      setGeneratedKey(r.api_key);
      setNewKeyName("");
      toast.success("API key created — copy it now!");
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to create key");
    }
  };

  const revokeKey = async (keyId) => {
    try {
      await api.revokeWorkspaceApiKey(keyId);
      toast.success("Key revoked");
      load();
    } catch { toast.error("Revoke failed"); }
  };

  const copyKey = () => {
    navigator.clipboard.writeText(generatedKey).then(() => {
      setCopyDone(true);
      setTimeout(() => setCopyDone(false), 2000);
    });
  };

  const toggleScope = (s) => {
    setNewKeyScopes(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Onboarding</span>
        <div className="tab-bar">
          {["invites", "api-keys"].map(t => (
            <button key={t} className={`tab-btn${activeTab === t ? " active" : ""}`} onClick={() => setActiveTab(t)}>
              {t === "invites" ? "Invites" : "API Keys"}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "invites" && (
        <div>
          <form className="panel-form" onSubmit={sendInvite}>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                type="email"
                value={inviteEmail}
                onChange={e => setInviteEmail(e.target.value)}
                placeholder="colleague@company.com"
              />
              <select className="input" style={{ flex: "0 0 90px" }} value={inviteRole} onChange={e => setInviteRole(e.target.value)}>
                <option value="editor">editor</option>
                <option value="viewer">viewer</option>
                <option value="admin">admin</option>
              </select>
              <button className="btn-primary" type="submit">Invite</button>
            </div>
          </form>

          {loading ? (
            <div className="panel-empty">Loading…</div>
          ) : invites.length === 0 ? (
            <div className="panel-empty">No invites sent yet</div>
          ) : (
            <div className="panel-list">
              {invites.map(inv => (
                <div key={inv.invite_id} className="panel-item">
                  <div className="panel-item-row">
                    <div>
                      <div className="panel-item-title">{inv.email}</div>
                      <div className="panel-item-sub">{inv.role} · expires {inv.expires_at?.slice(0, 10)}</div>
                    </div>
                    <span className={`status-chip ${inv.status === "accepted" ? "green" : inv.status === "expired" ? "grey" : "amber"}`}>
                      {inv.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "api-keys" && (
        <div>
          {generatedKey && (
            <div className="key-reveal">
              <div style={{ fontSize: 11, color: "var(--amber)", marginBottom: 6 }}>
                ⚠ Copy this key now — it won't be shown again
              </div>
              <div style={{ fontFamily: "monospace", fontSize: 11, wordBreak: "break-all", background: "var(--surface-3)", padding: 8, borderRadius: 4 }}>
                {generatedKey}
              </div>
              <button className="btn-primary" onClick={copyKey} style={{ marginTop: 8 }}>
                {copyDone ? "Copied!" : "Copy Key"}
              </button>
              <button className="btn-sm" onClick={() => setGeneratedKey(null)} style={{ marginLeft: 8 }}>Dismiss</button>
            </div>
          )}

          <form className="panel-form" onSubmit={createKey}>
            <div className="form-group">
              <label>Key Name</label>
              <input className="input" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} placeholder="CI/CD Pipeline" />
            </div>
            <div className="form-group">
              <label>Scopes</label>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {SCOPES.map(s => (
                  <label key={s} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
                    <input type="checkbox" checked={newKeyScopes.includes(s)} onChange={() => toggleScope(s)} />
                    {s}
                  </label>
                ))}
              </div>
            </div>
            <button className="btn-primary" type="submit">Generate Key</button>
          </form>

          {loading ? (
            <div className="panel-empty">Loading…</div>
          ) : apiKeys.length === 0 ? (
            <div className="panel-empty">No API keys yet</div>
          ) : (
            <div className="panel-list">
              {apiKeys.map(k => (
                <div key={k.key_id} className="panel-item">
                  <div className="panel-item-row">
                    <div>
                      <div className="panel-item-title">{k.name}</div>
                      <div className="panel-item-sub">
                        <span style={{ fontFamily: "monospace" }}>{k.key_prefix}…</span>
                        {" · "}{(k.scopes || []).join(", ")}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      <span className={`status-chip ${k.is_active ? "green" : "grey"}`}>
                        {k.is_active ? "Active" : "Revoked"}
                      </span>
                      {k.is_active && (
                        <button className="btn-sm danger" onClick={() => revokeKey(k.key_id)}>Revoke</button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
