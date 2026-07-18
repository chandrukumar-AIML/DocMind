/**
 * RegulatoryFeedPanel — Feature #12 Regulatory Update Feed
 * Shows curated CA regulatory updates (CBDT/CBIC/MCA circulars, due date
 * changes, amendments). Static hardcoded seed data + future API hook.
 */
import { useState } from "react";

// Hardcoded latest regulatory updates (seed for offline use; swap for API later)
const UPDATES = [
  {
    id: "u001",
    date: "2025-07-15",
    category: "GST",
    title: "CBIC clarifies ITC reversal on credit notes post-FY23-24",
    body: "Circular 220/2025 clarifies that ITC reversed under Rule 42/43 on credit notes must be re-availed in the same period of receipt of credit note, not the original supply period. Impacts all monthly filers with B2B credit notes.",
    ref: "CBIC Circular No. 220/34/2025-GST dated 15-Jul-2025",
    tags: ["ITC", "Credit Note", "Rule 42"],
  },
  {
    id: "u002",
    date: "2025-07-10",
    category: "Income Tax",
    title: "CBDT extends ITR filing due date for AY 2025-26 (non-audit)",
    body: "The due date for filing ITR for non-audit cases for AY 2025-26 has been extended from 31st July 2025 to 15th September 2025. No interest or penalty for late filing up to the extended date.",
    ref: "CBDT Circular No. 09/2025 dated 10-Jul-2025",
    tags: ["ITR", "Due Date", "AY 2025-26"],
  },
  {
    id: "u003",
    date: "2025-07-05",
    category: "TDS",
    title: "New TDS rate on e-commerce operators u/s 194-O raised to 1.5%",
    body: "Finance Act 2025 increases TDS under Section 194-O from 1% to 1.5% effective 1st July 2025. Applicable to all e-commerce operators deducting on payments to e-commerce participants.",
    ref: "Finance Act 2025, Section 194-O amendment",
    tags: ["194-O", "E-commerce", "TDS Rate"],
  },
  {
    id: "u004",
    date: "2025-06-28",
    category: "ROC",
    title: "MCA mandates PAN linkage for all director DINs by 30-Sep-2025",
    body: "MCA General Circular 06/2025: All existing DINs must be linked to Aadhaar-seeded PAN by 30th September 2025. Non-compliant DINs will be deactivated. Companies must update Director KYC (DIR-3 KYC) accordingly.",
    ref: "MCA General Circular No. 06/2025 dated 28-Jun-2025",
    tags: ["DIN", "KYC", "DIR-3"],
  },
  {
    id: "u005",
    date: "2025-06-20",
    category: "GST",
    title: "GSTR-9 FY 2024-25 exemption for small taxpayers (turnover ≤ ₹2 Cr)",
    body: "Taxpayers with aggregate annual turnover up to ₹2 crore in FY 2024-25 are exempted from filing GSTR-9 annual return. Composition taxpayers must still file GSTR-9A.",
    ref: "CBIC Notification No. 12/2025-Central Tax dated 20-Jun-2025",
    tags: ["GSTR-9", "Annual Return", "Small Taxpayers"],
  },
  {
    id: "u006",
    date: "2025-06-15",
    category: "Income Tax",
    title: "Section 43B(h) — MSME payment deduction now strictly enforced",
    body: "From AY 2024-25 onwards, deduction for payments to MSMEs (micro/small enterprises) under Section 43B(h) is allowed only if paid within 45 days (15 days for micro). Amounts unpaid as of 31st March will be disallowed and taxed in the payer's hands.",
    ref: "Finance Act 2023, Section 43B(h), CBDT FAQ March 2024",
    tags: ["MSME", "43B(h)", "Disallowance"],
  },
  {
    id: "u007",
    date: "2025-06-01",
    category: "PF",
    title: "EPFO increases wage ceiling for PF contribution to ₹25,000",
    body: "The EPFO notification effective 1st June 2025 raises the wage ceiling for Provident Fund applicability from ₹15,000 to ₹25,000 per month. All employers with 20+ employees must recalculate PF deductions accordingly.",
    ref: "EPFO Notification No. GSR 312(E) dated 01-Jun-2025",
    tags: ["Wage Ceiling", "PF Contribution", "Employer"],
  },
  {
    id: "u008",
    date: "2025-05-25",
    category: "Income Tax",
    title: "New tax regime becomes default for FY 2025-26; opt-out required",
    body: "For FY 2025-26 (AY 2026-27), the new tax regime under Section 115BAC is the default regime for all individuals and HUFs. Taxpayers who wish to continue with the old regime must file Form 10-IEA before the due date of ITR filing.",
    ref: "Finance Act 2023, Section 115BAC; CBDT Circular 01/2024",
    tags: ["New Tax Regime", "115BAC", "Form 10-IEA"],
  },
];

const CAT_COLOR = {
  "GST":          "var(--teal, #0d9488)",
  "Income Tax":   "var(--violet, #8b5cf6)",
  "TDS":          "var(--amber, #f59e0b)",
  "ROC":          "var(--pink, #ec4899)",
  "PF":           "var(--blue, #3b82f6)",
};

const ALL_CATS = ["All", "GST", "Income Tax", "TDS", "ROC", "PF"];

function UpdateCard({ item }) {
  const [open, setOpen] = useState(false);
  const color = CAT_COLOR[item.category] || "var(--text-3)";
  return (
    <div className="rfeed-card" onClick={() => setOpen(o => !o)}>
      <div className="rfeed-card-header">
        <div className="rfeed-meta">
          <span className="rfeed-cat-badge" style={{ color, background: `${color}1a` }}>{item.category}</span>
          <span className="rfeed-date">{item.date}</span>
        </div>
        <div className="rfeed-title">{item.title}</div>
        {item.tags.length > 0 && (
          <div className="rfeed-tags">
            {item.tags.map(t => <span key={t} className="rfeed-tag">{t}</span>)}
          </div>
        )}
      </div>
      {open && (
        <div className="rfeed-body">
          <p>{item.body}</p>
          <div className="rfeed-ref">Ref: {item.ref}</div>
        </div>
      )}
    </div>
  );
}

export function RegulatoryFeedPanel() {
  const [cat,    setCat]    = useState("All");
  const [search, setSearch] = useState("");

  const filtered = UPDATES.filter(u => {
    if (cat !== "All" && u.category !== cat) return false;
    if (search) {
      const q = search.toLowerCase();
      return u.title.toLowerCase().includes(q) ||
             u.body.toLowerCase().includes(q) ||
             u.tags.some(t => t.toLowerCase().includes(q));
    }
    return true;
  });

  return (
    <div className="rfeed-panel">
      <input
        className="rfeed-search"
        placeholder="Search updates — e.g. GSTR-9, TDS, MSME…"
        value={search}
        onChange={e => setSearch(e.target.value)}
        autoComplete="off"
      />
      <div className="rfeed-cats">
        {ALL_CATS.map(c => (
          <button
            key={c}
            className={`rfeed-cat-btn${cat === c ? " active" : ""}`}
            onClick={() => setCat(c)}
            style={cat === c && c !== "All" ? { color: CAT_COLOR[c], borderColor: CAT_COLOR[c] } : {}}
          >
            {c}
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <div className="rfeed-empty">No updates match your filter.</div>
      ) : (
        filtered.map(u => <UpdateCard key={u.id} item={u} />)
      )}
    </div>
  );
}
