// frontend/src/hooks/useIngestProgress.js
// DVMELTSS-FIX: A - Async, E - Error handling, M - Modular
// ASCALE-FIX: S - Separation, C - Coupling, E - Error propagation
// FRONTEND-FIX: W - WebSocket, P - Polling fallback

import { useState, useCallback, useRef, useEffect } from "react";

// ════════════════════════════════════════════════════════════════════════
// CONFIG
// ════════════════════════════════════════════════════════════════════════
const API_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const WS_BASE = API_URL.replace(/^https?/, "ws");
const POLL_MS = 3000;
const WS_RECONNECT_DELAY_MS = 2000;

const STAGE_LABELS = {
  queued: "Waiting in queue",
  validate: "Validating file",
  ocr: "Extracting text (OCR)",
  chunking: "Chunking document",
  embedding: "Generating embeddings",
  indexing: "Indexing vectors",
  graph: "Building knowledge graph",
  versioning: "Registering version",
  bm25: "Updating keyword index",
  complete: "Complete",
  failed: "Failed",
  retry: "Retrying",
  cancelled: "Cancelled",
};

const STAGE_COLORS = {
  queued: "bg-gray-400",
  validate: "bg-blue-400",
  ocr: "bg-purple-500",
  chunking: "bg-indigo-500",
  embedding: "bg-blue-500",
  indexing: "bg-teal-500",
  graph: "bg-amber-500",
  versioning: "bg-cyan-500",
  bm25: "bg-lime-500",
  complete: "bg-green-500",
  failed: "bg-red-500",
  retry: "bg-yellow-500",
  cancelled: "bg-gray-500",
};

export function useIngestProgress() {
  const [tasks, setTasks] = useState({});
  const wsRefs = useRef({});
  const pollRefs = useRef({});
  const reconnectTimers = useRef({});

  const updateTask = useCallback((task_id, update) => {
    setTasks(prev => ({
      ...prev,
      [task_id]: { ...(prev[task_id] || {}), ...update, updated_at: Date.now() },
    }));
  }, []);

  const startTracking = useCallback((task_id, filename, ws_url, poll_url) => {
    // Initialize task state
    updateTask(task_id, {
      task_id, 
      filename,
      status: "queued", 
      stage: "queued",
      message: "Waiting in queue...",
      progress: 0,
      started_at: Date.now(),
    });

    let wsConnected = false;

    const connectWebSocket = () => {
      try {
        const ws = new WebSocket(`${WS_BASE}${ws_url}`);
        wsRefs.current[task_id] = ws;

        ws.onopen = () => {
          wsConnected = true;
          clearInterval(pollRefs.current[task_id]);
          updateTask(task_id, { status: "connected", message: "Connected to progress stream" });
        };

        ws.onmessage = (e) => {
          try {
            const event = JSON.parse(e.data);
            if (event.type === "ping") return;
            
            updateTask(task_id, {
              status: event.status,
              stage: event.stage,
              message: event.message,
              progress: event.progress,
              details: event.details,
              page_count: event.page_count,
              chunk_count: event.chunk_count,
              latency_seconds: event.latency_seconds,
              error: event.error,
              updated_at: Date.now(),
            });

            // Auto-close on terminal states
            if (["complete", "failed", "cancelled"].includes(event.status)) {
              ws.close();
              delete wsRefs.current[task_id];
            }
          } catch (err) {
            console.warn(`WebSocket parse error for ${task_id}:`, err);
          }
        };

        ws.onerror = (err) => {
          console.warn(`WebSocket error for ${task_id}:`, err);
          if (!wsConnected) {
            // Fallback to polling
            startPolling(task_id, poll_url);
          }
        };

        ws.onclose = () => {
          delete wsRefs.current[task_id];
          // Auto-reconnect for non-terminal states
          const task = tasks[task_id];
          if (task && !["complete", "failed", "cancelled"].includes(task.status)) {
            reconnectTimers.current[task_id] = setTimeout(() => {
              connectWebSocket();
            }, WS_RECONNECT_DELAY_MS);
          }
        };

      } catch (err) {
        console.warn(`WebSocket init failed for ${task_id}, falling back to polling:`, err);
        startPolling(task_id, poll_url);
      }
    };

    const startPolling = (tid, purl) => {
      const interval = setInterval(async () => {
        try {
          const token = localStorage.getItem("documind_access_token");
          const res = await fetch(`${API_URL}${purl}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          
          const data = await res.json();
          updateTask(tid, { ...data, updated_at: Date.now() });
          
          if (["complete", "failed", "cancelled"].includes(data.status)) {
            clearInterval(interval);
            delete pollRefs.current[tid];
          }
        } catch (err) {
          console.warn(`Polling error for ${tid}:`, err);
        }
      }, POLL_MS);
      pollRefs.current[tid] = interval;
    };

    // Start with WebSocket, fallback to polling
    connectWebSocket();
  }, [updateTask, tasks]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      Object.values(wsRefs.current).forEach(ws => {
        if (ws.readyState === WebSocket.OPEN) ws.close();
      });
      Object.values(pollRefs.current).forEach(clearInterval);
      Object.values(reconnectTimers.current).forEach(clearTimeout);
    };
  }, []);

  const upload = useCallback(async (file, options = {}) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("priority", options.priority || "default");
    formData.append("tags", (options.tags || []).join(","));

    const headers = {};
    const token = localStorage.getItem("documind_access_token");
    if (token) headers["Authorization"] = `Bearer ${token}`;
    headers["X-Correlation-ID"] = `ingest_${Date.now()}`;

    const res = await fetch(`${API_URL}/api/v1/ingest`, {
      method: "POST",
      headers,
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    startTracking(data.task_id, data.filename, data.ws_url, data.poll_url);
    return data;
  }, [startTracking]);

  const cancel = useCallback((task_id) => {
    const ws = wsRefs.current[task_id];
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    if (pollRefs.current[task_id]) clearInterval(pollRefs.current[task_id]);
    if (reconnectTimers.current[task_id]) clearTimeout(reconnectTimers.current[task_id]);
    
    updateTask(task_id, { 
      status: "cancelled", 
      progress: 0,
      message: "Cancelled by user",
      cancelled_at: Date.now(),
    });
  }, [updateTask]);

  const getTask = useCallback((task_id) => tasks[task_id], [tasks]);
  const getActiveTasks = useCallback(() => 
    Object.values(tasks).filter(t => !["complete", "failed", "cancelled"].includes(t.status)), 
    [tasks]
  );

  return {
    tasks,
    upload,
    cancel,
    getTask,
    getActiveTasks,
    STAGE_LABELS,
    STAGE_COLORS,
  };
}