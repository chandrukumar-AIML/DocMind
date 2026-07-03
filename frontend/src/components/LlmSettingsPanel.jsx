// frontend/src/components/LlmSettingsPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

export function LlmSettingsPanel() {
  const { canManageLlmSettings } = usePermissions();

  const [providers, setProviders] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [showKey, setShowKey] = useState(false);

  const [provider, setProvider] = useState("groq");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [provRes, curRes] = await Promise.all([
        api.listLlmProviders(),
        api.getLlmSettings(),
      ]);
      setProviders(provRes.providers || []);
      if (curRes.configured) {
        setCurrent(curRes);
        setProvider(curRes.provider);
        setModel(curRes.model || "");
        setBaseUrl(curRes.base_url || "");
      } else {
        setCurrent(null);
        const def = (provRes.providers || [])[0];
        if (def) { setProvider(def.id); setModel(def.default_model); setBaseUrl(def.base_url || ""); }
      }
    } catch {
      toast.error("Failed to load LLM settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onProviderChange = (id) => {
    setProvider(id);
    const entry = providers.find(p => p.id === id);
    if (entry) { setModel(entry.default_model); setBaseUrl(entry.base_url || ""); }
    setTestResult(null);
  };

  const save = async (e) => {
    e.preventDefault();
    if (!apiKey.trim()) { toast.error("API key required"); return; }
    setSaving(true);
    try {
      const r = await api.updateLlmSettings({
        provider, model: model || undefined, base_url: baseUrl || undefined, api_key: apiKey,
      });
      setCurrent(r);
      setApiKey("");
      setTestResult(null);
      toast.success("LLM settings saved");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testLlmSettings();
      setTestResult(r);
      if (r.success) toast.success(`Connected — ${r.latency_ms}ms`);
      else toast.error(r.error || "Test failed");
    } catch (err) {
      setTestResult({ success: false, error: err?.response?.data?.detail || "Test failed" });
      toast.error("Test failed");
    } finally {
      setTesting(false);
    }
  };

  const resetToDefault = async () => {
    if (!confirm("Revert this workspace to the platform default LLM?")) return;
    try {
      await api.deleteLlmSettings();
      toast.success("Reverted to platform default");
      setApiKey("");
      setTestResult(null);
      load();
    } catch {
      toast.error("Failed to reset");
    }
  };

  if (!canManageLlmSettings) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Workspace admin access required.
      </div>
    );
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 640 }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>AI Model</div>
        <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
          Bring your own LLM provider &amp; key for this workspace. Your documents and
          queries go directly to the provider you configure here — not the platform default.
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
                  <div className="panel-item-title">
                    Currently using: {providers.find(p => p.id === current.provider)?.label || current.provider}
                  </div>
                  <div className="panel-item-sub">
                    {current.model} · key {current.api_key_masked}
                  </div>
                </div>
                <span className="status-chip green">Active</span>
              </div>
            </div>
          )}

          <form onSubmit={save}>
            <div className="form-group">
              <label>Provider</label>
              <select className="input" value={provider} onChange={e => onProviderChange(e.target.value)}>
                {providers.map(p => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>Model</label>
              <input className="input" value={model} onChange={e => setModel(e.target.value)}
                placeholder="e.g. llama-3.3-70b-versatile" />
            </div>

            <div className="form-group">
              <label>Base URL <span style={{ color: "var(--text-4)" }}>optional override</span></label>
              <input className="input" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
                placeholder="Leave default unless self-hosting" />
            </div>

            <div className="form-group">
              <label>API Key</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input className="input" type={showKey ? "text" : "password"} value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  placeholder={current ? "Enter a new key to replace the saved one" : "Paste your provider API key"} />
                <button type="button" className="btn-sm" onClick={() => setShowKey(s => !s)}>
                  {showKey ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            {testResult && (
              <div style={{ fontSize: 12, marginBottom: 12,
                color: testResult.success ? "var(--green)" : "var(--red)" }}>
                {testResult.success
                  ? `✓ Connected — ${testResult.latency_ms}ms — "${testResult.sample_response}"`
                  : `✗ ${testResult.error}`}
              </div>
            )}

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              {current && (
                <button type="button" className="btn-sm" onClick={resetToDefault}>
                  Reset to platform default
                </button>
              )}
              {current && (
                <button type="button" className="btn-sm" onClick={testConnection} disabled={testing}>
                  {testing ? "Testing…" : "Test Connection"}
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
