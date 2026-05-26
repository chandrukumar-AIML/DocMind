// frontend/src/components/MonitoringDashboard.jsx
// DVMELTSS-FIX: M - Modular, E - Error handling, A - Async
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useEffect, useCallback } from "react";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const THRESHOLD_CONFIG = {
  confidence_mean:       { threshold: 0.65, label: "Confidence",        higher_better: true  },
  faithfulness_mean:     { threshold: 0.70, label: "Faithfulness",       higher_better: true  },
  context_precision_mean:{ threshold: 0.55, label: "Context Precision",  higher_better: true  },
  latency_ms_p95:        { threshold: 8000, label: "P95 Latency (ms)",   higher_better: false },
  web_search_rate:       { threshold: 0.40, label: "Web Search Rate",    higher_better: false },
};

function StatCard({ label, value, threshold, higherBetter, unit = "" }) {
  if (value === null || value === undefined) {
    return (
      <div className="p-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <p className="text-xs text-gray-400">{label}</p>
        <p className="text-sm text-gray-400 mt-1">No data</p>
      </div>
    );
  }

  const isAlert = higherBetter ? value < threshold : value > threshold;
  const pct = typeof value === "number" && value <= 1.0
    ? `${(value * 100).toFixed(1)}%`
    : typeof value === "number"
    ? `${value.toFixed(0)}${unit}`
    : value;

  return (
    <div className={`
      p-3 rounded-xl border
      ${isAlert
        ? "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/20"
        : "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
      }
    `}>
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-500 dark:text-gray-400">{label}</p>
        {isAlert && <span className="text-xs text-red-500" aria-hidden="true">⚠️</span>}
      </div>
      <p className={`text-lg font-bold mt-1 ${
        isAlert ? "text-red-600 dark:text-red-400" : "text-gray-800 dark:text-gray-200"
      }`}>
        {pct}
      </p>
      <p className="text-[10px] text-gray-400 mt-0.5">
        threshold: {typeof threshold === "number" && threshold <= 1.0
          ? `${(threshold * 100).toFixed(0)}%`
          : threshold}{unit}
      </p>
    </div>
  );
}

