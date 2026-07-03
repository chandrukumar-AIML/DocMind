import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Deterministic component test: mock the api boundary + permissions hook so the panel
// always renders with a workspace-admin identity and sample provider/config data.
vi.mock("../api/client", () => ({
  api: {
    listLlmProviders: async () => ({
      providers: [
        { id: "openai", label: "OpenAI", default_model: "gpt-4o", base_url: null },
        { id: "groq", label: "Groq (Free)", default_model: "llama-3.3-70b-versatile", base_url: "https://api.groq.com/openai/v1" },
      ],
    }),
    getLlmSettings: async () => ({
      configured: true,
      provider: "groq",
      model: "llama-3.3-70b-versatile",
      base_url: "https://api.groq.com/openai/v1",
      api_key_masked: "****demo",
      is_active: true,
      updated_at: new Date().toISOString(),
    }),
  },
}));

vi.mock("../hooks/usePermissions", () => ({
  usePermissions: () => ({ canManageLlmSettings: true }),
}));

import { LlmSettingsPanel } from "./LlmSettingsPanel";

describe("LlmSettingsPanel", () => {
  it("renders the workspace's current provider instead of an empty/error state", async () => {
    render(<LlmSettingsPanel />);
    await waitFor(() => {
      expect(screen.getByText(/Currently using: Groq \(Free\)/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/\*\*\*\*demo/)).toBeInTheDocument();
  });
});

describe("LlmSettingsPanel — access control", () => {
  it("shows an access-denied message for non workspace-admins", async () => {
    vi.doMock("../hooks/usePermissions", () => ({
      usePermissions: () => ({ canManageLlmSettings: false }),
    }));
    vi.resetModules();
    const { LlmSettingsPanel: GatedPanel } = await import("./LlmSettingsPanel");
    render(<GatedPanel />);
    expect(screen.getByText(/Workspace admin access required/i)).toBeInTheDocument();
  });
});
