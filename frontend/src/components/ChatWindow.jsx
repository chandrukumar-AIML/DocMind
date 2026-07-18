// frontend/src/components/ChatWindow.jsx — Nebula Dark
import { useEffect, useRef, memo, useState, lazy, Suspense } from "react";
import PropTypes from "prop-types";
import { api } from "../api/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CitationCardV2 } from "./CitationCardV2";

// Lazy-loaded: react-syntax-highlighter (~300 KB gzip) is only fetched when the
// first fenced code block actually appears in a response — not at app startup.
const LazyCodeBlock = lazy(() => import("./CodeBlock"));

// ── Streaming cursor ───────────────────────────────────────
function StreamCursor() {
  return <span className="stream-cursor" aria-hidden="true" />;
}

// ── Thinking dots ──────────────────────────────────────────
function ThinkingDots() {
  return (
    <span className="thinking-dots" aria-label="Thinking">
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
    </span>
  );
}

// ── Status step indicator ──────────────────────────────────
function StatusStep({ step }) {
  if (!step) return null;
  const label = step === "searching" ? "Searching documents…" : step === "generating" ? "Generating answer…" : step;
  return (
    <div className="status-step">
      <span className="status-step-dot" />
      <span>{label}</span>
    </div>
  );
}

// ── Copy button ────────────────────────────────────────────
function ExportPdfButton({ question, answer, citations }) {
  const [loading, setLoading] = useState(false);
  const handle = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await api.exportAnswerPdf(question, answer, citations);
    } catch {
      /* toast shown in api layer */
    } finally {
      setLoading(false);
    }
  };
  return (
    <button
      className="copy-answer-btn"
      onClick={handle}
      disabled={loading}
      title="Export as PDF"
      aria-label="Export answer as PDF"
    >
      {loading ? (
        <span style={{ fontSize: 9, color: "var(--text-3)" }}>…</span>
      ) : (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="12" y1="18" x2="12" y2="12"/>
          <line x1="9" y1="15" x2="15" y2="15"/>
        </svg>
      )}
    </button>
  );
}

function CopyButton({ text, className = "copy-answer-btn" }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button className={className} onClick={copy} title={copied ? "Copied!" : "Copy"} aria-label="Copy">
      {copied ? "✓" : (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
        </svg>
      )}
    </button>
  );
}

// ── Code block — suspends until vendor-syntax chunk arrives ──
// Plain-text <pre> is shown while the chunk loads (instant on repeat visits).
function SuspendedCodeBlock({ children, className }) {
  return (
    <Suspense
      fallback={
        <pre className="md-code-block" style={{ padding: "10px 14px", fontSize: 12 }}>
          <code>{String(children).replace(/\n$/, "")}</code>
        </pre>
      }
    >
      <LazyCodeBlock className={className}>{children}</LazyCodeBlock>
    </Suspense>
  );
}

// ── Markdown renderer ──────────────────────────────────────
const MD_COMPONENTS = {
  code({ inline, className, children, ...props }) {
    if (inline) {
      return <code className="md-inline-code" {...props}>{children}</code>;
    }
    return <SuspendedCodeBlock className={className}>{children}</SuspendedCodeBlock>;
  },
  a({ href, children }) {
    return <a href={href} target="_blank" rel="noopener noreferrer" className="md-link">{children}</a>;
  },
  table({ children }) {
    return <div className="md-table-wrap"><table className="md-table">{children}</table></div>;
  },
};

function MarkdownAnswer({ content, streaming }) {
  return (
    <div className="md-answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
        {content}
      </ReactMarkdown>
      {streaming && <StreamCursor />}
    </div>
  );
}

// CitationCard is now CitationCardV2 — imported above for confidence-level display

// ── User message ───────────────────────────────────────────
const UserMessage = memo(function UserMessage({ message }) {
  return (
    <div className="msg msg-user">
      <div className="msg-user-bubble" role="article" aria-label="Your message">
        {message.content}
      </div>
    </div>
  );
});

