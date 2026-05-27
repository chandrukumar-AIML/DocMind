// frontend/src/components/DomainPanel.jsx
import { useState, useCallback } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const DOMAIN_TYPES = [
  { id: "legal",      label: "Legal",    icon: "⚖️",  desc: "Clauses, risk & obligations" },
  { id: "medical",    label: "Medical",  icon: "🏥",  desc: "ICD-10, drugs & interactions" },
  { id: "logistics",  label: "Invoices", icon: "📦",  desc: "Invoice fields & anomalies" },
  { id: "bills",      label: "Bills",    icon: "🧾",  desc: "Merge & calculate multiple bills" },
  { id: "forms",      label: "Forms",    icon: "📋",  desc: "Extract form fields via Vision AI" },
  { id: "signature",  label: "Sign",     icon: "✍️",  desc: "Detect handwritten signatures" },
];

function RiskBadge({ level }) {
  const colors = { low: "#10B981", medium: "#F59E0B", high: "#F87171", critical: "#EF4444", unknown: "#94A3B8" };
  const color = colors[(level || "unknown").toLowerCase()] || colors.unknown;
  return (
    <span className="risk-badge" style={{ background: `${color}22`, color, borderColor: `${color}55` }}>
      {(level || "unknown").toUpperCase()}
    </span>
  );
}

