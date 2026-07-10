import { useState, useEffect } from "react";

type StepStatus = "running" | "done";

interface AgentStep {
  node: string;
  status: StepStatus;
}

interface UseAgentStepsProps {
  queryMode: string;
  isStreaming: boolean;
  lastStatusStep: string | undefined;
  sessionId: string;
}

interface UseAgentStepsReturn {
  agentSteps: AgentStep[];
}

export function useAgentSteps({
  queryMode,
  isStreaming,
  lastStatusStep,
  sessionId,
}: UseAgentStepsProps): UseAgentStepsReturn {
  const [agentSteps, setAgentSteps] = useState<AgentStep[]>([]);

  useEffect(() => {
    if (queryMode !== "agent" || !lastStatusStep || !isStreaming) return;
    setAgentSteps(prev => {
      if (prev[prev.length - 1]?.node === lastStatusStep) return prev;
      return [...prev, { node: lastStatusStep, status: "running" }];
    });
  }, [lastStatusStep, isStreaming, queryMode]);

  useEffect(() => {
    if (!isStreaming && agentSteps.length > 0) {
      setAgentSteps(prev => prev.map(s => ({ ...s, status: "done" as StepStatus })));
    }
  }, [isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setAgentSteps([]); }, [sessionId]);

  return { agentSteps };
}
