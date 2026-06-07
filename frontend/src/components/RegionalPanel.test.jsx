import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Deterministic component test: mock the api boundary. The demo-layer routing
// for validateIndianId is covered in api/demo.test.js and api/client.test.js.
vi.mock("../api/client", () => ({
  api: {
    validateIndianId: async (value, type) => ({ is_valid: true, type, value }),
  },
}));

import { RegionalPanel } from "./RegionalPanel";

describe("RegionalPanel", () => {
  it("shows a valid result after validating an Indian ID", async () => {
    render(<RegionalPanel />);

    // Switch to the "validate" tab (only the tab button exists at this point).
    fireEvent.click(screen.getByRole("button", { name: "Validate" }));

    const input = screen.getByPlaceholderText("ABCDE1234F");
    fireEvent.change(input, { target: { value: "ABCDE1234F" } });

    // After switching there are two "Validate" buttons (tab + submit); the
    // submit button is the last one rendered.
    const validateButtons = screen.getAllByRole("button", { name: "Validate" });
    fireEvent.click(validateButtons[validateButtons.length - 1]);

    // "✓ Valid PAN" is unique to the result banner (the "Validate" tab/button
    // contain "Valid" but never "Valid PAN").
    await waitFor(() => {
      expect(screen.getByText(/Valid PAN/i)).toBeInTheDocument();
    });
  });
});
