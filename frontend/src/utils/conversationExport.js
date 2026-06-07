// Conversation export helpers — extracted from App.jsx to keep the shell lean.
// Each takes the chat `messages` array and performs the browser-side export.

function citationLabel(c) {
  const file = (c.source_file || "").split("/").pop().split("\\").pop();
  return `${file} p.${c.page_number ?? "?"}`;
}

/** Build a Markdown transcript and trigger a download. */
export function downloadConversationMarkdown(messages) {
  if (!messages || messages.length === 0) return;
  const lines = messages.map((m) => {
    const role = m.role === "human" ? "**You**" : "**DocuMind AI**";
    const content = m.content || "";
    const citations = (m.citations || []).length > 0
      ? "\n\n_Sources: " + m.citations.map(citationLabel).join(", ") + "_"
      : "";
    return `${role}\n\n${content}${citations}`;
  });
  const md = `# DocuMind AI Conversation\n_Exported ${new Date().toLocaleString()}_\n\n---\n\n${lines.join("\n\n---\n\n")}`;
  const blob = new Blob([md], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `docmind-chat-${Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Open a print-ready HTML report of the conversation in a new window. */
export function printConversationPdf(messages) {
  if (!messages || messages.length === 0) return;
  const rows = messages.map((m) => {
    const role = m.role === "human" ? "You" : "DocuMind AI";
    const content = (m.content || "")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
    const cites = (m.citations || [])
      .map((c) => `<small>[${citationLabel(c)}]</small>`)
      .join(" ");
    return `<div class="msg-block ${m.role}"><div class="msg-role">${role}</div><div class="msg-body">${content}${cites ? `<div class="cites">${cites}</div>` : ""}</div></div>`;
  }).join("");

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>DocuMind AI Report</title>
<style>body{font-family:Georgia,serif;max-width:720px;margin:40px auto;color:#1e293b;line-height:1.6}
h1{font-size:22px;color:#0f172a;margin-bottom:4px}.meta{font-size:12px;color:#64748b;margin-bottom:28px}
.msg-block{margin-bottom:20px;padding:14px 18px;border-radius:8px}
.msg-block.human{background:#f1f5f9;border-left:3px solid #0d9488}
.msg-block.assistant{background:#f8fafc;border-left:3px solid #0ea5e9}
.msg-role{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;color:#64748b}
.msg-body{font-size:14px}
.cites{margin-top:8px;font-size:11px;color:#94a3b8}
small{margin-right:4px}</style></head>
<body><h1>DocuMind AI — Conversation Report</h1>
<div class="meta">Exported ${new Date().toLocaleString()} · ${messages.length} messages</div>
${rows}</body></html>`;

  const w = window.open("", "_blank");
  if (w) {
    w.document.write(html);
    w.document.close();
    w.onload = () => { w.print(); };
  }
}
