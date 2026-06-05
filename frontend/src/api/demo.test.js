import { describe, it, expect } from "vitest";
import { demoApi, DEMO_DOCS, DEMO_USER } from "./demo";

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

  it("returns legal analysis with clauses and a risk score", async () => {
    const res = await demoApi.analyzeLegal("Service_Agreement_v3.docx");
    expect(res.clauses.length).toBeGreaterThan(0);
    expect(typeof res.risk_score).toBe("number");
    expect(res.clauses[0]).toHaveProperty("type");
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
});
