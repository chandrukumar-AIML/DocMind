// frontend/src/AppRouter.jsx — top-level route definitions
import { useEffect } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import App from "./App";
import { LandingPage } from "./components/LandingPage";
import { InviteAccept } from "./components/InviteAccept";
import { OnboardingWizard } from "./components/OnboardingWizard";
import { APIKeyManager } from "./components/APIKeyManager";
import { AuditLogViewer } from "./components/AuditLogViewer";
import { SuperAdminDashboard } from "./components/SuperAdminDashboard";
import { UnauthorizedPage } from "./components/UnauthorizedPage";
import { LegalPage } from "./components/LegalPage";
import { RoleGuard } from "./components/RoleGuard";
import { setNavigator } from "./utils/navigator";

export default function AppRouter() {
  const navigate = useNavigate();

  useEffect(() => {
    setNavigator(navigate);
  }, [navigate]);

  return (
    <Routes>
      {/* Public landing page */}
      <Route path="/" element={<LandingPage />} />

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

      {/* Main app — /app/* and all other paths go to authenticated shell */}
      <Route path="/app/*" element={<App />} />
      <Route path="/*" element={<App />} />
    </Routes>
  );
}
