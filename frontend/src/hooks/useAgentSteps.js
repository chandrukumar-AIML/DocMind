import { useState, useEffect } from "react";

/**
 * Accumulates agent reasoning steps during a streaming agent-mode query.
 * Steps are reset when the session changes and marked "done" when streaming ends.
 */
export function useAgentSteps({ queryMode, isStreaming, lastStatusStep, sessionId }) {
  const [agentSteps, setAgentSteps] = useState([]);

  useEffect(() => {
    if (queryMode !== "agent" || !lastStatusStep || !isStreaming) return;
    setAgentSteps(prev => {
      if (prev[prev.length - 1]?.node === lastStatusStep) return prev;
      return [...prev, { node: lastStatusStep, status: "running" }];
    });
  }, [lastStatusStep, isStreaming, queryMode]);

  useEffect(() => {
    if (!isStreaming && agentSteps.length > 0) {
      setAgentSteps(prev => prev.map(s => ({ ...s, status: "done" })));
    }
  }, [isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setAgentSteps([]); }, [sessionId]);

  return { agentSteps };
}
