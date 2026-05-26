// frontend/src/components/FineTuningPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

export function FineTuningPanel({ workspaceId }) {
  const [models, setModels] = useState([]);
  const [datasetStatus, setDatasetStatus] = useState(null);
  const [loading, setLoading] = useState({});
  const [modelInput, setModelInput] = useState("");

  const set = (key, val) => setLoading(prev => ({ ...prev, [key]: val }));

  useEffect(() => {
    api.listFineTuneModels(workspaceId).then(d => setModels(d.models || [])).catch(() => {});
    api.getDatasetStatus(workspaceId).then(setDatasetStatus).catch(() => {});
  }, [workspaceId]);

  const generateDataset = useCallback(async () => {
    set("dataset", true);
    const toastId = toast.loading("Generating fine-tune dataset…");
    try {
      const r = await api.generateDataset(workspaceId);
      toast.success(`Dataset: ${r.triplet_count ?? 0} triplets`, { id: toastId });
      setDatasetStatus(r);
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || "Failed";
      if (err.response?.status === 501) {
        toast.error("Fine-tuning module not installed", { id: toastId });
      } else {
        toast.error(msg, { id: toastId });
      }
    } finally {
      set("dataset", false);
    }
  }, [workspaceId]);

  const pullModel = useCallback(async () => {
    const name = modelInput.trim();
    if (!name) return;
    set("pull", true);
    const toastId = toast.loading(`Pulling model: ${name}…`);
    try {
      await api.pullFineTuneModel(name, workspaceId);
      toast.success(`Model pulled: ${name}`, { id: toastId });
      setModelInput("");
      api.listFineTuneModels(workspaceId).then(d => setModels(d.models || [])).catch(() => {});
    } catch (err) {
      toast.error(err.response?.data?.detail || "Pull failed", { id: toastId });
    } finally {
      set("pull", false);
    }
  }, [modelInput, workspaceId]);

  const reembed = useCallback(async () => {
    set("reembed", true);
    const toastId = toast.loading("Re-embedding workspace…");
    try {
      const r = await api.reembedWorkspace(workspaceId);
      toast.success(`Re-embedded: ${r.chunks_reembedded ?? 0} chunks`, { id: toastId });
    } catch (err) {
      toast.error(err.response?.data?.detail || "Re-embed failed", { id: toastId });
    } finally {
      set("reembed", false);
    }
  }, [workspaceId]);

  return (
    <div className="ft-panel">
      {/* Dataset generation */}
      <div className="ft-section">
        <div className="ft-section-title">Training Dataset</div>
        <p className="ft-desc">Generate triplet dataset from your documents for fine-tuning embedding models.</p>
        {datasetStatus && (
          <div className="ft-status">
            {datasetStatus.triplet_count > 0 && <span>{datasetStatus.triplet_count} triplets</span>}
            {datasetStatus.status && <span className={`ft-badge ${datasetStatus.status}`}>{datasetStatus.status}</span>}
          </div>
        )}
        <button className="ft-btn primary" onClick={generateDataset} disabled={loading.dataset}>
          {loading.dataset ? "Generating…" : "Generate Dataset"}
        </button>
      </div>

      {/* Re-embed */}
      <div className="ft-section">
        <div className="ft-section-title">Re-embed Workspace</div>
        <p className="ft-desc">Re-process all documents with the current embedding model.</p>
        <button className="ft-btn" onClick={reembed} disabled={loading.reembed}>
          {loading.reembed ? "Re-embedding…" : "Re-embed All Docs"}
        </button>
      </div>

      {/* Pull model */}
      <div className="ft-section">
        <div className="ft-section-title">Pull Ollama Model</div>
        <p className="ft-desc">Download a model from Ollama registry.</p>
        <div className="ft-input-row">
          <input
            className="ft-input"
            placeholder="e.g. llama3.2:7b"
            value={modelInput}
            onChange={e => setModelInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && pullModel()}
          />
          <button className="ft-btn" onClick={pullModel} disabled={!modelInput.trim() || loading.pull}>
            {loading.pull ? "…" : "Pull"}
          </button>
        </div>
        {models.length > 0 && (
          <div className="ft-models">
            {models.map((m, i) => (
              <div key={i} className="ft-model-chip">
                {typeof m === "string" ? m : (m.name || m.model || JSON.stringify(m))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

FineTuningPanel.propTypes = { workspaceId: PropTypes.string };
