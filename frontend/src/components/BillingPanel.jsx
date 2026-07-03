// frontend/src/components/BillingPanel.jsx
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

// ── helpers ──────────────────────────────────────────────────────────────────

function loadRazorpayScript() {
  return new Promise((resolve) => {
    if (window.Razorpay) { resolve(true); return; }
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload  = () => resolve(true);
    s.onerror = () => resolve(false);
    document.body.appendChild(s);
  });
}

function UsageBar({ label, used, limit }) {
  const unlimited = limit === null || limit === undefined;
  const pct  = unlimited ? 0 : Math.min(100, Math.round((used / limit) * 100));
  const over  = !unlimited && used >= limit;
  const warn  = !unlimited && pct >= 80;
  const color = over ? "var(--red)" : warn ? "#f59e0b" : "var(--accent)";
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>
        <span>{label}</span>
        <span style={{ color: over ? "var(--red)" : undefined }}>
          {unlimited ? `${used} / ∞` : `${used} / ${limit}`}
        </span>
      </div>
      {!unlimited && (
        <div style={{ height: 6, borderRadius: 3, background: "var(--surface-3, var(--bg-3))", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, borderRadius: 3, background: color, transition: "width .4s" }} />
        </div>
      )}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export function BillingPanel() {
  const { canManageBilling } = usePermissions();

  const [plans,            setPlans]           = useState([]);
  const [subscription,     setSubscription]     = useState(null);
  const [usage,            setUsage]            = useState(null);
  const [loading,          setLoading]          = useState(true);
  const [paying,           setPaying]           = useState(null);
  const [razorpayReady,    setRazorpayReady]    = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [plansRes, subRes, usageRes, rzpConfig] = await Promise.allSettled([
        api.listPlans(),
        api.getSubscription(),
        api.getBillingUsage(),
        api.razorpayConfig(),
      ]);
      if (plansRes.status === "fulfilled") setPlans(plansRes.value.plans || []);
      if (subRes.status   === "fulfilled") setSubscription(subRes.value);
      if (usageRes.status === "fulfilled") setUsage(usageRes.value);
      setRazorpayReady(rzpConfig.status === "fulfilled" && !!rzpConfig.value?.key_id);
    } catch {
      toast.error("Failed to load billing info");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Razorpay checkout flow ────────────────────────────────────────────────
  const handleUpgrade = async (planId) => {
    setPaying(planId);
    try {
      const loaded = await loadRazorpayScript();
      if (!loaded) { toast.error("Could not load Razorpay — check your internet connection"); return; }

      // Create subscription on backend
      const sub = await api.razorpaySubscribe(planId);

      const options = {
        key:             sub.key_id,
        subscription_id: sub.subscription_id,
        name:            "DocuMind AI",
        description:     `${plans.find(p => p.id === planId)?.label || planId} Plan`,
        image:           "/logo.png",
        theme:           { color: "#0D9488" },
        handler: async (response) => {
          try {
            await api.razorpayVerify({
              razorpay_payment_id:      response.razorpay_payment_id,
              razorpay_subscription_id: response.razorpay_subscription_id,
              razorpay_signature:       response.razorpay_signature,
              plan:                     planId,
            });
            toast.success(`Upgraded to ${plans.find(p => p.id === planId)?.label}! 🎉`);
            load(); // refresh usage + subscription
          } catch {
            toast.error("Payment verification failed — contact support");
          }
        },
        modal: {
          ondismiss: () => setPaying(null),
        },
        prefill: {
          name:  subscription?.email || "",
          email: subscription?.email || "",
        },
        notes: { plan: planId },
      };

      const rzp = new window.Razorpay(options);
      rzp.on("payment.failed", (resp) => {
        toast.error(`Payment failed: ${resp.error?.description || "Unknown error"}`);
        setPaying(null);
      });
      rzp.open();

    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to start payment");
      setPaying(null);
    }
  };

  // ── plan label helpers ────────────────────────────────────────────────────
  const planLabel = (p) => {
    if (p.max_docs == null) return "Unlimited docs & queries · Custom storage";
    const q = p.max_queries_per_month == null ? "Unlimited queries" : `${p.max_queries_per_month} queries/mo`;
    return `${p.max_docs} docs · ${q} · ${p.max_storage_gb} GB`;
  };

  const inrPrice = (p) => {
    if (p.price_inr === 0) return "Free";
    if (!p.price_inr)      return p.price_display;
    return `₹${p.price_inr.toLocaleString("en-IN")}/mo`;
  };

  // ── guard ─────────────────────────────────────────────────────────────────
  if (!canManageBilling) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Workspace admin access required.
      </div>
    );
  }

  // ── render ────────────────────────────────────────────────────────────────
  const S = {
    wrap:    { padding: "20px 24px", maxWidth: 680, display: "flex", flexDirection: "column", gap: 16 },
    heading: { fontSize: 16, fontWeight: 700, marginBottom: 2 },
    sub:     { fontSize: 11, color: "var(--text-3)" },
    card:    { background: "var(--bg-2)", border: "1px solid var(--bg-3)", borderRadius: 8, padding: "14px 16px" },
    row:     { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 },
    planName:{ fontWeight: 600, fontSize: 13 },
    planSub: { fontSize: 11, color: "var(--text-3)", marginTop: 2 },
    price:   { fontWeight: 700, fontSize: 15, color: "var(--accent)", whiteSpace: "nowrap" },
    chip:    (color) => ({
      fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 99,
      background: color === "green" ? "#0d948822" : "var(--bg-3)",
      color:      color === "green" ? "#0d9488"   : "var(--text-3)",
      border:     `1px solid ${color === "green" ? "#0d948844" : "var(--bg-3)"}`,
    }),
    upgradeBtn: (disabled) => ({
      fontSize: 11, fontWeight: 600, padding: "5px 14px", borderRadius: 6,
      background: disabled ? "var(--bg-3)" : "var(--accent)",
      color:      disabled ? "var(--text-3)" : "#fff",
      border:     "none", cursor: disabled ? "not-allowed" : "pointer",
      opacity:    disabled ? 0.6 : 1,
    }),
    badge: (color) => ({
      display: "inline-block", width: 8, height: 8, borderRadius: "50%",
      background: color, marginRight: 6,
    }),
  };

  return (
    <div style={S.wrap}>

      {/* header */}
      <div>
        <div style={S.heading}>Billing</div>
        <div style={S.sub}>Manage this workspace's subscription · Payments via Razorpay (INR)</div>
      </div>

      {loading ? (
        <div style={{ color: "var(--text-3)", fontSize: 12, padding: 20 }}>Loading…</div>
      ) : (
        <>
          {/* current plan + status */}
          {subscription && (
            <div style={S.card}>
              <div style={S.row}>
                <div>
                  <div style={S.planName}>
                    <span style={S.badge(subscription.subscription_status === "active" ? "#0d9488" : subscription.subscription_status === "past_due" ? "#f59e0b" : "#6b7280")} />
                    Current plan: {plans.find(p => p.id === subscription.plan)?.label || subscription.plan}
                  </div>
                  <div style={S.planSub}>
                    Status: {subscription.subscription_status === "none" ? "No active subscription" : subscription.subscription_status}
                  </div>
                </div>
                <span style={S.chip(subscription.subscription_status === "active" ? "green" : "grey")}>
                  {subscription.subscription_status === "active" ? "Active" : subscription.subscription_status === "past_due" ? "Payment due" : "Free tier"}
                </span>
              </div>
              {subscription.subscription_status === "past_due" && (
                <div style={{ marginTop: 10, padding: "8px 12px", background: "#f59e0b22", borderRadius: 6, fontSize: 11, color: "#f59e0b", border: "1px solid #f59e0b44" }}>
                  ⚠ Payment failed. Please upgrade again to restore full access.
                </div>
              )}
            </div>
          )}

          {/* usage */}
          {usage && (
            <div style={S.card}>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 12 }}>Usage this month</div>
              <UsageBar label="Documents"      used={usage.docs?.used ?? 0}          limit={usage.docs?.limit} />
              <UsageBar label="Queries / month" used={usage.queries_today?.used ?? 0}  limit={usage.queries_today?.limit} />
              <UsageBar label="Storage (MB)"   used={usage.storage_mb?.used ?? 0}     limit={usage.storage_mb?.limit_mb} />
            </div>
          )}

          {/* plan cards */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {plans.map(plan => {
              const isCurrent  = subscription?.plan === plan.id;
              const isProcessing = paying === plan.id;
              const isFree     = plan.price_inr === 0;
              const isEnterprise = !plan.self_serve && !isFree;

              return (
                <div key={plan.id} style={{
                  ...S.card,
                  border: isCurrent ? "1px solid #0d948866" : "1px solid var(--bg-3)",
                  background: isCurrent ? "#0d948808" : "var(--bg-2)",
                }}>
                  <div style={S.row}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={S.planName}>{plan.label}</span>
                        {isCurrent && <span style={S.chip("green")}>Current</span>}
                      </div>
                      <div style={S.planSub}>{planLabel(plan)}</div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                      <span style={S.price}>{inrPrice(plan)}</span>
                      {isCurrent ? null
                        : isEnterprise ? (
                          <a
                            href="mailto:terazionservices@gmail.com?subject=DocuMind Enterprise Enquiry"
                            style={{ ...S.upgradeBtn(false), textDecoration: "none", display: "inline-block" }}
                          >
                            Contact us
                          </a>
                        ) : plan.self_serve ? (
                          razorpayReady ? (
                            <button
                              style={S.upgradeBtn(!!paying)}
                              disabled={!!paying}
                              onClick={() => handleUpgrade(plan.id)}
                            >
                              {isProcessing ? "Opening…" : "Upgrade"}
                            </button>
                          ) : (
                            <span style={S.chip("grey")} title="Payments coming soon">
                              Coming Soon
                            </span>
                          )
                        ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* GST invoice download — shown when on a paid active plan */}
          {subscription?.subscription_status === "active" && subscription?.plan !== "free" && (
            <div style={S.card}>
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8 }}>GST Invoice</div>
              <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 10 }}>
                Download a GST-compliant tax invoice for your subscription. Required for B2B input tax credit.
              </div>
              <GstInvoiceForm plan={subscription.plan} plans={plans} />
            </div>
          )}

          {/* footer note */}
          <div style={{ fontSize: 10, color: "var(--text-3)", textAlign: "center" }}>
            Payments processed securely by Razorpay · UPI · NetBanking · Cards · 18% GST applicable
          </div>
        </>
      )}
    </div>
  );
}

