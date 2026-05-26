// frontend/src/components/TableViewer.jsx
// DVMELTSS-FIX: V - Validate, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useCallback } from "react";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

function MarkdownTable({ markdown }) {
  if (!markdown) return null;
  const lines = markdown.trim().split("\n");
  if (lines.length < 3) return <pre className="text-xs whitespace-pre-wrap">{markdown}</pre>;

  const headers = lines[0].split("|").map(c => c.trim()).filter(Boolean);
  const rows = lines.slice(2).map(l =>
    l.split("|").map(c => c.trim()).filter(Boolean)
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-gray-100 dark:bg-gray-800">
            {headers.map((h, i) => (
              <th key={i} className="border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-left font-medium text-gray-700 dark:text-gray-300">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className={ri % 2 === 0 ? "" : "bg-gray-50 dark:bg-gray-800/30"}>
              {row.map((cell, ci) => (
                <td key={ci} className="border border-gray-200 dark:border-gray-700 px-3 py-1 text-gray-700 dark:text-gray-300">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TableViewer({ tableId, summary, tableType, rowCount, colCount, API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [expanded, setExpanded] = useState(false);
  const [tableData, setTableData] = useState(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);

  const loadTable = useCallback(async () => {
    if (tableData) { 
      setExpanded(e => !e); 
      return; 
    }
    setLoading(true);
    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(`${API_URL}/api/v1/extraction/table/${tableId}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      const data = await res.json();
      setTableData(data);
      setExpanded(true);
    } catch (err) {
      toast.error(`Failed to load table: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [tableData, tableId, API_URL]);

  const askQuestion = useCallback(async () => {
    if (!question.trim() || !tableData) return;
    setLoading(true);
    setAnswer("");
    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(
        `${API_URL}/api/v1/extraction/table/${tableId}/query`,
        {
          method: "POST",
          headers: { 
            "Content-Type": "application/json",
            "Authorization": token ? `Bearer ${token}` : "",
          },
          body: JSON.stringify({ operation: "describe", question }),
        }
      );
      const data = await res.json();
      setAnswer(data.answer || "No answer.");
    } catch (err) {
      toast.error(`Table query failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [question, tableData, tableId, API_URL]);

  const TYPE_BADGE = {
    financial:  "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
    schedule:   "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    comparison: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
    data:       "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300",
  };

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
      {/* Header */}
      <button
        onClick={loadTable}
        disabled={loading}
        className="w-full flex items-center gap-3 px-4 py-3 bg-gray-50 dark:bg-gray-800/50 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-left focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-expanded={expanded}
        aria-controls={`table-content-${tableId}`}
      >
        <span className="text-lg" aria-hidden="true">⊞</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
              Table
            </span>
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${TYPE_BADGE[tableType] || TYPE_BADGE.data}`}>
              {tableType}
            </span>
            <span className="text-xs text-gray-400">
              {rowCount}r × {colCount}c
            </span>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">
            {summary}
          </p>
        </div>
        <span className="text-gray-400 text-xs" aria-hidden="true">
          {loading ? "⋯" : expanded ? "▲" : "▼"}
        </span>
      </button>

      {/* Expanded content */}
      {expanded && tableData && (
        <div id={`table-content-${tableId}`} className="p-4 space-y-3 border-t border-gray-200 dark:border-gray-700">
          <MarkdownTable markdown={tableData.markdown} />

          {/* Ask a question about the table */}
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && askQuestion()}
              placeholder="Ask about this table..."
              className="flex-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
              aria-label="Ask a question about this table"
            />
            <button
              onClick={askQuestion}
              disabled={loading || !question.trim()}
              className="text-xs px-3 py-1.5 rounded-lg bg-blue-500 hover:bg-blue-600 disabled:opacity-50 text-white transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Ask question about table"
            >
              Ask
            </button>
          </div>

          {answer && (
            <div className="text-xs p-2 rounded-lg bg-blue-50 dark:bg-blue-950/30 text-blue-800 dark:text-blue-200">
              {answer}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

TableViewer.propTypes = {
  tableId: PropTypes.string.isRequired,
  summary: PropTypes.string,
  tableType: PropTypes.string,
  rowCount: PropTypes.number,
  colCount: PropTypes.number,
  API_URL: PropTypes.string,
};
