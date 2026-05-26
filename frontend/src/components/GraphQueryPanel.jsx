// frontend/src/components/GraphQueryPanel.jsx
// DVMELTSS-FIX: V - Validate, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useCallback } from "react";
import { GraphViewer } from "./GraphViewer";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const MODE_OPTIONS = [
  { value: "auto",   label: "Auto",   desc: "Smart routing" },
  { value: "hybrid", label: "Hybrid", desc: "Graph + Vector" },
  { value: "graph",  label: "Graph",  desc: "Neo4j only" },
  { value: "vector", label: "Vector", desc: "ChromaDB only" },
];

export function GraphQueryPanel({ API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState("auto");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleSubmit = useCallback(async () => {
    if (!question.trim() || loading) return;
    
    setLoading(true);
    setResult(null);
    setError(null);

    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(`${API_URL}/api/v1/graph/query`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "Authorization": token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({ 
          question, 
          mode, 
          top_k: 10,
          include_visualization: true,
        }),
      });
      
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      
      const data = await res.json();
      setResult(data);
      
    } catch (err) {
      const errMsg = err.message || "Graph query failed";
      setError(errMsg);
      toast.error(errMsg);
    } finally {
      setLoading(false);
    }
  }, [question, mode, loading, API_URL]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  return (
    <div className="flex flex-col gap-4" role="region" aria-label="Graph query interface">
      {/* Query input */}
      <div className="flex flex-col gap-2">
        <label 
          htmlFor="graph-question"
          className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide"
        >
          Graph Query
        </label>
        <textarea
          id="graph-question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about relationships... e.g. 'Which companies are involved in contracts containing liability clauses?'"
          rows={2}
          disabled={loading}
          aria-label="Enter your graph query"
          className="resize-none rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50"
        />
      </div>

      {/* Mode selector */}
      <div className="flex gap-2" role="radiogroup" aria-label="Retrieval mode">
        {MODE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setMode(opt.value)}
            role="radio"
            aria-checked={mode === opt.value}
            className={`
              flex-1 text-xs py-1.5 px-2 rounded-lg border transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500
              ${mode === opt.value
                ? "border-purple-500 bg-purple-50 dark:bg-purple-950/40 text-purple-600 dark:text-purple-300"
                : "border-gray-200 dark:border-gray-700 text-gray-500 hover:border-gray-300"
              }
            `}
          >
            <div className="font-medium">{opt.label}</div>
            <div className="text-gray-400 text-[10px]">{opt.desc}</div>
          </button>
        ))}
      </div>

      {/* Submit button */}
      <button
        onClick={handleSubmit}
        disabled={loading || !question.trim()}
        aria-label={loading ? "Querying graph" : "Run graph query"}
        className="w-full py-2 rounded-xl bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500"
      >
        {loading ? "Querying graph..." : "Run Graph Query"}
      </button>

      {/* Error display */}
      {error && (
        <div className="p-3 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400" role="alert">
          {error}
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="flex flex-col gap-4" aria-live="polite">
          {/* Mode badge */}
          <div className="flex items-center gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-purple-100 dark:bg-purple-950/40 text-purple-600 dark:text-purple-300 font-medium">
              {result.retrieval_mode} retrieval
            </span>
            <span className="text-xs text-gray-400">
              {result.vector_chunks} chunks · {result.graph_records} graph nodes · {result.latency_seconds?.toFixed(2)}s
            </span>
          </div>

          {/* Answer */}
          <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-800/50">
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Answer</p>
            <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">
              {result.answer}
            </p>
          </div>

          {/* Graph visualization */}
          {result.visualization?.nodes?.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">
                Knowledge Graph ({result.visualization.nodes.length} nodes)
              </p>
              <GraphViewer
                nodes={result.visualization.nodes}
                edges={result.visualization.edges}
                height={350}
              />
            </div>
          )}

          {/* Graph context text */}
          {result.graph_context && (
            <details className="rounded-xl border border-gray-200 dark:border-gray-700">
              <summary className="px-4 py-2 text-xs font-medium text-gray-500 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300">
                Raw graph context
              </summary>
              <pre className="px-4 pb-4 text-xs text-gray-500 dark:text-gray-400 whitespace-pre-wrap overflow-auto max-h-40">
                {result.graph_context}
              </pre>
            </details>
          )}

          {/* Citations */}
          {result.citations?.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-gray-400 uppercase tracking-wide">Sources</p>
              {result.citations.map((c, i) => (
                <div 
                  key={`${c.source_file}-${c.page_number}-${i}`} 
                  className="text-xs p-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400"
                >
                  <span className="font-medium">{c.source_file}</span>
                  {" · "}p.{c.page_number}
                  <p className="mt-1 text-gray-500 line-clamp-2">{c.chunk_text}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

GraphQueryPanel.propTypes = {
  API_URL: PropTypes.string,
};