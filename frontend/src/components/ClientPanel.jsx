import { useState, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

// ── Icons ────────────────────────────────────────────────────────────────

function PlusIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
    </svg>
  );
}

function FolderIcon({ open }) {
  return open ? (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M3 6a2 2 0 012-2h4l2 2h8a2 2 0 012 2v1H3V6zm0 3h18l-1.5 9A2 2 0 0117.5 20h-11a2 2 0 01-1.98-1.78L3 9z"/>
    </svg>
  ) : (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M3 6a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V6z"/>
    </svg>
  );
}

function EditIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/>
      <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/>
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

// ── CreateClientForm ─────────────────────────────────────────────────────

function CreateClientForm({ workspaceId, onCreated, onCancel }) {
  const [name, setName]   = useState("");
  const [gstin, setGstin] = useState("");
  const [pan, setPan]     = useState("");
  const [busy, setBusy]   = useState(false);
  const inputRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const client = await api.createClient({ name: name.trim(), gstin: gstin || null, pan: pan || null, workspace_id: workspaceId });
      onCreated(client);
      toast.success(`Client "${client.name}" created`);
    } catch {
      toast.error("Could not create client");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="client-create-form">
      <input
        ref={inputRef}
        className="client-input"
        placeholder="Client name *"
        value={name}
        onChange={e => setName(e.target.value)}
        maxLength={128}
        required
      />
      <div style={{ display: "flex", gap: 4 }}>
        <input
          className="client-input"
          placeholder="GSTIN (optional)"
          value={gstin}
          onChange={e => setGstin(e.target.value.toUpperCase())}
          maxLength={15}
          style={{ flex: 1 }}
        />
        <input
          className="client-input"
          placeholder="PAN"
          value={pan}
          onChange={e => setPan(e.target.value.toUpperCase())}
          maxLength={10}
          style={{ flex: 1 }}
        />
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button type="submit" disabled={busy || !name.trim()} className="client-btn-primary">
          {busy ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={onCancel} className="client-btn-ghost">Cancel</button>
      </div>
    </form>
  );
}

// ── EditClientForm ───────────────────────────────────────────────────────

