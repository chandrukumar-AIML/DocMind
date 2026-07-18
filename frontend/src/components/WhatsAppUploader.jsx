/**
 * WhatsAppUploader — Feature #8
 * Upload a WhatsApp .txt chat export and ingest it into the workspace.
 */
import { useState, useRef } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

async function uploadWhatsApp(file, label, workspaceId, token) {
  const fd = new FormData();
  fd.append("file", file);
  if (label)       fd.append("label", label);
  if (workspaceId) fd.append("workspace_id", workspaceId);

  const res = await fetch(`${BASE_URL}/api/v1/whatsapp/ingest`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export function WhatsAppUploader({ workspaceId, onIngested }) {
  const [file,    setFile]    = useState(null);
  const [label,   setLabel]   = useState("");
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);
  const inputRef = useRef(null);

  const token = localStorage.getItem("documind_access_token") || "";

  const handleFile = (f) => {
    if (!f) return;
    if (!f.name.endsWith(".txt")) { toast.error("Only .txt WhatsApp exports supported"); return; }
    setFile(f);
    setResult(null);
    if (!label) setLabel(f.name.replace(".txt", ""));
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setLoading(true);
    try {
      const data = await uploadWhatsApp(file, label, workspaceId, token);
      setResult(data);
      toast.success(`Ingested ${data.message_count} messages from ${data.senders.join(", ")}`);
      onIngested?.(data);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wa-uploader">
      {/* Drop zone */}
      <div
        className={`wa-dropzone${file ? " has-file" : ""}`}
        onDragOver={e => e.preventDefault()}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".txt"
          style={{ display: "none" }}
          onChange={e => handleFile(e.target.files?.[0])}
        />
        <div className="wa-dropzone-icon">💬</div>
        {file ? (
          <div className="wa-dropzone-name">{file.name}</div>
        ) : (
          <>
            <div className="wa-dropzone-label">Drop WhatsApp .txt export here</div>
            <div className="wa-dropzone-hint">Export chat → Without Media → share the .txt file</div>
          </>
        )}
      </div>

      {/* Label + ingest */}
      {file && (
        <div className="wa-controls">
          <input
            className="draft-context-input"
            style={{ minHeight: "unset", padding: "6px 9px" }}
            placeholder="Chat label (e.g. ABC Pvt Ltd — GST query)"
            value={label}
            onChange={e => setLabel(e.target.value)}
          />
          <button className="draft-generate-btn" onClick={submit} disabled={loading} style={{ marginTop: 4 }}>
            {loading ? <><span className="draft-spinner" /> Ingesting…</> : "⬆ Ingest Chat"}
          </button>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="wa-result">
          <div className="wa-result-row"><span>Messages</span><strong>{result.message_count}</strong></div>
          <div className="wa-result-row"><span>Participants</span><strong>{result.senders.join(", ")}</strong></div>
          <div className="wa-result-row"><span>Date range</span><strong>{result.date_range}</strong></div>
          <div className="wa-result-row"><span>Chunks stored</span><strong>{result.chunk_count}</strong></div>
          <div className="wa-result-note">Chat is now searchable — ask anything about this conversation.</div>
        </div>
      )}
    </div>
  );
}

WhatsAppUploader.propTypes = {
  workspaceId: PropTypes.string,
  onIngested:  PropTypes.func,
};
