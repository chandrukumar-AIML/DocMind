// frontend/src/hooks/useStreamQuery.js
// DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
// ASCALE-FIX: S - Separation, C - Coupling, E - Error propagation
// FRONTEND-FIX: S - Session, C - Correlation, W - Workspace

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "../api/client";
import toast from "react-hot-toast";
// [OK] FIXED: Import shared constant — no more magic string literals for storage key
import { WORKSPACE_STORAGE_KEY, DEFAULT_WORKSPACE_ID } from "../utils/constants";

// ════════════════════════════════════════════════════════════════════════
// CONFIG
// ════════════════════════════════════════════════════════════════════════
const SESSION_STORAGE_KEY = "documind-session-id";
const SESSION_ID_PATTERN = /^chat_[a-zA-Z0-9]{16,32}$/;
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 500;

// Generate cryptographically secure session ID
function createSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `chat_${crypto.randomUUID().replace(/-/g, "")}`;
  }
  // Fallback for older browsers
  const arr = new Uint8Array(16);
  crypto.getRandomValues(arr);
  return `chat_${Array.from(arr, b => b.toString(16).padStart(2, '0')).join('')}`;
}

function getOrCreateSessionId() {
  if (typeof window === "undefined") return createSessionId();
  
  const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  if (existing && SESSION_ID_PATTERN.test(existing)) {
    return existing;
  }
  const nextSessionId = createSessionId();
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, nextSessionId);
  return nextSessionId;
}

// Generate unique message ID
let _msgCounter = 0;
const nextId = () => `msg_${Date.now()}_${++_msgCounter}`;

