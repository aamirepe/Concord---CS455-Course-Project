import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, fireEvent, screen } from "@testing-library/react";
import { Server, WebSocket as MockWebSocket } from "mock-socket";
import * as Y from "yjs";
import { AIProposal, api, setToken } from "../lib/api";
import { ApiError, dismissToast, subscribeToasts, Toast } from "../lib/errors";

// --- Mock @monaco-editor/react -------------------------------------------
// CollabEditor only ever touches this package through the onMount callback,
// and the only thing it does with the resulting `editor` is call
// `.getModel()` (to hand the model to MonacoBinding). So the minimum viable
// fake fires onMount with an editor whose getModel() returns a truthy,
// opaque object, and otherwise renders an inert placeholder.
const fakeModel = { __brand: "fake-model", getValueInRange: () => "picked text" };
let selectionListener: (() => void) | null = null;
let mouseDownListener: ((e: unknown) => void) | null = null;
let fakeSelection: { isEmpty: () => boolean; startLineNumber: number; endLineNumber: number; endColumn: number } | null = null;
const fakeEditorInstance = {
  getModel: () => fakeModel,
  getSelection: () => fakeSelection,
  onDidChangeCursorSelection: (fn: () => void) => { selectionListener = fn; },
  revealLineInCenter: vi.fn(),
  revealLineInCenterIfOutsideViewport: vi.fn(),
  onMouseDown: (fn: (e: unknown) => void) => { mouseDownListener = fn; },
  deltaDecorations: vi.fn((_old: string[], next: unknown[]) => next.map((_, i) => `d${i}`)),
  setSelection: vi.fn(),
  focus: vi.fn(),
};
const fakeMonacoInstance = {};

const lastMonacoProps: { current: { options?: { readOnly?: boolean } } | null } = { current: null };

