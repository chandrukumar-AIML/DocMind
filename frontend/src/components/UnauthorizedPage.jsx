// frontend/src/components/UnauthorizedPage.jsx
import { useNavigate } from "react-router-dom";

export function UnauthorizedPage() {
  const navigate = useNavigate();
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center",
      justifyContent: "center", background: "var(--bg-1)" }}>
      <div style={{ textAlign: "center", maxWidth: 400 }}>
        <div style={{ fontSize: 64, marginBottom: 16 }}>🔒</div>
        <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 8 }}>Access Denied</div>
        <div style={{ fontSize: 14, color: "var(--text-3)", marginBottom: 24 }}>
          You don't have permission to view this page.
          Contact your workspace administrator if you believe this is a mistake.
        </div>
        <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
          <button className="btn-primary" onClick={() => navigate("/")}>Go to Dashboard</button>
          <button className="btn-sm" onClick={() => navigate(-1)}>Go Back</button>
        </div>
      </div>
    </div>
  );
}
