// frontend/src/components/IngestProgressPanel.jsx
// DVMELTSS-FIX: A - Async, E - Error handling, M - Modular
// ASCALE-FIX: S - Separation, L - Layered
import { useCallback } from "react";
import { useIngestProgress } from "./hooks/useIngestProgress";
import toast from "react-hot-toast";
import PropTypes from "prop-types";

const STAGE_ICONS = {
  queued:    "⏳",
  validate:  "🔍",
  ocr:       "👁️",
  chunking:  "✂️",
  embedding: "🧠",
  indexing:  "📚",
  graph:     "🕸️",
  versioning:"📌",
  bm25:      "🔤",
  complete:  "✅",
  failed:    "❌",
};

function TaskProgressCard({ task, onCancel }) {
  const isTerminal = ["complete", "failed", "cancelled"].includes(task.status);
  const icon = STAGE_ICONS[task.stage] || "⚙️";

  const barColor = task.status === "complete" ? "bg-green-500"
                 : task.status === "failed"   ? "bg-red-500"
                 : "bg-blue-500";

  return (
    <div className={`
      rounded-xl border p-3 space-y-2
      ${task.status === "complete"
        ? "border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/20"
        : task.status === "failed"
        ? "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/20"
        : "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900"
      }
    `} role="article" aria-label={`Task: ${task.filename}`}>
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-base" aria-hidden="true">{icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-gray-700 dark:text-gray-300 truncate">
            {task.filename}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {task.message}
          </p>
        </div>
        {!isTerminal && (
          <button
            onClick={() => onCancel(task.task_id)}
            className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 rounded"
            aria-label={`Cancel task: ${task.filename}`}
          >
            ✕
          </button>
        )}
      </div>

      {/* Progress bar */}
      {!isTerminal && (
        <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden" role="progressbar" aria-valuenow={task.progress} aria-valuemin={0} aria-valuemax={100}>
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${task.progress}%` }}
          />
        </div>
      )}

      {/* Completion details */}
      {task.status === "complete" && (
        <div className="flex gap-3 text-xs text-gray-500 dark:text-gray-400" aria-label="Processing complete">
          <span>📄 {task.page_count} pages</span>
          <span>✂️ {task.chunk_count} chunks</span>
          <span>⚡ {task.latency_seconds?.toFixed(1)}s</span>
        </div>
      )}

      {/* Error */}
      {task.status === "failed" && task.error && (
        <p className="text-xs text-red-500 dark:text-red-400 line-clamp-2" role="alert">
          {task.error}
        </p>
      )}

      {/* Details */}
      {task.details && Object.keys(task.details).length > 0 && !isTerminal && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(task.details).slice(0, 3).map(([k, v]) => (
            <span 
              key={k} 
              className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
            >
              {k}: {typeof v === "number" ? v.toFixed?.(1) ?? v : v}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function IngestProgressPanel({ onDocumentIndexed }) {
  const { tasks, upload, cancel } = useIngestProgress();

  const handleDrop = useCallback(async (e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer?.files || []);
    for (const file of files) {
      try {
        const result = await upload(file);
        onDocumentIndexed?.(result);
        toast.success(`Queued: ${file.name}`);
      } catch (err) {
        toast.error(`Failed to queue ${file.name}: ${err.message}`);
      }
    }
  }, [upload, onDocumentIndexed]);

  const handleFileInput = useCallback(async (e) => {
    const files = Array.from(e.target.files || []);
    for (const file of files) {
      try {
        const result = await upload(file);
        onDocumentIndexed?.(result);
        toast.success(`Queued: ${file.name}`);
      } catch (err) {
        toast.error(`Failed: ${err.message}`);
      }
    }
    e.target.value = ""; // reset input
  }, [upload, onDocumentIndexed]);

  const taskList = Object.values(tasks);
  const completedCount = taskList.filter(t => t.status === "complete").length;

  return (
    <div className="space-y-3" role="region" aria-label="Upload progress">
      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => document.getElementById("batch-file-input")?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            document.getElementById("batch-file-input")?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Drop files or click to upload multiple files"
        className="border-2 border-dashed border-gray-300 dark:border-gray-600
          rounded-xl p-5 text-center cursor-pointer
          hover:border-blue-400 dark:hover:border-blue-500 transition-colors
          focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <input
          id="batch-file-input"
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.png,.jpg,.jpeg,.docx,.xlsx,.mp3,.mp4,.wav,.m4a"
          onChange={handleFileInput}
          aria-label="Select multiple files to upload"
        />
        <p className="text-sm text-gray-600 dark:text-gray-400">
          📤 Drop files or click to upload
        </p>
        <p className="text-xs text-gray-400 mt-1">
          Multiple files supported — processed in parallel
        </p>
      </div>

      {/* Task list */}
      {taskList.length > 0 && (
        <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
              Processing queue ({taskList.length})
            </p>
            {completedCount > 0 && (
              <span className="text-xs text-green-500" aria-live="polite">
                {completedCount} complete
              </span>
            )}
          </div>
          {taskList.map(task => (
            <TaskProgressCard
              key={task.task_id}
              task={task}
              onCancel={cancel}
            />
          ))}
        </div>
      )}
    </div>
  );
}

IngestProgressPanel.propTypes = {
  onDocumentIndexed: PropTypes.func,
};