function MockMonacoEditor(props: any) {
  lastMonacoProps.current = props;
  React.useEffect(() => {
    props.onMount?.(fakeEditorInstance, fakeMonacoInstance);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <div data-testid="fake-monaco-editor" />;
}

vi.mock("@monaco-editor/react", () => ({
  default: MockMonacoEditor,
  DiffEditor: () => <div data-testid="fake-diff-editor" />,
}));

// --- Mock y-monaco's MonacoBinding ---------------------------------------
// Editor.tsx only constructs this and later calls .destroy() on it — it
// never inspects what MonacoBinding does internally. Mocking the real sync
// behavior is y-monaco's own test suite's job, not this component's.
const destroySpy = vi.fn();
const monacoBindingCtor = vi.fn().mockImplementation(() => ({ destroy: destroySpy }));

vi.mock("y-monaco", () => ({
  MonacoBinding: monacoBindingCtor,
}));

// Import after the mocks above are registered.
const { default: CollabEditor } = await import("./Editor");
const { cursorStyles } = await import("../lib/cursors");

function makeToken(sub: number, username: string): string {
  const header = btoa(JSON.stringify({ alg: "none" }));
  const payload = btoa(JSON.stringify({ sub, username }));
  return `${header}.${payload}.sig`;
}

const wsBase = "ws://127.0.0.1:8000";

describe("CollabEditor", () => {
  let server: Server;

  beforeEach(() => {
    vi.stubGlobal("WebSocket", MockWebSocket);
    localStorage.clear();
    destroySpy.mockClear();
    monacoBindingCtor.mockClear();
    server = new Server(`${wsBase}/ws/projects/1/files/2?token=${encodeURIComponent(makeToken(1, "alice"))}`);
    // Like the real gateway: the first frame is the document, and updates are acked.
    server.on("connection", (socket) => {
      const empty = Y.encodeStateAsUpdate(new Y.Doc());
      socket.send(empty.buffer.slice(empty.byteOffset, empty.byteOffset + empty.byteLength) as ArrayBuffer);
      socket.on("message", (data) => {
        if (JSON.parse(data as string).op === "update") socket.send(JSON.stringify({ op: "ack" }));
      });
    });
  });

  afterEach(async () => {
    cleanup();
    await server.stop();
    vi.unstubAllGlobals();
    if (vi.isMockFunction(api.saveFile)) vi.mocked(api.saveFile).mockRestore();
  });

  function renderEditor(overrides: Partial<{ onExecEvent: (e: any) => void; onStructuralUpdate: (m: any) => void; canApply: boolean }> = {}) {
    const token = makeToken(1, "alice");
    setToken({ access_token: token, token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } });

    const onExecEvent = overrides.onExecEvent ?? vi.fn();
    const onStructuralUpdate = overrides.onStructuralUpdate ?? vi.fn();
    const file = { id: 2, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };

    const utils = render(
      <CollabEditor project={{ id: 1 }} file={file} onExecEvent={onExecEvent} onStructuralUpdate={onStructuralUpdate} canApply={overrides.canApply} />
    );
    return { ...utils, onExecEvent, onStructuralUpdate };
  }

  it("renders without crashing when a valid token exists", () => {
    const { getByTestId } = renderEditor();
    expect(getByTestId("fake-monaco-editor")).toBeInTheDocument();
  });

  it("constructs a MonacoBinding with the connection's ytext and awareness once mounted", async () => {
    renderEditor();
    await vi.waitFor(() => expect(monacoBindingCtor).toHaveBeenCalledTimes(1));

    const [ytext, model, editors, awareness] = monacoBindingCtor.mock.calls[0];
    expect(ytext.toString()).toBe(""); // a real Y.Text instance from conn.doc
    expect(model).toBe(fakeModel);
    expect(editors.has(fakeEditorInstance)).toBe(true);
    expect(awareness).toBeDefined();
  });

  it("destroys the previous binding and closes the connection on unmount", async () => {
    const { unmount } = renderEditor();
    await vi.waitFor(() => expect(monacoBindingCtor).toHaveBeenCalledTimes(1));

    unmount();
    expect(destroySpy).toHaveBeenCalledTimes(1);
  });

  it("forwards exec_event WS messages to onExecEvent", async () => {
    const { onExecEvent } = renderEditor();

    await vi.waitFor(() => expect(server.clients()).toHaveLength(1));
    server.emit("message", JSON.stringify({ op: "exec_event", event: { status: "running" } }));

    await vi.waitFor(() => expect(onExecEvent).toHaveBeenCalledWith({ status: "running" }));
  });

  it("forwards WORKSPACE_* WS messages to onStructuralUpdate", async () => {
    const { onStructuralUpdate } = renderEditor();

    await vi.waitFor(() => expect(server.clients()).toHaveLength(1));
    server.emit("message", JSON.stringify({ op: "WORKSPACE_FILE_CREATED", file: { id: 3 } }));

    await vi.waitFor(() =>
      expect(onStructuralUpdate).toHaveBeenCalledWith(expect.objectContaining({ op: "WORKSPACE_FILE_CREATED" }))
    );
  });

  it("does not open a WebSocket connection when no valid token exists", () => {
    localStorage.clear(); // no token set
    const file = { id: 2, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };
    const wsCtorSpy = vi.fn(MockWebSocket as any);
    vi.stubGlobal("WebSocket", wsCtorSpy);

    render(<CollabEditor project={{ id: 1 }} file={file} onExecEvent={vi.fn()} onStructuralUpdate={vi.fn()} />);

    expect(wsCtorSpy).not.toHaveBeenCalled();
  });

  describe("AI proposals", () => {
    const file = { id: 2, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };
    const proposal: AIProposal = {
      file_id: 2,
      original_text: "range(n)",
      proposed_text: "range(n + 1)",
      explanation: "include n",
      syntax_ok: true,
    };

    async function renderWithText(text: string, props: Record<string, unknown>) {
      const token = makeToken(1, "alice");
      setToken({ access_token: token, token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } });
      const onProposalDone = vi.fn();
      const base = { project: { id: 1 }, file, onExecEvent: vi.fn(), onProposalDone };
      const utils = render(<CollabEditor {...base} />);
      await vi.waitFor(() => expect(monacoBindingCtor).toHaveBeenCalledTimes(1));
      const ytext = monacoBindingCtor.mock.calls[0][0];
      ytext.insert(0, text);
      utils.rerender(<CollabEditor {...base} {...props} />);
      return { ...utils, ytext, onProposalDone };
    }

    it("Accept replaces the original span in the shared Y.Text", async () => {
      const { ytext, onProposalDone } = await renderWithText("for i in range(n):\n    print(i)", { proposal });

      expect(screen.getByText("AI Proposal")).toBeInTheDocument();
      expect(screen.getByText("include n")).toBeInTheDocument();
      fireEvent.click(screen.getByText("Accept Change"));

      expect(ytext.toString()).toBe("for i in range(n + 1):\n    print(i)");
      expect(onProposalDone).toHaveBeenCalledTimes(1);
    });

    it("jumps to a search result in this file once it has loaded", async () => {
      const reveal = { fileId: file.id, line: 3, column: 5, length: 4, nonce: 1 };
      const utils = await renderWithText("a\nb\nc   word\n", { reveal: { ...reveal, fileId: 999, nonce: 0 } });
      expect(fakeEditorInstance.revealLineInCenter).not.toHaveBeenCalled(); // another file's result
      utils.rerender(<CollabEditor project={{ id: 1 }} file={file} onExecEvent={vi.fn()} reveal={reveal} />);
      await vi.waitFor(() => expect(fakeEditorInstance.revealLineInCenter).toHaveBeenCalledWith(3));
      expect(fakeEditorInstance.setSelection).toHaveBeenCalledWith({ startLineNumber: 3, startColumn: 5, endLineNumber: 3, endColumn: 9 });
      expect(fakeEditorInstance.focus).toHaveBeenCalled();
      utils.unmount();
    });

    it("breakpoints: clicking the margin toggles one; dots and the paused line are drawn", async () => {
      const onToggleBreakpoint = vi.fn();
      const utils = await renderWithText("a\nb\nc\n", { breakpoints: [2], onToggleBreakpoint });
      mouseDownListener!({ target: { type: 2, position: { lineNumber: 3 } } });
      mouseDownListener!({ target: { type: 6, position: { lineNumber: 1 } } }); // in the text: nothing
      expect(onToggleBreakpoint.mock.calls).toEqual([[3]]);
      const lastDecorations = () => fakeEditorInstance.deltaDecorations.mock.calls.at(-1)![1] as { range: { startLineNumber: number }; options: { glyphMarginClassName?: string; className?: string } }[];
      expect(lastDecorations().map((d) => [d.range.startLineNumber, d.options.glyphMarginClassName])).toEqual([[2, "bp-glyph"]]);

      utils.rerender(<CollabEditor project={{ id: 1 }} file={file} onExecEvent={vi.fn()} breakpoints={[2]} pausedLine={2} />);
      await vi.waitFor(() => expect(lastDecorations()).toHaveLength(2));
      expect(lastDecorations()[1].options).toMatchObject({ className: "debug-current-line", glyphMarginClassName: "bp-glyph debug-arrow" });
      expect(fakeEditorInstance.revealLineInCenterIfOutsideViewport).toHaveBeenCalledWith(2);

      // Without the debugger role there is no toggle.
      utils.rerender(<CollabEditor project={{ id: 1 }} file={file} onExecEvent={vi.fn()} breakpoints={[]} />);
      mouseDownListener!({ target: { type: 2, position: { lineNumber: 1 } } });
      expect(onToggleBreakpoint).toHaveBeenCalledTimes(1);
      utils.unmount();
    });

    it("Reject leaves the text alone", async () => {
      const { ytext, onProposalDone } = await renderWithText("x = range(n)", { proposal });
      fireEvent.click(screen.getByText("Reject"));
      expect(ytext.toString()).toBe("x = range(n)");
      expect(onProposalDone).toHaveBeenCalledTimes(1);
    });

    it("says so and changes nothing when the original text is gone", async () => {
      const shown: Toast[][] = [];
      const unsubscribe = subscribeToasts((t) => shown.push(t));
      const { ytext, onProposalDone } = await renderWithText("someone already changed this", { proposal });
      fireEvent.click(screen.getByText("Accept Change"));
      expect(shown.at(-1)?.[0]?.text).toMatch(/could not find the original text/i);
      expect(ytext.toString()).toBe("someone already changed this");
      expect(onProposalDone).toHaveBeenCalledTimes(1);
      shown.at(-1)?.forEach((t) => dismissToast(t.id));
      unsubscribe();
    });

    it("viewers see the proposal but cannot apply it", async () => {
      const { ytext } = await renderWithText("x = range(n)", { proposal, canApply: false });
      const accept = screen.getByText("Accept Change");
      expect(accept).toBeDisabled();
      expect(screen.getByText("Only editors can apply changes")).toBeInTheDocument();
      fireEvent.click(accept);
      expect(ytext.toString()).toBe("x = range(n)");
    });

    it("blocks a proposal that failed the syntax check", async () => {
      await renderWithText("x = range(n)", { proposal: { ...proposal, syntax_ok: false } });
      expect(screen.getByText("Accept Change")).toBeDisabled();
      expect(screen.getByText("This change would not parse")).toBeInTheDocument();
    });

    it("does not show a proposal meant for another file", async () => {
      await renderWithText("x = range(n)", { proposal: { ...proposal, file_id: 99 } });
      expect(screen.queryByText("AI Proposal")).not.toBeInTheDocument();
    });
  });

  it("is read-only for viewers and editable otherwise, once the file has loaded", async () => {
    const { unmount } = renderEditor({ canApply: false });
    expect(lastMonacoProps.current?.options?.readOnly).toBe(true);
    expect(screen.getByRole("note")).toHaveTextContent("Read-only");
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    unmount();

    renderEditor();
    expect(lastMonacoProps.current?.options?.readOnly).toBe(true); // still loading
    await vi.waitFor(() => expect(lastMonacoProps.current?.options?.readOnly).toBe(false));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("labels presence with the user's name from the login, not the token id", async () => {
    renderEditor();
    const dot = await screen.findByTitle("alice");
    expect(dot).toHaveTextContent("al");
  });

  it("reports the selection (whole lines), and null when it's cleared", async () => {
    const onSelectionChange = vi.fn();
    const token = makeToken(1, "alice");
    setToken({ access_token: token, token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } });
    const file = { id: 2, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };
    render(<CollabEditor project={{ id: 1 }} file={file} onExecEvent={vi.fn()} onSelectionChange={onSelectionChange} />);

    fakeSelection = { isEmpty: () => false, startLineNumber: 2, endLineNumber: 5, endColumn: 1 };
    selectionListener?.();
    await vi.waitFor(() => expect(onSelectionChange).toHaveBeenCalledWith({ start_line: 2, end_line: 4, text: "picked text" }));
    fakeSelection = { isEmpty: () => true, startLineNumber: 1, endLineNumber: 1, endColumn: 1 };
    selectionListener?.();
    await vi.waitFor(() => expect(onSelectionChange).toHaveBeenLastCalledWith(null));
  });

  it("gives other people's cursors their colour and a sanitized name tag", () => {
    const states = new Map<number, unknown>([
      [1, { username: "me", color: "#3fb950" }],
      [2, { username: 'bob"}body{display:none', color: "red;}x{" }],
      [3, { color: "#3fb950" }],
    ]);
    const css = cursorStyles(states as Map<number, { username?: unknown; color?: unknown }>, 1);
    expect(css).toContain('.yRemoteSelectionHead-2::after { content: "bobbodydisplaynone"; background: #58a6ff; }');
    expect(css).not.toContain("-1 ");
    expect(css).not.toContain("-3");
    expect(css.split("\n")).toHaveLength(3);
  });

  it("gives each file its own model and language", () => {
    renderEditor();
    const props = lastMonacoProps.current as { path?: string; language?: string };
    expect(props.path).toBe("concord/1/2/main.py");
    expect(props.language).toBe("python");
  });

  describe("sync status and Save", () => {
    it("shows the connection state", async () => {
      renderEditor();
      expect(screen.getByRole("status")).toHaveTextContent("Connecting");
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("All changes synced"));

      server.clients()[0].close();
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Offline"));
    });

    it("Save writes a version and says so", async () => {
      const saveSpy = vi.spyOn(api, "saveFile").mockResolvedValue({ file_id: 2, version: 3, created: true, saved_at: "2026-09-27T10:00:00Z" });
      renderEditor();
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("All changes synced"));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved as version 3"));
      expect(saveSpy).toHaveBeenCalledWith(1, 2);
    });

    it("Ctrl+S saves too, and reports when nothing changed", async () => {
      const saveSpy = vi.spyOn(api, "saveFile").mockResolvedValue({ file_id: 2, version: 3, created: false, saved_at: null });
      renderEditor();
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("All changes synced"));

      const event = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true); // no browser "Save page" dialog
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("No changes since version 3"));
      expect(saveSpy).toHaveBeenCalledTimes(1);
    });

    it("History opens the file's version history", async () => {
      vi.spyOn(api, "listVersions").mockResolvedValue([]);
      renderEditor();
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("All changes synced"));
      fireEvent.click(screen.getByRole("button", { name: "History" }));
      expect(await screen.findByRole("dialog", { name: "History of main.py" })).toBeInTheDocument();
      expect(await screen.findByText("No versions yet.")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      vi.mocked(api.listVersions).mockRestore();
    });

    it("reports a failed save", async () => {
      vi.spyOn(api, "saveFile").mockRejectedValue(new ApiError(503, "Service Unavailable", '{"detail":"database is down"}'));
      renderEditor();
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("All changes synced"));
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await vi.waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Save failed. database is down"));
    });
  });
});
