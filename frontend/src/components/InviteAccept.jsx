// frontend/src/components/InviteAccept.jsx
// Public page — no auth required. Handles /invite/:token
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import toast from "react-hot-toast";

export function InviteAccept() {
  const { token } = useParams();
  const navigate = useNavigate();

  const [info, setInfo] = useState(null);
  const [validating, setValidating] = useState(true);
  const [invalid, setInvalid] = useState(false);
  const [form, setForm] = useState({ full_name: "", password: "", confirm: "" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.validateInviteToken(token)
      .then(data => { setInfo(data); setValidating(false); })
      .catch(() => { setInvalid(true); setValidating(false); });
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    if (form.password !== form.confirm) {
      toast.error("Passwords do not match"); return;
    }
    if (form.password.length < 8) {
      toast.error("Password must be at least 8 characters"); return;
    }
    setSubmitting(true);
    try {
      const result = await api.acceptInviteToken(token, {
        full_name: form.full_name,
        password: form.password,
      });
      // Store token and redirect to onboarding
      localStorage.setItem("access_token", result.access_token);
      localStorage.setItem("refresh_token", result.refresh_token);
      toast.success(`Welcome to ${info.workspace_name}!`);
      navigate("/onboarding");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to accept invite");
    } finally { setSubmitting(false); }
  };

  if (validating) {
    return (
      <div className="invite-page">
        <div className="invite-card">
          <div className="invite-logo">D</div>
          <div style={{ textAlign: "center", color: "var(--text-3)" }}>Validating invite…</div>
        </div>
      </div>
    );
  }

  if (invalid || !info) {
    return (
      <div className="invite-page">
        <div className="invite-card">
          <div className="invite-logo">D</div>
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 8, color: "var(--red)" }}>
              Invalid Invite
            </div>
            <div style={{ fontSize: 13, color: "var(--text-3)", marginBottom: 20 }}>
              This invite link is invalid or has expired.
              Contact your administrator for a new invite.
            </div>
            <button className="btn-primary" onClick={() => navigate("/login")}>Go to Login</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="invite-page">
      <div className="invite-card">
        <div className="invite-logo">D</div>
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>
            You're invited!
          </div>
          <div style={{ fontSize: 13, color: "var(--text-3)" }}>
            {info.inviter_name || "Someone"} invited you to join
            <strong style={{ color: "var(--text-1)" }}> {info.workspace_name}</strong>
            {" "}as a <strong style={{ color: "var(--accent)" }}>{info.role}</strong>.
          </div>
          <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 6 }}>
            {info.email}
          </div>
        </div>

        <form onSubmit={submit}>
          <div className="form-group">
            <label>Full Name</label>
            <input className="input" required value={form.full_name}
              onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
              placeholder="Your full name" autoFocus />
          </div>
          <div className="form-group">
            <label>Set Password</label>
            <input className="input" type="password" required minLength={8}
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              placeholder="At least 8 characters" />
          </div>
          <div className="form-group">
            <label>Confirm Password</label>
            <input className="input" type="password" required
              value={form.confirm}
              onChange={e => setForm(f => ({ ...f, confirm: e.target.value }))}
              placeholder="Repeat password" />
          </div>
          <button className="btn-primary" type="submit" disabled={submitting}
            style={{ width: "100%", marginTop: 8 }}>
            {submitting ? "Setting up your account…" : "Accept Invite & Get Started"}
          </button>
        </form>
      </div>
    </div>
  );
}