// ── GST Invoice mini-form ─────────────────────────────────────────────────────

const PLAN_PRICES = { starter: 2499, pro: 6599, enterprise: 24999 };

const INDIAN_STATES = [
  ["01","Jammu & Kashmir"],["02","Himachal Pradesh"],["03","Punjab"],["04","Chandigarh"],
  ["05","Uttarakhand"],["06","Haryana"],["07","Delhi"],["08","Rajasthan"],["09","Uttar Pradesh"],
  ["10","Bihar"],["11","Sikkim"],["12","Arunachal Pradesh"],["13","Nagaland"],["14","Manipur"],
  ["15","Mizoram"],["16","Tripura"],["17","Meghalaya"],["18","Assam"],["19","West Bengal"],
  ["20","Jharkhand"],["21","Odisha"],["22","Chhattisgarh"],["23","Madhya Pradesh"],
  ["24","Gujarat"],["26","Dadra & Nagar Haveli"],["27","Maharashtra"],["28","Andhra Pradesh"],
  ["29","Karnataka"],["30","Goa"],["31","Lakshadweep"],["32","Kerala"],["33","Tamil Nadu"],
  ["34","Puducherry"],["35","Andaman & Nicobar"],["36","Telangana"],["37","Andhra Pradesh (New)"],
];

