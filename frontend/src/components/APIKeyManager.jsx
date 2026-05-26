// frontend/src/components/APIKeyManager.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import toast from "react-hot-toast";

const ALL_SCOPES = ["read", "write", "ingest", "query", "admin"];

function CreateKeyModal({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState(["read", "write"]);
  const [expireDays, setExpireDays] = useState("");
  const [loading, setLoading] = useState(false);

  const toggle = (s) => setScopes(p => p.includes(s) ? p.filter(x => x !== s) : [...p, s]);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { toast.error("Key name required"); return; }
    setLoading(true);
    try {
      const r = await api.createApiKey({
        name, scopes,
        expires_in_days: expireDays ? +expireDays : undefined,
      });
      onCreated(r);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to create key");
    } finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()} style={{ maxWidth: 400 }}>
        <div style={{ fontWeight: 700, marginBottom: 16 }}>Generate New API Key</div>
        <form onSubmit={submit}>
          <div className="form-group">
            <label>Key Name</label>
            <input className="input" required value={name} onChange={e => setName(e.target.value)}
              placeholder="CI/CD Pipeline, Mobile App…" autoFocus />
          </div>
          <div className="form-group">
            <label>Scopes</label>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
              {ALL_SCOPES.map(s => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: 4,
                  fontSize: 12, cursor: "pointer" }}>
                  <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
                  {s}
                </label>
              ))}
            </div>
          </div>
          <div className="form-group">
            <label>Expires in (days) <span style={{ color: "var(--text-4)" }}>optional</span></label>
            <input className="input" type="number" min={1} max={365} value={expireDays}
              onChange={e => setExpireDays(e.target.value)} placeholder="Never" />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn-sm" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? "Generating…" : "Generate Key"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function KeyReveal({ apiKey, onDismiss }) {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(apiKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    });
  };

  return (
    <div className="key-reveal" style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 12, color: "var(--amber)", fontWeight: 600, marginBottom: 8 }}>
        ⚠ Save this key now — it will NOT be shown again.
      </div>
      <div style={{ fontFamily: "monospace", fontSize: 11, wordBreak: "break-all",
        background: "var(--surface-3)", padding: 12, borderRadius: 6, marginBottom: 10 }}>
        {apiKey}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn-primary" onClick={copy}>
          {copied ? "Copied!" : "Copy Key"}
        </button>
        <button className="btn-sm" onClick={onDismiss}>Dismiss</button>
      </div>
    </div>
  );
}

export function APIKeyManager() {
  const { user } = useAuth();
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newKey, setNewKey] = useState(null);

  const load = useCallback(async () => {
    if (!user?.workspace_id) return;
    setLoading(true);
    try {
      const r = await api.listApiKeys(user.workspace_id);
      setKeys(r.api_keys || []);
    } catch { toast.error("Failed to load keys"); }
    finally { setLoading(false); }
  }, [user]);

  useEffect(() => { load(); }, [load]);

  const handleCreated = (r) => {
    setNewKey(r.api_key);
    setShowCreate(false);
    load();
    toast.success("API key created!");
  };

  const revoke = async (keyId) => {
    if (!confirm("Revoke this key? All integrations using it will stop working.")) return;
    try {
      await api.revokeApiKey(keyId);
      toast.success("Key revoked");
      load();
    } catch { toast.error("Revoke failed"); }
  };

  const rotate = async (keyId) => {
    if (!confirm("Rotate this key? The old key will be invalidated immediately.")) return;
    try {
      const r = await api.rotateApiKey(keyId);
      setNewKey(r.api_key);
      toast.success("Key rotated — copy the new key!");
      load();
    } catch { toast.error("Rotate failed"); }
  };

  return (
    <div style={{ padding: "24px 32px", maxWidth: 860 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>API Keys</div>
          <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
            Manage API keys for programmatic access to your workspace.
          </div>
        </div>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>+ New Key</button>
      </div>

      {newKey && <KeyReveal apiKey={newKey} onDismiss={() => setNewKey(null)} />}

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : keys.length === 0 ? (
        <div className="panel-empty">
          No API keys yet. Create one to integrate DocuMind into your app.
        </div>
      ) : (
        <div className="panel-list">
          {keys.map(k => (
            <div key={k.key_id} className="panel-item">
              <div className="panel-item-row">
                <div style={{ flex: 1 }}>
                  <div className="panel-item-title">{k.name}</div>
                  <div className="panel-item-sub">
                    <span style={{ fontFamily: "monospace" }}>{k.key_prefix}…</span>
                    {" · "}{(k.scopes || []).join(", ")}
                    {k.expires_at && (
                      <span style={{ marginLeft: 8, color: "var(--amber)" }}>
                        expires {new Date(k.expires_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                  <div className="panel-item-sub" style={{ fontSize: 10, marginTop: 2 }}>
                    {k.usage_count} uses
                    {k.last_used_at && ` · last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                    {" · created "}{new Date(k.created_at).toLocaleDateString()}
                    {k.created_by_email && ` by ${k.created_by_email}`}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span className={`status-chip ${k.is_active ? "green" : "grey"}`}>
                    {k.is_active ? "Active" : "Revoked"}
                  </span>
                  {k.is_active && (
                    <>
                      <button className="btn-sm" onClick={() => rotate(k.key_id)}>Rotate</button>
                      <button className="btn-sm danger" onClick={() => revoke(k.key_id)}>Revoke</button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateKeyModal onClose={() => setShowCreate(false)} onCreated={handleCreated} />
      )}
    </div>
  );
}
