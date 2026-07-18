/**
 * RegulatoryPanel — Feature #9 Built-in Regulatory Knowledge
 * Instant keyword search across CA regulatory knowledge base.
 */
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";

const BASE_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

async function searchReg(q, token) {
  const res = await fetch(`${BASE_URL}/api/v1/regulatory/search?q=${encodeURIComponent(q)}&limit=6`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  const d = await res.json();
  return d.results || [];
}

async function listReg(token) {
  const res = await fetch(`${BASE_URL}/api/v1/regulatory/list`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return {};
  const d = await res.json();
  return d.categories || {};
}

const CAT_COLOR = {
  GST:          "var(--teal, #0d9488)",
  "Income Tax": "var(--violet, #8b5cf6)",
  TDS:          "var(--amber, #f59e0b)",
  PF:           "var(--blue, #3b82f6)",
  ROC:          "var(--pink, #ec4899)",
};

function RegCard({ item }) {
  const [open, setOpen] = useState(false);
  const color = CAT_COLOR[item.category] || "var(--text-3)";
  return (
    <div className="reg-card" onClick={() => setOpen(o => !o)}>
      <div className="reg-card-header">
        <span className="reg-cat-badge" style={{ color, background: `${color}1a` }}>{item.category}</span>
        <div className="reg-card-title">{item.title}</div>
        <div className="reg-card-section">{item.section}</div>
      </div>
      {open && <div className="reg-card-body">{item.content}</div>}
    </div>
  );
}

export function RegulatoryPanel() {
  const [query,    setQuery]   = useState("");
  const [results,  setResults] = useState(null);
  const [browse,   setBrowse]  = useState(null);
  const [loading,  setLoading] = useState(false);
  const [tab,      setTab]     = useState("search");
  const timer = useRef(null);
  const token = localStorage.getItem("documind_access_token") || "";

  // debounced search
  useEffect(() => {
    clearTimeout(timer.current);
    if (!query.trim()) { setResults(null); return; }
    setLoading(true);
    timer.current = setTimeout(async () => {
      const r = await searchReg(query, token);
      setResults(r);
      setLoading(false);
    }, 350);
  }, [query, token]);

  // browse all
  useEffect(() => {
    if (tab === "browse" && !browse) {
      listReg(token).then(setBrowse);
    }
  }, [tab, token, browse]);

  return (
    <div className="reg-panel">
      <div className="reg-tabs">
        <button className={`reg-tab${tab === "search" ? " active" : ""}`} onClick={() => setTab("search")}>Search</button>
        <button className={`reg-tab${tab === "browse" ? " active" : ""}`} onClick={() => setTab("browse")}>Browse All</button>
      </div>

      {tab === "search" && (
        <>
          <input
            className="reg-search-input"
            placeholder="e.g. ITC claim limit, Section 73, TDS default…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            autoComplete="off"
          />
          {loading && <div className="reg-loading">Searching…</div>}
          {results?.length === 0 && !loading && query && (
            <div className="reg-empty">No matching provisions found for "{query}"</div>
          )}
          {results && results.map(r => <RegCard key={r.id} item={r} />)}
          {!query && (
            <div className="reg-hint">
              Search sections, penalties, or topics — e.g. "ITC reversal", "148 notice", "DRC-03"
            </div>
          )}
        </>
      )}

      {tab === "browse" && browse && (
        Object.entries(browse).map(([cat, items]) => (
          <div key={cat}>
            <div className="reg-browse-cat" style={{ color: CAT_COLOR[cat] || "var(--text-3)" }}>{cat}</div>
            {items.map(r => <RegCard key={r.id} item={r} />)}
          </div>
        ))
      )}
    </div>
  );
}
