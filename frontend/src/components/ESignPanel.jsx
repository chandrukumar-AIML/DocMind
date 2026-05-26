// frontend/src/components/ESignPanel.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const STATUS_COLORS = { pending: "var(--amber)", completed: "var(--green)", declined: "var(--red)" };

function SignatureCanvas({ onSave, onCancel }) {
  const canvasRef = useRef(null);
  const drawing = useRef(false);

  const startDraw = (e) => {
    drawing.current = true;
    const ctx = canvasRef.current.getContext("2d");
    ctx.beginPath();
    const rect = canvasRef.current.getBoundingClientRect();
    ctx.moveTo(e.clientX - rect.left, e.clientY - rect.top);
  };
  const draw = (e) => {
    if (!drawing.current) return;
    const ctx = canvasRef.current.getContext("2d");
    const rect = canvasRef.current.getBoundingClientRect();
    ctx.lineTo(e.clientX - rect.left, e.clientY - rect.top);
    ctx.strokeStyle = "var(--accent)";
    ctx.lineWidth = 2;
    ctx.stroke();
  };
  const stopDraw = () => { drawing.current = false; };
  const clear = () => {
    const ctx = canvasRef.current.getContext("2d");
    ctx.clearRect(0, 0, 300, 120);
  };
  const save = () => {
    const data = canvasRef.current.toDataURL("image/png");
    onSave(data);
  };

  return (
    <div style={{ background: "var(--surface-2)", borderRadius: 8, padding: 12, marginTop: 8 }}>
      <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>Draw your signature:</div>
      <canvas
        ref={canvasRef}
        width={300} height={120}
        style={{ background: "var(--surface-3)", borderRadius: 4, cursor: "crosshair", display: "block" }}
        onMouseDown={startDraw}
        onMouseMove={draw}
        onMouseUp={stopDraw}
        onMouseLeave={stopDraw}
      />
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button className="btn-primary" onClick={save}>Save Signature</button>
        <button className="btn-sm" onClick={clear}>Clear</button>
        <button className="btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export function ESignPanel({ selectedFile }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [signingFor, setSigningFor] = useState(null);
  const [signers, setSigners] = useState([{ name: "", email: "", order: 1 }]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.listESignRequests();
      setRequests(data.requests || []);
    } catch { toast.error("Failed to load requests"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const addSigner = () => setSigners(s => [...s, { name: "", email: "", order: s.length + 1 }]);
  const removeSigner = (i) => setSigners(s => s.filter((_, j) => j !== i));
  const updateSigner = (i, key, val) => {
    setSigners(s => { const a = [...s]; a[i] = { ...a[i], [key]: val }; return a; });
  };

  const handleRequest = async (e) => {
    e.preventDefault();
    if (!selectedFile) { toast.error("Select a document first"); return; }
    if (signers.some(s => !s.name || !s.email)) { toast.error("Fill all signer details"); return; }
    try {
      await api.requestSignature(selectedFile, signers, null);
      toast.success("Signature request sent");
      setShowForm(false);
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Request failed");
    }
  };

  const handleInAppSign = async (requestId, signatureData) => {
    try {
      await api.inappSign(requestId, signatureData);
      toast.success("Document signed");
      setSigningFor(null);
      load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Signing failed");
    }
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">E-Signature</span>
        <button className="btn-primary" onClick={() => setShowForm(s => !s)}>
          {showForm ? "Cancel" : "+ Request"}
        </button>
      </div>

      {showForm && (
        <form className="panel-form" onSubmit={handleRequest}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>
            Document: {selectedFile ? selectedFile.split("/").pop().split("\\").pop() : "—none selected—"}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <label style={{ fontSize: 12 }}>Signers</label>
            <button type="button" className="btn-sm" onClick={addSigner}>+ Signer</button>
          </div>
          {signers.map((s, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 50px auto", gap: 4, marginTop: 4 }}>
              <input className="input" value={s.name} onChange={e => updateSigner(i, "name", e.target.value)} placeholder="Full Name" />
              <input className="input" value={s.email} onChange={e => updateSigner(i, "email", e.target.value)} placeholder="email@co.com" />
              <input className="input" type="number" min="1" value={s.order} onChange={e => updateSigner(i, "order", parseInt(e.target.value))} />
              {i > 0 && <button type="button" className="btn-sm danger" onClick={() => removeSigner(i)}>×</button>}
            </div>
          ))}
          <button className="btn-primary" type="submit" style={{ marginTop: 10, width: "100%" }}>Send for Signature</button>
        </form>
      )}

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : requests.length === 0 ? (
        <div className="panel-empty">No signature requests yet</div>
      ) : (
        <div className="panel-list">
          {requests.map(r => {
            const name = r.source_file?.split("/").pop().split("\\").pop();
            const color = STATUS_COLORS[r.status] || "var(--text-4)";
            return (
              <div key={r.request_id} className="panel-item">
                <div className="panel-item-row">
                  <div>
                    <div className="panel-item-title">{name}</div>
                    <div className="panel-item-sub">{r.provider} · {r.created_at?.slice(0, 10)}</div>
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <span style={{ fontSize: 11, color, fontWeight: 600 }}>{r.status}</span>
                    {r.status === "pending" && r.provider === "in_app" && (
                      <button className="btn-sm" onClick={() => setSigningFor(r.request_id)}>Sign</button>
                    )}
                  </div>
                </div>
                {signingFor === r.request_id && (
                  <SignatureCanvas
                    onSave={(data) => handleInAppSign(r.request_id, data)}
                    onCancel={() => setSigningFor(null)}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

ESignPanel.propTypes = { selectedFile: PropTypes.string };
