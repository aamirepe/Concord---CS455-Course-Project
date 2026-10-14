import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Markdown from "./Markdown";
import { parseBlocks } from "../lib/markdown";

describe("parseBlocks", () => {
  it("splits code, headings, lists and paragraphs", () => {
    const blocks = parseBlocks("# Title\n\nSome text\nmore text\n\n- a\n- b\n  continued\n1. one\n\n```js\nlet x = 1;\n\n```\nend");
    expect(blocks).toEqual([
      { kind: "heading", level: 1, text: "Title" },
      { kind: "para", text: "Some text\nmore text" },
      { kind: "list", ordered: false, items: ["a", "b continued"] },
      { kind: "list", ordered: true, items: ["one"] },
      { kind: "code", lang: "js", code: "let x = 1;\n" },
      { kind: "para", text: "end" },
    ]);
  });

  it("keeps an unclosed code block to the end", () => {
    expect(parseBlocks("```\nx")).toEqual([{ kind: "code", lang: "", code: "x" }]);
  });
});

describe("Markdown", () => {
  it("renders inline code, bold and italic", () => {
    const { container } = render(<Markdown text={"Call `f(x)` **now**, *really*."} />);
    expect(container.querySelector("code")).toHaveTextContent("f(x)");
    expect(container.querySelector("strong")).toHaveTextContent("now");
    expect(container.querySelector("em")).toHaveTextContent("really");
  });

  it("never renders HTML from the text", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <script>alert(2)</script>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent("<img src=x");
  });

  it("links only http(s) URLs, opening safely", () => {
    const { container } = render(<Markdown text={"[docs](https://example.com) and [bad](javascript:alert(1))"} />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://example.com");
    expect(links[0]).toHaveAttribute("rel", "noopener noreferrer");
    expect(container).toHaveTextContent("bad (javascript:alert(1))");
  });

  it("copies a code block", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Markdown text={"```python\nprint('hi')\n```"} />);
    fireEvent.click(screen.getByText("Copy"));
    await waitFor(() => expect(screen.getByText("Copied")).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith("print('hi')");
  });
});
