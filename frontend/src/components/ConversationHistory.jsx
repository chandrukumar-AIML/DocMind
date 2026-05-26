// frontend/src/components/ConversationHistory.jsx
import { memo } from "react";
import PropTypes from "prop-types";

function timeAgo(ts) {
  const diff = Date.now() - ts;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function DeleteIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  );
}

const ConversationItem = memo(function ConversationItem({ conv, isActive, onSelect, onDelete }) {
  return (
    <div
      className={`conv-item${isActive ? " active" : ""}`}
      onClick={() => onSelect(conv.id)}
      role="button"
      tabIndex={0}
      aria-label={conv.title}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && onSelect(conv.id)}
    >
      <div className="conv-item-body">
        <div className="conv-item-title">{conv.title}</div>
        <div className="conv-item-meta">
          {conv.messageCount > 0 && <span>{conv.messageCount} msgs</span>}
          <span>{timeAgo(conv.timestamp)}</span>
        </div>
      </div>
      <button
        className="conv-delete-btn"
        onClick={e => { e.stopPropagation(); onDelete(conv.id); }}
        aria-label="Delete conversation"
        title="Delete"
      >
        <DeleteIcon />
      </button>
    </div>
  );
});

export function ConversationHistory({ conversations, activeSessionId, onSelect, onDelete, onClearAll, onNewChat }) {
  return (
    <div className="conv-history">
      <div className="conv-history-actions">
        <button className="conv-new-btn" onClick={onNewChat}>
          + New Chat
        </button>
        {conversations.length > 0 && (
          <button className="conv-clear-btn" onClick={onClearAll} title="Clear all history">
            Clear all
          </button>
        )}
      </div>

      {conversations.length === 0 ? (
        <div className="conv-empty">No past conversations yet</div>
      ) : (
        <div className="conv-list">
          {conversations.map(conv => (
            <ConversationItem
              key={conv.id}
              conv={conv}
              isActive={conv.id === activeSessionId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

ConversationHistory.propTypes = {
  conversations: PropTypes.array.isRequired,
  activeSessionId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onDelete: PropTypes.func.isRequired,
  onClearAll: PropTypes.func.isRequired,
  onNewChat: PropTypes.func.isRequired,
};
