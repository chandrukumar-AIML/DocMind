// frontend/src/hooks/useAgentStream.js
// DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
// ASCALE-FIX: S - Separation, C - Coupling, E - Error propagation
// FRONTEND-FIX: A - Auth, R - Retry, C - Correlation, W - Workspace

import { useState, useCallback, useRef, useEffect } from "react";
import toast from "react-hot-toast";

// ════════════════════════════════════════════════════════════════════════
// CONFIG (Centralized, env-driven)
// ════════════════════════════════════════════════════════════════════════
const API_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 500;

// Generate unique thread ID with crypto fallback
let _threadCounter = 0;
const newThreadId = () => {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `thread_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`;
  }
  return `thread_${Date.now()}_${++_threadCounter}`;
};

// Generate correlation ID for distributed tracing
const newCorrelationId = () => `req-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

export function useAgentStream() {
  const [messages, setMessages] = useState([]);
  const [agentSteps, setAgentSteps] = useState([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [agentMeta, setAgentMeta] = useState(null);
  const [error, setError] = useState(null);
  
  const threadIdRef = useRef(newThreadId());
  const abortRef = useRef(null);
  const retryCountRef = useRef(0);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  const submit = useCallback(async ({ 
    question, 
    workspaceId = "default",
    correlationId = null,
    onStep,
    onToken,
    onCitations,
    onDone,
    onError 
  }) => {
    if (!question.trim() || isStreaming) return;

    // Abort any existing stream
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const corrId = correlationId || newCorrelationId();
    const userMsgId = Date.now();
    const assistantId = Date.now() + 1;

    // Add user message + placeholder assistant message
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "human", content: question, correlation_id: corrId },
      { 
        id: assistantId, 
        role: "assistant", 
        content: "", 
        streaming: true,
        citations: [], 
        agentSteps: [],
        correlation_id: corrId 
      },
    ]);
    setAgentSteps([]);
    setAgentMeta(null);
    setError(null);
    setIsStreaming(true);
    retryCountRef.current = 0;

    const runStream = async () => {
      try {
        const res = await fetch(`${API_URL}/api/v1/agent/query`, {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "Authorization": `Bearer ${localStorage.getItem("auth_token")}`,
            "X-Correlation-ID": corrId,
          },
          body: JSON.stringify({
            question,
            workspace_id: workspaceId,
            thread_id: threadIdRef.current,
            stream: true,
            correlation_id: corrId,
          }),
          signal: controller.signal,
        });

        if (!res.ok) {
          if (res.status === 429 && retryCountRef.current < MAX_RETRIES) {
            // Exponential backoff retry
            retryCountRef.current++;
            const delay = RETRY_DELAY_MS * Math.pow(2, retryCountRef.current - 1);
            await new Promise(resolve => setTimeout(resolve, delay));
            return runStream(); // Retry
          }
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `HTTP ${res.status}`);
        }

        const reader = res.body?.getReader();
        if (!reader) throw new Error("ReadableStream not supported");
        
        const decoder = new TextDecoder("utf-8", { fatal: false });
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
          
          if (done) {
            // Flush remaining buffer
            if (buffer.trim()) {
              const lines = buffer.split("\n");
              for (const line of lines) {
                if (line.startsWith(" ")) processEvent(line.slice(6));
              }
            }
            break;
          }

          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith(" ")) {
              processEvent(line.slice(6));
            }
          }
        }

        // Process SSE event
        function processEvent(payload) {
          if (!payload || payload === "[DONE]") return;
          
          let event;
          try {
            event = JSON.parse(payload);
          } catch {
            // Fallback: treat as raw token
            if (onToken) onToken(payload);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + payload } : m
            ));
            return;
          }

          if (event.type === "step") {
            setAgentSteps((prev) => [...prev, event]);
            if (onStep) onStep(event);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId
                ? { ...m, agentSteps: [...(m.agentSteps || []), event] }
                : m
            ));
          } else if (event.type === "token") {
            if (onToken) onToken(event.content);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + event.content } : m
            ));
          } else if (event.type === "citations") {
            if (onCitations) onCitations(event.content);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, citations: event.content } : m
            ));
          } else if (event.type === "agent_summary") {
            setAgentMeta(event);
          } else if (event.type === "done") {
            if (onDone) onDone(event);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId
                ? { ...m, streaming: false, latency: event.latency_seconds, correlation_id: corrId }
                : m
            ));
          } else if (event.type === "error") {
            const errMsg = event.message || "Agent error";
            if (onError) onError(errMsg);
            toast.error(`Agent error: ${errMsg}`);
            setMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, streaming: false, error: errMsg } : m
            ));
          }
        }

      } catch (err) {
        if (err.name === "AbortError") return;
        
        // Retry on transient errors
        if ((err.message.includes("429") || err.message.includes("503")) && retryCountRef.current < MAX_RETRIES) {
          retryCountRef.current++;
          const delay = RETRY_DELAY_MS * Math.pow(2, retryCountRef.current - 1);
          await new Promise(resolve => setTimeout(resolve, delay));
          return runStream();
        }
        
        const errMsg = err.message || "Agent stream failed";
        setError(errMsg);
        if (onError) onError(errMsg);
        toast.error(errMsg);
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId ? { ...m, streaming: false, error: errMsg } : m
        ));
      } finally {
        setIsStreaming(false);
      }
    };

    await runStream();
  }, [isStreaming]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
    setAgentSteps([]);
    setAgentMeta(null);
    setError(null);
    threadIdRef.current = newThreadId(); // New thread = new conversation memory
  }, []);

  const newConversation = useCallback(() => {
    clear();
    threadIdRef.current = newThreadId();
  }, [clear]);

  return {
    messages,
    agentSteps,
    agentMeta,
    isStreaming,
    error,
    submit,
    cancel,
    clear,
    newConversation,
    threadId: threadIdRef.current,
  };
}