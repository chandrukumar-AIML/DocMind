// frontend/src/components/LandingPage.jsx — DocuMind AI Public Landing Page
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";

// ── Icons ──────────────────────────────────────────────────────
function Icon({ d, size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}

const ICONS = {
  brain:    "M9.5 2a2.5 2.5 0 0 1 5 0v1a2.5 2.5 0 0 1-5 0V2zM12 7c-4 0-7 2.5-7 6 0 1.5.5 3 1.5 4H17.5c1-1 1.5-2.5 1.5-4 0-3.5-3-6-7-6zM8.5 17v3M15.5 17v3",
  graph:    "M17 12h-5v5h5zM3 3h5v5H3zM3 14l4-4 4 4 4-4 4 4",
  shield:   "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  zap:      "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  search:   "M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z",
  layers:   "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  users:    "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  check:    "M20 6L9 17l-5-5",
  arrow:    "M5 12h14M12 5l7 7-7 7",
  doc:      "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
  chart:    "M18 20V10M12 20V4M6 20v-6",
  lock:     "M19 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2zM7 11V7a5 5 0 0 1 10 0v4",
  star:     "M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z",
  globe:    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z",
  menu:     "M3 12h18M3 6h18M3 18h18",
  x:        "M18 6L6 18M6 6l12 12",
  sign:     "M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z",
  mic:      "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8",
  bolt:     "M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z",
};

// ── Pricing data ───────────────────────────────────────────────
const PLANS = [
  {
    name: "Free",
    price: "₹0",
    priceUsd: "$0",
    period: "/month",
    desc: "Try DocuMind AI — no card required.",
    cta: "Get Started Free",
    highlight: false,
    features: [
      "5 documents",
      "50 queries / month",
      "1 workspace · 1 user",
      "Basic RAG search",
      "PDF, Word, Excel support",
    ],
    missing: ["Domain AI", "Knowledge Graph", "Team access", "API access"],
  },
  {
    name: "Starter",
    price: "₹2,499",
    priceUsd: "$29",
    period: "/month",
    desc: "For freelancers and small teams.",
    cta: "Start Free Trial",
    highlight: true,
    badge: "Most Popular",
    features: [
      "100 documents",
      "500 queries / month",
      "3 workspaces · 5 users",
      "CRAG + Hybrid RAG",
      "1 domain specialty",
      "API access",
      "Email support",
    ],
    missing: ["Knowledge Graph", "SSO / OIDC", "DocuSign"],
  },
  {
    name: "Pro",
    price: "₹6,599",
    priceUsd: "$79",
    period: "/month",
    desc: "For growing teams that need more.",
    cta: "Start Free Trial",
    highlight: false,
    features: [
      "1,000 documents",
      "Unlimited queries",
      "10 workspaces · 25 users",
      "Full RAG stack + HyDE",
      "All domain specialties",
      "Knowledge Graph",
      "DocuSign integration",
      "API access",
      "Priority support",
    ],
    missing: ["SSO / OIDC", "Dedicated instance"],
  },
  {
    name: "Enterprise",
    price: "₹24,999",
    priceUsd: "$299",
    period: "/month",
    desc: "Full power, dedicated infra.",
    cta: "Contact Sales",
    highlight: false,
    enterprise: true,
    features: [
      "Unlimited documents",
      "Unlimited queries",
      "Unlimited workspaces & users",
      "Full RAG stack + HyDE",
      "All domain specialties",
      "Knowledge Graph",
      "DocuSign integration",
      "SSO / OIDC",
      "Dedicated instance",
      "Custom branding",
      "SLA + dedicated support",
    ],
    missing: [],
  },
];

// ── Feature cards ──────────────────────────────────────────────
const FEATURES = [
  {
    icon: "search",
    title: "Hybrid RAG + CRAG",
    desc: "BM25 + dense vector search fused with RRF, self-grading, and live web fallback when docs don't have the answer.",
    tag: "Core Search",
  },
  {
    icon: "graph",
    title: "Knowledge Graph",
    desc: "Entities and relationships extracted from every document — ask questions that span across hundreds of files.",
    tag: "Unique",
  },
  {
    icon: "layers",
    title: "HyDE + Reranking",
    desc: "Hypothetical Document Embeddings generate better queries. Cross-encoder reranker picks the most relevant chunks.",
    tag: "Advanced RAG",
  },
  {
    icon: "shield",
    title: "Domain Expertise",
    desc: "Medical (ICD-10, drug interactions, PII redaction), Legal (clause extraction, risk scoring), Logistics (invoice OCR, anomaly detection).",
    tag: "Unique",
  },
  {
    icon: "chart",
    title: "Accuracy Scores (RAGAS)",
    desc: "Every answer gets a faithfulness and relevance score. See exactly how confident the AI is — no black box.",
    tag: "Unique",
  },
  {
    icon: "sign",
    title: "DocuSign Integration",
    desc: "Extract, review, and route contracts for e-signature — without leaving DocuMind AI.",
    tag: "Enterprise",
  },
  {
    icon: "users",
    title: "Multi-workspace + RBAC",
    desc: "Separate workspaces per client or project. Roles: viewer, editor, admin, workspace_admin, superadmin.",
    tag: "Teams",
  },
  {
    icon: "mic",
    title: "Audio & Web Ingestion",
    desc: "Transcribe meeting recordings, ingest live web pages, PDFs, Word, Excel — one unified knowledge base.",
    tag: "Ingest",
  },
  {
    icon: "lock",
    title: "SSO / OIDC + Audit Log",
    desc: "PKCE-compliant SSO for enterprise identity providers. Full audit trail of every query and action.",
    tag: "Security",
  },
];

// ── Verticals ──────────────────────────────────────────────────
const VERTICALS = [
  {
    emoji: "⚖️",
    title: "Legal Firms",
    color: "#6366f1",
    items: [
      "Contract clause extraction & risk scoring",
      "Bulk contract comparison",
      "PII redaction before sharing",
      "DocuSign e-signature workflow",
      "Full audit trail per matter",
    ],
  },
  {
    emoji: "🏥",
    title: "Healthcare",
    color: "#0D9488",
    items: [
      "Patient record Q&A",
      "Drug interaction checker",
      "ICD-10 code extraction",
      "PII redaction (mandatory)",
      "Research paper summarization",
    ],
  },
  {
    emoji: "📦",
    title: "Logistics",
    color: "#f59e0b",
    items: [
      "Invoice & PO data extraction",
      "Shipment anomaly detection",
      "Compliance document Q&A",
      "Bulk invoice batch processing",
      "Excel/CSV export of extracted data",
    ],
  },
];

// ── Stat counters ──────────────────────────────────────────────
const STATS = [
  { value: "3", suffix: " query modes", label: "RAG · Agent · Graph" },
  { value: "9", suffix: " file types", label: "PDF, DOCX, XLSX, images, audio, URLs…" },
  { value: "14", suffix: " features", label: "competitors don't have" },
  { value: "3", suffix: " domains", label: "Legal · Medical · Logistics" },
];

// ── Main Component ─────────────────────────────────────────────
export function LandingPage() {
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  // If already logged in, skip landing page and go straight to the app.
  useEffect(() => {
    const token = localStorage.getItem("documind_access_token") || localStorage.getItem("auth_token");
    if (token) navigate("/app", { replace: true });
  }, [navigate]);

  useEffect(() => {
    // index.css locks html/body/root to overflow:hidden for the app shell.
    // Landing page needs normal scroll — override while mounted, restore on leave.
    const els = [document.documentElement, document.body, document.getElementById("root")];
    const prev = els.map(el => el ? { overflow: el.style.overflow, height: el.style.height } : null);
    els.forEach(el => { if (el) { el.style.overflow = "auto"; el.style.height = "auto"; } });
    return () => {
      els.forEach((el, i) => {
        if (el && prev[i]) { el.style.overflow = prev[i].overflow; el.style.height = prev[i].height; }
      });
    };
  }, []);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const goToApp = () => navigate("/app");

  return (
    <div style={S.page}>
      {/* ── Nav ───────────────────────────────────────────── */}
      <nav style={{ ...S.nav, ...(scrolled ? S.navScrolled : {}) }}>
        <div style={S.navInner}>
          <div style={S.logo}>
            <div style={S.logoDot}>D</div>
            <span style={S.logoText}>DocuMind <span style={{ color: "var(--accent)" }}>AI</span></span>
            <span style={S.logoBy}>by Terazion</span>
          </div>

          {/* Desktop links */}
          <div style={S.navLinks}>
            <a href="#features" style={S.navLink}>Features</a>
            <a href="#domains"  style={S.navLink}>Solutions</a>
            <a href="#pricing"  style={S.navLink}>Pricing</a>
            <button style={S.btnOutline} onClick={goToApp}>Sign In</button>
            <button style={S.btnPrimary} onClick={goToApp}>Get Started Free</button>
          </div>

          {/* Mobile hamburger */}
          <button style={S.hamburger} onClick={() => setMenuOpen(v => !v)} aria-label="Toggle menu">
            <Icon d={menuOpen ? ICONS.x : ICONS.menu} size={22} />
          </button>
        </div>

        {/* Mobile menu */}
        {menuOpen && (
          <div style={S.mobileMenu}>
            <a href="#features" style={S.mobileLink} onClick={() => setMenuOpen(false)}>Features</a>
            <a href="#domains"  style={S.mobileLink} onClick={() => setMenuOpen(false)}>Solutions</a>
            <a href="#pricing"  style={S.mobileLink} onClick={() => setMenuOpen(false)}>Pricing</a>
            <button style={{ ...S.btnPrimary, width: "100%", marginTop: 8 }} onClick={goToApp}>Get Started Free</button>
          </div>
        )}
      </nav>

      {/* ── Hero ──────────────────────────────────────────── */}
      <section style={S.hero}>
        <div style={S.heroGlow} />
        <div style={S.heroGlow2} />
        <div style={S.container}>
          <div style={S.heroBadge}>
            <Icon d={ICONS.star} size={13} />
            <span>Enterprise Document Intelligence Platform</span>
          </div>
          <h1 style={S.heroH1}>
            The AI that truly<br />
            <span style={S.heroGradient}>understands</span> your documents
          </h1>
          <p style={S.heroSub}>
            Not just keyword search. Not just a chatbot. DocuMind AI combines CRAG, Knowledge Graphs,
            domain expertise, and verified accuracy scores — so you can trust every answer.
          </p>
          <div style={S.heroCtas}>
            <button style={S.btnHero} onClick={goToApp}>
              Get Started Free
              <Icon d={ICONS.arrow} size={17} />
            </button>
            <button style={S.btnHeroOutline} onClick={goToApp}>
              View Demo →
            </button>
          </div>
          <p style={S.heroHint}>No credit card required · Free plan forever</p>

          {/* App preview mockup */}
          <div style={S.heroMockup}>
            <div style={S.mockupBar}>
              <div style={S.mockupDot1} /><div style={S.mockupDot2} /><div style={S.mockupDot3} />
              <span style={S.mockupUrl}>app.documindai.com</span>
            </div>
            <div style={S.mockupBody}>
              <div style={S.mockupSidebar}>
                <div style={S.mockupSidebarItem}>📄 contract_2024.pdf</div>
                <div style={{ ...S.mockupSidebarItem, ...S.mockupSidebarActive }}>📋 policy_docs.pdf</div>
                <div style={S.mockupSidebarItem}>🏥 patient_records.pdf</div>
                <div style={S.mockupSidebarItem}>📦 invoice_batch.xlsx</div>
              </div>
              <div style={S.mockupChat}>
                <div style={S.mockupMsg}>
                  <div style={S.mockupMsgUser}>What are the key risk clauses in this contract?</div>
                </div>
                <div style={S.mockupMsgAi}>
                  <div style={S.mockupAiIcon}>D</div>
                  <div style={S.mockupAiText}>
                    <div style={S.mockupAiLine} />
                    <div style={{ ...S.mockupAiLine, width: "85%" }} />
                    <div style={{ ...S.mockupAiLine, width: "60%" }} />
                    <div style={S.mockupAiBadge}>
                      <span style={{ color: "#4ade80" }}>✓</span> 94% confidence · 3 citations
                    </div>
                  </div>
                </div>
                <div style={S.mockupInput}>
                  <span style={S.mockupInputText}>Ask anything about your documents…</span>
                  <div style={S.mockupSend}>→</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Stats ─────────────────────────────────────────── */}
      <section style={S.statsSection}>
        <div style={{ ...S.container, ...S.statsGrid }}>
          {STATS.map((s) => (
            <div key={s.label} style={S.statCard}>
              <div style={S.statValue}>{s.value}<span style={S.statSuffix}>{s.suffix}</span></div>
              <div style={S.statLabel}>{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────── */}
      <section id="features" style={S.section}>
        <div style={S.container}>
          <div style={S.sectionHeader}>
            <div style={S.sectionBadge}>What makes us different</div>
            <h2 style={S.sectionH2}>Not just another RAG chatbot</h2>
            <p style={S.sectionSub}>
              ChatPDF and Humata are toys. DocuMind AI is an enterprise-grade intelligence platform
              with features you won't find anywhere else.
            </p>
          </div>
          <div style={S.featuresGrid}>
            {FEATURES.map((f) => (
              <div key={f.title} style={S.featureCard}>
                <div style={S.featureTop}>
                  <div style={S.featureIcon}>
                    <Icon d={ICONS[f.icon]} size={20} />
                  </div>
                  {f.tag === "Unique" && <span style={S.featureTagUnique}>{f.tag}</span>}
                  {f.tag === "Enterprise" && <span style={S.featureTagEnt}>{f.tag}</span>}
                </div>
                <h3 style={S.featureTitle}>{f.title}</h3>
                <p style={S.featureDesc}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Domain Verticals ──────────────────────────────── */}
      <section id="domains" style={{ ...S.section, background: "var(--bg-1)" }}>
        <div style={S.container}>
          <div style={S.sectionHeader}>
            <div style={S.sectionBadge}>Built for your industry</div>
            <h2 style={S.sectionH2}>Domain-specific AI expertise</h2>
            <p style={S.sectionSub}>
              Generic AI gives generic answers. DocuMind AI ships with deep domain knowledge
              for three high-value verticals out of the box.
            </p>
          </div>
          <div style={S.verticalsGrid}>
            {VERTICALS.map((v) => (
              <div key={v.title} style={{ ...S.verticalCard, borderColor: v.color + "44" }}>
                <div style={{ ...S.verticalIcon, background: v.color + "22", color: v.color }}>
                  {v.emoji}
                </div>
                <h3 style={S.verticalTitle}>{v.title}</h3>
                <ul style={S.verticalList}>
                  {v.items.map((item) => (
                    <li key={item} style={S.verticalItem}>
                      <span style={{ color: v.color, marginRight: 8, flexShrink: 0 }}>✓</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Accuracy section ──────────────────────────────── */}
      <section style={S.section}>
        <div style={S.container}>
          <div style={S.accuracyRow}>
            <div style={S.accuracyLeft}>
              <div style={S.sectionBadge}>RAGAS Evaluation</div>
              <h2 style={{ ...S.sectionH2, textAlign: "left", marginBottom: 16 }}>
                Every answer is scored.<br />No guessing.
              </h2>
              <p style={{ ...S.sectionSub, textAlign: "left", maxWidth: 480 }}>
                DocuMind AI runs automatic faithfulness and relevance scoring on every response.
                You see the confidence score alongside every answer — not a black box,
                an accountable AI.
              </p>
              <ul style={{ listStyle: "none", marginTop: 24, display: "flex", flexDirection: "column", gap: 12 }}>
                {["Faithfulness score per answer", "Source citation with page references", "Accuracy trend over time", "Alert when quality drops"].map(item => (
                  <li key={item} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "var(--text-2)" }}>
                    <span style={{ color: "var(--accent)", flexShrink: 0 }}>
                      <Icon d={ICONS.check} size={16} />
                    </span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
            <div style={S.accuracyRight}>
              <div style={S.scoreCard}>
                <div style={S.scoreCardTitle}>Answer Quality Report</div>
                <div style={S.scoreRow}>
                  <span style={S.scoreLabel}>Faithfulness</span>
                  <div style={S.scoreBarWrap}>
                    <div style={{ ...S.scoreBar, width: "94%" }} />
                  </div>
                  <span style={S.scoreVal}>94%</span>
                </div>
                <div style={S.scoreRow}>
                  <span style={S.scoreLabel}>Relevance</span>
                  <div style={S.scoreBarWrap}>
                    <div style={{ ...S.scoreBar, width: "88%", background: "#6366f1" }} />
                  </div>
                  <span style={S.scoreVal}>88%</span>
                </div>
                <div style={S.scoreRow}>
                  <span style={S.scoreLabel}>Context recall</span>
                  <div style={S.scoreBarWrap}>
                    <div style={{ ...S.scoreBar, width: "91%", background: "#f59e0b" }} />
                  </div>
                  <span style={S.scoreVal}>91%</span>
                </div>
                <div style={S.scoreCite}>
                  <span style={{ color: "var(--accent)" }}>📌</span>
                  <span>3 citations · contract_2024.pdf · p. 14, 22, 31</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Pricing ───────────────────────────────────────── */}
      <section id="pricing" style={{ ...S.section, background: "var(--bg-1)" }}>
        <div style={S.container}>
          <div style={S.sectionHeader}>
            <div style={S.sectionBadge}>Pricing</div>
            <h2 style={S.sectionH2}>Start free. Scale as you grow.</h2>
            <p style={S.sectionSub}>
              Free plan is permanent — no tricks. Upgrade when you need more power.
            </p>
          </div>
          <div style={S.pricingGrid}>
            {PLANS.map((plan) => (
              <div key={plan.name} style={{
                ...S.planCard,
                ...(plan.highlight ? S.planCardHighlight : {}),
                ...(plan.enterprise ? S.planCardEnt : {}),
              }}>
                {plan.badge && <div style={S.planBadge}>{plan.badge}</div>}
                <div style={S.planName}>{plan.name}</div>
                <div style={S.planPrice}>
                  {plan.price}<span style={S.planPeriod}>{plan.period}</span>
                </div>
                {plan.priceUsd && plan.price !== "₹0" && (
                  <div style={{ fontSize: 10, color: "var(--tx-2, #9ca3af)", marginTop: 2, marginBottom: 4 }}>
                    {plan.priceUsd}/mo · +18% GST for India
                  </div>
                )}
                <p style={S.planDesc}>{plan.desc}</p>
                <button
                  style={plan.highlight ? S.btnPlanHighlight : S.btnPlan}
                  onClick={plan.name === "Enterprise" ? undefined : goToApp}
                >
                  {plan.cta}
                </button>
                <div style={S.planDivider} />
                <ul style={S.planFeatures}>
                  {plan.features.map(f => (
                    <li key={f} style={S.planFeat}>
                      <span style={{ color: "#4ade80" }}><Icon d={ICONS.check} size={13} /></span>
                      {f}
                    </li>
                  ))}
                  {plan.missing.map(f => (
                    <li key={f} style={{ ...S.planFeat, opacity: 0.35 }}>
                      <span style={{ color: "var(--text-3)" }}>✗</span>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Final CTA ─────────────────────────────────────── */}
      <section style={S.ctaSection}>
        <div style={S.ctaGlow} />
        <div style={{ ...S.container, position: "relative", zIndex: 1, textAlign: "center" }}>
          <h2 style={{ ...S.sectionH2, marginBottom: 16 }}>
            Ready to make your documents intelligent?
          </h2>
          <p style={{ ...S.sectionSub, marginBottom: 32 }}>
            Join teams already using DocuMind AI to answer questions, extract data, and
            automate document workflows — in minutes, not months.
          </p>
          <div style={S.heroCtas}>
            <button style={S.btnHero} onClick={goToApp}>
              Get Started Free
              <Icon d={ICONS.arrow} size={17} />
            </button>
            <button style={S.btnHeroOutline} onClick={goToApp}>
              Sign In →
            </button>
          </div>
          <p style={S.heroHint}>No credit card · Free plan forever · Setup in 2 minutes</p>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────── */}
      <footer style={S.footer}>
        <div style={{ ...S.container, ...S.footerInner }}>
          <div style={S.footerBrand}>
            <div style={S.logo}>
              <div style={S.logoDot}>D</div>
              <span style={S.logoText}>DocuMind <span style={{ color: "var(--accent)" }}>AI</span></span>
            </div>
            <p style={S.footerTagline}>Enterprise Document Intelligence.</p>
            <p style={{ ...S.footerTagline, marginTop: 2 }}>A product by <strong style={{ color: "var(--text-1)" }}>Terazion</strong></p>
          </div>
          <div style={S.footerLinks}>
            <div style={S.footerCol}>
              <div style={S.footerColTitle}>Product</div>
              <a href="#features" style={S.footerLink}>Features</a>
              <a href="#domains"  style={S.footerLink}>Solutions</a>
              <a href="#pricing"  style={S.footerLink}>Pricing</a>
            </div>
            <div style={S.footerCol}>
              <div style={S.footerColTitle}>Legal</div>
              <a href="/legal/terms-of-service" style={S.footerLink}>Terms of Service</a>
              <a href="/legal/privacy-policy"   style={S.footerLink}>Privacy Policy</a>
              <a href="/legal/dpa"              style={S.footerLink}>DPA</a>
            </div>
            <div style={S.footerCol}>
              <div style={S.footerColTitle}>Company</div>
              <a href="mailto:hello@terazion.com" style={S.footerLink}>Contact</a>
              <a href="mailto:sales@terazion.com" style={S.footerLink}>Sales</a>
            </div>
          </div>
        </div>
        <div style={S.footerBottom}>
          <span>© 2026 Terazion. All rights reserved.</span>
          <span>Built with ❤️ in India</span>
        </div>
      </footer>
    </div>
  );
}

// ── Styles ─────────────────────────────────────────────────────
const S = {
  page: {
    fontFamily: "var(--font, 'Inter', sans-serif)",
    background: "var(--bg-0, #070B14)",
    color: "var(--text-1, #F1F5F9)",
    minHeight: "100vh",
    overflowX: "hidden",
  },

  // Nav
  nav: {
    position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
    padding: "0 24px",
    transition: "background 0.3s, border-color 0.3s",
    borderBottom: "1px solid transparent",
  },
  navScrolled: {
    background: "rgba(7,11,20,0.92)",
    backdropFilter: "blur(16px)",
    borderBottom: "1px solid var(--border)",
  },
  navInner: {
    maxWidth: 1160, margin: "0 auto",
    display: "flex", alignItems: "center", justifyContent: "space-between",
    height: 64,
  },
  logo: { display: "flex", alignItems: "center", gap: 10, textDecoration: "none" },
  logoDot: {
    width: 32, height: 32, borderRadius: 8,
    background: "var(--grad-brand, linear-gradient(135deg,#0D9488,#0EA5E9))",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: 15, fontWeight: 800, color: "#fff",
  },
  logoText: { fontSize: 17, fontWeight: 800, color: "var(--text-1)" },
  logoBy: { fontSize: 11, color: "var(--text-3)", marginLeft: -4 },
  navLinks: { display: "flex", alignItems: "center", gap: 24 },
  navLink: {
    fontSize: 14, color: "var(--text-2)", textDecoration: "none",
    transition: "color 0.2s",
  },
  hamburger: {
    display: "none", background: "none", border: "none",
    color: "var(--text-2)", cursor: "pointer",
    "@media(max-width:768px)": { display: "block" },
  },
  mobileMenu: {
    display: "flex", flexDirection: "column", gap: 8,
    padding: "16px 24px 20px",
    borderTop: "1px solid var(--border)",
    background: "var(--bg-0)",
  },
  mobileLink: {
    fontSize: 15, color: "var(--text-2)", textDecoration: "none", padding: "8px 0",
  },

  // Buttons
  btnPrimary: {
    background: "var(--accent, #0D9488)", color: "#fff", border: "none",
    padding: "9px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
    cursor: "pointer", transition: "opacity 0.2s",
  },
  btnOutline: {
    background: "transparent", color: "var(--text-2)",
    border: "1px solid var(--border-2)", padding: "8px 18px",
    borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: "pointer",
  },
  btnHero: {
    display: "flex", alignItems: "center", gap: 8,
    background: "var(--grad-brand, linear-gradient(135deg,#0D9488,#0EA5E9))",
    color: "#fff", border: "none", padding: "13px 28px",
    borderRadius: 10, fontSize: 15, fontWeight: 700, cursor: "pointer",
    boxShadow: "0 0 32px rgba(13,148,136,0.4)",
  },
  btnHeroOutline: {
    background: "transparent", color: "var(--text-1)",
    border: "1px solid var(--border-3)", padding: "13px 28px",
    borderRadius: 10, fontSize: 15, fontWeight: 600, cursor: "pointer",
  },

  // Hero
  hero: {
    paddingTop: 120, paddingBottom: 80,
    position: "relative", overflow: "hidden",
    textAlign: "center",
  },
  heroGlow: {
    position: "absolute", top: -100, left: "50%", transform: "translateX(-50%)",
    width: 700, height: 500,
    background: "radial-gradient(ellipse, rgba(13,148,136,0.18) 0%, transparent 70%)",
    pointerEvents: "none",
  },
  heroGlow2: {
    position: "absolute", top: 100, right: -200,
    width: 500, height: 500,
    background: "radial-gradient(ellipse, rgba(14,165,233,0.1) 0%, transparent 70%)",
    pointerEvents: "none",
  },
  container: { maxWidth: 1160, margin: "0 auto", padding: "0 24px", position: "relative" },
  heroBadge: {
    display: "inline-flex", alignItems: "center", gap: 6,
    background: "rgba(13,148,136,0.12)", border: "1px solid rgba(13,148,136,0.3)",
    color: "var(--accent)", borderRadius: 20, padding: "5px 14px",
    fontSize: 12, fontWeight: 600, marginBottom: 24,
  },
  heroH1: {
    fontSize: "clamp(36px, 6vw, 68px)", fontWeight: 800, lineHeight: 1.12,
    letterSpacing: "-0.02em", marginBottom: 20,
    color: "var(--text-1)",
  },
  heroGradient: {
    background: "linear-gradient(135deg, #0D9488 0%, #38BDF8 100%)",
    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
  },
  heroSub: {
    fontSize: 18, color: "var(--text-2)", lineHeight: 1.6,
    maxWidth: 620, margin: "0 auto 32px",
  },
  heroCtas: { display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" },
  heroHint: { marginTop: 14, fontSize: 12, color: "var(--text-3)" },

  // Mockup
  heroMockup: {
    marginTop: 56, borderRadius: 14, overflow: "hidden",
    border: "1px solid var(--border-2)",
    boxShadow: "0 32px 80px rgba(0,0,0,0.6), 0 0 0 1px rgba(13,148,136,0.1)",
    maxWidth: 860, margin: "56px auto 0",
    background: "var(--bg-1)",
  },
  mockupBar: {
    display: "flex", alignItems: "center", gap: 6,
    padding: "10px 14px", background: "var(--bg-2)",
    borderBottom: "1px solid var(--border)",
  },
  mockupDot1: { width: 10, height: 10, borderRadius: "50%", background: "#ef4444" },
  mockupDot2: { width: 10, height: 10, borderRadius: "50%", background: "#f59e0b" },
  mockupDot3: { width: 10, height: 10, borderRadius: "50%", background: "#22c55e" },
  mockupUrl: {
    fontSize: 11, color: "var(--text-3)", marginLeft: 8,
    background: "var(--bg-3)", padding: "2px 10px", borderRadius: 4,
  },
  mockupBody: { display: "flex", height: 260 },
  mockupSidebar: {
    width: 200, borderRight: "1px solid var(--border)",
    padding: 12, display: "flex", flexDirection: "column", gap: 4,
    flexShrink: 0,
  },
  mockupSidebarItem: {
    fontSize: 11, color: "var(--text-3)", padding: "6px 8px",
    borderRadius: 6, cursor: "pointer",
  },
  mockupSidebarActive: {
    background: "var(--accent-dim, rgba(13,148,136,0.18))",
    color: "var(--accent)", fontWeight: 600,
  },
  mockupChat: {
    flex: 1, display: "flex", flexDirection: "column",
    padding: 16, gap: 12, overflow: "hidden",
  },
  mockupMsg: { display: "flex", justifyContent: "flex-end" },
  mockupMsgUser: {
    background: "var(--accent-dim)", color: "var(--accent)",
    fontSize: 12, padding: "8px 12px", borderRadius: "10px 10px 2px 10px",
    maxWidth: "70%",
  },
  mockupMsgAi: { display: "flex", gap: 10, alignItems: "flex-start" },
  mockupAiIcon: {
    width: 26, height: 26, borderRadius: 6, flexShrink: 0,
    background: "var(--grad-brand)", color: "#fff",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: 11, fontWeight: 800,
  },
  mockupAiText: { flex: 1, display: "flex", flexDirection: "column", gap: 6 },
  mockupAiLine: {
    height: 8, borderRadius: 4, background: "var(--bg-4)", width: "100%",
  },
  mockupAiBadge: {
    fontSize: 10, color: "var(--text-3)", marginTop: 4,
    display: "flex", alignItems: "center", gap: 4,
  },
  mockupInput: {
    marginTop: "auto", display: "flex", alignItems: "center",
    background: "var(--bg-2)", borderRadius: 8, padding: "8px 12px",
    border: "1px solid var(--border)",
  },
  mockupInputText: { flex: 1, fontSize: 11, color: "var(--text-4)" },
  mockupSend: {
    width: 26, height: 26, borderRadius: 6,
    background: "var(--accent)", color: "#fff",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: 12,
  },

  // Stats
  statsSection: {
    borderTop: "1px solid var(--border)", borderBottom: "1px solid var(--border)",
    padding: "40px 24px", background: "var(--bg-1)",
  },
  statsGrid: {
    display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 24,
    textAlign: "center",
  },
  statCard: { padding: 16 },
  statValue: { fontSize: 40, fontWeight: 800, color: "var(--text-1)", lineHeight: 1 },
  statSuffix: { fontSize: 18, color: "var(--accent)", fontWeight: 700 },
  statLabel: { fontSize: 12, color: "var(--text-3)", marginTop: 6 },

  // Section shared
  section: { padding: "96px 24px" },
  sectionHeader: { textAlign: "center", marginBottom: 56 },
  sectionBadge: {
    display: "inline-block",
    background: "rgba(13,148,136,0.12)", border: "1px solid rgba(13,148,136,0.25)",
    color: "var(--accent)", borderRadius: 20, padding: "4px 14px",
    fontSize: 12, fontWeight: 600, marginBottom: 16,
  },
  sectionH2: {
    fontSize: "clamp(28px, 4vw, 44px)", fontWeight: 800,
    letterSpacing: "-0.02em", lineHeight: 1.2, marginBottom: 16,
  },
  sectionSub: {
    fontSize: 16, color: "var(--text-2)", lineHeight: 1.6,
    maxWidth: 600, margin: "0 auto",
  },

  // Features grid
  featuresGrid: {
    display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16,
  },
  featureCard: {
    background: "var(--bg-2)", border: "1px solid var(--border)",
    borderRadius: 12, padding: 24,
    transition: "border-color 0.2s",
  },
  featureTop: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 },
  featureIcon: {
    width: 40, height: 40, borderRadius: 10,
    background: "var(--accent-dim, rgba(13,148,136,0.15))",
    color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center",
  },
  featureTagUnique: {
    fontSize: 10, fontWeight: 700, padding: "3px 8px",
    background: "rgba(13,148,136,0.15)", color: "var(--accent)",
    borderRadius: 6, border: "1px solid rgba(13,148,136,0.3)",
  },
  featureTagEnt: {
    fontSize: 10, fontWeight: 700, padding: "3px 8px",
    background: "rgba(99,102,241,0.15)", color: "#818cf8",
    borderRadius: 6, border: "1px solid rgba(99,102,241,0.3)",
  },
  featureTitle: { fontSize: 15, fontWeight: 700, marginBottom: 8 },
  featureDesc: { fontSize: 13, color: "var(--text-2)", lineHeight: 1.6 },

  // Verticals
  verticalsGrid: { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 20 },
  verticalCard: {
    background: "var(--bg-2)", border: "1px solid",
    borderRadius: 14, padding: 28,
  },
  verticalIcon: {
    width: 48, height: 48, borderRadius: 12, fontSize: 24,
    display: "flex", alignItems: "center", justifyContent: "center",
    marginBottom: 16,
  },
  verticalTitle: { fontSize: 18, fontWeight: 700, marginBottom: 16 },
  verticalList: { listStyle: "none", display: "flex", flexDirection: "column", gap: 10 },
  verticalItem: { display: "flex", alignItems: "flex-start", fontSize: 13, color: "var(--text-2)", lineHeight: 1.5 },

  // Accuracy
  accuracyRow: {
    display: "grid", gridTemplateColumns: "1fr 1fr", gap: 64,
    alignItems: "center",
  },
  accuracyLeft: {},
  accuracyRight: {},
  scoreCard: {
    background: "var(--bg-2)", border: "1px solid var(--border-2)",
    borderRadius: 14, padding: 24,
    boxShadow: "0 0 40px rgba(13,148,136,0.08)",
  },
  scoreCardTitle: { fontSize: 13, fontWeight: 700, marginBottom: 20, color: "var(--text-1)" },
  scoreRow: { display: "flex", alignItems: "center", gap: 12, marginBottom: 14 },
  scoreLabel: { fontSize: 12, color: "var(--text-3)", width: 100, flexShrink: 0 },
  scoreBarWrap: { flex: 1, height: 6, background: "var(--bg-4)", borderRadius: 3, overflow: "hidden" },
  scoreBar: { height: "100%", borderRadius: 3, background: "var(--accent)", transition: "width 1s ease" },
  scoreVal: { fontSize: 13, fontWeight: 700, color: "var(--text-1)", width: 36, textAlign: "right" },
  scoreCite: {
    marginTop: 16, padding: "10px 12px",
    background: "var(--bg-3)", borderRadius: 8,
    fontSize: 11, color: "var(--text-3)",
    display: "flex", alignItems: "center", gap: 8,
  },

  // Pricing
  pricingGrid: { display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16 },
  planCard: {
    background: "var(--bg-2)", border: "1px solid var(--border)",
    borderRadius: 14, padding: 24, position: "relative",
  },
  planCardHighlight: {
    border: "1px solid var(--accent)",
    boxShadow: "0 0 32px rgba(13,148,136,0.12)",
  },
  planCardEnt: { border: "1px solid rgba(99,102,241,0.4)" },
  planBadge: {
    position: "absolute", top: -11, left: "50%", transform: "translateX(-50%)",
    background: "var(--accent)", color: "#fff",
    fontSize: 10, fontWeight: 700, padding: "3px 12px", borderRadius: 20,
    whiteSpace: "nowrap",
  },
  planName: { fontSize: 14, fontWeight: 700, color: "var(--text-2)", marginBottom: 8 },
  planPrice: { fontSize: 36, fontWeight: 800, lineHeight: 1, marginBottom: 6 },
  planPeriod: { fontSize: 14, color: "var(--text-3)", fontWeight: 400 },
  planDesc: { fontSize: 12, color: "var(--text-3)", marginBottom: 16, lineHeight: 1.5 },
  btnPlan: {
    width: "100%", padding: "10px", borderRadius: 8,
    background: "var(--bg-3)", color: "var(--text-1)",
    border: "1px solid var(--border-2)", fontSize: 13, fontWeight: 600, cursor: "pointer",
  },
  btnPlanHighlight: {
    width: "100%", padding: "10px", borderRadius: 8,
    background: "var(--accent)", color: "#fff",
    border: "none", fontSize: 13, fontWeight: 600, cursor: "pointer",
  },
  planDivider: { borderTop: "1px solid var(--border)", margin: "16px 0" },
  planFeatures: { listStyle: "none", display: "flex", flexDirection: "column", gap: 8 },
  planFeat: { display: "flex", alignItems: "flex-start", gap: 8, fontSize: 12, color: "var(--text-2)" },

  // CTA
  ctaSection: {
    padding: "96px 24px", textAlign: "center", position: "relative", overflow: "hidden",
  },
  ctaGlow: {
    position: "absolute", top: "50%", left: "50%",
    transform: "translate(-50%, -50%)",
    width: 600, height: 400,
    background: "radial-gradient(ellipse, rgba(13,148,136,0.15) 0%, transparent 70%)",
    pointerEvents: "none",
  },

  // Footer
  footer: {
    borderTop: "1px solid var(--border)",
    background: "var(--bg-1)",
    padding: "48px 24px 24px",
  },
  footerInner: {
    display: "flex", gap: 48, flexWrap: "wrap",
    paddingBottom: 32, marginBottom: 24,
    borderBottom: "1px solid var(--border)",
  },
  footerBrand: { flex: "0 0 200px" },
  footerTagline: { fontSize: 12, color: "var(--text-3)", marginTop: 10, lineHeight: 1.6 },
  footerLinks: { flex: 1, display: "flex", gap: 48, flexWrap: "wrap" },
  footerCol: { display: "flex", flexDirection: "column", gap: 10 },
  footerColTitle: { fontSize: 12, fontWeight: 700, color: "var(--text-1)", marginBottom: 4 },
  footerLink: { fontSize: 13, color: "var(--text-3)", textDecoration: "none" },
  footerBottom: {
    display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8,
    fontSize: 12, color: "var(--text-4)",
  },
};
