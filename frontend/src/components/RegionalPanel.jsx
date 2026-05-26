// frontend/src/components/RegionalPanel.jsx
import { useState } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

const VALIDATORS = [
  { type: "pan", label: "PAN Card", placeholder: "ABCDE1234F" },
  { type: "gstin", label: "GSTIN", placeholder: "27AAPFU0939F1ZV" },
  { type: "aadhaar", label: "Aadhaar", placeholder: "2345 6789 0123" },
];

export function RegionalPanel() {
  const [activeTab, setActiveTab] = useState("query");
  const [query, setQuery] = useState("");
  const [queryResult, setQueryResult] = useState(null);
  const [queryLoading, setQueryLoading] = useState(false);
  const [textForEntities, setTextForEntities] = useState("");
  const [entityResult, setEntityResult] = useState(null);
  const [valType, setValType] = useState("pan");
  const [valValue, setValValue] = useState("");
  const [valResult, setValResult] = useState(null);
  const [numText, setNumText] = useState("");
  const [numResult, setNumResult] = useState(null);

  const preprocessQuery = async () => {
    if (!query.trim()) return;
    setQueryLoading(true);
    try {
      const r = await api.preprocessQuery(query);
      setQueryResult(r);
    } catch { toast.error("Preprocessing failed"); }
    finally { setQueryLoading(false); }
  };

  const extractEntities = async () => {
    if (!textForEntities.trim()) return;
    try {
      const r = await api.extractIndianEntities(textForEntities);
      setEntityResult(r);
    } catch { toast.error("Extraction failed"); }
  };

  const validateId = async () => {
    if (!valValue.trim()) return;
    try {
      const r = await api.validateIndianId(valValue, valType);
      setValResult(r);
    } catch { toast.error("Validation failed"); }
  };

  const parseNumber = async () => {
    if (!numText.trim()) return;
    try {
      const r = await api.parseIndianNumber(numText);
      setNumResult(r);
    } catch { toast.error("Parse failed"); }
  };

  return (
    <div className="panel-root">
      <div className="panel-header">
        <span className="panel-title">Indian Language Tools</span>
        <div className="tab-bar">
          {["query", "entities", "validate", "numbers"].map(t => (
            <button key={t} className={`tab-btn${activeTab === t ? " active" : ""}`} onClick={() => setActiveTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "query" && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>
            Tanglish / multilingual query normalization
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="input"
              style={{ flex: 1 }}
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g. aadayam kanam thedi"
              onKeyDown={e => e.key === "Enter" && preprocessQuery()}
            />
            <button className="btn-primary" onClick={preprocessQuery} disabled={queryLoading}>
              {queryLoading ? "…" : "Process"}
            </button>
          </div>
          {queryResult && (
            <div className="regional-result">
              <div className="regional-row">
                <span className="regional-key">Original</span>
                <span>{queryResult.original_query}</span>
              </div>
              <div className="regional-row">
                <span className="regional-key">Normalized</span>
                <span style={{ color: "var(--accent)", fontWeight: 600 }}>{queryResult.normalized_query}</span>
              </div>
              {queryResult.detected_script && (
                <div className="regional-row">
                  <span className="regional-key">Script</span>
                  <span>{queryResult.detected_script}</span>
                </div>
              )}
              {queryResult.extracted_amounts?.length > 0 && (
                <div className="regional-row">
                  <span className="regional-key">Amounts</span>
                  <span>{queryResult.extracted_amounts.map(a => `₹${a.toLocaleString("en-IN")}`).join(", ")}</span>
                </div>
              )}
              {Object.values(queryResult.extracted_entities || {}).flat().length > 0 && (
                <div className="regional-row">
                  <span className="regional-key">Entities</span>
                  <span>{Object.entries(queryResult.extracted_entities || {})
                    .filter(([, v]) => v.length > 0)
                    .map(([k, v]) => `${k}: ${v.join(", ")}`)
                    .join(" | ")}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {activeTab === "entities" && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>
            Extract PAN, Aadhaar, GSTIN, mobile, pincode from text
          </div>
          <textarea
            className="input"
            value={textForEntities}
            onChange={e => setTextForEntities(e.target.value)}
            placeholder="Paste document text here…"
            style={{ minHeight: 80, resize: "vertical" }}
          />
          <button className="btn-primary" onClick={extractEntities} style={{ marginTop: 6 }}>
            Extract Entities
          </button>
          {entityResult && (
            <div className="regional-result" style={{ marginTop: 8 }}>
              {Object.entries(entityResult.entities || {}).map(([type, values]) => (
                values.length > 0 && (
                  <div key={type} className="regional-row">
                    <span className="regional-key">{type.toUpperCase()}</span>
                    <span style={{ fontFamily: "monospace", fontSize: 11 }}>{values.join(", ")}</span>
                  </div>
                )
              ))}
              {entityResult.detected_script && (
                <div className="regional-row">
                  <span className="regional-key">Script</span>
                  <span>{entityResult.detected_script}</span>
                </div>
              )}
              <div className="regional-row">
                <span className="regional-key">Total</span>
                <span>{entityResult.total_entities} entities found</span>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "validate" && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            {VALIDATORS.map(v => (
              <button
                key={v.type}
                className={`mode-chip${valType === v.type ? " active" : ""}`}
                onClick={() => { setValType(v.type); setValResult(null); }}
              >
                {v.label}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="input"
              style={{ flex: 1, fontFamily: "monospace" }}
              value={valValue}
              onChange={e => setValValue(e.target.value.toUpperCase())}
              placeholder={VALIDATORS.find(v => v.type === valType)?.placeholder}
              onKeyDown={e => e.key === "Enter" && validateId()}
            />
            <button className="btn-primary" onClick={validateId}>Validate</button>
          </div>
          {valResult && (
            <div style={{ marginTop: 10, padding: 12, borderRadius: 8, background: valResult.is_valid ? "var(--green)11" : "var(--red)11", border: `1px solid ${valResult.is_valid ? "var(--green)" : "var(--red)"}33` }}>
              <div style={{ fontWeight: 700, color: valResult.is_valid ? "var(--green)" : "var(--red)", fontSize: 14 }}>
                {valResult.is_valid ? "✓ Valid" : "✗ Invalid"} {valResult.type.toUpperCase()}
              </div>
              <div style={{ fontSize: 11, fontFamily: "monospace", marginTop: 4 }}>{valResult.value}</div>
            </div>
          )}
        </div>
      )}

      {activeTab === "numbers" && (
        <div style={{ padding: "8px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 6 }}>
            Parse Indian number expressions (lakhs, crores)
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="input"
              style={{ flex: 1 }}
              value={numText}
              onChange={e => setNumText(e.target.value)}
              placeholder="e.g. 5.2 crores, 18 lakhs"
              onKeyDown={e => e.key === "Enter" && parseNumber()}
            />
            <button className="btn-primary" onClick={parseNumber}>Parse</button>
          </div>
          {numResult && (
            <div className="regional-result" style={{ marginTop: 8 }}>
              <div className="regional-row">
                <span className="regional-key">Input</span>
                <span>{numResult.input}</span>
              </div>
              <div className="regional-row">
                <span className="regional-key">Value</span>
                <span style={{ fontWeight: 700, color: "var(--accent)" }}>
                  {numResult.parsed_value != null ? numResult.parsed_value.toLocaleString("en-IN") : "—"}
                </span>
              </div>
              {numResult.formatted && (
                <div className="regional-row">
                  <span className="regional-key">Formatted</span>
                  <span>{numResult.formatted}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
