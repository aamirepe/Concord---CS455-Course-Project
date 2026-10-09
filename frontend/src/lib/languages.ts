// Which Monaco language to highlight a file with, and whether it can be run.

const BY_EXTENSION: Record<string, string> = {
  py: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript",
  c: "cpp", cc: "cpp", cpp: "cpp", cxx: "cpp", h: "cpp", hpp: "cpp",
  json: "json", md: "markdown", html: "html", css: "css",
  yml: "yaml", yaml: "yaml", sh: "shell", sql: "sql", xml: "xml", txt: "plaintext",
};

/** Monaco's language id for a file (the backend calls JavaScript "nodejs"). */
export function monacoLanguage(path: string, language?: string): string {
  const ext = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  if (BY_EXTENSION[ext]) return BY_EXTENSION[ext];
  if (language === "nodejs") return "javascript";
  return language && language !== "plaintext" ? language : "plaintext";
}

/** The sandbox runs Python, JavaScript (Node) and C/C++. */
export function isRunnable(file: { language?: string } | null | undefined): boolean {
  return !!file && ["python", "nodejs", "cpp"].includes(file.language ?? "");
}

/** Only Python programs can be debugged. */
export function isDebuggable(file: { language?: string } | null | undefined): boolean {
  return !!file && file.language === "python";
}
