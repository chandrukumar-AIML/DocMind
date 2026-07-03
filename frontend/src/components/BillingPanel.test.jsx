import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    listPlans: async () => ({
      plans: [
        { id: "starter", label: "Starter", price_display: "Free", self_serve: false, max_docs: 100, max_queries_per_day: 500, max_storage_gb: 5.0 },
        { id: "business", label: "Business", price_display: "$49/mo", self_serve: true, max_docs: 1000, max_queries_per_day: 5000, max_storage_gb: 50.0 },
      ],
    }),
    getSubscription: async () => ({
      plan: "starter",
      subscription_status: "none",
      has_stripe_customer: false,
    }),
    getUsage: async () => ({
      docs: { used: 20, limit: 100 },
      queries_today: { used: 12, limit: 500 },
      storage_mb: { used: 340, limit_mb: 5120 },
    }),
  },
}));

vi.mock("../hooks/usePermissions", () => ({
  usePermissions: () => ({ canManageBilling: true }),
}));

import { BillingPanel } from "./BillingPanel";

describe("BillingPanel", () => {
  it("renders the workspace's current plan and an upgrade option", async () => {
    render(<BillingPanel />);
    await waitFor(() => {
      expect(screen.getByText(/Current plan: Starter/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Upgrade to Business/i)).toBeInTheDocument();
    expect(screen.getByText(/Usage this period/i)).toBeInTheDocument();
    expect(screen.getByText("20 / 100")).toBeInTheDocument();
  });
});

describe("BillingPanel — access control", () => {
  it("shows an access-denied message for non workspace-admins", async () => {
    vi.doMock("../hooks/usePermissions", () => ({
      usePermissions: () => ({ canManageBilling: false }),
    }));
    vi.resetModules();
    const { BillingPanel: GatedPanel } = await import("./BillingPanel");
    render(<GatedPanel />);
    expect(screen.getByText(/Workspace admin access required/i)).toBeInTheDocument();
  });
});
