export function CitationCard({ citation, index }) {
  const blockIcon = {
    table: "Tb",
    paragraph: "P",
    title: "T",
    header: "H",
    figure: "Fg",
  }[citation.block_type] || "P";

  const scoreColor =
    citation.rerank_score >= 0.7
      ? "text-emerald-700"
      : citation.rerank_score >= 0.4
      ? "text-amber-700"
      : "text-stone-500";

  return (
    <div className="flex gap-3 p-3 rounded-2xl border border-[rgba(120,92,62,0.12)] bg-[rgba(255,250,243,0.85)] text-sm shadow-sm">
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-teal-100 text-teal-800 text-xs font-semibold flex items-center justify-center mt-0.5">
        {index + 1}
      </div>

      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-semibold text-stone-700 truncate max-w-[180px]">
            {citation.source_file}
          </span>
          <span className="text-stone-500 text-xs">
            p.{citation.page_display ?? (citation.page_number + 1)}
          </span>
          <span className="text-xs px-1.5 py-0.5 rounded-full bg-stone-200 text-stone-700 font-mono">
            {blockIcon} {citation.block_type}
          </span>
          <span className={`text-xs font-medium ml-auto ${scoreColor}`}>
            {typeof citation.rerank_score === "number"
              ? `${(citation.rerank_score * 100).toFixed(0)}%`
              : ""}
          </span>
        </div>

        <p className="text-stone-600 text-xs leading-relaxed line-clamp-3">
          {citation.chunk_text}
        </p>
      </div>
    </div>
  );
}