function EditClientForm({ client, onSaved, onCancel }) {
  const [name, setName]   = useState(client.name);
  const [gstin, setGstin] = useState(client.gstin || "");
  const [pan, setPan]     = useState(client.pan || "");
  const [busy, setBusy]   = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const updated = await api.updateClient(client.id, { name: name.trim(), gstin: gstin || null, pan: pan || null });
      onSaved(updated);
    } catch {
      toast.error("Could not update client");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="client-create-form" onClick={e => e.stopPropagation()}>
      <input className="client-input" value={name} onChange={e => setName(e.target.value)} maxLength={128} required />
      <div style={{ display: "flex", gap: 4 }}>
        <input className="client-input" placeholder="GSTIN" value={gstin} onChange={e => setGstin(e.target.value.toUpperCase())} maxLength={15} style={{ flex: 1 }} />
        <input className="client-input" placeholder="PAN" value={pan} onChange={e => setPan(e.target.value.toUpperCase())} maxLength={10} style={{ flex: 1 }} />
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button type="submit" disabled={busy || !name.trim()} className="client-btn-primary">{busy ? "Saving…" : "Save"}</button>
        <button type="button" onClick={onCancel} className="client-btn-ghost">Cancel</button>
      </div>
    </form>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function ClientPanel({ workspaceId, selectedClientId, onSelectClient, onDocumentMapChange }) {
  const [clients,    setClients]    = useState([]);
  const [loading,    setLoading]    = useState(false);
  const [creating,   setCreating]   = useState(false);
  const [editingId,  setEditingId]  = useState(null);
  const [docMap,     setDocMap]     = useState({});

  const load = async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const [list, map] = await Promise.all([
        api.listClients(workspaceId),
        api.getDocumentClientMap(workspaceId),
      ]);
      setClients(list);
      setDocMap(map);
      onDocumentMapChange?.(map);
    } catch {
      // silent — sidebar shouldn't crash on optional feature
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [workspaceId]); // eslint-disable-line

  const handleCreated = (client) => {
    setClients(prev => [...prev, { ...client, doc_count: 0 }].sort((a, b) => a.name.localeCompare(b.name)));
    setCreating(false);
  };

  const handleSaved = (updated) => {
    setClients(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
    setEditingId(null);
  };

  const handleDelete = async (clientId, clientName) => {
    if (!window.confirm(`Delete client "${clientName}"? Documents will become unassigned.`)) return;
    try {
      await api.deleteClient(clientId);
      setClients(prev => prev.filter(c => c.id !== clientId));
      if (selectedClientId === clientId) onSelectClient(null);
      toast.success("Client deleted");
    } catch {
      toast.error("Could not delete client");
    }
  };

  const totalDocs = Object.keys(docMap).length;

  return (
    <div className="client-panel">
      {/* Header row */}
      <div className="client-panel-header">
        <span className="client-panel-title">Clients</span>
        <button
          className="client-add-btn"
          onClick={() => setCreating(v => !v)}
          title="Add client"
          aria-label="Add client"
        >
          <PlusIcon />
        </button>
      </div>

      {/* Create form */}
      {creating && (
        <CreateClientForm
          workspaceId={workspaceId}
          onCreated={handleCreated}
          onCancel={() => setCreating(false)}
        />
      )}

      {/* Client list */}
      <div className="client-list" role="listbox" aria-label="Client folders">
        {/* All clients row */}
        <button
          className={`client-row${!selectedClientId ? " active" : ""}`}
          role="option"
          aria-selected={!selectedClientId}
          onClick={() => onSelectClient(null)}
        >
          <span className="client-row-icon" style={{ color: "var(--teal, #0d9488)" }}>
            <FolderIcon open={!selectedClientId} />
          </span>
          <span className="client-row-name">All clients</span>
          <span className="client-row-count">{totalDocs}</span>
        </button>

        {loading && <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--tx-3)" }}>Loading…</div>}

        {!loading && clients.map(client => (
          <div key={client.id} className="client-row-wrap">
            {editingId === client.id ? (
              <EditClientForm
                client={client}
                onSaved={handleSaved}
                onCancel={() => setEditingId(null)}
              />
            ) : (
              <button
                className={`client-row${selectedClientId === client.id ? " active" : ""}`}
                role="option"
                aria-selected={selectedClientId === client.id}
                onClick={() => onSelectClient(client.id === selectedClientId ? null : client.id)}
              >
                <span className="client-row-icon" style={{ color: selectedClientId === client.id ? "var(--teal, #0d9488)" : "var(--tx-3)" }}>
                  <FolderIcon open={selectedClientId === client.id} />
                </span>
                <span className="client-row-name">{client.name}</span>
                {client.gstin && (
                  <span className="client-row-meta">{client.gstin.slice(0, 6)}…</span>
                )}
                <span className="client-row-count">{client.doc_count}</span>
                <span className="client-row-actions" onClick={e => e.stopPropagation()}>
                  <button
                    className="client-icon-btn"
                    onClick={() => setEditingId(client.id)}
                    title="Edit"
                    aria-label="Edit client"
                  ><EditIcon /></button>
                  <button
                    className="client-icon-btn danger"
                    onClick={() => handleDelete(client.id, client.name)}
                    title="Delete"
                    aria-label="Delete client"
                  ><TrashIcon /></button>
                </span>
              </button>
            )}
          </div>
        ))}

        {!loading && clients.length === 0 && !creating && (
          <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--tx-3)", lineHeight: 1.5 }}>
            No clients yet. Click <strong>+</strong> to add your first client.
          </div>
        )}
      </div>
    </div>
  );
}

ClientPanel.propTypes = {
  workspaceId: PropTypes.string,
  selectedClientId: PropTypes.string,
  onSelectClient: PropTypes.func.isRequired,
  onDocumentMapChange: PropTypes.func,
};
