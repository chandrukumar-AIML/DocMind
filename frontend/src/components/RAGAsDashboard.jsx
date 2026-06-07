// frontend/src/components/RAGAsDashboard.jsx
// DVMELTSS-FIX: M - Modular, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useCallback } from "react";
import toast from "react-hot-toast";
import PropTypes from "prop-types";
import { isDemoMode } from "../api/demo";

const METRIC_CONFIG = {
  faithfulness:      { label: "Faithfulness",       color: "#14B8A6", threshold: 0.75 },
  answer_relevancy:  { label: "Answer Relevancy",   color: "#378ADD", threshold: 0.65 },
  context_precision: { label: "Context Precision",  color: "#1D9E75", threshold: 0.60 },
  context_recall:    { label: "Context Recall",     color: "#EF9F27", threshold: 0.55 },
};

function MetricGauge({ label, value, threshold, color }) {
  const pct = Math.round(value * 100);
  const isBelowAlert = value < threshold;
  const barColor = isBelowAlert ? "#E24B4A" : color;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between items-baseline">
        <span className="text-xs font-medium text-gray-600 dark:text-gray-400">{label}</span>
        <span
          className={`text-sm font-bold ${isBelowAlert ? "text-red-500" : "text-gray-800 dark:text-gray-200"}`}
        >
          {pct}%
          {isBelowAlert && <span className="ml-1 text-xs" aria-hidden="true">⚠️</span>}
        </span>
      </div>
      <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: barColor }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-gray-400">
        <span>0%</span>
        <span className="text-amber-500">threshold {Math.round(threshold * 100)}%</span>
        <span>100%</span>
      </div>
    </div>
  );
}

