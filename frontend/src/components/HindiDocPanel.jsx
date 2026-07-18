/**
 * HindiDocPanel — Feature #13 Hindi/Vernacular Document Support
 * Transliterates / translates document content to Hindi or regional language.
 * Uses the existing /api/v1/query endpoint with a translation instruction prefix.
 */
import { useState } from "react";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

const LANGUAGES = [
  { code: "hi", label: "Hindi (हिंदी)" },
  { code: "mr", label: "Marathi (मराठी)" },
  { code: "gu", label: "Gujarati (ગુજરાતી)" },
  { code: "ta", label: "Tamil (தமிழ்)" },
  { code: "te", label: "Telugu (తెలుగు)" },
  { code: "kn", label: "Kannada (ಕನ್ನಡ)" },
  { code: "bn", label: "Bengali (বাংলা)" },
  { code: "pa", label: "Punjabi (ਪੰਜਾਬੀ)" },
];

const LANG_NAMES = {
  hi: "Hindi", mr: "Marathi", gu: "Gujarati", ta: "Tamil",
  te: "Telugu", kn: "Kannada", bn: "Bengali", pa: "Punjabi",
};

const PRESETS = [
  { label: "Summarize document", key: "summary" },
  { label: "Key tax amounts", key: "amounts" },
  { label: "Important dates / deadlines", key: "dates" },
  { label: "Party names & GSTINs", key: "parties" },
];

const PRESET_PROMPTS = {
  summary: (lang) =>
    `Please summarize this document in ${LANG_NAMES[lang]}. Use simple language that a non-English-speaker can understand. Include all important facts, amounts, and deadlines.`,
  amounts: (lang) =>
    `Extract all monetary amounts, tax figures, penalties, and refunds from this document and present them as a clear list in ${LANG_NAMES[lang]}.`,
  dates: (lang) =>
    `List all dates, deadlines, due dates, and time limits mentioned in this document in ${LANG_NAMES[lang]}.`,
  parties: (lang) =>
    `Extract all party names, GSTINs, PAN numbers, and entity details from this document and present them in ${LANG_NAMES[lang]}.`,
};

export function HindiDocPanel({ selectedFile, workspaceId }) {
  const [lang,    setLang]    = useState("hi");
  const [preset,  setPreset]  = useState("summary");
  const [custom,  setCustom]  = useState("");
  const [loading, setLoading] = useState(false);
  const [result,  setResult]  = useState(null);
  const [error,   setError]   = useState(null);
  const token = localStorage.getItem("documind_access_token") || "";

  const handleTranslate = async () => {
    const question = custom.trim() || PRESET_PROMPTS[preset]?.(lang) || PRESET_PROMPTS.summary(lang);
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await fetch(`${BASE_URL}/api/v1/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          question,
          workspace_id: workspaceId,
          selected_file: selectedFile || undefined,
          ca_mode: false,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const d = await res.json();
      setResult(d.answer || d.response || "No response");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="hindi-panel">
      <div className="hindi-lang-row">
        <label className="hindi-label">Output Language</label>
        <select className="hindi-lang-select" value={lang} onChange={e => setLang(e.target.value)}>
          {LANGUAGES.map(l => (
            <option key={l.code} value={l.code}>{l.label}</option>
          ))}
        </select>
      </div>

      <div className="hindi-presets">
        {PRESETS.map(p => (
          <button
            key={p.key}
            className={`hindi-preset-btn${preset === p.key ? " active" : ""}`}
            onClick={() => { setPreset(p.key); setCustom(""); }}
          >
            {p.label}
          </button>
        ))}
      </div>

      <textarea
        className="hindi-custom-input"
        placeholder={`Or type a custom question in English — the answer will be in ${LANG_NAMES[lang]}…`}
        value={custom}
        onChange={e => setCustom(e.target.value)}
        rows={2}
      />

      {!selectedFile && (
        <div className="hindi-no-doc">Select a document from the library first to analyze it.</div>
      )}

      <button
        className="hindi-btn"
        onClick={handleTranslate}
        disabled={loading || !selectedFile}
      >
        {loading ? "Translating…" : `Get Answer in ${LANG_NAMES[lang]}`}
      </button>

      {error && <div className="hindi-error">{error}</div>}

      {result && (
        <div className="hindi-result">
          <div className="hindi-result-lang-badge">{LANGUAGES.find(l => l.code === lang)?.label}</div>
          <div className="hindi-result-text">{result}</div>
          <button
            className="hindi-copy-btn"
            onClick={() => navigator.clipboard?.writeText(result).catch(() => {})}
          >
            Copy
          </button>
        </div>
      )}
    </div>
  );
}
