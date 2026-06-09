// frontend/src/components/LoginForm.jsx — Nebula Dark
import { useState, useCallback } from "react";
import PropTypes from "prop-types";
import { toast } from "react-hot-toast";
import { isDemoMode } from "../api/demo";

function IconEye({ show }) {
  return show ? (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/>
      <path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/>
      <line x1="1" y1="1" x2="23" y2="23"/>
    </svg>
  ) : (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>
  );
}

function IconSpinner() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true" style={{ animation: "spin 0.7s linear infinite" }}>
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
    </svg>
  );
}

const HERO_FEATURES = [
  {
    icon: "🔍",
    title: "Three query modes",
    desc: "RAG for cited answers, Agent for multi-step reasoning, Graph for entity relationships — switch instantly.",
  },
  {
    icon: "⚖️",
    title: "Legal & compliance AI",
    desc: "Extract clauses, flag risk, and scan for GDPR / HIPAA / RBI obligations across every document.",
  },
  {
    icon: "📄",
    title: "Any document format",
    desc: "PDF, Word, Excel, images (OCR), audio (Whisper transcription), and live web URLs.",
  },
  {
    icon: "🔒",
    title: "Enterprise-grade security",
    desc: "Workspace isolation, role-based access control, JWT auth, and a full audit trail.",
  },
];