function SingleEvalPanel({ API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [context, setContext] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleEval = useCallback(async () => {
    if (!question || !answer || !context) {
      toast.error("Fill in question, answer and at least one context");
      return;
    }
    setLoading(true);
    setResult(null);
    if (isDemoMode()) {
      await new Promise(r => setTimeout(r, 800));
      setResult({ faithfulness: 0.88, answer_relevancy: 0.82, context_precision: 0.76, context_recall: 0.69, composite_score: 0.79 });
      setLoading(false);
      return;
    }
    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(`${API_URL}/api/v1/evaluation/sample`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "Authorization": token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({
          question,
          answer,
          contexts: context.split("\n---\n").filter(Boolean),
          ground_truth: "",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setResult(data);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }, [question, answer, context, API_URL]);

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        Evaluate a single response
      </p>
      <textarea 
        value={question} 
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Question..." 
        rows={2}
        className="w-full text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-teal-500"
        aria-label="Enter question to evaluate"
      />
      <textarea 
        value={answer} 
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Generated answer..." 
        rows={3}
        className="w-full text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-teal-500"
        aria-label="Enter generated answer"
      />
      <textarea 
        value={context} 
        onChange={(e) => setContext(e.target.value)}
        placeholder="Context chunks (separate multiple with ---)" 
        rows={3}
        className="w-full text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-teal-500"
        aria-label="Enter context chunks"
      />
      <button 
        onClick={handleEval} 
        disabled={loading}
        className="w-full py-2 rounded-lg bg-teal-600 hover:bg-teal-700 disabled:opacity-50 text-white text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-teal-500"
        aria-label={loading ? "Evaluating" : "Run RAGAs evaluation"}
      >
        {loading ? "Evaluating..." : "Run RAGAs Evaluation"}
      </button>

      {result && (
        <div className="space-y-2 pt-2 border-t border-gray-200 dark:border-gray-700">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Results</p>
          {Object.entries(METRIC_CONFIG).map(([key, cfg]) => (
            <MetricGauge
              key={key}
              label={cfg.label}
              value={result[key] ?? 0}
              threshold={cfg.threshold}
              color={cfg.color}
            />
          ))}
          <div className="mt-2 p-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-center">
            <span className="text-xs text-gray-500">Composite</span>
            <div className="text-lg font-bold text-gray-800 dark:text-gray-200">
              {Math.round((result.composite_score ?? 0) * 100)}%
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PipelinePanel({ API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [domain, setDomain] = useState("general");
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState(null);

  const handleRun = useCallback(async () => {
    setRunning(true);
    if (isDemoMode()) {
      await new Promise(r => setTimeout(r, 1200));
      setLastRun({
        n_samples: 24, mean_faithfulness: 0.86, mean_answer_relevancy: 0.81,
        mean_context_precision: 0.75, mean_context_recall: 0.68,
        faithfulness_alert: false, alerts: [], mlflow_run_id: "demo-run-8f2a", duration_seconds: 12.4,
      });
      toast.success("Evaluation complete. Results logged to MLflow.");
      setRunning(false);
      return;
    }
    try {
      const token = localStorage.getItem("documind_access_token");
      const res = await fetch(`${API_URL}/api/v1/evaluation/run`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "Authorization": token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({ domain, concurrency: 2 }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLastRun(data);
      if (data.faithfulness_alert) {
        toast.error(`Faithfulness alert: ${(data.mean_faithfulness * 100).toFixed(0)}% < 75%`);
      } else {
        toast.success("Evaluation complete. Results logged to MLflow.");
      }
    } catch (err) {
      toast.error(err.message);
    } finally {
      setRunning(false);
    }
  }, [domain, API_URL]);

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
        Automated pipeline
      </p>
      <select 
        value={domain} 
        onChange={(e) => setDomain(e.target.value)}
        className="w-full text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-teal-500"
        aria-label="Select evaluation domain"
      >
        {["general", "legal", "invoice", "medical"].map(d => (
          <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>
        ))}
      </select>
      <button 
        onClick={handleRun} 
        disabled={running}
        className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-label={running ? "Running pipeline" : "Run weekly evaluation"}
      >
        {running ? "Running pipeline..." : "Run Weekly Evaluation"}
      </button>

      {lastRun && (
        <div className="space-y-2 pt-2 border-t border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-gray-500">Pipeline results</p>
            <span className="text-xs text-gray-400">{lastRun.n_samples} samples</span>
          </div>
          {Object.entries(METRIC_CONFIG).map(([key, cfg]) => (
            <MetricGauge
              key={key}
              label={cfg.label}
              value={lastRun[`mean_${key}`] ?? 0}
              threshold={cfg.threshold}
              color={cfg.color}
            />
          ))}
          {lastRun.alerts?.length > 0 && (
            <div className="p-2 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800">
              {lastRun.alerts.map((a, i) => (
                <p key={i} className="text-xs text-red-600 dark:text-red-400">{a}</p>
              ))}
            </div>
          )}
          <p className="text-xs text-gray-400">
            MLflow run: {lastRun.mlflow_run_id || "logged"} | {lastRun.duration_seconds?.toFixed(1)}s
          </p>
        </div>
      )}
    </div>
  );
}

export function RAGAsDashboard({ API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [activeTab, setActiveTab] = useState("single");

  return (
    <div className="flex flex-col gap-4" role="region" aria-label="RAGAs evaluation dashboard">
      {/* Tab selector */}
      <div className="flex gap-1 p-1 rounded-xl bg-gray-100 dark:bg-gray-800" role="tablist">
        {[
          { id: "single",   label: "Single Eval" },
          { id: "pipeline", label: "Pipeline" },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`${tab.id}-panel`}
            className={`
              flex-1 text-xs py-1.5 rounded-lg font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-teal-500
              ${activeTab === tab.id
                ? "bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 shadow-sm"
                : "text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              }
            `}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div 
        id="single-panel" 
        role="tabpanel" 
        aria-labelledby="single-tab"
        hidden={activeTab !== "single"}
      >
        <SingleEvalPanel API_URL={API_URL} />
      </div>
      
      <div 
        id="pipeline-panel" 
        role="tabpanel" 
        aria-labelledby="pipeline-tab"
        hidden={activeTab !== "pipeline"}
      >
        <PipelinePanel API_URL={API_URL} />
      </div>
    </div>
  );
}

RAGAsDashboard.propTypes = {
  API_URL: PropTypes.string,
};