// frontend/src/components/DropZone.jsx — Nebula Dark
import { useState, useCallback, useRef } from "react";
import PropTypes from "prop-types";

const ACCEPT = ".pdf,.txt,.png,.jpg,.jpeg,.tiff,.bmp,.docx,.doc,.xlsx,.xls,.csv,.mp3,.mp4,.wav,.m4a";

function IconUpload() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
      <polyline points="17 8 12 3 7 8"/>
      <line x1="12" y1="3" x2="12" y2="15"/>
    </svg>
  );
}

function IconSpinner() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true" style={{ animation: "spin 0.7s linear infinite" }}>
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
    </svg>
  );
}

function BatchQueue({ queue }) {
  if (!queue || queue.length === 0) return null;
  return (
    <div className="batch-queue">
      {queue.map((item, i) => (
        <div key={i} className={`batch-item batch-item-${item.status}`}>
          <span className="batch-item-name">{item.name}</span>
          <span className="batch-item-status">
            {item.status === "pending" ? "⏳" :
             item.status === "uploading" ? <span style={{ animation: "spin 0.7s linear infinite", display: "inline-block" }}>↻</span> :
             item.status === "done" ? "✓" : "✗"}
          </span>
        </div>
      ))}
    </div>
  );
}

export function DropZone({ onDrop, uploading, progress, visionEnabled, onVisionChange, batchQueue }) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  const handleFiles = useCallback((files) => {
    if (!files || files.length === 0) return;
    onDrop(files.length === 1 ? files[0] : files);
  }, [onDrop]);

  const onDragOver = useCallback((e) => { e.preventDefault(); setDragOver(true); }, []);
  const onDragLeave = useCallback(() => setDragOver(false), []);
  const onDropHandler = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const onKeyDown = useCallback((e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); }
  }, []);

  const pct = typeof progress === "number" ? Math.min(100, Math.max(0, progress)) : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        className={`upload-zone${dragOver ? " drag-over" : ""}${uploading ? " uploading" : ""}`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDropHandler}
        onClick={() => !uploading && inputRef.current?.click()}
        onKeyDown={onKeyDown}
        role="button"
        tabIndex={uploading ? -1 : 0}
        aria-label={uploading ? `Uploading: ${pct}%` : "Click or drag to upload documents"}
        aria-busy={uploading}
      >
        <div className="upload-icon">
          {uploading ? <IconSpinner /> : <IconUpload />}
        </div>

        {uploading && batchQueue && batchQueue.length > 1 ? (
          <>
            <div className="upload-label">Uploading batch…</div>
            <div className="upload-hint">
              {batchQueue.filter(q => q.status === "done").length}/{batchQueue.length} complete
            </div>
          </>
        ) : uploading ? (
          <>
            <div className="upload-label">Uploading…</div>
            <div className="upload-hint">{pct > 0 ? `${pct}% complete` : "Processing…"}</div>
            {pct > 0 && (
              <div className="upload-progress-bar">
                <div className="upload-progress-fill" style={{ width: `${pct}%` }} />
              </div>
            )}
          </>
        ) : (
          <>
            <div className="upload-label">Upload Documents</div>
            <div className="upload-hint">Click or drop · PDF · TXT · DOCX · Image · multi-file</div>
          </>
        )}

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          multiple
          style={{ display: "none" }}
          onChange={e => handleFiles(e.target.files)}
          aria-hidden="true"
          tabIndex={-1}
        />
      </div>

      {/* Batch queue display */}
      <BatchQueue queue={batchQueue} />

      {/* Vision toggle */}
      <div className="vision-toggle">
        <span className="vision-label">Vision OCR</span>
        <div
          className={`toggle${visionEnabled ? " on" : ""}`}
          role="switch"
          aria-checked={visionEnabled}
          aria-label="Toggle vision OCR"
          tabIndex={0}
          onClick={() => onVisionChange?.(!visionEnabled)}
          onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onVisionChange?.(!visionEnabled); }}}
        >
          <div className="toggle-dot" />
        </div>
      </div>
    </div>
  );
}

DropZone.propTypes = {
  onDrop: PropTypes.func.isRequired,
  uploading: PropTypes.bool,
  progress: PropTypes.number,
  visionEnabled: PropTypes.bool,
  onVisionChange: PropTypes.func,
  batchQueue: PropTypes.array,
};
