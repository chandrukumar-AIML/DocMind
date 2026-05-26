// frontend/src/components/PDFViewer.jsx
// DVMELTSS-FIX: A - Accessibility, E - Error handling, P - Performance
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useCallback, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import PropTypes from "prop-types";

// Required: configure PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.js`;

const HIGHLIGHT_COLORS = {
  green:  "rgba(34, 197, 94,  0.35)",
  yellow: "rgba(234, 179, 8,  0.35)",
  red:    "rgba(239, 68, 68,  0.35)",
};

function ConfidenceBadge({ score, color }) {
  const pct = Math.round(score * 100);
  const colors = {
    green:  "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
    yellow: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    red:    "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  };
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${colors[color] || colors.yellow}`}>
      {pct}% confidence
    </span>
  );
}

export function PDFViewer({ sourceFile, initialPage = 1, citations = [], API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [numPages, setNumPages] = useState(null);
  const [currentPage, setCurrentPage] = useState(initialPage);
  const [pageCitations, setPageCitations] = useState([]);
  const [allCitations, setAllCitations] = useState([]);
  const [loadingCitations, setLoadingCitations] = useState(false);
  const [scale, setScale] = useState(1.2);
  const [pdfError, setPdfError] = useState(null);

  // Build PDF URL
  const pdfUrl = sourceFile
    ? `${API_URL}/api/v1/documents/${encodeURIComponent(sourceFile)}/file`
    : null;

  // Load citations for this document
  useEffect(() => {
    if (!sourceFile) return;
    setLoadingCitations(true);
    fetch(
      `${API_URL}/api/v1/provenance/documents/${encodeURIComponent(sourceFile)}/citations?limit=100`
    )
      .then(r => r.json())
      .then(data => {
        setAllCitations(data.citations || []);
      })
      .catch(() => {})
      .finally(() => setLoadingCitations(false));
  }, [sourceFile, API_URL]);

  useEffect(() => {
    if (citations.length > 0) {
      setAllCitations(citations);
    }
  }, [citations]);

  // Filter citations for current page
  useEffect(() => {
    const forPage = allCitations.filter(
      c => c.page_number === currentPage - 1
    );
    setPageCitations(forPage);
  }, [currentPage, allCitations]);

  const onDocumentLoadSuccess = useCallback(({ numPages }) => {
    setNumPages(numPages);
  }, []);

  const onDocumentLoadError = useCallback((err) => {
    console.error("PDF load error:", err);
    setPdfError(err.message || "Failed to load PDF");
  }, []);

  const goToPage = useCallback((page) => {
    setCurrentPage(Math.max(1, Math.min(page, numPages || 1)));
  }, [numPages]);

  const jumpToPage = useCallback((pageDisplay) => {
    goToPage(pageDisplay);
  }, [goToPage]);

  const handleZoomIn = useCallback(() => {
    setScale(s => Math.min(2.5, s + 0.2));
  }, []);

  const handleZoomOut = useCallback(() => {
    setScale(s => Math.max(0.5, s - 0.2));
  }, []);

  if (!pdfUrl) {
    return (
      <div 
        className="flex items-center justify-center h-48 text-gray-400 text-sm"
        role="status"
        aria-live="polite"
      >
        No document selected
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full" role="region" aria-label="PDF viewer">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex-shrink-0">
        <button
          onClick={() => goToPage(currentPage - 1)}
          disabled={currentPage <= 1}
          className="text-xs px-2 py-1 rounded bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
          aria-label="Previous page"
        >
          ←
        </button>
        <span className="text-xs text-gray-600 dark:text-gray-400" aria-live="polite">
          {currentPage} / {numPages || "?"}
        </span>
        <button
          onClick={() => goToPage(currentPage + 1)}
          disabled={currentPage >= (numPages || 1)}
          className="text-xs px-2 py-1 rounded bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
          aria-label="Next page"
        >
          →
        </button>

        <div className="ml-auto flex items-center gap-1">
          <button 
            onClick={handleZoomOut}
            className="text-xs px-2 py-1 rounded bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Zoom out"
          >
            −
          </button>
          <span className="text-xs text-gray-500 w-10 text-center">
            {Math.round(scale * 100)}%
          </span>
          <button 
            onClick={handleZoomIn}
            className="text-xs px-2 py-1 rounded bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Zoom in"
          >
            +
          </button>
        </div>

        {pageCitations.length > 0 && (
          <span className="text-xs text-blue-500 dark:text-blue-400 ml-2">
            {pageCitations.length} citation{pageCitations.length > 1 ? "s" : ""} on this page
          </span>
        )}

        {loadingCitations && (
          <span className="text-xs text-gray-400 ml-2" aria-live="polite">
            Loading citations...
          </span>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* PDF canvas */}
        <div className="flex-1 overflow-auto bg-gray-100 dark:bg-gray-900 p-4">
          {pdfError ? (
            <div className="p-4 text-center text-sm text-red-500" role="alert">
              Failed to load PDF: {pdfError}
              <button 
                onClick={() => { setPdfError(null); window.location.reload(); }}
                className="ml-2 text-blue-500 underline focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
              >
                Retry
              </button>
            </div>
          ) : (
            <Document
              file={pdfUrl}
              onLoadSuccess={onDocumentLoadSuccess}
              onLoadError={onDocumentLoadError}
              className="flex flex-col items-center"
              loading={<div className="text-gray-400 text-sm">Loading PDF...</div>}
              error={<div className="text-red-500 text-sm">Failed to load PDF</div>}
            >
              <div className="relative">
                <Page
                  pageNumber={currentPage}
                  scale={scale}
                  renderTextLayer={true}
                  renderAnnotationLayer={false}
                  loading={<div className="text-gray-400 text-sm">Loading page...</div>}
                />
                {/* Citation highlight overlays */}
                {pageCitations.map((cit, i) => (
                  cit.char_offset_start != null && (
                    <CitationHighlight
                      key={cit.citation_id}
                      citation={cit}
                      index={i}
                      scale={scale}
                    />
                  )
                ))}
              </div>
            </Document>
          )}
        </div>

        {/* Citation sidebar */}
        {pageCitations.length > 0 && (
          <div className="w-56 border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-y-auto flex-shrink-0">
            <div className="p-3">
              <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">
                Page citations
              </p>
              <div className="space-y-3">
                {pageCitations.map((cit, i) => (
                  <div 
                    key={cit.citation_id}
                    className="p-2 rounded-lg border border-gray-200 dark:border-gray-700 text-xs cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    tabIndex={0}
                    onClick={() => jumpToPage(cit.page_display)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        jumpToPage(cit.page_display);
                      }
                    }}
                    role="button"
                    aria-label={`Jump to citation ${i + 1} on page ${cit.page_display}`}
                  >
                    <div className="flex items-center gap-1 mb-1">
                      <span
                        className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: HIGHLIGHT_COLORS[cit.highlight_color]?.replace("0.35", "0.9") }}
                        aria-hidden="true"
                      />
                      <ConfidenceBadge
                        score={cit.confidence_score}
                        color={cit.highlight_color}
                      />
                    </div>
                    <p className="text-gray-600 dark:text-gray-400 line-clamp-3 leading-relaxed">
                      {cit.chunk_text}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Page thumbnail strip */}
      {numPages && numPages > 1 && (
        <div className="flex gap-1 px-3 py-2 border-t border-gray-200 dark:border-gray-700 overflow-x-auto bg-gray-50 dark:bg-gray-800 flex-shrink-0">
          {Array.from({ length: Math.min(numPages, 20) }, (_, i) => {
            const pg = i + 1;
            const hasCitation = allCitations.some(c => c.page_number === pg - 1);
            return (
              <button
                key={pg}
                onClick={() => goToPage(pg)}
                className={`
                  relative flex-shrink-0 w-8 h-8 rounded text-xs font-medium
                  transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500
                  ${currentPage === pg
                    ? "bg-blue-500 text-white"
                    : "bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600"
                  }
                `}
                aria-label={`Go to page ${pg}`}
                aria-current={currentPage === pg ? "page" : undefined}
              >
                {pg}
                {hasCitation && (
                  <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-amber-400" aria-hidden="true" />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Highlight overlay positioned over cited text on the PDF page
function CitationHighlight({ citation, index, scale }) {
  const color = HIGHLIGHT_COLORS[citation.highlight_color] || HIGHLIGHT_COLORS.yellow;

  // Simplified: show a marker at top-right with color indicator
  // Full implementation requires PDF.js text layer coordinate mapping
  return (
    <div
      className="absolute top-2 right-2 z-10 text-xs px-1.5 py-0.5 rounded-full font-medium text-white"
      style={{
        backgroundColor: color.replace("0.35", "0.9"),
        top: `${20 + index * 28 * scale}px`,
      }}
      title={citation.chunk_text}
      aria-label={`Citation ${index + 1}`}
    >
      {index + 1}
    </div>
  );
}

PDFViewer.propTypes = {
  sourceFile: PropTypes.string,
  initialPage: PropTypes.number,
  citations: PropTypes.arrayOf(PropTypes.shape({
    citation_id: PropTypes.string,
    page_number: PropTypes.number,
    page_display: PropTypes.number,
    chunk_text: PropTypes.string,
    confidence_score: PropTypes.number,
    highlight_color: PropTypes.oneOf(["green", "yellow", "red"]),
    char_offset_start: PropTypes.number,
  })),
  API_URL: PropTypes.string,
};
