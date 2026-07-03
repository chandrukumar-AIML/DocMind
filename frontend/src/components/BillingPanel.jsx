// frontend/src/components/BillingPanel.jsx
import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import { usePermissions } from "../hooks/usePermissions";
import toast from "react-hot-toast";

function UsageBar({ label, used, limit }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const over = used >= limit;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-3)", marginBottom: 4 }}>
        <span>{label}</span>
        <span style={{ color: over ? "var(--red)" : "var(--text-3)" }}>{used} / {limit}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "var(--surface-3)", overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${pct}%`, borderRadius: 3,
          background: over ? "var(--red)" : "var(--accent)",
        }} />
      </div>
    </div>
  );
}

export function BillingPanel() {
  const { canManageBilling } = usePermissions();

  const [plans, setPlans] = useState([]);
  const [subscription, setSubscription] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [checkingOut, setCheckingOut] = useState(false);
  const [openingPortal, setOpeningPortal] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [plansRes, subRes, usageRes] = await Promise.all([
        api.listPlans(),
        api.getSubscription(),
        api.getUsage(),
      ]);
      setPlans(plansRes.plans || []);
      setSubscription(subRes);
      setUsage(usageRes);
    } catch {
      toast.error("Failed to load billing info");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const upgrade = async (planId) => {
    setCheckingOut(true);
    try {
      const r = await api.startCheckout(planId);
      if (r.checkout_url && r.checkout_url.startsWith("#")) {
        toast.error("Checkout isn't available in this environment yet");
      } else {
        window.location.href = r.checkout_url;
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to start checkout");
    } finally {
      setCheckingOut(false);
    }
  };

  const manageBilling = async () => {
    setOpeningPortal(true);
    try {
      const r = await api.openBillingPortal();
      if (r.portal_url && r.portal_url.startsWith("#")) {
        toast.error("Billing portal isn't available in this environment yet");
      } else {
        window.location.href = r.portal_url;
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to open billing portal");
    } finally {
      setOpeningPortal(false);
    }
  };

  if (!canManageBilling) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--text-3)" }}>
        Workspace admin access required.
      </div>
    );
  }

  return (
    <div style={{ padding: "24px 32px", maxWidth: 720 }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 18, fontWeight: 700 }}>Billing</div>
        <div style={{ fontSize: 12, color: "var(--text-3)", marginTop: 2 }}>
          Manage this workspace's subscription plan.
        </div>
      </div>

      {loading ? (
        <div className="panel-empty">Loading…</div>
      ) : (
        <>
          {subscription && (
            <div className="panel-item" style={{ marginBottom: 20 }}>
              <div className="panel-item-row">
                <div style={{ flex: 1 }}>
                  <div className="panel-item-title">
                    Current plan: {plans.find(p => p.id === subscription.plan)?.label || subscription.plan}
                  </div>
                  <div className="panel-item-sub">
                    Status: {subscription.subscription_status}
                  </div>
                </div>
                <span className={`status-chip ${subscription.subscription_status === "active" ? "green" : "grey"}`}>
                  {subscription.subscription_status === "active" ? "Active" : "No subscription"}
                </span>
              </div>
            </div>
          )}

          {usage && (
            <div className="panel-item" style={{ marginBottom: 20 }}>
              <div className="panel-item-title" style={{ marginBottom: 12 }}>Usage this period</div>
              <UsageBar label="Documents" used={usage.docs.used} limit={usage.docs.limit} />
              <UsageBar label="Queries today" used={usage.queries_today.used} limit={usage.queries_today.limit} />
              <UsageBar label="Storage (MB)" used={usage.storage_mb.used} limit={usage.storage_mb.limit_mb} />
            </div>
          )}

          <div className="panel-list">
            {plans.map(plan => {
              const isCurrent = subscription?.plan === plan.id;
              return (
                <div key={plan.id} className="panel-item">
                  <div className="panel-item-row">
                    <div style={{ flex: 1 }}>
                      <div className="panel-item-title">{plan.label} — {plan.price_display}</div>
                      <div className="panel-item-sub">
                        {plan.max_docs == null
                          ? "Unlimited docs & queries"
                          : `${plan.max_docs} docs · ${plan.max_queries_per_day} queries/day · ${plan.max_storage_gb}GB`}
                      </div>
                    </div>
                    {isCurrent ? (
                      <span className="status-chip green">Current</span>
                    ) : plan.self_serve ? (
                      <button className="btn-primary" disabled={checkingOut} onClick={() => upgrade(plan.id)}>
                        {checkingOut ? "Redirecting…" : `Upgrade to ${plan.label}`}
                      </button>
                    ) : (
                      <span className="status-chip grey">Contact sales</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {subscription?.has_stripe_customer && (
            <div style={{ marginTop: 16, textAlign: "right" }}>
              <button className="btn-sm" disabled={openingPortal} onClick={manageBilling}>
                {openingPortal ? "Opening…" : "Manage Billing"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
