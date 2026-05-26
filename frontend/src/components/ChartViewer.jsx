// frontend/src/components/ChartViewer.jsx
// DVMELTSS-FIX: A - Accessibility, V - Validate
// ASCALE-FIX: S - Separation
import PropTypes from "prop-types";

export function ChartViewer({ chart }) {
  if (!chart) return null;

  const TYPE_ICON = {
    bar_chart:   "📊",
    line_chart:  "📈",
    pie_chart:   "🥧",
    flowchart:   "🔀",
    scatter_plot:"⚡",
    other:       "🖼️",
  };

  const chartType = chart.chart_type || "other";
  const icon = TYPE_ICON[chartType] || TYPE_ICON.other;
  const title = chart.title || chartType.replace(/_/g, " ");

  return (
    <div 
      className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-2"
      role="figure"
      aria-label={`Chart: ${title}`}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden="true">{icon}</span>
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
          {title}
        </span>
        <span className="text-xs text-gray-400 ml-auto">p.{(chart.page_number || 0) + 1}</span>
      </div>

      {chart.key_takeaway && (
        <p className="text-xs text-gray-600 dark:text-gray-400 italic">
          💡 {chart.key_takeaway}
        </p>
      )}

      {chart.description && (
        <p className="text-xs text-gray-500 dark:text-gray-500">
          {chart.description}
        </p>
      )}

      {chart.data_points?.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-gray-400 uppercase tracking-wide">Data Points</p>
          <div className="flex flex-wrap gap-1.5">
            {chart.data_points.slice(0, 8).map((dp, i) => (
              <span 
                key={`${dp.label}-${i}`} 
                className="text-xs px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400"
              >
                {dp.label}: {dp.value}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

ChartViewer.propTypes = {
  chart: PropTypes.shape({
    chart_type: PropTypes.string,
    title: PropTypes.string,
    page_number: PropTypes.number,
    key_takeaway: PropTypes.string,
    description: PropTypes.string,
    data_points: PropTypes.arrayOf(PropTypes.shape({
      label: PropTypes.string,
      value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    })),
  }),
};