// frontend/src/components/DomainPanel.jsx
import { useState, useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

/**
 * Detect the best analysis domain from a filename.
 * Returns a domain id string or null if uncertain.
 */
export function detectDomain(filename) {
  if (!filename) return null;
  const f = filename.toLowerCase().replace(/[_\-]/g, " ");
  if (/gst|gstin|tax notice|scn|drc|gstr/.test(f))                           return "gst";
  if (/itr|income tax|balance sheet|profit|p l|financial|tds|form 16|26as/.test(f)) return "itr";
  if (/legal|contract|agreement|nda|mou|deed|lease|license|terms|policy/.test(f))   return "legal";
  if (/medical|icd|prescription|diagnosis|patient|clinical|drug/.test(f))           return "medical";
  if (/invoice|logistics|supply|delivery|shipment|purchase order/.test(f))           return "logistics";
  if (/form|application|kyc|registration/.test(f))                                  return "forms";
  if (/sign|signature/.test(f))                                                      return "signature";
  return null;
}

const DOMAIN_TYPES = [
  { id: "gst",        label: "GST",      icon: "🧾",  desc: "GSTIN, CGST/SGST/IGST splits, ITC eligibility, anomalies", group: "ca" },
  { id: "itr",        label: "ITR/FS",   icon: "📊",  desc: "ITR, Balance Sheet, P&L, Form 16/26AS, TDS analysis", group: "ca" },
  { id: "legal",      label: "Legal",    icon: "⚖️",  desc: "Clauses, risk & obligations", group: "other" },
  { id: "medical",    label: "Medical",  icon: "🏥",  desc: "ICD-10, drugs & interactions", group: "other" },
  { id: "logistics",  label: "Invoices", icon: "📦",  desc: "Invoice fields & anomalies", group: "other" },
  { id: "bills",      label: "Bills",    icon: "💰",  desc: "Merge & calculate multiple bills (INR)", group: "ca" },
  { id: "forms",      label: "Forms",    icon: "📋",  desc: "Extract form fields via Vision AI", group: "other" },
  { id: "signature",  label: "Sign",     icon: "✍️",  desc: "Detect handwritten signatures", group: "other" },
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

function ComplianceBadge({ status }) {
  const map = {
    compliant: { color: "#10B981", label: "COMPLIANT" },
    has_issues: { color: "#F59E0B", label: "HAS ISSUES" },
    critical_issues: { color: "#EF4444", label: "CRITICAL" },
  };
  const s = map[status] || { color: "#94A3B8", label: (status || "UNKNOWN").toUpperCase() };
  return (
    <span className="risk-badge" style={{ background: `${s.color}22`, color: s.color, borderColor: `${s.color}55` }}>
      {s.label}
    </span>
  );
}

function MoneyRow({ label, value, bold, highlight }) {
  if (value == null || value === 0) return null;
  return (
    <div className="domain-result-row" style={highlight ? { background: "var(--bg-3)", borderRadius: 4, padding: "2px 6px" } : {}}>
      <span className="domain-result-label">{label}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: bold ? 700 : 400, color: value < 0 ? "var(--red, #EF4444)" : "inherit" }}>
        ₹{Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        {value < 0 ? " (CR)" : ""}
      </span>
    </div>
  );
}

function GSTResult({ data }) {
  const anomalies = data.anomalies || [];
  const critical = anomalies.filter(a => a.severity === "high");
  const lineItems = data.line_items || [];
  const totals = data.totals || {};
  const supplier = data.supplier || {};
  const buyer = data.buyer || {};
  const notice = data.gst_notice_details || {};

  return (
    <div className="domain-result">
      {/* Action Required banner — reply due date for GST notices */}
      {notice.reply_due_date && (
        <div className="gst-deadline-banner">
          <div className="gst-deadline-title">⚠ ACTION REQUIRED</div>
          <div className="gst-deadline-body">
            Reply due by <strong>{notice.reply_due_date}</strong>
            {notice.notice_type && <> &nbsp;·&nbsp; {notice.notice_type}</>}
            {notice.total_demand > 0 && (
              <> &nbsp;·&nbsp; Demand: ₹{notice.total_demand.toLocaleString("en-IN", { minimumFractionDigits: 2 })}</>
            )}
          </div>
        </div>
      )}

      {/* Header */}
      <div className="domain-result-section">
        <div className="domain-result-row">
          <span className="domain-result-label">Document Type</span>
          <span style={{ textTransform: "capitalize", fontSize: 12 }}>{(data.document_type || "unknown").replace(/_/g, " ")}</span>
        </div>
        <div className="domain-result-row">
          <span className="domain-result-label">Compliance</span>
          <ComplianceBadge status={data.compliance_status} />
        </div>
        {data.summary && <p className="domain-summary">{data.summary}</p>}
      </div>

      {/* Anomalies — show first if critical */}
      {anomalies.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">
            {critical.length > 0 ? `⚠ ${critical.length} Critical Issue${critical.length > 1 ? "s" : ""}` : "Issues Found"} ({anomalies.length})
          </div>
          {anomalies.map((a, i) => (
            <div key={i} className="domain-list-item">
              <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                <RiskBadge level={a.severity === "high" ? "critical" : a.severity} />
                <div>
                  <div className="domain-list-title" style={{ fontSize: 11 }}>{a.type?.replace(/_/g, " ").toUpperCase()}</div>
                  <div className="domain-list-sub">{a.description}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Supplier / Buyer */}
      {(supplier.name || buyer.name) && (
        <div className="domain-result-section">
          <div className="domain-section-title">Parties</div>
          <div className="invoice-grid">
            {supplier.name && <div><span>Supplier</span>{supplier.name}</div>}
            {supplier.gstin && <div><span>Supplier GSTIN</span><code style={{ fontSize: 11 }}>{supplier.gstin}</code></div>}
            {buyer.name && <div><span>Buyer</span>{buyer.name}</div>}
            {buyer.gstin && <div><span>Buyer GSTIN</span><code style={{ fontSize: 11 }}>{buyer.gstin}</code></div>}
            {data.invoice_details?.invoice_number && <div><span>Invoice #</span>{data.invoice_details.invoice_number}</div>}
            {data.invoice_details?.invoice_date && <div><span>Date</span>{data.invoice_details.invoice_date}</div>}
            {data.invoice_details?.supply_type && <div><span>Supply Type</span>{data.invoice_details.supply_type.replace(/_/g, " ")}</div>}
          </div>
        </div>
      )}

      {/* Tax Totals */}
      {totals.grand_total > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">GST Summary</div>
          <MoneyRow label="Taxable Value" value={totals.taxable_value} />
          <MoneyRow label="CGST" value={totals.cgst} />
          <MoneyRow label="SGST" value={totals.sgst} />
          <MoneyRow label="IGST" value={totals.igst} />
          {totals.cess > 0 && <MoneyRow label="Cess" value={totals.cess} />}
          <MoneyRow label="Grand Total" value={totals.grand_total} bold highlight />
          {totals.total_itc_eligible > 0 && <MoneyRow label="ITC Eligible" value={totals.total_itc_eligible} />}
        </div>
      )}

      {/* GST Notice details */}
      {notice.total_demand > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title" style={{ color: "var(--red, #EF4444)" }}>
            GST Demand Notice — {notice.notice_type}
          </div>
          <MoneyRow label="Tax Demand" value={notice.demand_amount} />
          <MoneyRow label="Interest" value={notice.interest} />
          <MoneyRow label="Penalty" value={notice.penalty} />
          <MoneyRow label="Total Demand" value={notice.total_demand} bold highlight />
          {notice.reply_due_date && (
            <div className="domain-result-row">
              <span className="domain-result-label">Reply Due</span>
              <span style={{ color: "var(--red, #EF4444)", fontWeight: 600 }}>{notice.reply_due_date}</span>
            </div>
          )}
          {notice.period_from && (
            <div className="domain-result-row">
              <span className="domain-result-label">Period</span>
              <span>{notice.period_from} – {notice.period_to}</span>
            </div>
          )}
          {(notice.grounds || []).length > 0 && (
            <div className="domain-list">
              {notice.grounds.map((g, i) => (
                <div key={i} className="domain-list-item">
                  <div className="domain-list-sub">• {g}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Line items summary */}
      {lineItems.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Line Items ({lineItems.length})</div>
          <div className="domain-list">
            {lineItems.slice(0, 6).map((item, i) => (
              <div key={i} className="domain-list-item">
                <div className="domain-list-title" style={{ fontSize: 11 }}>
                  {item.description?.slice(0, 60)}
                  {item.hsn_sac && <span className="primary-chip" style={{ marginLeft: 4 }}>HSN {item.hsn_sac}</span>}
                  {item.gst_rate > 0 && <span className="icd-code" style={{ marginLeft: 4 }}>{item.gst_rate}%</span>}
                </div>
                <div className="domain-list-sub">
                  Taxable: ₹{(item.taxable_value || 0).toLocaleString("en-IN")} &nbsp;|&nbsp;
                  GST: ₹{((item.cgst || 0) + (item.sgst || 0) + (item.igst || 0)).toLocaleString("en-IN")}
                  {item.itc_eligible === false && <span style={{ color: "var(--red, #EF4444)", marginLeft: 6 }}>ITC blocked</span>}
                </div>
              </div>
            ))}
            {lineItems.length > 6 && <div className="domain-list-sub">+{lineItems.length - 6} more items</div>}
          </div>
        </div>
      )}

      {/* Raw GSTINs found */}
      {(data.raw_gstins || []).length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">GSTINs Detected</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {data.raw_gstins.map((g, i) => (
              <code key={i} style={{ fontSize: 11, background: "var(--bg-3)", padding: "2px 6px", borderRadius: 4 }}>{g}</code>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ITRResult({ data }) {
  const income = data.income_summary || {};
  const tax = data.tax_computation || {};
  const bs = data.balance_sheet || {};
  const pl = data.profit_loss || {};
  const ratios = data.financial_ratios || {};
  const redFlags = data.red_flags || [];
  const obs = data.key_observations || [];

  return (
    <div className="domain-result">
      {/* Header */}
      <div className="domain-result-section">
        <div className="domain-result-row">
          <span className="domain-result-label">Document</span>
          <span style={{ textTransform: "capitalize", fontSize: 12 }}>{(data.document_type || "unknown").replace(/_/g, " ")}</span>
        </div>
        {data.taxpayer_name && (
          <div className="domain-result-row">
            <span className="domain-result-label">Taxpayer</span>
            <span style={{ fontWeight: 600 }}>{data.taxpayer_name}</span>
          </div>
        )}
        {data.pan && (
          <div className="domain-result-row">
            <span className="domain-result-label">PAN</span>
            <code style={{ fontSize: 11 }}>{data.pan}</code>
          </div>
        )}
        {data.assessment_year && (
          <div className="domain-result-row">
            <span className="domain-result-label">Assessment Year</span>
            <span>{data.assessment_year}</span>
          </div>
        )}
        {data.summary && <p className="domain-summary">{data.summary}</p>}
      </div>

      {/* Red Flags */}
      {redFlags.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">⚠ Red Flags ({redFlags.length})</div>
          {redFlags.map((f, i) => (
            <div key={i} className="domain-list-item">
              <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                <RiskBadge level={f.severity === "high" ? "critical" : f.severity} />
                <div className="domain-list-sub">{f.description}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Income Summary */}
      {income.gross_total_income > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Income Summary</div>
          <MoneyRow label="Salary Income" value={income.salary_income} />
          <MoneyRow label="Business Income" value={income.business_income} />
          <MoneyRow label="Capital Gains (STCG)" value={income.capital_gains_short} />
          <MoneyRow label="Capital Gains (LTCG)" value={income.capital_gains_long} />
          <MoneyRow label="Other Income" value={income.other_income} />
          <MoneyRow label="Gross Total Income" value={income.gross_total_income} bold />
          <MoneyRow label="Deductions (80C etc)" value={income.deductions_80c + (income.deductions_other || 0)} />
          <MoneyRow label="Net Taxable Income" value={income.net_taxable_income} bold highlight />
        </div>
      )}

      {/* Tax Computation */}
      {tax.total_tax_liability > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Tax Computation</div>
          <MoneyRow label="Tax on Income" value={tax.tax_on_income} />
          <MoneyRow label="Surcharge" value={tax.surcharge} />
          <MoneyRow label="Health & Ed. Cess" value={tax.health_education_cess} />
          <MoneyRow label="Total Tax Liability" value={tax.total_tax_liability} bold />
          <MoneyRow label="TDS Deducted" value={tax.tds_deducted} />
          <MoneyRow label="Advance Tax Paid" value={tax.advance_tax_paid} />
          {tax.tax_refund > 0
            ? <MoneyRow label="Refund Due" value={tax.tax_refund} bold highlight />
            : <MoneyRow label="Tax Payable" value={tax.tax_payable} bold highlight />
          }
        </div>
      )}

      {/* P&L */}
      {pl.total_revenue > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">Profit & Loss</div>
          <MoneyRow label="Total Revenue" value={pl.total_revenue} />
          <MoneyRow label="COGS" value={pl.cost_of_goods_sold} />
          <MoneyRow label="Gross Profit" value={pl.gross_profit} bold />
          <MoneyRow label="EBITDA" value={pl.ebitda} />
          <MoneyRow label="PBT" value={pl.profit_before_tax} />
          <MoneyRow label="Net Profit" value={pl.net_profit} bold highlight />
        </div>
      )}

      {/* Financial Ratios */}
      {(ratios.net_profit_margin > 0 || ratios.current_ratio > 0) && (
        <div className="domain-result-section">
          <div className="domain-section-title">Key Ratios</div>
          {ratios.gross_profit_margin > 0 && (
            <div className="domain-result-row">
              <span className="domain-result-label">Gross Margin</span>
              <span>{ratios.gross_profit_margin.toFixed(1)}%</span>
            </div>
          )}
          {ratios.net_profit_margin > 0 && (
            <div className="domain-result-row">
              <span className="domain-result-label">Net Margin</span>
              <span>{ratios.net_profit_margin.toFixed(1)}%</span>
            </div>
          )}
          {ratios.current_ratio > 0 && (
            <div className="domain-result-row">
              <span className="domain-result-label">Current Ratio</span>
              <span style={{ color: ratios.current_ratio < 1 ? "var(--red, #EF4444)" : "inherit" }}>
                {ratios.current_ratio.toFixed(2)}
              </span>
            </div>
          )}
          {ratios.debt_equity_ratio > 0 && (
            <div className="domain-result-row">
              <span className="domain-result-label">D/E Ratio</span>
              <span>{ratios.debt_equity_ratio.toFixed(2)}</span>
            </div>
          )}
        </div>
      )}

      {/* Key Observations */}
      {obs.length > 0 && (
        <div className="domain-result-section">
          <div className="domain-section-title">CA Notes</div>
          {obs.map((o, i) => (
            <div key={i} className="domain-list-item">
              <div className="domain-list-sub">• {o}</div>
            </div>
          ))}
        </div>
      )}
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

export function DomainPanel({ selectedFile, documents, workspaceId, autoRun = false, compact = false }) {
  const detected = detectDomain(selectedFile);
  const [activeDomain, setActiveDomain] = useState(detected || "gst");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [resultDomain, setResultDomain] = useState(null);
  const [error, setError] = useState(null);
  // Bills: multi-select
  const [billFiles, setBillFiles] = useState([]);
  const [billCurrency, setBillCurrency] = useState("INR");

  // autoRun: detect domain from filename and run analysis automatically when file changes
  const lastAutoRunFile = useRef(null);
  const runRef = useRef(null);

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
      if (activeDomain === "gst") data = await api.analyzeGST(selectedFile);
      else if (activeDomain === "itr") data = await api.analyzeITR(selectedFile);
      else if (activeDomain === "legal") data = await api.analyzeLegal(selectedFile);
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
        setError("This document hasn't been processed yet. Please re-process it and try again.");
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
    setTimeout(() => { try { win.print(); } catch { /* print dialog blocked — user can print manually */ } }, 400);
  }, [result, resultDomain, selectedFile]);

  // Keep runRef current so the auto-run effect always calls the latest run
  runRef.current = run;

  useEffect(() => {
    if (!autoRun || !selectedFile || selectedFile === lastAutoRunFile.current) return;
    const domain = detectDomain(selectedFile);
    if (!domain) return;
    lastAutoRunFile.current = selectedFile;
    setActiveDomain(domain);
    setResult(null);
    setError(null);
    // Delay to let setActiveDomain re-render first so runRef.current has correct domain
    const t = setTimeout(() => runRef.current?.(), 150);
    return () => clearTimeout(t);
  }, [selectedFile, autoRun]); // eslint-disable-line react-hooks/exhaustive-deps

  const noDoc = !selectedFile;
  const shortName = selectedFile ? selectedFile.split("/").pop().split("\\").pop() : null;

  const toggleBillFile = (sf) => {
    setBillFiles(prev =>
      prev.includes(sf) ? prev.filter(f => f !== sf) : [...prev, sf]
    );
  };

  return (
    <div className={`domain-panel${compact ? " domain-panel--compact" : ""}`}>
      {/* Domain type selector — hidden in compact mode (domain is auto-detected) */}
      {!compact && (
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
      )}

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

      {/* Description — hidden in compact mode */}
      {!compact && !result && !error && (
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
      {result && resultDomain === "gst" && <GSTResult data={result} />}
      {result && resultDomain === "itr" && <ITRResult data={result} />}
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
  documents:    PropTypes.array,
  workspaceId:  PropTypes.string,
  autoRun:      PropTypes.bool,
  compact:      PropTypes.bool,
};
