// frontend/src/components/OnboardingWizard.jsx
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import toast from "react-hot-toast";

const STEPS = [
  { id: 1, title: "Choose Domain", desc: "Tell us what kind of documents you'll analyze." },
  { id: 2, title: "Upload First Document", desc: "Drag a PDF, DOCX, or image to get started." },
  { id: 3, title: "Run Your First Query", desc: "Ask DocuMind AI anything about your document." },
  { id: 4, title: "Get Your API Key", desc: "Use the API to integrate DocuMind into your workflow." },
  { id: 5, title: "You're All Set!", desc: "Your workspace is ready. Let's go!" },
];

const DOMAINS = ["legal", "medical", "financial", "logistics", "hr", "general"];

export function OnboardingWizard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [domain, setDomain] = useState("");
  const [apiKey, setApiKey] = useState(null);
  const [keyCopied, setKeyCopied] = useState(false);
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    if (user?.workspace_id) {
      api.getOnboardingProgress(user.workspace_id)
        .then(p => setProgress(p))
        .catch(() => {});
    }
  }, [user]);

  const next = async () => {
    try {
      const r = await api.wizardStep(step, { domain });
      if (r.next_step) setStep(r.next_step);
    } catch { setStep(s => Math.min(s + 1, 5)); }
  };

  const generateKey = async () => {
    try {
      const r = await api.createWorkspaceApiKey("My First Key", ["read", "write"]);
      setApiKey(r.api_key);
    } catch { toast.error("Failed to generate key"); }
  };

  const copyKey = () => {
    navigator.clipboard.writeText(apiKey).then(() => {
      setKeyCopied(true);
      setTimeout(() => setKeyCopied(false), 2500);
    });
  };

  const currentStep = STEPS.find(s => s.id === step);
  const pct = Math.round(((step - 1) / (STEPS.length - 1)) * 100);

  return (
    <div className="invite-page" style={{ alignItems: "flex-start", paddingTop: 60 }}>
      <div className="invite-card" style={{ maxWidth: 520, width: "100%" }}>
        <div className="invite-logo">D</div>

        {/* Progress bar */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11,
            color: "var(--text-4)", marginBottom: 6 }}>
            <span>Step {step} of {STEPS.length}</span>
            <span>{pct}% complete</span>
          </div>
          <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
            <div style={{ height: "100%", width: `${pct}%`, background: "var(--accent)",
              borderRadius: 2, transition: "width 0.4s ease" }} />
          </div>
        </div>

        {/* Step header */}
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>{currentStep?.title}</div>
          <div style={{ fontSize: 13, color: "var(--text-3)" }}>{currentStep?.desc}</div>
        </div>

        {/* Step content */}
        {step === 1 && (
          <div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 20 }}>
              {DOMAINS.map(d => (
                <button key={d} onClick={() => setDomain(d)}
                  className={`mode-chip${domain === d ? " active" : ""}`}
                  style={{ textTransform: "capitalize" }}>
                  {d}
                </button>
              ))}
            </div>
            <button className="btn-primary" disabled={!domain} onClick={next}
              style={{ width: "100%" }}>
              Continue →
            </button>
          </div>
        )}

        {step === 2 && (
          <div style={{ textAlign: "center" }}>
            <div style={{ padding: "32px 0", color: "var(--text-3)", fontSize: 13 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📄</div>
              Head to the <strong>Docs</strong> tab and upload your first document,
              then come back here.
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button className="btn-sm" onClick={() => navigate("/")}>Go to Docs</button>
              <button className="btn-primary" onClick={next}>I've uploaded a doc →</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div style={{ textAlign: "center" }}>
            <div style={{ padding: "24px 0", color: "var(--text-3)", fontSize: 13 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>💬</div>
              Head to the main chat and ask anything about your document.
              Try: <em>"What are the key clauses?"</em>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button className="btn-sm" onClick={() => navigate("/")}>Go to Chat</button>
              <button className="btn-primary" onClick={next}>I've run a query →</button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div>
            {!apiKey ? (
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 13, color: "var(--text-3)", marginBottom: 16 }}>
                  Your API key lets you integrate DocuMind AI into your application.
                </div>
                <button className="btn-primary" onClick={generateKey}>Generate API Key</button>
              </div>
            ) : (
              <div>
                <div style={{ fontSize: 12, color: "var(--amber)", marginBottom: 8 }}>
                  ⚠ Copy this key now — it won't be shown again.
                </div>
                <div style={{ fontFamily: "monospace", fontSize: 11, wordBreak: "break-all",
                  background: "var(--surface-3)", padding: 12, borderRadius: 6, marginBottom: 12 }}>
                  {apiKey}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn-primary" onClick={copyKey} style={{ flex: 1 }}>
                    {keyCopied ? "Copied!" : "Copy Key"}
                  </button>
                  <button className="btn-primary" onClick={next} disabled={!keyCopied}
                    style={{ flex: 1 }}>
                    Done →
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {step === 5 && (
          <div style={{ textAlign: "center" }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🎉</div>
            <div style={{ fontSize: 14, color: "var(--text-2)", marginBottom: 20 }}>
              Your DocuMind AI workspace is ready!
              Start uploading documents and asking questions.
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button className="btn-primary" onClick={() => navigate("/")}>Go to Dashboard</button>
              <button className="btn-sm" onClick={() => navigate("/settings/apikeys")}>
                Manage API Keys
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
