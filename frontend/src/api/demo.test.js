import { describe, it, expect, afterEach, vi } from "vitest";
import { demoApi, DEMO_DOCS, DEMO_USER, isDemoMode } from "./demo";

describe("demoApi", () => {
  it("returns a non-empty document library", async () => {
    const { documents } = await demoApi.listDocuments();
    expect(Array.isArray(documents)).toBe(true);
    expect(documents.length).toBeGreaterThan(0);
    expect(documents.length).toBe(DEMO_DOCS.length);
  });

  it("answers a query with citations", async () => {
    const res = await demoApi.query({ question: "What are the key findings?" });
    expect(res.answer).toBeTruthy();
    expect(typeof res.answer).toBe("string");
    expect(Array.isArray(res.citations)).toBe(true);
  });

  it("returns legal analysis matching the DomainPanel shape", async () => {
    const res = await demoApi.analyzeLegal("Service_Agreement_v3.docx");
    const { analysis } = res;
    expect(typeof analysis.risk.overall_score).toBe("number");
    expect(analysis.clauses.items.length).toBeGreaterThan(0);
    expect(analysis.clauses.items[0]).toHaveProperty("type");
    expect(Array.isArray(analysis.obligations)).toBe(true);
  });

  it("logs in and returns a token + workspace + user", async () => {
    const res = await demoApi.login("demo@documind.ai", "x");
    expect(res.access_token).toBeTruthy();
    expect(res.workspace_id).toBeTruthy();
    expect(res.user.email).toBe(DEMO_USER.email);
  });

  it("rejects login with an invalid email", async () => {
    await expect(demoApi.login("not-an-email", "x")).rejects.toThrow();
  });

  it("returns a compliance result with scores and violations", async () => {
    const res = await demoApi.checkCompliance("x.pdf", ["GDPR", "HIPAA"]);
    expect(res.overall_score).toBeTypeOf("number");
    expect(res.scores.GDPR).toBeTypeOf("number");
    expect(res.violations.length).toBeGreaterThan(0);
    expect(res.violations[0]).toHaveProperty("severity");
  });

  it("validates Indian IDs and parses Indian numbers", async () => {
    const pan = await demoApi.validateIndianId("ABCDE1234F", "pan");
    expect(pan.is_valid).toBe(true);
    expect(pan.type).toBe("pan");
    const num = await demoApi.parseIndianNumber("52 lakhs");
    expect(num.parsed_value).toBeTypeOf("number");
  });

  it("exposes admin stats so the demo superuser sees data", async () => {
    const stats = await demoApi.adminGetStats();
    expect(stats.total_workspaces).toBeGreaterThan(0);
    expect(DEMO_USER.is_superuser).toBe(true);
  });
});

describe("isDemoMode", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    window.history.replaceState({}, "", "/");
  });

  it("is true when VITE_DEMO_MODE=true", () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    expect(isDemoMode()).toBe(true);
  });

  it("is true when the URL has ?demo=true", () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.history.replaceState({}, "", "/?demo=true");
    expect(isDemoMode()).toBe(true);
  });

  it("is false by default (no env, no url param)", () => {
    vi.stubEnv("VITE_DEMO_MODE", "false");
    window.history.replaceState({}, "", "/");
    expect(isDemoMode()).toBe(false);
  });
});
