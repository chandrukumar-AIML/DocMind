// frontend/src/hooks/useIngest.js
import { useState, useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";

const MAX_FILE_SIZE_MB = 50;

const ALLOWED_EXTENSIONS = new Set([
  "pdf", "png", "jpg", "jpeg", "tiff", "tif", "bmp",
  "docx", "doc",
  "xlsx", "xls", "csv",
  "mp3", "mp4", "wav", "m4a", "ogg", "flac",
  "txt",
]);

export function useIngest(onSuccess) {
  const [uploading, setUploading]     = useState(false);
  const [progress, setProgress]       = useState(0);
  const [lastResult, setLastResult]   = useState(null);
  const [error, setError]             = useState(null);
  // Batch state: array of { name, status: 'pending'|'uploading'|'done'|'error' }
  const [batchQueue, setBatchQueue]   = useState([]);
  const refreshTimerRef               = useRef(null);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, []);

  const validateFile = useCallback((file) => {
    if (!file) return "No file selected";
    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024)
      return `File too large — maximum ${MAX_FILE_SIZE_MB} MB`;
    const ext = file.name.split(".").pop().toLowerCase();
    if (!ALLOWED_EXTENSIONS.has(ext))
      return `Unsupported file type: .${ext}`;
    return null;
  }, []);

  const upload = useCallback(async (file, options = {}) => {
    const validationError = validateFile(file);
    if (validationError) { toast.error(validationError); return null; }
    if (uploading) return null;

    setUploading(true);
    setProgress(0);
    setError(null);

    const toastId = toast.loading(`Processing ${file.name}…`);
    const workspaceId = options.workspaceId
      || localStorage.getItem("documind_workspace_id")
      || "default";

    try {
      const result = await api.ingest(file, {
        ...options,
        workspaceId,
        onProgress: (evt) => {
          if (evt.total) setProgress(Math.round((evt.loaded / evt.total) * 100));
        },
      });

      setLastResult(result);
      toast.success(
        `Indexed: ${result.page_count ?? 1} pages, ${result.child_chunks ?? result.chunk_count ?? 0} chunks`,
        { id: toastId, duration: 4000 }
      );

      if (onSuccess) {
        clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = setTimeout(() => onSuccess(result), 300);
      }
      return result;
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || "Upload failed";
      setError(msg);
      toast.error(msg, { id: toastId });
      return null;
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }, [uploading, onSuccess, validateFile]);

  // Upload multiple files sequentially with per-file status
  const uploadBatch = useCallback(async (files, options = {}) => {
    const fileArray = Array.from(files).filter(f => !validateFile(f));
    const invalidFiles = Array.from(files).filter(f => validateFile(f));
    invalidFiles.forEach(f => toast.error(`${f.name}: ${validateFile(f)}`));

    if (fileArray.length === 0) return;
    if (fileArray.length === 1) return upload(fileArray[0], options);

    const workspaceId = options.workspaceId
      || localStorage.getItem("documind_workspace_id")
      || "default";

    setBatchQueue(fileArray.map(f => ({ name: f.name, status: "pending" })));
    setUploading(true);
    setError(null);

    let successCount = 0;
    for (let i = 0; i < fileArray.length; i++) {
      const file = fileArray[i];
      setBatchQueue(prev =>
        prev.map((q, idx) => idx === i ? { ...q, status: "uploading" } : q)
      );
      const toastId = toast.loading(`Processing ${file.name} (${i + 1}/${fileArray.length})…`);
      try {
        const result = await api.ingest(file, {
          ...options,
          workspaceId,
          onProgress: (evt) => {
            if (evt.total) setProgress(Math.round((evt.loaded / evt.total) * 100));
          },
        });
        toast.success(
          `${file.name}: ${result.child_chunks ?? 0} chunks`,
          { id: toastId, duration: 3000 }
        );
        setBatchQueue(prev =>
          prev.map((q, idx) => idx === i ? { ...q, status: "done" } : q)
        );
        successCount++;
      } catch (err) {
        const msg = err.response?.data?.detail || err.message || "Upload failed";
        toast.error(`${file.name}: ${msg}`, { id: toastId });
        setBatchQueue(prev =>
          prev.map((q, idx) => idx === i ? { ...q, status: "error" } : q)
        );
      }
    }

    setUploading(false);
    setProgress(0);

    if (successCount > 0 && onSuccess) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = setTimeout(() => onSuccess(), 300);
    }

    // Clear queue after short display
    setTimeout(() => setBatchQueue([]), 3000);
  }, [upload, onSuccess, validateFile]);

  const cancel = useCallback(() => {
    setUploading(false);
    setProgress(0);
    setBatchQueue([]);
  }, []);

  return { upload, uploadBatch, uploading, progress, lastResult, error, cancel, validateFile, batchQueue };
}
