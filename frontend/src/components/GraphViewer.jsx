// frontend/src/components/GraphViewer.jsx
// DVMELTSS-FIX: A - Accessibility, M - Modular, P - Performance
// ASCALE-FIX: S - Separation, L - Layered
import { useEffect, useRef, useState, memo } from "react";
import ForceGraph2D from "react-force-graph-2d";
import PropTypes from "prop-types";

// Color map per entity type (accessible contrast)
const TYPE_COLORS = {
  Person:       "#7F77DD",
  Organization: "#378ADD",
  Contract:     "#1D9E75",
  Clause:       "#EF9F27",
  Date:         "#E24B4A",
  Location:     "#D85A30",
  Concept:      "#888780",
  Amount:       "#639922",
  Document:     "#533AB7",
};

const DEFAULT_COLOR = "#888780";

export const GraphViewer = memo(function GraphViewer({ nodes = [], edges = [], height = 400 }) {
  const graphRef = useRef(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [isInitialized, setIsInitialized] = useState(false);

  // Transform to react-force-graph format
  const graphData = {
    nodes: nodes.map((n) => ({
      id: n.id,
      label: n.name,
      type: n.entity_type,
      desc: n.description,
      color: TYPE_COLORS[n.entity_type] || DEFAULT_COLOR,
    })),
    links: edges.map((e, i) => ({
      id: `edge-${i}`,
      source: e.from_id,
      target: e.to_id,
      label: e.relationship_type,
    })),
  };

  // Center graph on load
  useEffect(() => {
    if (graphRef.current && nodes.length > 0 && !isInitialized) {
      // Small delay to ensure DOM is ready
      const timer = setTimeout(() => {
        graphRef.current?.zoomToFit(400);
        setIsInitialized(true);
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [nodes, edges, isInitialized]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (graphRef.current) {
        graphRef.current._destructor?.();
      }
    };
  }, []);

  if (nodes.length === 0) {
    return (
      <div 
        className="flex items-center justify-center h-40 text-gray-400 dark:text-gray-500 text-sm"
        role="status"
        aria-live="polite"
      >
        No graph data — upload a document to build the knowledge graph.
      </div>
    );
  }

  return (
    <div 
      className="relative rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden bg-gray-950"
      role="img"
      aria-label={`Knowledge graph with ${nodes.length} nodes and ${edges.length} connections`}
    >
      {/* Legend */}
      <div className="absolute top-2 left-2 z-10 flex flex-wrap gap-1.5 max-w-[200px]" aria-hidden="true">
        {Object.entries(TYPE_COLORS).slice(0, 6).map(([type, color]) => (
          <span
            key={type}
            className="text-xs px-1.5 py-0.5 rounded font-medium"
            style={{ backgroundColor: color + "30", color }}
          >
            {type}
          </span>
        ))}
      </div>

      {/* Hovered node tooltip */}
      {hoveredNode && (
        <div 
          className="absolute top-2 right-2 z-10 bg-gray-900 border border-gray-700 rounded-lg p-3 max-w-[200px] text-xs"
          role="tooltip"
          aria-live="polite"
        >
          <p className="font-medium text-white">{hoveredNode.label}</p>
          <p className="text-gray-400">{hoveredNode.type}</p>
          {hoveredNode.desc && (
            <p className="text-gray-500 mt-1 line-clamp-2">{hoveredNode.desc}</p>
          )}
        </div>
      )}

      <ForceGraph2D
        ref={graphRef}
        graphData={graphData}
        height={height}
        backgroundColor="#0a0a0f"
        nodeLabel="label"
        nodeColor={(n) => n.color}
        nodeRelSize={6}
        linkLabel="label"
        linkColor={() => "#444"}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        linkCurvature={0.1}
        onNodeHover={setHoveredNode}
        onNodeClick={(node) => {
          // Optional: handle node click for drill-down
          console.log("Node clicked:", node);
        }}
        nodeCanvasObject={(node, ctx, globalScale) => {
          const label = node.label;
          const fontSize = Math.max(10 / globalScale, 3);
          ctx.font = `${fontSize}px Sans-Serif`;

          // Node circle
          ctx.beginPath();
          ctx.arc(node.x, node.y, 6, 0, 2 * Math.PI);
          ctx.fillStyle = node.color;
          ctx.fill();

          // Node border
          ctx.strokeStyle = "#fff2";
          ctx.lineWidth = 0.5;
          ctx.stroke();

          // Label below node (only when zoomed in)
          if (globalScale > 0.8) {
            ctx.fillStyle = "#ccc";
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(
              label.length > 18 ? label.slice(0, 18) + "…" : label,
              node.x, 
              node.y + 8
            );
          }
        }}
      />
    </div>
  );
});

GraphViewer.propTypes = {
  nodes: PropTypes.arrayOf(PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    entity_type: PropTypes.string,
    description: PropTypes.string,
  })),
  edges: PropTypes.arrayOf(PropTypes.shape({
    from_id: PropTypes.string.isRequired,
    to_id: PropTypes.string.isRequired,
    relationship_type: PropTypes.string,
  })),
  height: PropTypes.number,
};