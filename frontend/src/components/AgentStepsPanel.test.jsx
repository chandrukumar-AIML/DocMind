import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentStepsPanel } from "./AgentStepsPanel";

describe("AgentStepsPanel", () => {
  it("renders each agent step's content", () => {
    const steps = [
      { node: "query_analyzer", content: "Analyzing the question" },
      { node: "vector_retriever", content: "Retrieving 20 chunks" },
    ];
    render(<AgentStepsPanel steps={steps} isStreaming={false} />);
    expect(screen.getByText("Analyzing the question")).toBeInTheDocument();
    expect(screen.getByText("Retrieving 20 chunks")).toBeInTheDocument();
  });

  it("shows a thinking indicator while streaming", () => {
    render(<AgentStepsPanel steps={[]} isStreaming={true} />);
    expect(screen.getByText(/Agent thinking/i)).toBeInTheDocument();
  });

  it("renders safely with no steps", () => {
    const { container } = render(<AgentStepsPanel steps={[]} isStreaming={false} />);
    expect(container).toBeTruthy();
  });
});
