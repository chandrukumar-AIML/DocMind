// frontend/src/components/DocumentList.jsx — Nebula Dark
import { useState, useCallback, useEffect } from "react";
import toast from "react-hot-toast";
import { api } from "../api/client";
import PropTypes from "prop-types";

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6"/>
      <path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/>
      <path d="M10 11v6M14 11v6"/>
      <path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/>
    </svg>
  );
}

function ChunksIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
      <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
    </svg>
  );
}

function ReindexIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="23 4 23 10 17 10"/>
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
  );
}

function XlsxIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="2"/>
      <path d="M3 9h18M9 21V9"/>
    </svg>
  );
}

function FileEmptyIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="9" y1="13" x2="15" y2="13"/>
      <line x1="9" y1="17" x2="12" y2="17"/>
    </svg>
  );
}

function getFileExt(filename) {
  return (filename || "").split(".").pop().toLowerCase();
}

function getIconClass(filename) {
  const ext = getFileExt(filename);
  if (ext === "pdf") return "pdf";
  if (["docx", "doc"].includes(ext)) return "docx";
  if (["xlsx", "xls", "csv"].includes(ext)) return "xlsx";
  if (["mp3", "wav", "m4a", "mp4"].includes(ext)) return "audio";
  if (["png", "jpg", "jpeg", "tiff", "bmp"].includes(ext)) return "img";
  return "other";
}

function getIconLabel(filename) {
  const ext = getFileExt(filename);
  const labels = { pdf: "PDF", docx: "DOC", doc: "DOC", xlsx: "XLS", xls: "XLS",
    csv: "CSV", mp3: "MP3", wav: "WAV", m4a: "M4A", mp4: "MP4",
    png: "IMG", jpg: "IMG", jpeg: "IMG", tiff: "IMG", bmp: "IMG" };
  return labels[ext] || ext.toUpperCase() || "FILE";
}

