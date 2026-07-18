/**
 * DeadlineDashboard — CA Deadline Dashboard (Feature #4)
 *
 * Shows upcoming GST / Income-Tax / ROC statutory deadlines with
 * live countdown and urgency colouring. No backend required —
 * deadlines are computed client-side from the current date.
 */
import { useMemo, useState } from "react";

// ── Deadline definitions ──────────────────────────────────────────────────────
// Each entry: { id, label, category, description, dayOfMonth, month (0-based, null = monthly) }
// month: null → repeats every month on dayOfMonth
// month: 0-11 → occurs once a year

const MONTHLY_DEADLINES = [
  { id: "gstr1-monthly",  label: "GSTR-1",        category: "GST",  description: "Monthly outward supplies (turnover > ₹5 Cr)",  day: 11 },
  { id: "gstr3b",         label: "GSTR-3B",        category: "GST",  description: "Monthly summary return & tax payment",          day: 20 },
  { id: "gstr7",          label: "GSTR-7",         category: "GST",  description: "TDS deductors monthly return",                  day: 10 },
  { id: "gstr8",          label: "GSTR-8",         category: "GST",  description: "TCS collectors monthly return",                 day: 10 },
  { id: "tds-payment",    label: "TDS Payment",    category: "TDS",  description: "TDS / TCS deposit for previous month",          day: 7  },
  { id: "pf-payment",     label: "PF Payment",     category: "PF",   description: "Employee PF contribution deposit",              day: 15 },
  { id: "esi-payment",    label: "ESI Payment",    category: "ESI",  description: "Employee State Insurance contribution",         day: 15 },
];

// Fixed annual deadlines (month is 0-indexed)
const ANNUAL_DEADLINES = [
  { id: "gstr1-q1",   label: "GSTR-1 Q1",      category: "GST",    description: "Quarterly GSTR-1 (QRMP — Apr-Jun)",     month: 6,  day: 13 },
  { id: "gstr1-q2",   label: "GSTR-1 Q2",      category: "GST",    description: "Quarterly GSTR-1 (QRMP — Jul-Sep)",     month: 9,  day: 13 },
  { id: "gstr1-q3",   label: "GSTR-1 Q3",      category: "GST",    description: "Quarterly GSTR-1 (QRMP — Oct-Dec)",     month: 0,  day: 13 },
  { id: "gstr1-q4",   label: "GSTR-1 Q4",      category: "GST",    description: "Quarterly GSTR-1 (QRMP — Jan-Mar)",     month: 3,  day: 13 },
  { id: "gstr9",      label: "GSTR-9",          category: "GST",    description: "Annual GST return",                     month: 11, day: 31 },
  { id: "gstr9c",     label: "GSTR-9C",         category: "GST",    description: "GST reconciliation statement",          month: 11, day: 31 },
  { id: "itr-non",    label: "ITR (Non-Audit)", category: "IT",     description: "Income Tax Return for non-audit cases", month: 6,  day: 31 },
  { id: "itr-audit",  label: "ITR (Audit)",     category: "IT",     description: "Income Tax Return for audit cases",     month: 9,  day: 31 },
  { id: "tax-audit",  label: "Tax Audit Report",category: "IT",     description: "Form 3CD tax audit report",             month: 9,  day: 30 },
  { id: "26as-tds-q1",label: "TDS Return Q1",   category: "TDS",   description: "Quarterly TDS return (Form 24Q/26Q)",   month: 6,  day: 31 },
  { id: "26as-tds-q2",label: "TDS Return Q2",   category: "TDS",   description: "Quarterly TDS return (Form 24Q/26Q)",   month: 9,  day: 31 },
  { id: "26as-tds-q3",label: "TDS Return Q3",   category: "TDS",   description: "Quarterly TDS return (Form 24Q/26Q)",   month: 0,  day: 31 },
  { id: "26as-tds-q4",label: "TDS Return Q4",   category: "TDS",   description: "Quarterly TDS return (Form 24Q/26Q)",   month: 5,  day: 31 },
  { id: "roc-mgt7",   label: "MGT-7 (ROC)",     category: "ROC",    description: "Annual return for private limited co",  month: 10, day: 29 },
  { id: "roc-aoc4",   label: "AOC-4 (ROC)",     category: "ROC",    description: "Financial statements filing with ROC",  month: 9,  day: 29 },
  { id: "advance-q1", label: "Advance Tax Q1",  category: "IT",     description: "15% advance tax payment",              month: 5,  day: 15 },
  { id: "advance-q2", label: "Advance Tax Q2",  category: "IT",     description: "45% advance tax payment",              month: 8,  day: 15 },
  { id: "advance-q3", label: "Advance Tax Q3",  category: "IT",     description: "75% advance tax payment",              month: 11, day: 15 },
  { id: "advance-q4", label: "Advance Tax Q4",  category: "IT",     description: "100% advance tax payment",             month: 2,  day: 15 },
];

const CATEGORY_COLOR = {
  GST: "var(--teal, #0d9488)",
  IT:  "var(--violet, #8b5cf6)",
  TDS: "var(--amber, #f59e0b)",
  PF:  "var(--blue, #3b82f6)",
  ESI: "var(--blue, #3b82f6)",
  ROC: "var(--pink, #ec4899)",
};