function LegalResult({ data }) {
  const { analysis } = data;
  const risk = analysis?.risk || {};
  const clauses = analysis?.clauses?.items || [];
  const obligations = analysis?.obligations || [];

  return (
    <div className="domain-result">
      {risk.overall_score != null && (
        <div className="domain-result-section">
          <div className="domain-result-row">
            <span className="domain-result-label">Overall Risk</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div className="risk-score-bar">
                <div className="risk-score-fill" style={{ width: `${(risk.overall_score * 10)}%` }} />
              </div>
              <RiskBadge level={risk.risk_level} />
            </div>
          </div>
          {risk.executive_summary && (
            <p className="domain-summary">{risk.executive_summary}</p>
          )}
        </div>
      )}

      {clauses.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Clauses ({analysis.clauses.count ?? clauses.length})</div>
          {analysis.clauses.missing?.length > 0 && (
            <div className="domain-warning">
              ⚠ Missing: {analysis.clauses.missing.join(", ")}
            </div>
          )}
          <div className="domain-list">
            {clauses.slice(0, 8).map((c, i) => (
              <div key={i} className="domain-list-item">
                <div className="domain-list-title">{c.type || c.title || "Clause"}</div>
                {c.text && <div className="domain-list-sub">{c.text.slice(0, 120)}{c.text.length > 120 ? "…" : ""}</div>}
                {c.risk > 0 && <RiskBadge level={c.risk > 7 ? "critical" : c.risk > 5 ? "high" : c.risk > 3 ? "medium" : "low"} />}
              </div>
            ))}
          </div>
        </div>
      )}

      {obligations.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Obligations ({obligations.length})</div>
          <div className="domain-list">
            {obligations.slice(0, 6).map((o, i) => (
              <div key={i} className="domain-list-item">
                <div className="domain-list-title">{o.party && <span className="party-chip">{o.party}</span>} {o.obligation?.slice(0, 100)}</div>
                {o.deadline && <div className="domain-list-sub">Deadline: {o.deadline}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MedicalResult({ data }) {
  const { analysis } = data;
  const codes = analysis?.icd10_codes || [];
  const interactions = analysis?.interactions || [];
  const summary = analysis?.interaction_summary || {};

  return (
    <div className="domain-result">
      <div className="domain-result-section">
        <div className="domain-hipaa-note">🔒 PII redacted (HIPAA compliant)</div>
      </div>

      {codes.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">ICD-10 Codes ({codes.length})</div>
          <div className="domain-list">
            {codes.slice(0, 8).map((c, i) => (
              <div key={i} className="domain-list-item">
                <div className="domain-list-title">
                  <span className="icd-code">{c.code}</span> {c.description}
                  {c.is_primary && <span className="primary-chip">Primary</span>}
                </div>
                {c.confidence > 0 && (
                  <div className="domain-list-sub">Confidence: {Math.round(c.confidence * 100)}%</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {summary.total_medications > 0 && (
        <div className="domain-result-section">
          <div className="domain-result-row">
            <span className="domain-result-label">Medications</span>
            <span className="domain-count">{summary.total_medications}</span>
          </div>
          {summary.requires_attention && (
            <div className="domain-warning">⚠ Requires clinical review ({summary.high_risk} high-risk interactions)</div>
          )}
          {interactions.slice(0, 4).map((inter, i) => (
            <div key={i} className="domain-list-item">
              <div className="domain-list-title">
                {inter.drug_1} ↔ {inter.drug_2}
                <RiskBadge level={inter.severity} />
              </div>
              {inter.description && <div className="domain-list-sub">{inter.description.slice(0, 100)}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BillsResult({ data }) {
  const { summary = {}, invoices = [], errors = [] } = data;
  return (
    <div className="domain-result">
      <div className="domain-result-section">
        <div className="domain-section-title">Consolidated Summary</div>
        <div className="invoice-grid">
          <div><span>Subtotal</span>{data.currency} {summary.subtotal?.toFixed(2)}</div>
          <div><span>Tax</span>{data.currency} {summary.tax?.toFixed(2)}</div>
          <div><span>Grand Total</span><strong>{data.currency} {summary.grand_total?.toFixed(2)}</strong></div>
          <div><span>Invoices</span>{summary.invoice_count}</div>
          <div><span>Line Items</span>{summary.line_item_count}</div>
        </div>
      </div>
      {invoices.map((inv, i) => (
        <div key={i} className="domain-result-section">
          <div className="domain-section-title">
            {inv.source_file?.split("/").pop()?.split("\\").pop()}
          </div>
          <div className="invoice-grid">
            {inv.invoice_number && <div><span>Invoice #</span>{inv.invoice_number}</div>}
            {inv.vendor && <div><span>Vendor</span>{inv.vendor}</div>}
            {inv.date && <div><span>Date</span>{inv.date}</div>}
            <div><span>Total</span>{inv.currency} {inv.total?.toFixed(2)}</div>
          </div>
        </div>
      ))}
      {errors.map((e, i) => (
        <div key={i} className="domain-list-sub" style={{ color: "var(--red)" }}>
          {e.source_file}: {e.error}
        </div>
      ))}
    </div>
  );
}

function FormsResult({ data }) {
  const { fields = [], field_count } = data;
  const filled = fields.filter(f => f.value != null);
  return (
    <div className="domain-result">
      <div className="domain-result-section">
        <div className="domain-result-row">
          <span className="domain-result-label">Fields Found</span>
          <span className="domain-count">{field_count}</span>
        </div>
        <div className="domain-result-row">
          <span className="domain-result-label">Filled</span>
          <span className="domain-count">{filled.length}</span>
        </div>
      </div>
      <div className="domain-list">
        {fields.slice(0, 30).map((f, i) => (
          <div key={i} className="domain-list-item">
            <div className="domain-list-title">
              <span className="icd-code">{f.field}</span>
              {f.field_type && <span className="primary-chip">{f.field_type}</span>}
            </div>
            <div className="domain-list-sub">
              {f.value != null ? String(f.value) : <em style={{ color: "var(--text-4)" }}>blank</em>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SignatureResult({ data }) {
  const { signatures = [], signatures_detected } = data;
  return (
    <div className="domain-result">
      <div className="domain-result-section">
        <div className="domain-result-row">
          <span className="domain-result-label">Signatures Detected</span>
          <span className="domain-count" style={{ color: signatures_detected > 0 ? "var(--green)" : "var(--text-4)" }}>
            {signatures_detected}
          </span>
        </div>
      </div>
      {signatures.length === 0 ? (
        <div className="domain-desc">{data.note || "No signatures found in image blocks."}</div>
      ) : (
        <div className="domain-list">
          {signatures.map((s, i) => (
            <div key={i} className="domain-list-item">
              <div className="domain-list-title">
                Page {s.page} — <RiskBadge level={s.confidence > 0.8 ? "low" : "medium"} />
              </div>
              <div className="domain-list-sub">{s.description?.slice(0, 150)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function LogisticsResult({ data }) {
  const { results = [], total_anomalies, requires_review } = data;

  return (
    <div className="domain-result">
      {requires_review && (
        <div className="domain-warning">⚠ {total_anomalies} anomalies detected — review required</div>
      )}
      {results.map((r, i) => {
        const inv = r.invoice || {};
        const anoms = r.anomalies || [];
        return (
          <div key={i} className="domain-result-section">
            <div className="domain-section-title">
              {(r.source_file || "").split("/").pop().split("\\").pop()}
              {r.confidence > 0 && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--text-4)" }}>{Math.round(r.confidence * 100)}%</span>}
            </div>
            {r.error ? (
              <div className="domain-list-sub" style={{ color: "var(--red)" }}>{r.error}</div>
            ) : (
              <>
                <div className="invoice-grid">
                  {inv.invoice_number && <div><span>Invoice #</span>{inv.invoice_number}</div>}
                  {inv.total_amount != null && <div><span>Total</span>{inv.currency || ""} {inv.total_amount}</div>}
                  {inv.vendor_name && <div><span>Vendor</span>{inv.vendor_name}</div>}
                  {inv.invoice_date && <div><span>Date</span>{inv.invoice_date}</div>}
                </div>
                {anoms.map((a, j) => (
                  <div key={j} className="domain-list-item">
                    <RiskBadge level={a.severity} />
                    <div className="domain-list-sub">{a.description}</div>
                  </div>
                ))}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function buildReportHtml(domain, data, sourceFile) {
  const domainMeta = DOMAIN_TYPES.find(d => d.id === domain) || {};
  const now = new Date().toLocaleString();
  const fileName = (sourceFile || "").split("/").pop().split("\\").pop() || "Multiple documents";

  let bodyHtml = "";

  if (domain === "legal") {
    const { analysis = {} } = data;
    const risk = analysis.risk || {};
    const clauses = analysis.clauses?.items || [];
    const obligations = analysis.obligations || [];
    bodyHtml = `
      ${risk.overall_score != null ? `
        <section>
          <h2>Overall Risk</h2>
          <p><strong>Score:</strong> ${(risk.overall_score).toFixed(1)} / 10 &nbsp;
             <span class="badge ${(risk.risk_level || "").toLowerCase()}">${(risk.risk_level || "UNKNOWN").toUpperCase()}</span></p>
          ${risk.executive_summary ? `<p>${risk.executive_summary}</p>` : ""}
        </section>` : ""}
      ${clauses.length ? `
        <section>
          <h2>Clauses (${analysis.clauses?.count ?? clauses.length})</h2>
          ${analysis.clauses?.missing?.length ? `<p class="warn">⚠ Missing: ${analysis.clauses.missing.join(", ")}</p>` : ""}
          <table><thead><tr><th>#</th><th>Type</th><th>Text</th><th>Risk</th></tr></thead><tbody>
          ${clauses.map((c, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${c.type || c.title || "—"}</td>
              <td>${(c.text || "").slice(0, 200)}${(c.text || "").length > 200 ? "…" : ""}</td>
              <td>${c.risk != null ? c.risk : "—"}</td>
            </tr>`).join("")}
          </tbody></table>
        </section>` : ""}
      ${obligations.length ? `
        <section>
          <h2>Obligations (${obligations.length})</h2>
          <table><thead><tr><th>#</th><th>Party</th><th>Obligation</th><th>Deadline</th></tr></thead><tbody>
          ${obligations.map((o, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${o.party || "—"}</td>
              <td>${(o.obligation || "").slice(0, 200)}</td>
              <td>${o.deadline || "—"}</td>
            </tr>`).join("")}
          </tbody></table>
        </section>` : ""}`;
  } else if (domain === "medical") {
    const { analysis = {} } = data;
    const codes = analysis.icd10_codes || [];
    const interactions = analysis.interactions || [];
    const summary = analysis.interaction_summary || {};
    bodyHtml = `
      <section>
        <p class="warn">🔒 PII redacted — HIPAA compliant report</p>
        ${summary.total_medications ? `<p><strong>Medications found:</strong> ${summary.total_medications}
          ${summary.high_risk ? ` &nbsp; <span class="badge critical">${summary.high_risk} HIGH-RISK</span>` : ""}</p>` : ""}
      </section>
      ${codes.length ? `
        <section>
          <h2>ICD-10 Codes (${codes.length})</h2>
          <table><thead><tr><th>Code</th><th>Description</th><th>Primary</th><th>Confidence</th></tr></thead><tbody>
          ${codes.map(c => `
            <tr>
              <td><strong>${c.code}</strong></td>
              <td>${c.description || "—"}</td>
              <td>${c.is_primary ? "✓" : ""}</td>
              <td>${c.confidence ? Math.round(c.confidence * 100) + "%" : "—"}</td>
            </tr>`).join("")}
          </tbody></table>
        </section>` : ""}
      ${interactions.length ? `
        <section>
          <h2>Drug Interactions (${interactions.length})</h2>
          <table><thead><tr><th>Drug 1</th><th>Drug 2</th><th>Severity</th><th>Description</th></tr></thead><tbody>
          ${interactions.map(i => `
            <tr>
              <td>${i.drug_1}</td>
              <td>${i.drug_2}</td>
              <td><span class="badge ${(i.severity || "").toLowerCase()}">${(i.severity || "—").toUpperCase()}</span></td>
              <td>${(i.description || "").slice(0, 150)}</td>
            </tr>`).join("")}
          </tbody></table>
        </section>` : ""}`;
  } else if (domain === "logistics") {
    const { results = [], total_anomalies } = data;
    bodyHtml = `
      ${total_anomalies ? `<section><p class="warn">⚠ ${total_anomalies} anomalies detected</p></section>` : ""}
      ${results.map(r => {
        const inv = r.invoice || {};
        const anoms = r.anomalies || [];
        return `<section>
          <h2>${(r.source_file || "").split("/").pop().split("\\").pop()}</h2>
          <table><tbody>
            ${inv.invoice_number ? `<tr><th>Invoice #</th><td>${inv.invoice_number}</td></tr>` : ""}
            ${inv.vendor_name ? `<tr><th>Vendor</th><td>${inv.vendor_name}</td></tr>` : ""}
            ${inv.invoice_date ? `<tr><th>Date</th><td>${inv.invoice_date}</td></tr>` : ""}
            ${inv.total_amount != null ? `<tr><th>Total</th><td>${inv.currency || ""} ${inv.total_amount}</td></tr>` : ""}
          </tbody></table>
          ${anoms.length ? `<h3>Anomalies</h3><ul>${anoms.map(a => `<li><span class="badge ${(a.severity||"").toLowerCase()}">${(a.severity||"").toUpperCase()}</span> ${a.description}</li>`).join("")}</ul>` : ""}
        </section>`;
      }).join("")}`;
  } else if (domain === "bills") {
    const { summary = {}, invoices = [] } = data;
    bodyHtml = `
      <section>
        <h2>Consolidated Summary</h2>
        <table><tbody>
          <tr><th>Subtotal</th><td>${data.currency} ${(summary.subtotal || 0).toFixed(2)}</td></tr>
          <tr><th>Tax</th><td>${data.currency} ${(summary.tax || 0).toFixed(2)}</td></tr>
          <tr><th>Grand Total</th><td><strong>${data.currency} ${(summary.grand_total || 0).toFixed(2)}</strong></td></tr>
          <tr><th>Invoices</th><td>${summary.invoice_count || 0}</td></tr>
          <tr><th>Line Items</th><td>${summary.line_item_count || 0}</td></tr>
        </tbody></table>
      </section>
      ${invoices.map((inv, i) => `
        <section>
          <h2>Invoice ${i + 1}: ${inv.source_file?.split("/").pop()?.split("\\").pop() || ""}</h2>
          <table><tbody>
            ${inv.invoice_number ? `<tr><th>Invoice #</th><td>${inv.invoice_number}</td></tr>` : ""}
            ${inv.vendor ? `<tr><th>Vendor</th><td>${inv.vendor}</td></tr>` : ""}
            ${inv.date ? `<tr><th>Date</th><td>${inv.date}</td></tr>` : ""}
            <tr><th>Total</th><td>${inv.currency} ${(inv.total || 0).toFixed(2)}</td></tr>
          </tbody></table>
        </section>`).join("")}`;
  } else if (domain === "forms") {
    const { fields = [], field_count } = data;
    bodyHtml = `
      <section>
        <p><strong>Fields Found:</strong> ${field_count} &nbsp; <strong>Filled:</strong> ${fields.filter(f => f.value != null).length}</p>
        <table><thead><tr><th>Field</th><th>Type</th><th>Value</th></tr></thead><tbody>
          ${fields.map(f => `
            <tr>
              <td>${f.field}</td>
              <td>${f.field_type || "—"}</td>
              <td>${f.value != null ? String(f.value) : "<em>blank</em>"}</td>
            </tr>`).join("")}
        </tbody></table>
      </section>`;
  } else if (domain === "signature") {
    const { signatures = [], signatures_detected } = data;
    bodyHtml = `
      <section>
        <p><strong>Signatures Detected:</strong> ${signatures_detected}</p>
        ${signatures.length ? `
          <table><thead><tr><th>Page</th><th>Confidence</th><th>Description</th></tr></thead><tbody>
          ${signatures.map(s => `
            <tr>
              <td>Page ${s.page}</td>
              <td>${s.confidence ? Math.round(s.confidence * 100) + "%" : "—"}</td>
              <td>${(s.description || "").slice(0, 200)}</td>
            </tr>`).join("")}
          </tbody></table>` : `<p>${data.note || "No signatures found."}</p>`}
      </section>`;
  }

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>DocuMind Analysis Report — ${domainMeta.label}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 13px; color: #0f172a; margin: 0; padding: 24px 32px; }
  h1 { font-size: 20px; margin: 0 0 4px; color: #1e293b; }
  .meta { font-size: 12px; color: #64748b; margin-bottom: 20px; }
  h2 { font-size: 14px; font-weight: 700; margin: 20px 0 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
  h3 { font-size: 12px; font-weight: 600; margin: 12px 0 6px; color: #334155; }
  section { margin-bottom: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
  th, td { border: 1px solid #e2e8f0; padding: 5px 8px; text-align: left; vertical-align: top; }
  th { background: #f8fafc; font-weight: 600; }
  p { margin: 4px 0 8px; line-height: 1.5; }
  ul { margin: 4px 0; padding-left: 18px; }
  li { margin-bottom: 4px; }
  .warn { background: #fef3c7; border: 1px solid #fcd34d; border-radius: 4px; padding: 6px 10px; color: #92400e; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 10px; font-weight: 700; }
  .badge.low, .badge.green { background: #d1fae5; color: #065f46; }
  .badge.medium { background: #fef3c7; color: #92400e; }
  .badge.high { background: #fee2e2; color: #991b1b; }
  .badge.critical { background: #fecaca; color: #7f1d1d; }
  .badge.unknown { background: #e2e8f0; color: #475569; }
  .footer { margin-top: 32px; border-top: 1px solid #e2e8f0; padding-top: 10px; font-size: 11px; color: #94a3b8; }
  @media print { body { padding: 0; } }
</style>
</head>
<body>
  <h1>${domainMeta.icon || ""} ${domainMeta.label || domain} Analysis Report</h1>
  <div class="meta">
    <strong>Document:</strong> ${fileName} &nbsp;&nbsp;
    <strong>Generated:</strong> ${now} &nbsp;&nbsp;
    <strong>Platform:</strong> DocuMind AI
  </div>
  ${bodyHtml}
  <div class="footer">Generated by DocuMind AI &mdash; Confidential. Do not distribute without authorization.</div>
</body>
</html>`;
}

export function DomainPanel({ selectedFile, documents, workspaceId }) {
  const [activeDomain, setActiveDomain] = useState("legal");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [resultDomain, setResultDomain] = useState(null);
  const [error, setError] = useState(null);
  // Bills: multi-select
  const [billFiles, setBillFiles] = useState([]);
  const [billCurrency, setBillCurrency] = useState("INR");

  const run = useCallback(async () => {
    const needsDoc = !["bills"].includes(activeDomain);
    if (needsDoc && !selectedFile) return;
    if (activeDomain === "bills" && billFiles.length < 2) {
      toast.error("Select at least 2 documents for bill calculation");
      return;
    }
    if (loading) return;
    setLoading(true);
    setResult(null);
    setError(null);
    const toastId = toast.loading(`Running ${activeDomain} analysis…`);
    try {
      let data;
      if (activeDomain === "legal") data = await api.analyzeLegal(selectedFile);
      else if (activeDomain === "medical") data = await api.analyzeMedical(selectedFile);
      else if (activeDomain === "logistics") data = await api.analyzeLogistics([selectedFile]);
      else if (activeDomain === "bills") data = await api.calculateBills(billFiles, billCurrency, workspaceId);
      else if (activeDomain === "forms") data = await api.extractFormFields(selectedFile, workspaceId);
      else if (activeDomain === "signature") data = await api.detectSignatures(selectedFile, workspaceId);
      setResult(data);
      setResultDomain(activeDomain);
      toast.success("Analysis complete", { id: toastId });
    } catch (err) {
      const status = err.response?.status;
      const msg = err.response?.data?.detail || err.message || "Analysis failed";
      if (status === 501) {
        setError("Domain module not installed on this server.");
      } else if (status === 404) {
        setError("Document not found in vector store. Re-index the document first.");
      } else {
        setError(msg);
      }
      toast.error(msg, { id: toastId });
    } finally {
      setLoading(false);
    }
  }, [selectedFile, activeDomain, loading, billFiles, billCurrency, workspaceId]);

  const exportReport = useCallback(() => {
    if (!result || !resultDomain) return;
    const sf = resultDomain === "bills" ? null : selectedFile;
    const html = buildReportHtml(resultDomain, result, sf);
    const win = window.open("", "_blank");
    if (!win) { toast.error("Pop-up blocked — allow pop-ups and retry"); return; }
    win.document.open();
    win.document.write(html);
    win.document.close();
    win.focus();
    setTimeout(() => { try { win.print(); } catch {} }, 400);
  }, [result, resultDomain, selectedFile]);

  const noDoc = !selectedFile;
  const shortName = selectedFile ? selectedFile.split("/").pop().split("\\").pop() : null;

  const toggleBillFile = (sf) => {
    setBillFiles(prev =>
      prev.includes(sf) ? prev.filter(f => f !== sf) : [...prev, sf]
    );
  };

  return (
    <div className="domain-panel">
      {/* Domain type selector */}
      <div className="domain-tabs" style={{ flexWrap: "wrap" }}>
        {DOMAIN_TYPES.map(d => (
          <button
            key={d.id}
            className={`domain-tab${activeDomain === d.id ? " active" : ""}`}
            onClick={() => { setActiveDomain(d.id); setResult(null); setError(null); }}
            title={d.desc}
          >
            <span>{d.icon}</span>
            <span>{d.label}</span>
          </button>
        ))}
      </div>

      {/* Bills tab: multi-select */}
      {activeDomain === "bills" ? (
        <div className="domain-bills-selector">
          <div className="domain-result-label" style={{ marginBottom: 6 }}>
            Select invoices to merge (min 2):
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, maxHeight: 160, overflowY: "auto" }}>
            {(documents || []).map(doc => {
              const sf = doc.source_file;
              const name = sf.split("/").pop().split("\\").pop();
              const checked = billFiles.includes(sf);
              return (
                <label key={sf} className="bill-file-check" style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, color: checked ? "var(--text-1)" : "var(--text-3)" }}>
                  <input type="checkbox" checked={checked} onChange={() => toggleBillFile(sf)} style={{ accentColor: "var(--accent)" }} />
                  {name}
                </label>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8 }}>
            <span style={{ fontSize: 11, color: "var(--text-4)" }}>Currency:</span>
            <select
              value={billCurrency}
              onChange={e => setBillCurrency(e.target.value)}
              style={{ fontSize: 11, background: "var(--bg-3)", color: "var(--text-1)", border: "1px solid var(--border)", borderRadius: 4, padding: "2px 6px" }}
            >
              {["INR", "USD", "EUR", "GBP", "AED", "SGD"].map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <button
              className="domain-run-btn"
              onClick={run}
              disabled={loading || billFiles.length < 2}
            >
              {loading ? "…" : "Calculate →"}
            </button>
          </div>
        </div>
      ) : (
        /* Selected doc badge */
        <div className="domain-target">
          {noDoc ? (
            <span className="domain-no-doc">Select a document from Library to analyze</span>
          ) : (
            <>
              <span className="domain-doc-name" title={selectedFile}>{shortName}</span>
              <button
                className="domain-run-btn"
                onClick={run}
                disabled={loading}
                aria-label={`Run ${activeDomain} analysis`}
              >
                {loading ? <span style={{ animation: "spin 0.8s linear infinite", display: "inline-block" }}>↻</span> : "Analyze →"}
              </button>
            </>
          )}
        </div>
      )}

      {/* Description */}
      {!result && !error && (
        <div className="domain-desc">
          {DOMAIN_TYPES.find(d => d.id === activeDomain)?.desc}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="domain-error">
          <span>⚠</span> {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 0 0" }}>
          <button
            className="doc-action-btn"
            onClick={exportReport}
            title="Download analysis report (print/PDF)"
            style={{
              fontSize: 11,
              padding: "3px 10px",
              display: "flex",
              alignItems: "center",
              gap: 4,
              color: "var(--accent)",
              borderColor: "var(--accent)",
            }}
          >
            📥 Download Report
          </button>
        </div>
      )}
      {result && resultDomain === "legal" && <LegalResult data={result} />}
      {result && resultDomain === "medical" && <MedicalResult data={result} />}
      {result && resultDomain === "logistics" && <LogisticsResult data={result} />}
      {result && resultDomain === "bills" && <BillsResult data={result} />}
      {result && resultDomain === "forms" && <FormsResult data={result} />}
      {result && resultDomain === "signature" && <SignatureResult data={result} />}
    </div>
  );
}

DomainPanel.propTypes = {
  selectedFile: PropTypes.string,
  documents: PropTypes.array,
  workspaceId: PropTypes.string,
};