function formatSize(bytes) {
  if (!bytes) return null;
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function ChunkViewer({ sourceFile, onClose }) {
  const [chunks, setChunks] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const wsId = typeof localStorage !== "undefined" ? localStorage.getItem("documind_workspace_id") : null;
    api.getDocumentChunks(sourceFile, wsId)
      .then(data => setChunks(data.chunks || data.results || []))
      .catch(() => setChunks([]))
      .finally(() => setLoading(false));
  }, [sourceFile]);

  return (
    <div className="chunk-viewer">
      <div className="chunk-viewer-header">
        <span style={{ fontSize: 11, color: "var(--text-3)", fontWeight: 600 }}>
          Indexed Chunks
        </span>
        <button className="chunk-viewer-close" onClick={onClose} aria-label="Close chunk viewer">✕</button>
      </div>
      {loading ? (
        <div style={{ padding: "12px", fontSize: 11, color: "var(--text-4)" }}>Loading chunks…</div>
      ) : chunks.length === 0 ? (
        <div style={{ padding: "12px", fontSize: 11, color: "var(--text-4)" }}>No chunks found</div>
      ) : (
        <div className="chunk-list">
          {chunks.slice(0, 20).map((c, i) => (
            <div key={i} className="chunk-item">
              <div className="chunk-num">{i + 1}</div>
              <div className="chunk-text">
                {(c.text || c.page_content || c.content || "").slice(0, 200)}
                {(c.text || c.page_content || c.content || "").length > 200 ? "…" : ""}
              </div>
            </div>
          ))}
          {chunks.length > 20 && (
            <div style={{ fontSize: 10, color: "var(--text-4)", padding: "4px 8px" }}>
              +{chunks.length - 20} more chunks
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function DocumentList({ documents, onDeleted, selectedFile, onSelect, workspaceId }) {
  const [deletingId, setDeletingId] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(null);
  const [reindexingId, setReindexingId] = useState(null);
  const [viewingChunksId, setViewingChunksId] = useState(null);
  const [search, setSearch] = useState("");

  const handleDownload = useCallback((sourceFile, e) => {
    e.stopPropagation();
    const wsId = workspaceId || localStorage.getItem("documind_workspace_id") || "";
    const url = api.downloadDocument(sourceFile, wsId);
    const a = document.createElement("a");
    a.href = url;
    a.download = sourceFile.split("/").pop().split("\\").pop();
    a.click();
  }, [workspaceId]);

  const handleExportXlsx = useCallback((sourceFile, e) => {
    e.stopPropagation();
    const wsId = workspaceId || localStorage.getItem("documind_workspace_id") || "";
    const url = api.exportTablesUrl(sourceFile, wsId);
    window.open(url, "_blank");
    toast.success("Downloading tables as XLSX…");
  }, [workspaceId]);

  const handleReindex = useCallback(async (sourceFile, e) => {
    e.stopPropagation();
    setReindexingId(sourceFile);
    try {
      await api.reindexDocument(sourceFile);
      toast.success("Document refreshed");
    } catch {
      toast.error("Refresh failed");
    } finally {
      setReindexingId(null);
    }
  }, []);

  const handleDelete = async (sourceFile) => {
    setDeletingId(sourceFile);
    setConfirmingDelete(null);
    try {
      await api.deleteDocument(sourceFile);
      toast.success("Document removed");
      onDeleted?.(sourceFile);
    } catch {
      toast.error("Failed to remove document");
    } finally {
      setDeletingId(null);
    }
  };

  if (!documents || documents.length === 0) {
    return (
      <div className="doc-empty">
        <FileEmptyIcon />
        <div style={{ marginTop: 8, fontWeight: 500, color: "var(--text-2)" }}>No documents yet</div>
        <div style={{ marginTop: 4 }}>Upload a file above to get started</div>
      </div>
    );
  }

  const filtered = search.trim()
    ? documents.filter(d =>
        d.source_file.toLowerCase().includes(search.toLowerCase())
      )
    : documents;

  return (
    <div className="doc-list">
      {/* Search box — appears when 4+ documents */}
      {documents.length >= 4 && (
        <div style={{ padding: "0 0 6px" }}>
          <input
            type="search"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search documents…"
            aria-label="Filter documents"
            style={{
              width: "100%",
              background: "var(--bg-3)",
              border: "1px solid var(--border-2)",
              borderRadius: "var(--r-sm)",
              color: "var(--text-1)",
              fontSize: 12,
              padding: "5px 10px",
              outline: "none",
            }}
          />
        </div>
      )}
      {filtered.length === 0 && search && (
        <div style={{ fontSize: 12, color: "var(--text-4)", padding: "12px 4px", textAlign: "center" }}>
          No documents match "{search}"
        </div>
      )}
      {filtered.map((doc) => {
        const isSelected    = selectedFile === doc.source_file;
        const isDeleting    = deletingId   === doc.source_file;
        const isConfirming  = confirmingDelete === doc.source_file;
        const isReindexing  = reindexingId === doc.source_file;
        const shortName    = doc.source_file.split("/").pop().split("\\").pop();
        const iconClass    = getIconClass(shortName);
        const iconLabel    = getIconLabel(shortName);
        const size         = formatSize(doc.file_size);

        return (
          <div key={doc.source_file} style={{ display: "contents" }}>
          <div
            className={`doc-item anim-fade-in${isSelected ? " selected" : ""}${isDeleting ? " deleting" : ""}`}
            onClick={() => !isConfirming && !isDeleting && onSelect?.(isSelected ? null : doc.source_file)}
            role="button"
            tabIndex={0}
            aria-selected={isSelected}
            aria-label={`${shortName}${isSelected ? ", selected" : ""}`}
            onKeyDown={e => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                if (!isConfirming && !isDeleting) onSelect?.(isSelected ? null : doc.source_file);
              }
            }}
          >
            <div className={`doc-icon ${iconClass}`}>{iconLabel}</div>

            <div className="doc-meta">
              <div className="doc-name" title={doc.source_file}>{shortName}</div>
              <div className="doc-info" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                {doc.page_count > 0 && <span>{doc.page_count}p</span>}
                {doc.chunk_count > 0 && <span>· {doc.chunk_count} chunks</span>}
                {size && <span>· {size}</span>}
                {doc.mean_ocr_confidence > 0 && (
                  <span style={{
                    color: doc.mean_ocr_confidence >= 0.9
                      ? "var(--green)"
                      : doc.mean_ocr_confidence >= 0.75
                      ? "var(--amber)"
                      : "var(--red)"
                  }}>
                    · {Math.round(doc.mean_ocr_confidence * 100)}%
                  </span>
                )}
              </div>
            </div>

            <div className="doc-actions" onClick={e => e.stopPropagation()}>
              {isConfirming ? (
                <>
                  <button
                    className="doc-action-btn danger"
                    onClick={() => handleDelete(doc.source_file)}
                    aria-label="Confirm delete"
                  >
                    Del
                  </button>
                  <button
                    className="doc-action-btn"
                    onClick={() => setConfirmingDelete(null)}
                    aria-label="Cancel delete"
                  >
                    ✕
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="doc-action-btn"
                    onClick={(e) => { e.stopPropagation(); setViewingChunksId(viewingChunksId === doc.source_file ? null : doc.source_file); }}
                    aria-label={`View chunks of ${shortName}`}
                    title="View indexed chunks"
                  >
                    <ChunksIcon />
                  </button>
                  <button
                    className="doc-action-btn"
                    onClick={(e) => handleExportXlsx(doc.source_file, e)}
                    aria-label={`Export tables from ${shortName}`}
                    title="Export tables as XLSX"
                  >
                    <XlsxIcon />
                  </button>
                  <button
                    className="doc-action-btn"
                    onClick={(e) => handleDownload(doc.source_file, e)}
                    aria-label={`Download ${shortName}`}
                    title="Download original file"
                  >
                    <DownloadIcon />
                  </button>
                  <button
                    className="doc-action-btn"
                    onClick={(e) => handleReindex(doc.source_file, e)}
                    disabled={isReindexing || isDeleting}
                    aria-label={`Refresh ${shortName}`}
                    title="Re-process document"
                  >
                    {isReindexing
                      ? <span style={{ animation: "spin 0.7s linear infinite", display: "inline-block" }}>↻</span>
                      : <ReindexIcon />}
                  </button>
                  <button
                    className="doc-action-btn"
                    onClick={() => setConfirmingDelete(doc.source_file)}
                    disabled={isDeleting || isReindexing}
                    aria-label={`Delete ${shortName}`}
                    title="Remove document"
                  >
                    {isDeleting ? "…" : <TrashIcon />}
                  </button>
                </>
              )}
            </div>
          </div>

          {viewingChunksId === doc.source_file && (
            <ChunkViewer
              sourceFile={doc.source_file}
              onClose={() => setViewingChunksId(null)}
            />
          )}
          </div>
        );
      })}
    </div>
  );
}

DocumentList.propTypes = {
  documents:    PropTypes.array.isRequired,
  selectedFile: PropTypes.string,
  onSelect:     PropTypes.func,
  onDeleted:    PropTypes.func,
  workspaceId:  PropTypes.string,
};
