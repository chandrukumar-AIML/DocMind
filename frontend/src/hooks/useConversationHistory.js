// frontend/src/hooks/useConversationHistory.js
import { useState, useCallback } from "react";

const STORAGE_KEY = "documind_conversations";
const MAX_HISTORY = 50;

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveHistory(hist) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(hist.slice(0, MAX_HISTORY)));
  } catch {}
}

export function useConversationHistory() {
  const [conversations, setConversations] = useState(loadHistory);

  const addOrUpdate = useCallback((sessionId, title, messageCount) => {
    if (!sessionId || !title) return;
    setConversations(prev => {
      const exists = prev.findIndex(c => c.id === sessionId);
      const entry = { id: sessionId, title: title.slice(0, 80), timestamp: Date.now(), messageCount };
      const next = exists >= 0
        ? prev.map((c, i) => (i === exists ? entry : c))
        : [entry, ...prev];
      saveHistory(next);
      return next;
    });
  }, []);

  const remove = useCallback((sessionId) => {
    setConversations(prev => {
      const next = prev.filter(c => c.id !== sessionId);
      saveHistory(next);
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    setConversations([]);
    try { localStorage.removeItem(STORAGE_KEY); } catch {}
  }, []);

  return { conversations, addOrUpdate, remove, clearAll };
}