// ── Feedback buttons ───────────────────────────────────────
function FeedbackButtons({ message }) {
  const [voted, setVoted] = useState(null);
  if (message.streaming || !message.content) return null;
  const queryId = message.correlation_id || message.id;
  const vote = (rating) => {
    setVoted(rating);
    api.submitFeedback(queryId, rating);
  };
  return (
    <div className="msg-feedback" role="group" aria-label="Rate this answer">
      <button
        className={`feedback-btn${voted === 5 ? " voted-good" : ""}`}
        onClick={() => vote(5)}
        disabled={voted !== null}
        aria-label="Good answer"
        title="Good answer"
      >👍</button>
      <button
        className={`feedback-btn${voted === 1 ? " voted-bad" : ""}`}
        onClick={() => vote(1)}
        disabled={voted !== null}
        aria-label="Bad answer"
        title="Bad answer"
      >👎</button>
    </div>
  );
}

// ── Follow-up suggestions ──────────────────────────────────
function generateFollowups(question, answer) {
  const q = (question || "").toLowerCase();
  const a = (answer || "").toLowerCase();
  const pool = [];

  if (q.includes("what") || q.includes("explain") || q.includes("describe"))
    pool.push("Can you give an example?", "What are the implications of this?");
  if (q.includes("how") || q.includes("process") || q.includes("steps"))
    pool.push("What are the potential risks?", "Are there any exceptions?");
  if (a.includes("section") || a.includes("clause") || a.includes("article"))
    pool.push("Show me the exact clause text", "How does this compare to industry standards?");
  if (a.includes("date") || a.includes("deadline") || a.includes("term"))
    pool.push("What happens if this deadline is missed?");
  if (a.includes("payment") || a.includes("fee") || a.includes("cost") || a.includes("price"))
    pool.push("What are the penalty clauses?", "Is there a payment schedule?");
  if (a.includes("risk") || a.includes("liability") || a.includes("obligation"))
    pool.push("Who is responsible for this?", "What are the consequences of non-compliance?");
  if (a.includes("summary") || a.includes("overview") || a.includes("brief"))
    pool.push("Dive deeper into the main topic", "What are the key action items?");

  const defaults = [
    "Tell me more about this",
    "What are the key takeaways?",
    "Are there any exceptions?",
    "What should I watch out for?",
  ];

  const merged = [...new Set([...pool, ...defaults])];
  return merged.slice(0, 3);
}

