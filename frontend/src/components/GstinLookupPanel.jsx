/**
 * GstinLookupPanel — Feature #10 GSTIN Lookup Integration
 * Validate a GSTIN and decode state, PAN, entity type.
 * Also supports bulk extraction from pasted text.
 */
import { useState } from "react";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

async function apiGet(path, token) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function Badge({ children, color }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: 10,
      fontWeight: 600,
      background: `${color}1a`,
      color,
      letterSpacing: "0.03em",
    }}>
      {children}
    </span>
  );
}

function ResultCard({ r }) {
  if (!r.valid) {
    return (
      <div className="gstin-result-card gstin-invalid">
        <span className="gstin-monospace">{r.gstin || "—"}</span>
        <span className="gstin-error">{r.error}</span>
      </div>
    );
  }
  return (
    <div className="gstin-result-card gstin-valid">
      <div className="gstin-result-top">
        <span className="gstin-monospace">{r.gstin}</span>
        <Badge color="var(--teal, #0d9488)">Valid</Badge>
      </div>
      <div className="gstin-result-grid">
        <div className="gstin-field"><span className="gstin-field-label">State</span><span className="gstin-field-value">{r.state}</span></div>
        <div className="gstin-field"><span className="gstin-field-label">State Code</span><span className="gstin-field-value">{r.state_code}</span></div>
        <div className="gstin-field"><span className="gstin-field-label">PAN</span><span className="gstin-field-value gstin-monospace">{r.pan}</span></div>
        <div className="gstin-field"><span className="gstin-field-label">Entity Type</span><span className="gstin-field-value">{r.entity_type}</span></div>
        <div className="gstin-field"><span className="gstin-field-label">Entity No.</span><span className="gstin-field-value">{r.entity_number}</span></div>
        <div className="gstin-field"><span className="gstin-field-label">Registration</span><span className="gstin-field-value">{r.registration_type}</span></div>
      </div>
    </div>
  );
}

export function GstinLookupPanel() {
  const [mode,    setMode]    = useState("single");  // "single" | "bulk"
  const [gstin,   setGstin]   = useState("");
  const [text,    setText]    = useState("");
  const [result,  setResult]  = useState(null);
  const [bulk,    setBulk]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);
  const token = localStorage.getItem("documind_access_token") || "";

  const handleValidate = async () => {
    if (!gstin.trim()) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const r = await apiGet(`/api/v1/gstin/validate?gstin=${encodeURIComponent(gstin.trim())}`, token);
      setResult(r);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleExtract = async () => {
    if (!text.trim()) return;
    setLoading(true); setError(null); setBulk(null);
    try {
      const r = await apiGet(`/api/v1/gstin/extract?text=${encodeURIComponent(text)}`, token);
      setBulk(r);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="gstin-panel">
      <div className="gstin-mode-toggle">
        <button className={`gstin-mode-btn${mode === "single" ? " active" : ""}`} onClick={() => { setMode("single"); setResult(null); setError(null); }}>
          Validate GSTIN
        </button>
        <button className={`gstin-mode-btn${mode === "bulk" ? " active" : ""}`} onClick={() => { setMode("bulk"); setBulk(null); setError(null); }}>
          Extract from Text
        </button>
      </div>

      {mode === "single" && (
        <div className="gstin-single">
          <div className="gstin-input-row">
            <input
              className="gstin-input"
              placeholder="e.g. 27AABCA1234A1ZC"
              value={gstin}
              maxLength={15}
              onChange={e => setGstin(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && handleValidate()}
              autoComplete="off"
              spellCheck={false}
            />
            <button className="gstin-btn" onClick={handleValidate} disabled={loading || !gstin.trim()}>
              {loading ? "…" : "Validate"}
            </button>
          </div>
          {error && <div className="gstin-error-msg">{error}</div>}
          {result && <ResultCard r={result} />}
        </div>
      )}

      {mode === "bulk" && (
        <div className="gstin-bulk">
          <textarea
            className="gstin-textarea"
            placeholder="Paste any document text or invoice content — all GSTINs will be extracted and validated"
            value={text}
            onChange={e => setText(e.target.value)}
            rows={5}
          />
          <button className="gstin-btn" onClick={handleExtract} disabled={loading || !text.trim()} style={{ alignSelf: "flex-end" }}>
            {loading ? "Extracting…" : "Extract GSTINs"}
          </button>
          {error && <div className="gstin-error-msg">{error}</div>}
          {bulk !== null && (
            bulk.length === 0
              ? <div className="gstin-empty">No GSTINs found in the provided text.</div>
              : <>
                  <div className="gstin-bulk-count">{bulk.length} GSTIN{bulk.length !== 1 ? "s" : ""} found</div>
                  {bulk.map((r, i) => <ResultCard key={i} r={r} />)}
                </>
          )}
        </div>
      )}
    </div>
  );
}
