import { useState, useCallback, useRef } from "react";
import { api } from "../api/client";

/**
 * Manages the document list for the current workspace.
 * Encapsulates loading state, error handling, and exponential-backoff retry.
 */
export function useDocuments({ getCurrentWorkspace, user }) {
  const [documents,   setDocuments]   = useState([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadError,   setLoadError]   = useState(null);
  const retryRef = useRef(null);

  const refresh = useCallback(async (workspaceId = null) => {
    setLoadingDocs(true);
    setLoadError(null);
    try {
      const wsId = workspaceId || getCurrentWorkspace()?.workspace_id;
      if (!user || !wsId) { setDocuments([]); return true; }
      const data = await api.listDocuments(wsId);
      setDocuments(data.documents || []);
      return true;
    } catch (err) {
      setLoadError(err.message || "Failed to load documents");
      return false;
    } finally {
      setLoadingDocs(false);
    }
  }, [getCurrentWorkspace, user]);

  const handleDocumentDeleted = useCallback((file, selectedFile, onDeselect) => {
    setDocuments(prev => prev.filter(d => d.source_file !== file));
    if (selectedFile === file) onDeselect?.();
  }, []);

  const clearRetry = useCallback(() => {
    if (retryRef.current) clearTimeout(retryRef.current);
  }, []);

  return {
    documents,
    loadingDocs,
    loadError,
    refresh,
    handleDocumentDeleted,
    retryRef,
    clearRetry,
  };
}
