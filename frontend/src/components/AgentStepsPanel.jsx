import { useEffect, useRef } from "react";
import PropTypes from "prop-types";

const NODE_ICONS = {
  query_analyzer:        "🔍",
  vector_retriever:      "📚",
  graph_retriever:       "🕸️",
  relevance_grader:      "⚖️",
  query_rewriter:        "✏️",
  answer_generator:      "💬",
  hallucination_checker: "🔬",
  human_review:          "👤",
};

const NODE_COLORS = {
  query_analyzer:        "text-blue-400",
  vector_retriever:      "text-teal-400",
  graph_retriever:       "text-purple-400",
  relevance_grader:      "text-amber-400",
  query_rewriter:        "text-orange-400",
  answer_generator:      "text-green-400",
  hallucination_checker: "text-red-400",
  human_review:          "text-pink-400",
};

export function AgentStepsPanel({ steps = [], isStreaming = false }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [steps]);

  if (steps.length === 0 && !isStreaming) return null;

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-3">
      <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">
        Agent reasoning
      </p>

      <div className="space-y-1.5 max-h-48 overflow-y-auto">
        {steps.map((step, i) => {
          const node  = step.node || "unknown";
          const icon  = NODE_ICONS[node]  || "•";
          const color = NODE_COLORS[node] || "text-gray-400";

          return (
            <div key={i} className="flex items-start gap-2 text-xs">
              <span className="flex-shrink-0 mt-0.5">{icon}</span>
              <div className="flex-1 min-w-0">
                <span className={`font-medium ${color}`}>
                  {node.replace(/_/g, " ")}
                </span>
                <span className="text-gray-500 dark:text-gray-400 ml-2">
                  {step.content}
                </span>
              </div>
            </div>
          );
        })}

        {isStreaming && (
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <span className="animate-pulse">⋯</span>
            <span>Agent thinking...</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}

AgentStepsPanel.propTypes = {
  steps: PropTypes.arrayOf(PropTypes.shape({
    node: PropTypes.string,
    content: PropTypes.string,
  })),
  isStreaming: PropTypes.bool,
};