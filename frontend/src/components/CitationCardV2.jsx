// frontend/src/components/CitationCardV2.jsx
// DVMELTSS-FIX: A - Accessibility, V - Validate, M - Modular
// ASCALE-FIX: S - Separation
import { useState, useCallback } from "react";
import PropTypes from "prop-types";

const COLOR_STYLES = {
  green:  {
    border: "border-green-200 dark:border-green-800",
    badge:  "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
    dot:    "bg-green-400",
    label:  "High confidence",
  },
  yellow: {
    border: "border-amber-200 dark:border-amber-800",
    badge:  "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    dot:    "bg-amber-400",
    label:  "Medium confidence",
  },
  red:    {
    border: "border-red-200 dark:border-red-800",
    badge:  "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
    dot:    "bg-red-400",
    label:  "Low confidence — verify",
  },
};

export function CitationCardV2({ citation, index, onViewInDocument }) {
  const [expanded, setExpanded] = useState(false);

  const confidence = citation.confidence_score ?? citation.rerank_score ?? 0;
  const color = citation.highlight_color ||
    (confidence >= 0.85 ? "green" : confidence >= 0.60 ? "yellow" : "red");
  const styles = COLOR_STYLES[color] || COLOR_STYLES.yellow;
  const pct = Math.round(confidence * 100);

  const handleToggle = useCallback(() => {
    setExpanded(e => !e);
  }, []);

  const handleKeyDown = useCallback((e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleToggle();
    }
  }, [handleToggle]);

  const handleViewClick = useCallback((e) => {
    e.stopPropagation();
    onViewInDocument?.(citation);
  }, [citation, onViewInDocument]);

  return (
    <div 
      className={`rounded-lg border ${styles.border} bg-white dark:bg-gray-900 text-sm overflow-hidden`}
      role="article"
      aria-label={`Citation ${index + 1}: ${citation.source_file}`}
    >
      {/* Header - clickable to expand */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
        onClick={handleToggle}
        onKeyDown={handleKeyDown}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls={`citation-content-${index}`}
      >
        <span className="flex-shrink-0 w-5 h-5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-xs font-medium flex items-center justify-center" aria-hidden="true">
          {index + 1}
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-medium text-gray-700 dark:text-gray-300 truncate max-w-[150px] text-xs">
              {citation.source_file}
            </span>
            <span className="text-gray-400 text-xs">p.{citation.page_display ?? (citation.page_number + 1)}</span>
            {citation.block_type && (
              <span className="text-xs text-gray-400">{citation.block_type}</span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${styles.badge}`}>
            {pct}%
          </span>
          <span className="text-gray-400 text-xs" aria-hidden="true">{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div 
          id={`citation-content-${index}`}
          className="px-3 pb-3 space-y-2 border-t border-gray-100 dark:border-gray-800"
        >
          {/* Chunk text */}
          <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed mt-2">
            {citation.chunk_text}
          </p>

          {/* Confidence label */}
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${styles.dot}`} aria-hidden="true" />
            <span className="text-xs text-gray-400">{styles.label}</span>
          </div>

          {/* View in document button */}
          {onViewInDocument && (
            <button
              onClick={handleViewClick}
              className="w-full text-xs py-1 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              📄 View in document → p.{citation.page_display ?? (citation.page_number + 1)}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

CitationCardV2.propTypes = {
  citation: PropTypes.shape({
    source_file: PropTypes.string.isRequired,
    page_number: PropTypes.number,
    page_display: PropTypes.number,
    block_type: PropTypes.string,
    chunk_text: PropTypes.string,
    confidence_score: PropTypes.number,
    rerank_score: PropTypes.number,
    highlight_color: PropTypes.oneOf(["green", "yellow", "red"]),
  }).isRequired,
  index: PropTypes.number.isRequired,
  onViewInDocument: PropTypes.func,
};