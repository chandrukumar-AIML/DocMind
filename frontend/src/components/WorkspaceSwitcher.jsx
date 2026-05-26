// frontend/src/components/WorkspaceSwitcher.jsx
// DVMELTSS-FIX: A - Accessibility, M - Modular, S - Security
// ASCALE-FIX: S - Separation, L - Layered
import { useState, useCallback } from "react";
import PropTypes from "prop-types";

export function WorkspaceSwitcher({ user, workspaces, onSwitch }) {
  const [open, setOpen] = useState(false);

  const ROLE_COLORS = {
    admin:  "text-purple-600 dark:text-purple-400",
    editor: "text-blue-600 dark:text-blue-400",
    viewer: "text-gray-500 dark:text-gray-400",
  };

  const handleSwitch = useCallback((workspaceId) => {
    onSwitch?.(workspaceId);
    setOpen(false);
  }, [onSwitch]);

  const handleKeyDown = useCallback((e, ws) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSwitch(ws.workspace_id);
    }
  }, [handleSwitch]);

  if (!user) return null;

  const currentWs = workspaces.find(
    w => w.workspace_id === user.workspace_id
  ) || { name: "Default", role: user.role };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen(o => !o);
          }
        }}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg
          border border-gray-200 dark:border-gray-700
          hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors text-sm
          focus:outline-none focus:ring-2 focus:ring-purple-500"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Current workspace: ${currentWs.name || "Default"}`}
      >
        <div className="w-5 h-5 rounded bg-gradient-to-br from-purple-400 to-blue-500
          flex items-center justify-center text-white text-xs font-bold" aria-hidden="true">
          {currentWs.name?.[0]?.toUpperCase() || "W"}
        </div>
        <div className="text-left hidden sm:block">
          <div className="text-xs font-medium text-gray-700 dark:text-gray-300
            truncate max-w-[120px]">
            {currentWs.name || "Workspace"}
          </div>
          <div className={`text-[10px] ${ROLE_COLORS[user.role] || ROLE_COLORS.viewer}`}>
            {user.role}
          </div>
        </div>
        <span className="text-gray-400 text-xs" aria-hidden="true">▼</span>
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div 
            className="absolute left-0 top-full mt-1 z-20 w-56
              bg-white dark:bg-gray-900 rounded-xl
              border border-gray-200 dark:border-gray-700
              shadow-lg overflow-hidden"
            role="listbox"
            aria-label="Workspace selector"
          >
            <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800">
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                Workspaces
              </p>
            </div>

            {workspaces.map(ws => (
              <button
                key={ws.workspace_id}
                onClick={() => handleSwitch(ws.workspace_id)}
                onKeyDown={(e) => handleKeyDown(e, ws)}
                role="option"
                aria-selected={ws.workspace_id === user.workspace_id}
                className={`
                  w-full flex items-center gap-2 px-3 py-2
                  hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors text-left
                  focus:outline-none focus:ring-2 focus:ring-purple-500
                  ${ws.workspace_id === user.workspace_id
                    ? "bg-blue-50 dark:bg-blue-950/30"
                    : ""
                  }
                `}
              >
                <div className="w-6 h-6 rounded bg-gradient-to-br from-purple-400 to-blue-500
                  flex items-center justify-center text-white text-xs font-bold flex-shrink-0" aria-hidden="true">
                  {ws.name?.[0]?.toUpperCase() || "W"}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-700 dark:text-gray-300 truncate">
                    {ws.name}
                  </div>
                  <div className={`text-xs ${ROLE_COLORS[ws.role] || ROLE_COLORS.viewer}`}>
                    {ws.role}
                  </div>
                </div>
                {ws.workspace_id === user.workspace_id && (
                  <span className="text-blue-500 text-xs" aria-hidden="true">✓</span>
                )}
              </button>
            ))}

            <div className="border-t border-gray-100 dark:border-gray-800 px-3 py-2">
              <p className="text-xs text-gray-400 truncate">{user.email}</p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

WorkspaceSwitcher.propTypes = {
  user: PropTypes.shape({
    user_id: PropTypes.string,
    email: PropTypes.string,
    workspace_id: PropTypes.string,
    role: PropTypes.oneOf(["admin", "editor", "viewer"]),
  }),
  workspaces: PropTypes.arrayOf(PropTypes.shape({
    workspace_id: PropTypes.string.isRequired,
    name: PropTypes.string,
    role: PropTypes.oneOf(["admin", "editor", "viewer"]),
  })),
  onSwitch: PropTypes.func,
};
