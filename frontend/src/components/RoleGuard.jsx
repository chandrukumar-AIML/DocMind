// frontend/src/components/RoleGuard.jsx
import PropTypes from "prop-types";
import { Navigate } from "react-router-dom";
import { usePermissions } from "../hooks/usePermissions";
import { useAuth } from "../hooks/useAuth";
// [OK] FIXED: Import shared ROLE_RANK — was duplicated with usePermissions.js
import { ROLE_RANK } from "../utils/constants";

/**
 * Wraps routes that require a minimum role.
 *
 * Usage:
 *   <RoleGuard requiredRole="superadmin"><SuperAdminDashboard /></RoleGuard>
 *   <RoleGuard requiredRole="workspace_admin"><APIKeyManager /></RoleGuard>
 */
export function RoleGuard({ requiredRole, children, fallback = "/unauthorized" }) {
  const { user, loading } = useAuth();
  const perms = usePermissions();

  if (loading) {
    return (
      <div className="role-guard-loading">
        <div className="loading-spinner" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  const required = ROLE_RANK[requiredRole] ?? 1;
  const actual   = ROLE_RANK[perms.role] ?? 1;
  const isSuperAdmin = perms.isSuperAdmin;

  // Superadmin bypasses all role checks
  if (!isSuperAdmin && actual < required) {
    return <Navigate to={fallback} replace />;
  }

  return children;
}

RoleGuard.propTypes = {
  requiredRole: PropTypes.string.isRequired,
  children: PropTypes.node,
  fallback: PropTypes.string,
};
