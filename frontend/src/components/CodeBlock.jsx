// frontend/src/components/CodeBlock.jsx
// Isolated so ChatWindow can lazy-load this module — keeping react-syntax-highlighter
// (~300 KB gzip) out of the initial bundle and inside vendor-syntax async chunk.
import { memo, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

const CodeBlock = memo(function CodeBlock({ children, className }) {
  const [copied, setCopied] = useState(false);
  const lang = /language-(\w+)/.exec(className || "")?.[1] || "";
  const code = String(children).replace(/\n$/, "");

  const copy = () => {
    navigator.clipboard?.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="md-code-block">
      <div className="md-code-header">
        <span className="md-code-lang">{lang || "code"}</span>
        <button className="md-code-copy" onClick={copy}>{copied ? "✓ Copied" : "Copy"}</button>
      </div>
      <SyntaxHighlighter
        language={lang || "text"}
        style={oneDark}
        PreTag="div"
        customStyle={{ margin: 0, borderRadius: "0 0 6px 6px", fontSize: 12 }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
});

export default CodeBlock;
