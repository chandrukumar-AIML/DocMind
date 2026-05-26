// frontend/src/utils/constants.js
// DVMELTSS-FIX: M - Modular, S - Single source of truth
// [OK] FIXED: ROLE_RANK was duplicated in usePermissions.js and RoleGuard.jsx.
// Single source of truth prevents drift between the two definitions.

/**
 * Numeric rank for each role — used for >= comparisons.
 * Higher rank = more permissions.
 *
 *   superadmin      4   — global platform admin
 *   workspace_admin 3   — admin of a single workspace
 *   admin           3   — legacy alias for workspace_admin
 *   editor          2   — can upload, annotate, query
 *   viewer          1   — read-only
 */
export const ROLE_RANK = {
  superadmin: 4,
  workspace_admin: 3,
  admin: 3,      // legacy alias
  editor: 2,
  viewer: 1,
};

/**
 * Default fallback workspace ID used when no workspace is selected.
 * Must match the backend's validate_workspace_id "default" literal.
 */
export const DEFAULT_WORKSPACE_ID = "default";

/**
 * localStorage key used to persist the active workspace ID across sessions.
 */
export const WORKSPACE_STORAGE_KEY = "documind_workspace_id";