const CATEGORY_BG = {
  GST: "rgba(13,148,136,0.1)",
  IT:  "rgba(139,92,246,0.1)",
  TDS: "rgba(245,158,11,0.1)",
  PF:  "rgba(59,130,246,0.1)",
  ESI: "rgba(59,130,246,0.1)",
  ROC: "rgba(236,72,153,0.1)",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function nextOccurrence(day, month, from) {
  const d = new Date(from);
  if (month === null || month === undefined) {
    // Monthly: try this month first
    let candidate = new Date(d.getFullYear(), d.getMonth(), day);
    if (candidate <= d) candidate = new Date(d.getFullYear(), d.getMonth() + 1, day);
    return candidate;
  }
  // Annual
  let year = d.getFullYear();
  let candidate = new Date(year, month, day);
  if (candidate <= d) candidate = new Date(year + 1, month, day);
  return candidate;
}

function daysUntil(date, from) {
  return Math.ceil((date - from) / 86400000);
}

function urgencyClass(days) {
  if (days <= 3)  return "urgent";
  if (days <= 7)  return "warning";
  if (days <= 14) return "soon";
  return "normal";
}

function urgencyColor(days) {
  if (days <= 3)  return "var(--red, #ef4444)";
  if (days <= 7)  return "var(--amber, #f59e0b)";
  if (days <= 14) return "var(--yellow, #eab308)";
  return "var(--text-3)";
}

function formatDate(d) {
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

// ── Component ─────────────────────────────────────────────────────────────────

const ALL_CATS = ["All", "GST", "IT", "TDS", "PF", "ESI", "ROC"];

export function DeadlineDashboard() {
  const [filter, setFilter] = useState("All");
  const [showAll, setShowAll] = useState(false);

  const now = useMemo(() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d;
  }, []);

  const deadlines = useMemo(() => {
    const all = [
      ...MONTHLY_DEADLINES.map(d => ({ ...d, month: null })),
      ...ANNUAL_DEADLINES,
    ].map(def => {
      const due = nextOccurrence(def.day, def.month, now);
      const days = daysUntil(due, now);
      return { ...def, due, days };
    });
    all.sort((a, b) => a.days - b.days);
    return all;
  }, [now]);

  const filtered = filter === "All" ? deadlines : deadlines.filter(d => d.category === filter);
  const visible  = showAll ? filtered : filtered.slice(0, 7);

  return (
    <div className="deadline-dashboard">

      {/* Category filter chips */}
      <div className="deadline-filter-row">
        {ALL_CATS.map(cat => (
          <button
            key={cat}
            className={`deadline-cat-chip${filter === cat ? " active" : ""}`}
            style={filter === cat && cat !== "All" ? {
              background: CATEGORY_BG[cat],
              color: CATEGORY_COLOR[cat],
              borderColor: CATEGORY_COLOR[cat],
            } : {}}
            onClick={() => setFilter(cat)}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Countdown to next deadline */}
      {filtered.length > 0 && (
        <div className="deadline-next-card" style={{
          borderColor: urgencyColor(filtered[0].days),
          background: filtered[0].days <= 3
            ? "rgba(239,68,68,0.07)"
            : filtered[0].days <= 7
            ? "rgba(245,158,11,0.07)"
            : "var(--bg-2)",
        }}>
          <div className="deadline-next-label">Next deadline</div>
          <div className="deadline-next-title">{filtered[0].label}</div>
          <div className="deadline-next-days" style={{ color: urgencyColor(filtered[0].days) }}>
            {filtered[0].days === 0 ? "Due TODAY" :
             filtered[0].days === 1 ? "1 day left" :
             `${filtered[0].days} days left`}
          </div>
          <div className="deadline-next-date">{formatDate(filtered[0].due)}</div>
        </div>
      )}

      {/* Deadline list */}
      <div className="deadline-list">
        {visible.map(d => (
          <div key={d.id} className={`deadline-row urgency-${urgencyClass(d.days)}`}>
            <div className="deadline-row-left">
              <span
                className="deadline-cat-badge"
                style={{ background: CATEGORY_BG[d.category], color: CATEGORY_COLOR[d.category] }}
              >
                {d.category}
              </span>
              <div className="deadline-row-info">
                <div className="deadline-row-name">{d.label}</div>
                <div className="deadline-row-desc">{d.description}</div>
              </div>
            </div>
            <div className="deadline-row-right">
              <div className="deadline-row-date">{formatDate(d.due)}</div>
              <div className="deadline-row-days" style={{ color: urgencyColor(d.days) }}>
                {d.days === 0 ? "Today" : d.days === 1 ? "1d" : `${d.days}d`}
              </div>
            </div>
          </div>
        ))}
      </div>

      {filtered.length > 7 && (
        <button className="deadline-show-more" onClick={() => setShowAll(v => !v)}>
          {showAll ? "Show less" : `Show ${filtered.length - 7} more`}
        </button>
      )}
    </div>
  );
}
