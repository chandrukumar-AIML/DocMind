/**
 * Global app state via Zustand.
 *
 * Replaces the remaining inline useState calls in App.jsx that need to be
 * accessible across multiple sibling components without prop-drilling:
 *   - selectedFile   → Sidebar, Topbar, ChatInput, PDFViewer, ChatWindow
 *   - queryMode      → Topbar, ChatInput, ChatWindow, AgentStepsPanel
 *   - showCompare    → Topbar, DocCompare
 *   - showPdfViewer  → Sidebar, PDFViewer panel
 *
 * Hooks that only serve a single component (docBrief, agentSteps, extraction)
 * remain as local custom hooks — global state only where sharing is needed.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

const useAppStore = create(
  devtools(
    (set) => ({
      // ── Document selection ─────────────────────────────────────────────
      selectedFile: null,
      setSelectedFile: (file) => set({ selectedFile: file }, false, "setSelectedFile"),
      clearSelectedFile: () => set({ selectedFile: null }, false, "clearSelectedFile"),

      // ── Query mode: "rag" | "agent" | "graph" ─────────────────────────
      queryMode: "rag",
      setQueryMode: (mode) => set({ queryMode: mode }, false, "setQueryMode"),

      // ── Panel visibility ───────────────────────────────────────────────
      showCompare: false,
      openCompare:  () => set({ showCompare: true  }, false, "openCompare"),
      closeCompare: () => set({ showCompare: false }, false, "closeCompare"),

      showPdfViewer: false,
      openPdfViewer:  () => set({ showPdfViewer: true  }, false, "openPdfViewer"),
      closePdfViewer: () => set({ showPdfViewer: false }, false, "closePdfViewer"),
      togglePdfViewer: () => set((s) => ({ showPdfViewer: !s.showPdfViewer }), false, "togglePdfViewer"),
    }),
    { name: "DocMind" }
  )
);

export default useAppStore;