export function useStreamQuery() {
  const [messages, setMessages] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const retryCountRef = useRef(0);
  const sessionIdRef = useRef(getOrCreateSessionId());

  // Hydrate session history on mount
  useEffect(() => {
    let cancelled = false;

    const hydrateSessionHistory = async () => {
      try {
        const workspaceId = localStorage.getItem(WORKSPACE_STORAGE_KEY) || DEFAULT_WORKSPACE_ID;
        const data = await api.queryHistory(sessionIdRef.current, workspaceId);
        if (cancelled || !Array.isArray(data.history) || data.history.length === 0) return;
        
        setMessages(
          data.history.map((message) => {
            const content = message.content;
            return {
              ...message,
              content: typeof content === "string" ? content : (content ? JSON.stringify(content) : ""),
              id: nextId(),
              citations: message.citations || [],
              latency: message.latency_seconds || null,
              streaming: false,
            };
          })
        );
      } catch (err) {
        // History restore is best-effort only — suppress 401 (expected when token expires)
        const status = err?.response?.status;
        if (status !== 401 && status !== 403) {
          console.warn("Failed to hydrate session history:", err);
        }
      }
    };

    hydrateSessionHistory();
    return () => { cancelled = true; };
  }, []);

  const submit = useCallback(
    async ({
      question,
      filterSourceFile = null,
      filterDocumentType = null,
      topKRetrieve = 20,
      topKRerank = 3,
      workspaceId = null,
      // Accept both camelCase and snake_case correlation ids (App.jsx uses snake_case)
      correlationId = null,
      correlation_id = null,
      // query mode: "rag" | "agent" | "graph"
      mode = "rag",
      onToken,
      onCitations,
      onDone,
      onError,
    }) => {
      if (!question.trim() || isStreaming) return;

      // Capture history BEFORE state update to avoid stale closure
      const history = messages
        .filter((m) => !m.streaming && m.content)
        .map((m) => ({ role: m.role === "human" ? "user" : m.role, content: m.content }));

      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const corrId = correlationId || correlation_id || `req-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const userMsgId = nextId();
      const assistantId = nextId();
      const wsId = workspaceId || localStorage.getItem(WORKSPACE_STORAGE_KEY) || DEFAULT_WORKSPACE_ID;

      setMessages((prev) => [
        ...prev,
        { role: "human", content: question, id: userMsgId, correlation_id: corrId },
        {
          role: "assistant",
          content: "",
          citations: [],
          latency: null,
          id: assistantId,
          streaming: true,
          correlation_id: corrId,
        },
      ]);
      setIsStreaming(true);
      setError(null);
      retryCountRef.current = 0;

      const runQuery = async () => {
        try {
          const request = {
            question,
            session_id: sessionIdRef.current,
            chat_history: history,
            filter_source_file: filterSourceFile,
            filter_document_type: filterDocumentType,
            top_k_retrieve: topKRetrieve,
            top_k_rerank: topKRerank,
            workspace_id: wsId,
            correlation_id: corrId,
            mode,
          };

          for await (const rawToken of api.queryStream(request, controller.signal)) {
            // Parse structured JSON events (Phase 8/12 protocol)
            let event;
            try {
              event = typeof rawToken === "string" ? JSON.parse(rawToken) : rawToken;
            } catch {
              // Backward compat: plain text token
              event = { type: "token", content: rawToken };
            }

            if (event.type === "token") {
              const tok = typeof event.content === "string" ? event.content : String(event.content ?? "");
              if (onToken) onToken(tok);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: m.content + tok } : m
                )
              );
            } else if (event.type === "citations") {
              if (onCitations) onCitations(event.content);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, citations: event.content } : m
                )
              );
            } else if (event.type === "status" || event.type === "step") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, statusStep: event.content } : m
                )
              );
            } else if (event.type === "done") {
              if (onDone) onDone(event);
              const latency = event.latency_seconds ?? (event.latency_ms ? event.latency_ms / 1000 : null);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        streaming: false,
                        latency,
                        correlation_id: corrId,
                        low_confidence: event.low_confidence ?? false,
                        retrieved_count: event.retrieved_count ?? 0,
                        reranked_count: event.reranked_count ?? 0,
                        statusStep: null,
                      }
                    : m
                )
              );
            } else if (event.type === "error") {
              const raw = event.message ?? event.detail ?? "Query failed";
              const errMsg = typeof raw === "string"
                ? raw
                : Array.isArray(raw)
                  ? raw.map(e => e?.msg || e?.message || JSON.stringify(e)).join("; ")
                  : (raw?.msg || raw?.message || raw?.detail || JSON.stringify(raw));
              if (onError) onError(errMsg);
              toast.error(`${errMsg}${event.reference_id ? ` (Ref: ${event.reference_id})` : ""}`);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, streaming: false, error: String(errMsg) } : m
                )
              );
            }
          }
        } catch (err) {
          if (err.name === "AbortError") return;
          
          // Retry on transient errors
          if ((err.message.includes("429") || err.message.includes("503")) && retryCountRef.current < MAX_RETRIES) {
            retryCountRef.current++;
            const delay = RETRY_DELAY_MS * Math.pow(2, retryCountRef.current - 1);
            await new Promise(resolve => setTimeout(resolve, delay));
            return runQuery();
          }
          
          const msg = err.message || "Stream failed";
          setError(msg);
          if (onError) onError(msg);
          toast.error(msg);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, streaming: false, error: msg } : m
            )
          );
        } finally {
          setIsStreaming(false);
        }
      };

      await runQuery();
    },
    [messages, isStreaming]
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.streaming ? { ...m, streaming: false, cancelled: true } : m))
    );
  }, []);

  const clear = useCallback(() => {
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
    sessionIdRef.current = getOrCreateSessionId();
    setMessages([]);
    setError(null);
  }, []);

  const newConversation = useCallback(() => {
    clear();
    sessionIdRef.current = getOrCreateSessionId();
  }, [clear]);

  const loadSession = useCallback(async (sessionId) => {
    if (!sessionId) return;
    setMessages([]);
    setError(null);
    setIsStreaming(false);
    sessionIdRef.current = sessionId;
    try {
      const workspaceId = localStorage.getItem(WORKSPACE_STORAGE_KEY) || DEFAULT_WORKSPACE_ID;
      const data = await api.queryHistory(sessionId, workspaceId);
      if (!Array.isArray(data.history) || data.history.length === 0) return;
      setMessages(
        data.history.map((message) => {
          const content = message.content;
          return {
            ...message,
            content: typeof content === "string" ? content : (content ? JSON.stringify(content) : ""),
            id: nextId(),
            citations: message.citations || [],
            latency: message.latency_seconds || null,
            streaming: false,
          };
        })
      );
    } catch {
      // Best-effort: session may have expired
    }
  }, []);

  return {
    messages,
    isStreaming,
    error,
    submit,
    cancel,
    clear,
    newConversation,
    loadSession,
    sessionId: sessionIdRef.current,
  };
}
