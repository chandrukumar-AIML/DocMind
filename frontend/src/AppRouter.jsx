// frontend/src/AppRouter.jsx — top-level route definitions
import { useEffect } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import App from "./App";
import { InviteAccept } from "./components/InviteAccept";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { APIKeyManager } from "./components/APIKeyManager";
import { AuditLogViewer } from "./components/AuditLogViewer";
import { SuperAdminDashboard } from "./components/SuperAdminDashboard";
import { UnauthorizedPage } from "./components/UnauthorizedPage";
import { LegalPage } from "./components/LegalPage";
import { RoleGuard } from "./components/RoleGuard";
// [OK] FIXED: Register navigator so axios interceptor can use React Router navigation
// instead of window.location.href (which drops React state on hard reload)
import { setNavigator } from "./utils/navigator";

export default function AppRouter() {
  const navigate = useNavigate();

  // Register navigator once so client.js interceptor can call navigateTo("/login")
  useEffect(() => {
    setNavigator(navigate);
  }, [navigate]);

  return (
    <Routes>
      {/* Public routes — no auth required */}
      <Route path="/invite/:token" element={<InviteAccept />} />
      <Route path="/unauthorized" element={<UnauthorizedPage />} />
      <Route path="/legal/:doc" element={<LegalPage />} />

      {/* Auth-required full-page routes */}
      <Route path="/onboarding" element={<OnboardingWizard />} />
      <Route
        path="/settings/apikeys"
        element={
          <RoleGuard requiredRole="workspace_admin">
            <APIKeyManager />
          </RoleGuard>
        }
      />
      <Route
        path="/audit"
        element={
          <RoleGuard requiredRole="workspace_admin">
            <AuditLogViewer />
          </RoleGuard>
        }
      />
      <Route
        path="/superadmin"
        element={
          <RoleGuard requiredRole="superadmin">
            <SuperAdminDashboard />
          </RoleGuard>
        }
      />

      {/* Main SPA — handles /login internally and all other paths */}
      <Route path="/*" element={<App />} />
    </Routes>
  );
}
