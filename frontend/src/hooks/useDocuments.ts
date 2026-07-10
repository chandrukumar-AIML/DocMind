import { useState, useCallback, useRef, MutableRefObject } from "react";
import { api } from "../api/client";

interface Document {
  source_file: string;
  [key: string]: unknown;
}

interface UseDocumentsProps {
  getCurrentWorkspace: () => { workspace_id: string } | null | undefined;
  user: unknown;
}

interface UseDocumentsReturn {
  documents: Document[];
  loadingDocs: boolean;
  loadError: string | null;
  refresh: (workspaceId?: string | null) => Promise<boolean>;
  handleDocumentDeleted: (file: string, selectedFile: string | null, onDeselect?: () => void) => void;
  retryRef: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  clearRetry: () => void;
}

export function useDocuments({ getCurrentWorkspace, user }: UseDocumentsProps): UseDocumentsReturn {
  const [documents,   setDocuments]   = useState<Document[]>([]);
  const [loadingDocs, setLoadingDocs] = useState(true);
  const [loadError,   setLoadError]   = useState<string | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async (workspaceId: string | null = null): Promise<boolean> => {
    setLoadingDocs(true);
    setLoadError(null);
    try {
      const wsId = workspaceId ?? getCurrentWorkspace()?.workspace_id;
      if (!user || !wsId) { setDocuments([]); return true; }
      const data = await api.listDocuments(wsId);
      setDocuments(data.documents ?? []);
      return true;
    } catch (err) {
      setLoadError((err as Error).message ?? "Failed to load documents");
      return false;
    } finally {
      setLoadingDocs(false);
    }
  }, [getCurrentWorkspace, user]);

  const handleDocumentDeleted = useCallback((
    file: string,
    selectedFile: string | null,
    onDeselect?: () => void,
  ) => {
    setDocuments(prev => prev.filter(d => d.source_file !== file));
    if (selectedFile === file) onDeselect?.();
  }, []);

  const clearRetry = useCallback(() => {
    if (retryRef.current) clearTimeout(retryRef.current);
  }, []);

  return { documents, loadingDocs, loadError, refresh, handleDocumentDeleted, retryRef, clearRetry };
}
