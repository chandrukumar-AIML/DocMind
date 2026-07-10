import { useState, useCallback } from "react";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

interface ExtractionResults {
  tables: unknown[];
  charts: unknown[];
}

interface UseExtractionProps {
  selectedFile: string | null;
  getCurrentWorkspace: () => { workspace_id: string } | null | undefined;
}

interface UseExtractionReturn {
  extractionResults: ExtractionResults | null;
  extracting: boolean;
  handleExtract: () => Promise<void>;
}

export function useExtraction({ selectedFile, getCurrentWorkspace }: UseExtractionProps): UseExtractionReturn {
  const [extractionResults, setExtractionResults] = useState<ExtractionResults | null>(null);
  const [extracting, setExtracting] = useState(false);

  const handleExtract = useCallback(async () => {
    const wsId = getCurrentWorkspace()?.workspace_id;
    if (!selectedFile || extracting) return;
    setExtracting(true);
    setExtractionResults(null);
    try {
      const [tabRes, chartRes] = await Promise.allSettled([
        api.extractTables(selectedFile, wsId),
        api.extractCharts(selectedFile, wsId),
      ]);
      setExtractionResults({
        tables: tabRes.status   === "fulfilled" ? (tabRes.value?.tables  ?? []) : [],
        charts: chartRes.status === "fulfilled" ? (chartRes.value?.charts ?? []) : [],
      });
    } catch {
      toast.error("Extraction failed");
    } finally {
      setExtracting(false);
    }
  }, [selectedFile, getCurrentWorkspace, extracting]);

  return { extractionResults, extracting, handleExtract };
}