function FollowupSuggestions({ message, onSuggestion }) {
  if (message.streaming || !message.content || message.low_confidence) return null;

  const suggestions = generateFollowups(
    message._userQuestion || "",
    message.content
  );

  return (
    <div className="followup-suggestions">
      <div className="followup-label">Follow-up</div>
      <div className="followup-list">
        {suggestions.map((s) => (
          <button
            key={s}
            className="followup-btn"
            onClick={() => onSuggestion?.(s)}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── AI message ─────────────────────────────────────────────
const EXTRACTIVE_PREFIX = /^(OpenAI unavailable[^:]*:\s*|Extractive answer[^:]*:\s*|LLM unavailable[^:]*:\s*|Extractive answer from indexed text:\s*)/i;

function cleanContent(text) {
  if (!text) return "";
  const s = typeof text === "string" ? text : JSON.stringify(text);
  return s.replace(EXTRACTIVE_PREFIX, "").trimStart();
}

const AIMessage = memo(function AIMessage({ message, onSuggestion, question }) {
  const rawContent = message.content || "";
  const content = cleanContent(rawContent);
  const isExtractive = EXTRACTIVE_PREFIX.test(rawContent);
  const hasContent = content.length > 0;
  const hasCitations = (message.citations || []).length > 0;
  const showStatus = message.streaming && message.statusStep && !hasContent;
  const showLowConf = !message.streaming && message.low_confidence && hasContent;

  return (
    <div className="msg msg-ai">
      <div className="msg-ai-avatar" aria-hidden="true">AI</div>
      <div className="msg-ai-content">
        <div
          className={`msg-ai-bubble${!hasContent && message.streaming ? " thinking" : ""}`}
          role="article"
          aria-label="AI response"
          aria-live={message.streaming ? "polite" : undefined}
        >
          {!hasContent && message.streaming ? (
            showStatus ? <StatusStep step={message.statusStep} /> : <ThinkingDots />
          ) : (
            <>
              {message.streaming && message.statusStep && (
                <StatusStep step={message.statusStep} />
              )}
              <MarkdownAnswer content={content} streaming={message.streaming} />
              {isExtractive && !message.streaming && (
                <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-4)", display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ opacity: 0.5 }}>⚡</span> Extractive answer · no LLM
                </div>
              )}
              {showLowConf && (
                <div className="low-confidence-notice">
                  <span>⚠</span> Low confidence — answer may be incomplete or outside the indexed documents.
                </div>
              )}
            </>
          )}
        </div>

        {message.error && (
          <div className="msg-error" role="alert">
            ⚠ {typeof message.error === "string" ? message.error : JSON.stringify(message.error)}
          </div>
        )}

        {hasCitations && (
          <div>
            <div className="citations-header"><span>Sources</span></div>
            <div className="citations-grid">
              {message.citations.map((c, i) => (
                <CitationCardV2
                  key={`${c.source_file}-${c.page_number}-${c.block_type}-${i}`}
                  citation={c}
                  index={i}
                />
              ))}
            </div>
          </div>
        )}

        {!message.streaming && hasContent && (
          <div className="msg-meta">
            <span className="msg-latency">
              {message.latency != null ? `${message.latency.toFixed(2)}s` : ""}
              {message.retrieved_count > 0 && ` · ${message.retrieved_count} chunks`}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <CopyButton text={content} />
              {question && (
                <ExportPdfButton
                  question={question}
                  answer={content}
                  citations={message.citations || []}
                />
              )}
              <FeedbackButtons message={message} />
            </div>
          </div>
        )}

        {!message.streaming && hasContent && !message.error && (
          <FollowupSuggestions message={message} onSuggestion={onSuggestion} />
        )}
      </div>
    </div>
  );
}, (prev, next) =>
  prev.message.content === next.message.content &&
  prev.message.streaming === next.message.streaming &&
  prev.message.statusStep === next.message.statusStep &&
  prev.message.citations === next.message.citations &&
  prev.message.low_confidence === next.message.low_confidence &&
  prev.message.error === next.message.error
);

// ── Empty state ────────────────────────────────────────────
const SUGGESTIONS = [
  { icon: "📋", text: "What are the key findings?" },
  { icon: "⚖️", text: "Summarize the payment terms" },
  { icon: "🔍", text: "Which section covers liability?" },
  { icon: "📊", text: "Extract all data tables" },
];

function EmptyState({ onSuggestion }) {
  return (
    <div className="chat-empty">
      <div className="chat-empty-logo" aria-hidden="true">D</div>
      <h1 className="chat-empty-title">DocuMind AI</h1>
      <p className="chat-empty-sub">
        Upload documents and ask anything. Powered by RAG, agentic reasoning,
        and knowledge graph retrieval.
      </p>
      <div className="chat-suggestions">
        {SUGGESTIONS.map(s => (
          <button
            key={s.text}
            className="chat-suggestion"
            onClick={() => onSuggestion?.(s.text)}
            aria-label={`Ask: ${s.text}`}
          >
            <div className="chat-suggestion-icon">{s.icon}</div>
            <div style={{ fontSize: 12, color: "var(--text-2)", lineHeight: 1.4 }}>
              "{s.text}"
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Chat window ────────────────────────────────────────────
export function ChatWindow({ messages, isStreaming, onSuggestion }) {
  const bottomRef = useRef(null);
  const prevLenRef = useRef(messages.length);

  useEffect(() => {
    const added = messages.length > prevLenRef.current;
    prevLenRef.current = messages.length;
    if (isStreaming || added) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [messages, isStreaming]);

  if (messages.length === 0) {
    return (
      <div className="chat-window" style={{ justifyContent: "center" }}>
        <EmptyState onSuggestion={onSuggestion} />
      </div>
    );
  }

  return (
    <div className="chat-window" role="log" aria-live="polite" aria-label="Chat conversation">
      {messages.map((msg, idx) => {
        if (msg.role === "human") return <UserMessage key={msg.id} message={msg} />;
        const prevQuestion = messages.slice(0, idx).reverse().find(m => m.role === "human")?.content || "";
        return <AIMessage key={msg.id} message={msg} onSuggestion={onSuggestion} question={prevQuestion} />;
      })}
      <div ref={bottomRef} aria-hidden="true" />
    </div>
  );
}

ChatWindow.propTypes = {
  messages: PropTypes.arrayOf(PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
    role: PropTypes.oneOf(["human", "assistant"]).isRequired,
    content: PropTypes.string,
    streaming: PropTypes.bool,
    citations: PropTypes.array,
    latency: PropTypes.number,
    error: PropTypes.string,
  })).isRequired,
  isStreaming: PropTypes.bool.isRequired,
  onSuggestion: PropTypes.func,
};
