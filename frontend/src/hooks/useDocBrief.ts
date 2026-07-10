import { useState, useCallback } from "react";
import { api } from "../api/client";

interface DocBrief {
  file: string;
  summary: string | null;
  loading: boolean;
}

interface UseDocBriefReturn {
  docBrief: DocBrief | null;
  triggerDocBrief: (sourceFile: string, workspaceId: string) => Promise<void>;
  dismissDocBrief: () => void;
}

export function useDocBrief(): UseDocBriefReturn {
  const [docBrief, setDocBrief] = useState<DocBrief | null>(null);

  const triggerDocBrief = useCallback(async (sourceFile: string, workspaceId: string) => {
    if (!sourceFile) return;
    setDocBrief({ file: sourceFile, summary: null, loading: true });
    try {
      const result = await api.query({
        question: "Give me a 2-3 sentence brief summary of this document. What is it about and what are the main topics?",
        filter_source_file: sourceFile,
        workspace_id: workspaceId,
        top_k_retrieve: 5,
        top_k_rerank: 2,
        stream: false,
      });
      const summary: string = result.answer ?? result.content ?? "";
      setDocBrief({
        file: sourceFile,
        summary: summary.replace(/^(Extractive answer|OpenAI unavailable)[^:]*:\s*/i, ""),
        loading: false,
      });
    } catch {
      setDocBrief(null);
    }
  }, []);

  const dismissDocBrief = useCallback(() => setDocBrief(null), []);

  return { docBrief, triggerDocBrief, dismissDocBrief };
}
