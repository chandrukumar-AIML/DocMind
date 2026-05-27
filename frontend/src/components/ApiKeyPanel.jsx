// frontend/src/components/ApiKeyPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

function CopyIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
    </svg>
  );
}

export function ApiKeyPanel() {
  const [keys, setKeys] = useState([]);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState(null); // shown once after creation
  const [copied, setCopied] = useState(null);

  const load = useCallback(() => {
    api.authListApiKeys().then(d => setKeys(d.keys || [])).catch(() => { /* key fetch failed */ });
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = useCallback(async () => {
    const n = name.trim();
    if (!n || creating) return;
    setCreating(true);
    try {
      const result = await api.authCreateApiKey(n, 365);
      setNewKey(result);
      setName("");
      load();
      toast.success("API key created — copy it now, it won't be shown again");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Failed to create key");
    } finally {
      setCreating(false);
    }
  }, [name, creating, load]);

  const revoke = useCallback(async (keyId) => {
    try {
      await api.authDeleteApiKey(keyId);
      setKeys(prev => prev.filter(k => k.key_id !== keyId));
      toast.success("API key revoked");
    } catch {
      toast.error("Failed to revoke key");
    }
  }, []);

  const copy = (text, id) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(id);
      setTimeout(() => setCopied(null), 2000);
    });
  };

  return (
    <div className="apikey-panel">
      <p className="apikey-desc">
        Generate long-lived tokens for programmatic access. Keep them secret.
      </p>

      {/* New key (shown once) */}
      {newKey && (
        <div className="apikey-new-banner">
          <div className="apikey-new-label">New key created — copy now</div>
          <div className="apikey-token-row">
            <code className="apikey-token">{newKey.token?.slice(0, 32)}…</code>
            <button className="apikey-copy-btn" onClick={() => copy(newKey.token, "new")}>
              {copied === "new" ? "✓" : <CopyIcon />}
            </button>
          </div>
          <button className="apikey-dismiss" onClick={() => setNewKey(null)}>Dismiss</button>
        </div>
      )}

      {/* Create form */}
      <div className="apikey-create-row">
        <input
          className="ft-input"
          placeholder="Key name (e.g. my-app)"
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === "Enter" && create()}
        />
        <button className="ft-btn primary" onClick={create} disabled={!name.trim() || creating}
          style={{ whiteSpace: "nowrap" }}>
          {creating ? "…" : "Generate"}
        </button>
      </div>

      {/* Key list */}
      {keys.length === 0 ? (
        <div className="apikey-empty">No API keys yet.</div>
      ) : (
        <div className="apikey-list">
          {keys.map(k => (
            <div key={k.key_id} className="apikey-item">
              <div className="apikey-item-body">
                <div className="apikey-name">{k.name}</div>
                <div className="apikey-meta">
                  ID: {k.key_id} · {k.expires_days}d · {new Date(k.created_at).toLocaleDateString()}
                </div>
              </div>
              <button className="apikey-revoke" onClick={() => revoke(k.key_id)} title="Revoke">
                <TrashIcon />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="apikey-hint">
        Use the token as: <code>Authorization: Bearer &lt;token&gt;</code>
      </div>
    </div>
  );
}
