import { ReactNode, useState } from "react";
import { parseBlocks } from "../lib/markdown";

/**
 * Renders the assistant's Markdown as React elements: fenced code blocks
 * (with a Copy button), inline code, bold, italic, links, headings, lists and
 * paragraphs. It never produces raw HTML, so text from the model (which can
 * echo file contents) can't inject markup; links are http(s) only.
 */
export default function Markdown({ text }: { text: string }) {
  return <div className="md">{blocks(text)}</div>;
}

function blocks(text: string): ReactNode[] {
  return parseBlocks(text).map((b, i) => {
    switch (b.kind) {
      case "code":
        return <CodeBlock key={i} lang={b.lang} code={b.code} />;
      case "heading":
        return <div key={i} className={`md-h md-h${b.level}`}>{inline(b.text)}</div>;
      case "list": {
        const items = b.items.map((it, j) => <li key={j}>{inline(it)}</li>);
        return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
      }
      default:
        return <p key={i}>{inline(b.text)}</p>;
    }
  });
}

const INLINE = /(`+)([\s\S]*?[^`])\1(?!`)|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|(?<![\w])_([^_\s][^_]*)_(?![\w])|\[([^\]]+)\]\(([^)\s]+)\)/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(INLINE)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<code key={key++}>{m[2]}</code>);
    else if (m[3] ?? m[4]) out.push(<strong key={key++}>{inline(m[3] ?? m[4])}</strong>);
    else if (m[5] ?? m[6]) out.push(<em key={key++}>{inline(m[5] ?? m[6])}</em>);
    else if (m[7]) {
      out.push(/^https?:\/\//i.test(m[8])
        ? <a key={key++} href={m[8]} target="_blank" rel="noopener noreferrer">{m[7]}</a>
        : `${m[7]} (${m[8]})`);
    }
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked: the user can still select the text */ }
  };
  return (
    <div className="md-code">
      <div className="md-code-bar">
        <span>{lang || "code"}</span>
        <button className="link" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  );
}
