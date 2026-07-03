import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

import { LegalPage } from "./LegalPage";

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/legal/:doc" element={<LegalPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LegalPage", () => {
  it("renders the Terms of Service document", () => {
    renderAt("/legal/terms");
    // Heading comes from the markdown H1.
    expect(screen.getByRole("heading", { level: 1, name: /Terms of Service/i })).toBeInTheDocument();
    // Draft/lawyer-review disclaimer must be present.
    expect(screen.getByText(/PENDING LEGAL REVIEW/i)).toBeInTheDocument();
  });

  it("renders the Privacy Policy document", () => {
    renderAt("/legal/privacy");
    expect(screen.getByRole("heading", { level: 1, name: /Privacy Policy/i })).toBeInTheDocument();
  });

  it("renders the Data Processing Agreement document", () => {
    renderAt("/legal/dpa");
    expect(screen.getByRole("heading", { level: 1, name: /Data Processing Agreement/i })).toBeInTheDocument();
  });

  it("shows a not-found message for an unknown document", () => {
    renderAt("/legal/nonsense");
    expect(screen.getByText(/Document not found/i)).toBeInTheDocument();
  });
});
