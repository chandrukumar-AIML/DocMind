import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Deterministic component test: mock the api boundary so the panel always
// receives sample data (the real demo-layer routing is covered separately in
// api/client.test.js and api/demo.test.js). This avoids env/timing flakiness.
vi.mock("../api/client", () => ({
  api: {
    listWebhooks: async () => ({
      webhooks: [
        { id: "wh-1", name: "Slack Notifier", url: "https://hooks.slack.com/x", events: ["document_ingested"] },
        { id: "wh-2", name: "Ops Pipeline", url: "https://ops.acme.com/hook", events: ["workflow_triggered"] },
      ],
    }),
  },
}));

import { WebhookPanel } from "./WebhookPanel";

describe("WebhookPanel", () => {
  it("renders the webhooks returned by the api instead of an empty state", async () => {
    render(<WebhookPanel />);
    await waitFor(() => {
      expect(screen.getByText("Slack Notifier")).toBeInTheDocument();
    });
    expect(screen.getByText("Ops Pipeline")).toBeInTheDocument();
    expect(screen.queryByText(/No webhooks registered/i)).not.toBeInTheDocument();
  });
});
