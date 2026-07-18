/**
 * DocStatusBadge — Team Collaboration / Review Workflow (Feature #7)
 *
 * Renders a small status chip on each document row.
 * Clicking it opens a dropdown to change status, assign to a team member,
 * and add a short note.
 */
import { useState, useRef, useEffect } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

// ── Config ────────────────────────────────────────────────────────────────────

export const STATUS_OPTIONS = [
  { value: "none",           label: "No status",      color: "var(--text-4)",          bg: "var(--bg-3)" },
  { value: "pending_review", label: "Pending Review", color: "var(--amber, #f59e0b)",  bg: "rgba(245,158,11,0.12)" },
  { value: "reviewed",       label: "Reviewed",       color: "var(--teal, #0d9488)",   bg: "rgba(13,148,136,0.12)" },
  { value: "filed",          label: "Filed",          color: "var(--blue, #3b82f6)",   bg: "rgba(59,130,246,0.12)" },
  { value: "flagged",        label: "Flagged",        color: "var(--red, #ef4444)",    bg: "rgba(239,68,68,0.12)" },
];

function getOption(value) {
  return STATUS_OPTIONS.find(o => o.value === value) || STATUS_OPTIONS[0];
}

// ── Component ─────────────────────────────────────────────────────────────────

export function DocStatusBadge({ documentId, statusData, workspaceId, onUpdated }) {
  const [open,     setOpen]     = useState(false);
  const [saving,   setSaving]   = useState(false);
  const [assignee, setAssignee] = useState(statusData?.assignee || "");
  const [note,     setNote]     = useState(statusData?.note || "");
  const ref = useRef(null);

  const current = statusData?.status || "none";
  const opt = getOption(current);

  // sync props → local state when parent refreshes
  useEffect(() => {
    setAssignee(statusData?.assignee || "");
    setNote(statusData?.note || "");
  }, [statusData]);

  // close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const save = async (newStatus) => {
    setSaving(true);
    try {
      const updated = await api.updateDocStatus(documentId, newStatus, assignee || null, note || null, workspaceId);
      onUpdated?.(documentId, updated);
      toast.success(`Status: ${getOption(newStatus).label}`);
      setOpen(false);
      api.logAudit("status_change", documentId,
        `${getOption(newStatus).label}${assignee ? ` → ${assignee}` : ""}`, workspaceId);
    } catch {
      toast.error("Failed to update status");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="ds-wrap" ref={ref}>
      {/* Badge chip */}
      <button
        className="ds-badge"
        style={{ background: opt.bg, color: opt.color }}
        onClick={() => setOpen(o => !o)}
        title={`Status: ${opt.label}${statusData?.assignee ? ` · ${statusData.assignee}` : ""}`}
      >
        {current === "none" ? "+" : opt.label}
      </button>

      {/* Dropdown */}
      {open && (
        <div className="ds-dropdown">
          <div className="ds-dropdown-title">Set Status</div>

          {/* Status options */}
          <div className="ds-status-list">
            {STATUS_OPTIONS.map(o => (
              <button
                key={o.value}
                className={`ds-status-opt${current === o.value ? " active" : ""}`}
                style={{ "--opt-color": o.color, "--opt-bg": o.bg }}
                onClick={() => save(o.value)}
                disabled={saving}
              >
                <span className="ds-status-dot" style={{ background: o.color }} />
                {o.label}
                {current === o.value && <span className="ds-status-check">✓</span>}
              </button>
            ))}
          </div>

          <div className="ds-divider" />

          {/* Assignee */}
          <div className="ds-field-label">Assign to</div>
          <input
            className="ds-input"
            placeholder="team member name or email"
            value={assignee}
            onChange={e => setAssignee(e.target.value)}
            onKeyDown={e => e.key === "Enter" && save(current)}
          />

          {/* Note */}
          <div className="ds-field-label" style={{ marginTop: 6 }}>Note</div>
          <input
            className="ds-input"
            placeholder="short note"
            value={note}
            onChange={e => setNote(e.target.value)}
            onKeyDown={e => e.key === "Enter" && save(current)}
          />

          <button
            className="ds-save-btn"
            onClick={() => save(current)}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>

          {statusData?.updated_by && (
            <div className="ds-meta">
              Last updated by {statusData.updated_by}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

DocStatusBadge.propTypes = {
  documentId:  PropTypes.string.isRequired,
  statusData:  PropTypes.object,
  workspaceId: PropTypes.string,
  onUpdated:   PropTypes.func,
};
