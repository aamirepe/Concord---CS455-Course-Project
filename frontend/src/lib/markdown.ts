// Block-level Markdown parsing for the assistant's answers (rendered by components/Markdown.tsx).

export type Block =
  | { kind: "code"; lang: string; code: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "para"; text: string };

export function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ kind: "para", text: para.join("\n") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/.exec(line);
    if (fence) {
      flush();
      const close = fence[1];
      const code: string[] = [];
      i++;
      while (i < lines.length && !(lines[i].trim().startsWith(close) && lines[i].trim().replace(/[`~]/g, "") === "")) {
        code.push(lines[i]);
        i++;
      }
      out.push({ kind: "code", lang: fence[2], code: code.join("\n") });
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      out.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      continue;
    }
    const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      flush();
      const ordered = /\d/.test(item[1]);
      const last = out[out.length - 1];
      if (last?.kind === "list" && last.ordered === ordered) last.items.push(item[2]);
      else out.push({ kind: "list", ordered, items: [item[2]] });
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    // A continuation line of the previous list item.
    const last = out[out.length - 1];
    if (!para.length && last?.kind === "list" && /^\s+\S/.test(line)) {
      last.items[last.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    para.push(line);
  }
  flush();
  return out;
}