function GstInvoiceForm({ plan, plans }) {
  const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
  const planInfo = plans.find(p => p.id === plan);
  const baseAmount = planInfo?.price_inr || PLAN_PRICES[plan] || 0;

  const [form, setForm] = useState({
    buyer_name: "", buyer_address: "", buyer_state: "Tamil Nadu",
    buyer_state_code: "33", buyer_gstin: "", buyer_email: "",
  });
  const [loading, setLoading] = useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleStateChange = (code) => {
    const entry = INDIAN_STATES.find(([c]) => c === code);
    set("buyer_state_code", code);
    set("buyer_state", entry?.[1] || "");
  };

  const download = async () => {
    if (!form.buyer_name || !form.buyer_address) {
      toast.error("Fill in buyer name and address"); return;
    }
    setLoading(true);
    try {
      const params = new URLSearchParams({
        plan,
        amount_inr: baseAmount,
        buyer_name: form.buyer_name,
        buyer_address: form.buyer_address,
        buyer_state: form.buyer_state,
        buyer_state_code: form.buyer_state_code,
        ...(form.buyer_gstin  ? { buyer_gstin:  form.buyer_gstin }  : {}),
        ...(form.buyer_email  ? { buyer_email:  form.buyer_email }  : {}),
      });
      window.open(`${API_BASE}/api/v1/gst-invoice/preview?${params}`, "_blank");
    } finally {
      setLoading(false);
    }
  };

  const inp = {
    width: "100%", padding: "6px 10px", borderRadius: 6, border: "1px solid var(--bg-3)",
    background: "var(--bg-1)", color: "var(--tx-1)", fontSize: 12, marginBottom: 8,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <input style={inp} placeholder="Company / Buyer name *" value={form.buyer_name} onChange={e => set("buyer_name", e.target.value)} />
      <input style={inp} placeholder="Billing address *" value={form.buyer_address} onChange={e => set("buyer_address", e.target.value)} />
      <select style={inp} value={form.buyer_state_code} onChange={e => handleStateChange(e.target.value)}>
        {INDIAN_STATES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      </select>
      <input style={inp} placeholder="GSTIN (optional — for input tax credit)" value={form.buyer_gstin} onChange={e => set("buyer_gstin", e.target.value.toUpperCase())} maxLength={15} />
      <input style={inp} placeholder="Email (optional)" value={form.buyer_email} onChange={e => set("buyer_email", e.target.value)} />
      <div style={{ fontSize: 10, color: "var(--text-3)", marginBottom: 6 }}>
        Base: ₹{baseAmount.toLocaleString("en-IN")} + 18% GST = ₹{(baseAmount * 1.18).toLocaleString("en-IN", { maximumFractionDigits: 0 })} total
      </div>
      <button
        style={{ padding: "7px 16px", borderRadius: 6, background: "var(--accent)", color: "#fff", border: "none", cursor: loading ? "not-allowed" : "pointer", fontSize: 12, fontWeight: 600, opacity: loading ? 0.6 : 1 }}
        disabled={loading}
        onClick={download}
      >
        {loading ? "Generating…" : "Download GST Invoice (PDF)"}
      </button>
    </div>
  );
}
