import { describe, it, expect } from "vitest";
import { ROLE_RANK, DEFAULT_WORKSPACE_ID } from "./constants";

describe("ROLE_RANK", () => {
  it("ranks roles in ascending privilege order", () => {
    expect(ROLE_RANK.viewer).toBeLessThan(ROLE_RANK.editor);
    expect(ROLE_RANK.editor).toBeLessThan(ROLE_RANK.workspace_admin);
    expect(ROLE_RANK.workspace_admin).toBeLessThan(ROLE_RANK.superadmin);
  });

  it("treats 'admin' as a legacy alias for workspace_admin", () => {
    expect(ROLE_RANK.admin).toBe(ROLE_RANK.workspace_admin);
  });

  it("gives superadmin the highest rank", () => {
    const ranks = Object.values(ROLE_RANK);
    expect(ROLE_RANK.superadmin).toBe(Math.max(...ranks));
  });
});

describe("DEFAULT_WORKSPACE_ID", () => {
  it("matches the backend 'default' literal", () => {
    expect(DEFAULT_WORKSPACE_ID).toBe("default");
  });
});
