import { create } from "zustand";
import { devtools } from "zustand/middleware";

export type QueryMode = "rag" | "agent" | "graph";

interface AppState {
  // Document selection
  selectedFile: string | null;
  setSelectedFile: (file: string | null) => void;
  clearSelectedFile: () => void;
  // Query mode
  queryMode: QueryMode;
  setQueryMode: (mode: QueryMode) => void;
  // Panel: document compare
  showCompare: boolean;
  openCompare: () => void;
  closeCompare: () => void;
  // Panel: PDF viewer
  showPdfViewer: boolean;
  openPdfViewer: () => void;
  closePdfViewer: () => void;
  togglePdfViewer: () => void;
}

const useAppStore = create<AppState>()(
  devtools(
    (set) => ({
      selectedFile: null,
      setSelectedFile: (file) => set({ selectedFile: file }, false, "setSelectedFile"),
      clearSelectedFile: () => set({ selectedFile: null }, false, "clearSelectedFile"),

      queryMode: "rag",
      setQueryMode: (mode) => set({ queryMode: mode }, false, "setQueryMode"),

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
