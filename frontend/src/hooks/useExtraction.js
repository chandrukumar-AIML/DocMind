import { useState, useCallback } from "react";
import { toast } from "react-hot-toast";
import { api } from "../api/client";

/**
 * Extracts tables and charts from the currently selected document.
 */
export function useExtraction({ selectedFile, getCurrentWorkspace }) {
  const [extractionResults, setExtractionResults] = useState(null);
  const [extracting,        setExtracting]        = useState(false);

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
        tables: tabRes.status   === "fulfilled" ? (tabRes.value?.tables   || []) : [],
        charts: chartRes.status === "fulfilled" ? (chartRes.value?.charts || []) : [],
      });
    } catch {
      toast.error("Extraction failed");
    } finally {
      setExtracting(false);
    }
  }, [selectedFile, getCurrentWorkspace, extracting]);

  return { extractionResults, extracting, handleExtract };
}