function TrendSparkline({ trend, metricKey }) {
  if (!trend || trend.length === 0) return null;

  const values = trend.map(d => d[metricKey]).filter(v => v != null);
  if (values.length === 0) return null;

  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;

  const w = 120, h = 32;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x},${y}`;
  }).join(" ");

  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden="true">
      <polyline
        points={pts}
        fill="none"
        stroke="#7F77DD"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function MonitoringDashboard({ API_URL = import.meta.env?.VITE_API_URL || "" }) {
  const [stats, setStats] = useState(null);
  const [trend, setTrend] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [hours, setHours] = useState(24);

  const authHeader = useCallback(() => {
    const token = localStorage.getItem("documind_access_token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, []);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, trendRes] = await Promise.all([
        fetch(`${API_URL}/api/v1/monitoring/stats?hours=${hours}`, { headers: authHeader() }),
        fetch(`${API_URL}/api/v1/monitoring/trend?days=14`, { headers: authHeader() }),
      ]);
      if (statsRes.ok) setStats(await statsRes.json());
      if (trendRes.ok) {
        const t = await trendRes.json();
        setTrend(t.trend || []);
      }
    } catch (err) {
      toast.error(`Failed to load monitoring data: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, [hours, authHeader, API_URL]);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  const runPipeline = useCallback(async () => {
    setRunning(true);
    try {
      const res = await fetch(
        `${API_URL}/api/v1/monitoring/run?async_mode=false`,
        { method: "POST", headers: authHeader() }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (data.is_healthy) {
        toast.success("Monitoring complete — system healthy");
      } else {
        toast.error(
          `Alerts detected: ${data.quality_alerts?.[0] || "check monitoring panel"}`
        );
      }
      await fetchStats();
    } catch (err) {
      toast.error(`Pipeline failed: ${err.message}`);
    } finally {
      setRunning(false);
    }
  }, [authHeader, fetchStats, API_URL]);

  const cragDist = stats?.crag_action_distribution || {};
  const totalCrag = Object.values(cragDist).reduce((a, b) => a + b, 0) || 1;

  return (
    <div className="space-y-4" role="region" aria-label="RAG monitoring dashboard">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            RAG Monitoring
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            {stats?.query_count ?? "—"} queries in last {hours}h
          </p>
        </div>
        <div className="flex gap-2">
          <select
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            className="text-xs rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 focus:outline-none focus:ring-2 focus:ring-purple-500"
            aria-label="Select time range"
          >
            <option value={6}>6h</option>
            <option value={24}>24h</option>
            <option value={72}>72h</option>
            <option value={168}>7d</option>
          </select>
          <button
            onClick={fetchStats}
            disabled={loading}
            className="text-xs px-2 py-1 rounded-lg border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-purple-500"
            aria-label="Refresh monitoring data"
          >
            ↻
          </button>
          <button
            onClick={runPipeline}
            disabled={running}
            className="text-xs px-3 py-1 rounded-lg bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white transition-colors focus:outline-none focus:ring-2 focus:ring-purple-500"
            aria-label={running ? "Pipeline running" : "Run monitoring pipeline"}
          >
            {running ? "Running..." : "Run pipeline"}
          </button>
        </div>
      </div>

      {loading && (
        <div className="text-xs text-gray-400 animate-pulse text-center py-4" role="status" aria-live="polite">
          Loading monitoring data...
        </div>
      )}

      {stats && (
        <>
          {/* Quality metrics grid */}
          <div className="grid grid-cols-2 gap-2">
            <StatCard
              label="Confidence"
              value={stats.confidence_mean}
              threshold={0.65}
              higherBetter
            />
            <StatCard
              label="Faithfulness"
              value={stats.faithfulness_mean}
              threshold={0.70}
              higherBetter
            />
            <StatCard
              label="Context Precision"
              value={stats.context_precision_mean}
              threshold={0.55}
              higherBetter
            />
            <StatCard
              label="P95 Latency"
              value={stats.latency_ms_p95}
              threshold={8000}
              higherBetter={false}
              unit="ms"
            />
          </div>

          {/* CRAG action distribution */}
          {totalCrag > 0 && (
            <div className="p-3 rounded-xl border border-gray-200 dark:border-gray-700">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
                CRAG Action Distribution
              </p>
              <div className="space-y-1.5">
                {Object.entries(cragDist).map(([action, count]) => (
                  <div key={action} className="flex items-center gap-2">
                    <span className="text-xs text-gray-500 w-24 truncate">{action}</span>
                    <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-purple-500"
                        style={{ width: `${(count / totalCrag) * 100}%` }}
                        role="progressbar"
                        aria-valuenow={(count / totalCrag) * 100}
                        aria-valuemin={0}
                        aria-valuemax={100}
                      />
                    </div>
                    <span className="text-xs text-gray-400 w-10 text-right">
                      {((count / totalCrag) * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Trend sparklines */}
          {trend.length > 1 && (
            <div className="p-3 rounded-xl border border-gray-200 dark:border-gray-700">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">
                14-day Trends
              </p>
              <div className="space-y-2">
                {[
                  ["confidence_mean",    "Confidence"],
                  ["faithfulness_mean",  "Faithfulness"],
                  ["latency_ms_p95",     "P95 Latency"],
                ].map(([key, label]) => (
                  <div key={key} className="flex items-center gap-3">
                    <span className="text-xs text-gray-400 w-24">{label}</span>
                    <TrendSparkline trend={trend} metricKey={key} />
                    <span className="text-xs text-gray-500 ml-auto">
                      {trend[trend.length - 1]?.[key] != null
                        ? (key.includes("latency")
                          ? `${trend[trend.length - 1][key]?.toFixed(0)}ms`
                          : `${(trend[trend.length - 1][key] * 100)?.toFixed(1)}%`)
                        : "—"
                      }
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Alerts */}
          {(stats.faithfulness_alert || stats.latency_alert) && (
            <div className="p-3 rounded-xl border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/20" role="alert" aria-live="assertive">
              <p className="text-xs font-medium text-red-600 dark:text-red-400 mb-1">
                ⚠️ Active Alerts
              </p>
              {stats.faithfulness_alert && (
                <p className="text-xs text-red-500">
                  Faithfulness below threshold — run pipeline to trigger auto-improvement
                </p>
              )}
              {stats.latency_alert && (
                <p className="text-xs text-red-500">
                  P95 latency exceeds 8s — retrieval optimization recommended
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

MonitoringDashboard.propTypes = {
  API_URL: PropTypes.string,
};
