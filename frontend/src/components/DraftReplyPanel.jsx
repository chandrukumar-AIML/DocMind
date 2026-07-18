import { useState, useRef } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";

// ── Icons ─────────────────────────────────────────────────────────────────

function CopyIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
      <polyline points="7 10 12 15 17 10"/>
      <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 2L9.5 9.5 2 12l7.5 2.5L12 22l2.5-7.5L22 12l-7.5-2.5z"/>
    </svg>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────

function shortName(f) {
  return (f || "").split("/").pop().split("\\").pop();
}

function isNoticeFile(filename) {
  if (!filename) return false;
  const f = filename.toLowerCase().replace(/[_\-\s]/g, "");
  return /gst|scn|showcause|drc|notice|demand|scrutiny|assessment|itr|incometax|intimation/.test(f);
}

// ── Main component ────────────────────────────────────────────────────────

export function DraftReplyPanel({ noticeFile, documents, workspaceId }) {
  const [supportingFiles, setSupportingFiles] = useState([]);
  const [context,         setContext]         = useState("");
  const [draft,           setDraft]           = useState(null);
  const [loading,         setLoading]         = useState(false);
  const draftRef = useRef(null);

  const otherDocs = documents.filter(d => d.source_file !== noticeFile);

  const toggleSupport = (sf) => {
    setSupportingFiles(prev =>
      prev.includes(sf) ? prev.filter(x => x !== sf) : [...prev, sf]
    );
  };

  const generate = async () => {
    if (!noticeFile) return;
    setLoading(true);
    setDraft(null);
    try {
      const result = await api.draftReply(noticeFile, supportingFiles, context, workspaceId);
      setDraft(result.draft);
      setTimeout(() => draftRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
      api.logAudit("draft_reply", noticeFile,
        `${supportingFiles.length} supporting doc(s)`, workspaceId);
    } catch (err) {
      const msg = err?.response?.data?.detail || "Draft generation failed";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = () => {
    if (!draft) return;
    navigator.clipboard.writeText(draft)
      .then(() => toast.success("Copied to clipboard"))
      .catch(() => toast.error("Copy failed"));
  };

  const downloadTxt = () => {
    if (!draft) return;
    const blob = new Blob([draft], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `reply-${shortName(noticeFile).replace(/\.[^.]+$/, "")}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="draft-reply-panel">
      {/* Notice file indicator */}
      <div className="draft-notice-row">
        <span className="draft-notice-label">Notice</span>
        <span className="draft-notice-name" title={noticeFile}>{shortName(noticeFile)}</span>
      </div>

      {/* Supporting docs */}
      {otherDocs.length > 0 && (
        <div className="draft-section">
          <div className="draft-section-label">
            Supporting documents
            <span className="draft-section-hint">select docs that prove your position</span>
          </div>
          <div className="draft-support-list">
            {otherDocs.map(d => {
              const sf = d.source_file;
              const checked = supportingFiles.includes(sf);
              return (
                <label key={sf} className={`draft-support-item${checked ? " checked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSupport(sf)}
                    style={{ display: "none" }}
                  />
                  <span className="draft-support-check">{checked ? "✓" : ""}</span>
                  <span className="draft-support-name" title={sf}>{shortName(sf)}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}

      {/* Additional context */}
      <div className="draft-section">
        <div className="draft-section-label">
          Additional context
          <span className="draft-section-hint">amounts paid, dates, any specific points</span>
        </div>
        <textarea
          className="draft-context-input"
          placeholder="e.g. We paid ₹2,45,000 GST on 15-Oct-2024 under challan CIN XXXXX. ITC was correctly availed on purchases from ABC Suppliers (GSTIN 27AABCA…)."
          value={context}
          onChange={e => setContext(e.target.value)}
          rows={3}
        />
      </div>

      {/* Generate button */}
      <button
        className="draft-generate-btn"
        onClick={generate}
        disabled={loading || !noticeFile}
      >
        {loading ? (
          <>
            <span className="draft-spinner" />
            Drafting reply…
          </>
        ) : (
          <>
            <SparkIcon />
            {draft ? "Regenerate Reply" : "Generate Reply Letter"}
          </>
        )}
      </button>

      {/* Draft output */}
      {draft && (
        <div className="draft-output" ref={draftRef}>
          <div className="draft-output-toolbar">
            <span className="draft-output-label">Draft Reply</span>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="draft-toolbar-btn" onClick={copyToClipboard} title="Copy to clipboard">
                <CopyIcon /> Copy
              </button>
              <button className="draft-toolbar-btn" onClick={downloadTxt} title="Download as .txt">
                <DownloadIcon /> Download
              </button>
            </div>
          </div>
          <div className="draft-output-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft}</ReactMarkdown>
          </div>
          <div className="draft-disclaimer">
            ⚠ AI-generated draft — review all figures, dates, and legal citations before filing.
          </div>
        </div>
      )}
    </div>
  );
}

DraftReplyPanel.propTypes = {
  noticeFile:  PropTypes.string.isRequired,
  documents:   PropTypes.array.isRequired,
  workspaceId: PropTypes.string,
};

export { isNoticeFile };
