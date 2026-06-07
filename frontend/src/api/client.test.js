import { describe, it, expect, afterEach, vi } from "vitest";

// The `api` export is built at module-load time based on isDemoMode(), so each
// case loads a fresh copy of the module with the env stubbed accordingly.
async function loadApi(demo) {
  vi.resetModules();
  vi.stubEnv("VITE_DEMO_MODE", demo ? "true" : "false");
  const mod = await import("./client");
  return mod.api;
}

describe("api demo-aware export (client.js)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("routes mocked endpoints to the demo layer in demo mode", async () => {
    const api = await loadApi(true);
    const [wh, wf, regs] = await Promise.all([
      api.listWebhooks(),
      api.listWorkflows(),
      api.listRegulations(),
    ]);
    expect(wh.webhooks.length).toBeGreaterThan(0);
    expect(wf.workflows.length).toBeGreaterThan(0);
    expect(Object.keys(regs.regulations)).toContain("GDPR");
  });

  it("returns the nested domain-analysis shape the UI expects", async () => {
    const api = await loadApi(true);
    const res = await api.analyzeLegal("Service_Agreement_v3.docx");
    expect(res.analysis.risk.overall_score).toBeTypeOf("number");
    expect(res.analysis.clauses.items.length).toBeGreaterThan(0);
  });

  it("falls back to the real impl for un-mocked URL builders (no network)", async () => {
    const api = await loadApi(true);
    const url = api.downloadDocument("uploads/Annual_Report_2024.pdf", "demo-workspace");
    expect(typeof url).toBe("string");
    expect(url).toContain("/api/v1/documents/");
    expect(url).toContain("Annual_Report_2024.pdf");
  });

  it("uses the real implementation (not the demo proxy) when demo mode is off", async () => {
    const api = await loadApi(false);
    // Real impl is plain functions; URL builders still work without a backend.
    const url = api.downloadDocument("uploads/x.pdf", "ws1");
    expect(url).toContain("/api/v1/documents/");
    expect(typeof api.listWebhooks).toBe("function");
  });
});
