import { describe, expect, it } from "vitest";
import { isDebuggable, isRunnable, monacoLanguage } from "./languages";

describe("languages", () => {
  it("highlights by extension, falling back to the stored language", () => {
    expect(monacoLanguage("src/app.js", "nodejs")).toBe("javascript");
    expect(monacoLanguage("main.py", "python")).toBe("python");
    expect(monacoLanguage("README.md", "plaintext")).toBe("markdown");
    expect(monacoLanguage("Makefile", "plaintext")).toBe("plaintext");
    expect(monacoLanguage("run", "nodejs")).toBe("javascript");
    expect(monacoLanguage("x.weird", "cpp")).toBe("cpp");
    expect(monacoLanguage("x.weird")).toBe("plaintext");
    expect(monacoLanguage("notes.constructor")).toBe("plaintext");
  });

  it("knows what can be run and debugged", () => {
    expect(isRunnable({ language: "cpp" })).toBe(true);
    expect(isRunnable({ language: "plaintext" })).toBe(false);
    expect(isRunnable(null)).toBe(false);
    expect(isDebuggable({ language: "python" })).toBe(true);
    expect(isDebuggable({ language: "nodejs" })).toBe(false);
  });
});