export function LoginForm({ onLogin, onRegister }) {
  const [mode, setMode]               = useState("login");
  const [email, setEmail]             = useState("");
  const [password, setPassword]       = useState("");
  const [name, setName]               = useState("");
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const handleSubmit = useCallback(async () => {
    if (!email || !password) { setError("Email and password are required"); return; }
    if (mode === "register" && !name.trim()) { setError("Full name is required"); return; }
    setLoading(true);
    setError("");
    try {
      if (mode === "login") {
        await onLogin(email, password);
      } else {
        await onRegister(email, password, name);
      }
    } catch (err) {
      setError(err.message || "Authentication failed. Check your credentials and try again.");
    } finally {
      setLoading(false);
    }
  }, [mode, email, password, name, onLogin, onRegister]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === "Enter") { e.preventDefault(); handleSubmit(); }
  }, [handleSubmit]);

  const switchMode = useCallback(() => {
    setMode(m => m === "login" ? "register" : "login");
    setError("");
  }, []);

  const handleForgotPassword = useCallback(() => {
    toast("To reset your password, contact support@documind.ai", {
      icon: "🔑",
      duration: 5000,
    });
  }, []);

  const demo = isDemoMode();

  return (
    <div className="auth-split">
      {/* Ambient glow */}
      <div style={{
        position: "fixed", inset: 0, pointerEvents: "none", zIndex: 0,
        background: "radial-gradient(ellipse 80% 60% at 30% 30%, rgba(13,148,136,0.18) 0%, transparent 65%)",
      }} aria-hidden="true" />

      {/* ── LEFT PANEL: Hero (desktop only via CSS) ── */}
      <div className="auth-hero-panel" aria-hidden="true">
        {/* Logo row */}
        <div className="hero-logo-row">
          <div className="hero-logo-mark">D</div>
          <span className="hero-logo-name">DocuMind AI</span>
        </div>

        {/* Headline */}
        <h1 className="hero-tagline">
          Document intelligence<br />
          for <span className="hero-tagline-accent">legal &amp; enterprise</span><br />
          teams.
        </h1>

        {/* Problem */}
        <p className="hero-problem">
          Legal teams spend hours hunting through contracts for specific clauses. One
          missed obligation costs thousands. DocuMind AI turns that into a 3-second
          question — with the exact source paragraph cited.
        </p>

        {/* Feature bullets */}
        <div className="hero-features">
          {HERO_FEATURES.map(f => (
            <div key={f.icon} className="hero-feature">
              <span className="hero-feature-icon" aria-hidden="true">{f.icon}</span>
              <p className="hero-feature-text">
                <strong>{f.title}</strong> — {f.desc}
              </p>
            </div>
          ))}
        </div>

        {/* Metrics bar */}
        <div className="hero-metrics">
          <div className="hero-metric">
            <div className="hero-metric-value">156</div>
            <div className="hero-metric-label">API Endpoints</div>
          </div>
          <div className="hero-metric">
            <div className="hero-metric-value">&lt;3s</div>
            <div className="hero-metric-label">First Answer</div>
          </div>
          <div className="hero-metric">
            <div className="hero-metric-value">10+</div>
            <div className="hero-metric-label">AI Features</div>
          </div>
          <div className="hero-metric">
            <div className="hero-metric-value">MIT</div>
            <div className="hero-metric-label">Licensed</div>
          </div>
        </div>

        {/* Demo link */}
        <a
          href="https://github.com/chandrukumar-AIML/DocMind"
          className="hero-demo-link"
          target="_blank"
          rel="noopener noreferrer"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          View on GitHub
        </a>
      </div>

      {/* ── RIGHT PANEL: Auth card ── */}
      <div className="auth-form-panel">
        <div className="auth-card anim-fade-in">

          {/* Demo mode banner */}
          {demo && (
            <div style={{
              background: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.35)",
              borderRadius: "var(--r)", padding: "10px 12px", marginBottom: 20,
              display: "flex", alignItems: "flex-start", gap: 10,
            }}>
              <span style={{ fontSize: 14, flexShrink: 0 }}>⚡</span>
              <div style={{ fontSize: 11, color: "var(--amber)", lineHeight: 1.5 }}>
                <strong>Demo Mode</strong> — No backend needed. Use any email/password to sign in,
                or click the button below to fill demo credentials.
              </div>
            </div>
          )}

          {/* Logo */}
          <div className="auth-logo">
            <div className="auth-logo-icon" aria-hidden="true">D</div>
            <div>
              <div className="auth-logo-name">DocuMind AI</div>
            </div>
          </div>

          <h1 className="auth-title">{mode === "login" ? "Welcome back" : "Get started"}</h1>
          <p className="auth-subtitle">
            {mode === "login" ? "Sign in to your workspace" : "Create your account"}
          </p>

          {/* Tabs */}
          <div className="auth-tabs" role="tablist">
            <button
              className={`auth-tab${mode === "login" ? " active" : ""}`}
              onClick={() => { setMode("login"); setError(""); }}
              role="tab"
              aria-selected={mode === "login"}
            >
              Sign In
            </button>
            <button
              className={`auth-tab${mode === "register" ? " active" : ""}`}
              onClick={() => { setMode("register"); setError(""); }}
              role="tab"
              aria-selected={mode === "register"}
            >
              Register
            </button>
          </div>

          {/* Form */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {mode === "register" && (
              <div className="form-field">
                <label className="form-label" htmlFor="auth-name">Full name</label>
                <input
                  id="auth-name"
                  className="input"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Jane Smith"
                  disabled={loading}
                  autoComplete="name"
                />
              </div>
            )}

            <div className="form-field">
              <label className="form-label" htmlFor="auth-email">Email</label>
              <input
                id="auth-email"
                className="input"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="you@company.com"
                disabled={loading}
                autoComplete="email"
                aria-required="true"
              />
            </div>

            <div className="form-field">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
                <label className="form-label" htmlFor="auth-password" style={{ margin: 0 }}>Password</label>
                {mode === "login" && (
                  <button
                    type="button"
                    onClick={handleForgotPassword}
                    style={{
                      background: "none", border: "none", cursor: "pointer",
                      fontSize: 11, color: "var(--text-3)", fontFamily: "var(--font)",
                      padding: 0, lineHeight: 1,
                    }}
                  >
                    Forgot password?
                  </button>
                )}
              </div>
              <div style={{ position: "relative" }}>
                <input
                  id="auth-password"
                  className="input"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="••••••••••••"
                  disabled={loading}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  aria-required="true"
                  style={{ paddingRight: 44 }}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  style={{
                    position: "absolute", right: 12, top: "50%", transform: "translateY(-50%)",
                    background: "none", border: "none", cursor: "pointer",
                    color: "var(--text-3)", padding: 2, display: "flex", alignItems: "center",
                  }}
                >
                  <IconEye show={showPassword} />
                </button>
              </div>
              {mode === "register" && (
                <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 5, lineHeight: 1.5 }}>
                  Min 12 chars · uppercase · lowercase · number · special char (e.g. <code style={{ fontSize: 11 }}>Chandru@1313!</code>)
                </div>
              )}
            </div>

            {demo && (
              <button
                type="button"
                onClick={() => { setEmail("demo@documind.ai"); setPassword("Demo@12345"); setError(""); }}
                style={{
                  background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.3)",
                  borderRadius: "var(--r)", padding: "9px 12px", cursor: "pointer",
                  fontSize: 12, color: "var(--amber)", fontWeight: 600,
                  width: "100%", fontFamily: "var(--font)",
                }}
              >
                ⚡ Fill demo credentials
              </button>
            )}

            {error && (
              <div
                role="alert"
                style={{
                  fontSize: 12, color: "var(--red)",
                  background: "rgba(239,68,68,0.08)",
                  border: "1px solid rgba(239,68,68,0.25)",
                  borderRadius: "var(--r)", padding: "10px 12px",
                }}
              >
                {error}
              </div>
            )}

            <button
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={loading || !email || !password}
              aria-label={loading
                ? (mode === "login" ? "Signing in…" : "Creating account…")
                : (mode === "login" ? "Sign in" : "Create account")}
              style={{ marginTop: 4, height: 44 }}
            >
              {loading ? (
                <span style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center" }}>
                  <IconSpinner />
                  {mode === "login" ? "Signing in…" : "Creating account…"}
                </span>
              ) : (
                mode === "login" ? "Sign in" : "Create account"
              )}
            </button>
          </div>

          {/* Footer */}
          <div style={{ marginTop: 20, textAlign: "center", fontSize: 12, color: "var(--text-3)" }}>
            {mode === "login" ? "Don't have an account? " : "Already have an account? "}
            <button
              onClick={switchMode}
              style={{
                background: "none", border: "none", cursor: "pointer",
                color: "var(--violet-2)", fontWeight: 600, fontSize: "inherit",
                padding: 0, textDecoration: "underline", textUnderlineOffset: 3,
              }}
            >
              {mode === "login" ? "Register" : "Sign in"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

LoginForm.propTypes = {
  onLogin:    PropTypes.func.isRequired,
  onRegister: PropTypes.func.isRequired,
};
