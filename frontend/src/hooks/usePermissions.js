// frontend/src/hooks/usePermissions.js
import { useMemo } from "react";
import { useAuth } from "./useAuth";
// [OK] FIXED: Import shared ROLE_RANK — was duplicated with RoleGuard.jsx
import { ROLE_RANK } from "../utils/constants";

export function usePermissions() {
  const { user } = useAuth();

  return useMemo(() => {
    if (!user) {
      return {
        role: null,
        isSuperAdmin: false,
        isWorkspaceAdmin: false,
        isEditor: false,
        isViewer: false,
        canUpload: false,
        canDelete: false,
        canQuery: false,
        canAnnotate: false,
        canManageKeys: false,
        canManageLlmSettings: false,
        canManageBilling: false,
        canManageSso: false,
        canManageWebhooks: false,
        canManageWorkflows: false,
        canManageTeam: false,
        canViewAudit: false,
        canSuspendWorkspace: false,
        canImpersonate: false,
      };
    }

    const role = user.role || "viewer";
    const isSuperAdmin = Boolean(user.is_superuser) || role === "superadmin";
    const rank = ROLE_RANK[role] ?? 1;

    const isWorkspaceAdmin = rank >= 3;
    const isEditor = rank >= 2;
    const isViewer = rank >= 1;

    return {
      role,
      isSuperAdmin,
      isWorkspaceAdmin,
      isEditor,
      isViewer,

      // Feature permissions
      canUpload:           isEditor,
      canDelete:           isWorkspaceAdmin,
      canQuery:            isViewer,
      canAnnotate:         isEditor,
      canManageKeys:       isWorkspaceAdmin,
      canManageLlmSettings: isWorkspaceAdmin,
      canManageBilling:    isWorkspaceAdmin,
      canManageSso:        isWorkspaceAdmin,
      canManageWebhooks:   isWorkspaceAdmin,
      canManageWorkflows:  isWorkspaceAdmin,
      canManageTeam:       isWorkspaceAdmin,
      canViewAudit:        isWorkspaceAdmin,
      canSuspendWorkspace: isSuperAdmin,
      canImpersonate:      isSuperAdmin,
    };
  }, [user]);
}
