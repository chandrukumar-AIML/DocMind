// frontend/src/components/LegalPage.jsx — public, auth-free legal document pages
import { useParams, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Canonical, lawyer-reviewable markdown lives in src/legal/*.md and is imported raw
// (Vite ?raw) so there's a single source of truth — edit the .md files, not this file.
import termsMd from "../legal/terms-of-service.md?raw";
import privacyMd from "../legal/privacy-policy.md?raw";
import dpaMd from "../legal/dpa.md?raw";

const DOCS = {
  terms: { title: "Terms of Service", body: termsMd },
  privacy: { title: "Privacy Policy", body: privacyMd },
  dpa: { title: "Data Processing Agreement", body: dpaMd },
};

export function LegalPage() {
  const { doc } = useParams();
  const navigate = useNavigate();
  const entry = DOCS[doc];

  return (
    <div style={{ minHeight: "100vh", background: "var(--bg-1)", color: "var(--text-1)" }}>
      {/* Header */}
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "16px 24px", borderBottom: "1px solid var(--border)",
          position: "sticky", top: 0, background: "var(--bg-1)", zIndex: 1,
        }}
      >
        <button
          onClick={() => navigate("/")}
          style={{
            display: "flex", alignItems: "center", gap: 10, background: "none",
            border: "none", cursor: "pointer", color: "var(--text-1)", fontFamily: "var(--font)",
          }}
          aria-label="Back to DocuMind AI"
        >
          <span style={{
            width: 28, height: 28, borderRadius: 8, display: "grid", placeItems: "center",
            background: "var(--accent, #0d9488)", color: "#fff", fontWeight: 800, fontSize: 15,
          }}>D</span>
          <strong style={{ fontSize: 15 }}>DocuMind AI</strong>
        </button>
        <nav style={{ display: "flex", gap: 16, fontSize: 13 }}>
          <a href="/legal/terms" style={{ color: doc === "terms" ? "var(--violet-2)" : "var(--text-3)" }}>Terms</a>
          <a href="/legal/privacy" style={{ color: doc === "privacy" ? "var(--violet-2)" : "var(--text-3)" }}>Privacy</a>
          <a href="/legal/dpa" style={{ color: doc === "dpa" ? "var(--violet-2)" : "var(--text-3)" }}>DPA</a>
        </nav>
      </div>

      {/* Body */}
      <div className="legal-doc" style={{ maxWidth: 760, margin: "0 auto", padding: "32px 24px 80px", lineHeight: 1.7 }}>
        {!entry ? (
          <div style={{ textAlign: "center", paddingTop: 60 }}>
            <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>Document not found</div>
            <p style={{ color: "var(--text-3)", marginBottom: 20 }}>
              No legal document matches "{doc}". Available: Terms, Privacy, DPA.
            </p>
            <button className="btn-primary" onClick={() => navigate("/legal/terms")}>View Terms of Service</button>
          </div>
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.body}</ReactMarkdown>
        )}
      </div>

      <style>{`
        .legal-doc h1 { font-size: 26px; font-weight: 800; margin: 0 0 4px; }
        .legal-doc h2 { font-size: 18px; font-weight: 700; margin: 28px 0 8px; }
        .legal-doc h3 { font-size: 15px; font-weight: 700; margin: 20px 0 6px; }
        .legal-doc p { margin: 10px 0; color: var(--text-2, inherit); }
        .legal-doc ul, .legal-doc ol { margin: 10px 0; padding-left: 22px; }
        .legal-doc li { margin: 4px 0; }
        .legal-doc blockquote {
          border-left: 3px solid var(--amber, #f59e0b);
          background: rgba(245,158,11,0.08); margin: 16px 0; padding: 10px 16px;
          border-radius: 6px; font-size: 13px; color: var(--text-2, inherit);
        }
        .legal-doc code {
          background: var(--bg-2, rgba(127,127,127,0.15)); padding: 1px 5px;
          border-radius: 4px; font-size: 0.9em;
        }
        .legal-doc table { border-collapse: collapse; margin: 14px 0; width: 100%; font-size: 13px; }
        .legal-doc th, .legal-doc td { border: 1px solid var(--border); padding: 8px 10px; text-align: left; }
        .legal-doc a { color: var(--violet-2); }
        .legal-doc hr { border: none; border-top: 1px solid var(--border); margin: 28px 0; }
      `}</style>
    </div>
  );
}
