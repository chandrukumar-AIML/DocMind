import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/client", () => ({
  api: {
    getSsoConfig: async () => ({
      configured: true,
      client_id: "0oaTestClientId",
      client_secret_masked: "****abcd",
      issuer: "https://test.okta.com",
      is_active: true,
      updated_at: new Date().toISOString(),
    }),
  },
}));

vi.mock("../hooks/usePermissions", () => ({
  usePermissions: () => ({ canManageSso: true }),
}));

import { SsoSettingsPanel } from "./SsoSettingsPanel";

describe("SsoSettingsPanel", () => {
  it("renders the workspace's current SSO config", async () => {
    render(<SsoSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText(/SSO configured/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/test\.okta\.com/)).toBeInTheDocument();
    expect(screen.getByText(/\*\*\*\*abcd/)).toBeInTheDocument();
  });
});

describe("SsoSettingsPanel — access control", () => {
  it("shows an access-denied message for non workspace-admins", async () => {
    vi.doMock("../hooks/usePermissions", () => ({
      usePermissions: () => ({ canManageSso: false }),
    }));
    vi.resetModules();
    const { SsoSettingsPanel: GatedPanel } = await import("./SsoSettingsPanel");
    render(<GatedPanel />);
    expect(screen.getByText(/Workspace admin access required/i)).toBeInTheDocument();
  });
});
