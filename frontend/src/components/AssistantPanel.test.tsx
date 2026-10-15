import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AssistantPanel from "./AssistantPanel";
import { AIAssistantResponse, AIAssistantStatus, AIEvent, api } from "../lib/api";
import { ApiError } from "../lib/errors";

const file = { id: 10, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };
const other = { id: 11, project_id: 1, folder_id: null, path: "util.py", language: "python", created_at: "now" };

const configured: AIAssistantStatus = {
  enabled: true, configured: true, source: "server", provider: "mistral", model: "codestral-latest",
};

function reply(overrides: Partial<AIAssistantResponse> = {}): AIAssistantResponse {
  return {
    response: "Here you go.",
    provider: "mistral",
    model_used: "codestral-latest",
    prompt_tokens: 1, completion_tokens: 1, total_tokens: 2, latency_ms: 5,
    proposal: null,
    steps: [],
    truncated: false,
    ...overrides,
  };
}

function renderPanel(props: Partial<React.ComponentProps<typeof AssistantPanel>> = {}) {
  const onProposal = vi.fn();
  render(
    <AssistantPanel projectId={1} activeFile={file} files={[file, other]} isOwner onProposal={onProposal} {...props} />
  );
  return { onProposal };
}

// Stands in for SessionPage's project-socket fan-out.
function aiBus() {
  const listeners = new Set<(e: AIEvent) => void>();
  return {
    subscribeAi: (fn: (e: AIEvent) => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    emit: (e: AIEvent) => act(() => { listeners.forEach((fn) => fn(e)); }),
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

async function ask(text: string) {
  const input = await screen.findByLabelText("Message the assistant");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByText("Send"));
}

describe("AssistantPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getAssistStatus").mockResolvedValue(configured);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends prompt, mode, active file and history; renders answer and steps", async () => {
    const request = vi.spyOn(api, "requestAssistant")
      .mockResolvedValueOnce(reply({
        response: "It prints numbers.",
        steps: [{ tool: "read_file", summary: "read_file(file 10)", ok: true }],
      }))
      .mockResolvedValueOnce(reply({ response: "Sure." }));
    renderPanel();

    await ask("what does this do?");
    expect(await screen.findByText("It prints numbers.")).toBeInTheDocument();
    expect(screen.getByText("read main.py")).toBeInTheDocument();
    expect(request).toHaveBeenLastCalledWith(1, {
      prompt: "what does this do?", mode: "ask", file_id: 10, history: [], request_id: expect.any(String),
    });

    await ask("in reverse?");
    await screen.findByText("Sure.");
    expect(request.mock.calls[1][1].history).toEqual([
      { role: "user", text: "what does this do?" },
      { role: "assistant", text: "It prints numbers." },
    ]);
  });

  it("fix mode works with an empty prompt and hands the proposal to the editor", async () => {
    const proposal = {
      file_id: 10, original_text: "range(n)", proposed_text: "range(n + 1)", explanation: "off by one", syntax_ok: true,
    };
    const request = vi.spyOn(api, "requestAssistant").mockResolvedValue(reply({ proposal }));
    const { onProposal } = renderPanel();

    fireEvent.change(await screen.findByLabelText("Assistant mode"), { target: { value: "fix" } });
    fireEvent.click(screen.getByText("Send"));

    await screen.findByText("Proposed a change. Review it in the editor.");
    expect(request.mock.calls[0][1]).toMatchObject({ mode: "fix", prompt: "My last run failed. Find and fix the bug." });
    expect(onProposal).toHaveBeenCalledWith(proposal);
  });

  it("points to the other file when the proposal targets it", async () => {
    vi.spyOn(api, "requestAssistant").mockResolvedValue(reply({
      proposal: { file_id: 11, original_text: "a", proposed_text: "b", explanation: null, syntax_ok: true },
    }));
    renderPanel();
    await ask("fix util");
    expect(await screen.findByText("Proposed a change to util.py. Open it to review.")).toBeInTheDocument();
  });

  it("shows a friendly message when rate limited", async () => {
    vi.spyOn(api, "requestAssistant").mockRejectedValue(new ApiError(429, "Too Many Requests", '{"detail":"The AI assistant allows 10 requests per 60 s per project. Try again in 42 s."}', 42));
    renderPanel();
    await ask("hi");
    expect(await screen.findByText("The AI assistant allows 10 requests per 60 s per project. Try again in 42 s.")).toBeInTheDocument();
  });

  it("does not send failed turns as history", async () => {
    const request = vi.spyOn(api, "requestAssistant")
      .mockRejectedValueOnce(new ApiError(502, "Bad Gateway", '{"detail":"AI assistant error: down"}'))
      .mockResolvedValueOnce(reply());
    renderPanel();
    await ask("first");
    await screen.findByText("AI assistant error: down");
    await ask("second");
    await screen.findByText("Here you go.");
    expect(request.mock.calls[1][1].history).toEqual([{ role: "user", text: "first" }]);
  });

  it("not configured: owner can save a key", async () => {
    const status = vi.spyOn(api, "getAssistStatus")
      .mockResolvedValueOnce({ ...configured, configured: false, source: null, provider: null, model: null })
      .mockResolvedValueOnce({ ...configured, source: "project" });
    const save = vi.spyOn(api, "saveAssistConfig").mockResolvedValue({
      id: 1, project_id: 1, provider: "mistral", model_name: null, is_active: true,
    });
    renderPanel({ isOwner: true });

    fireEvent.change(await screen.findByLabelText("Mistral API key"), { target: { value: "sk-abcdefghijkl" } });
    fireEvent.click(screen.getByText("Save key"));

    await waitFor(() => expect(save).toHaveBeenCalledWith(1, { provider: "mistral", api_key: "sk-abcdefghijkl" }));
    expect(await screen.findByLabelText("Message the assistant")).toBeInTheDocument();
    expect(status).toHaveBeenCalledTimes(2);
  });

  it("not configured: owner gets feedback for an obviously bad key", async () => {
    vi.spyOn(api, "getAssistStatus").mockResolvedValue({ ...configured, configured: false, source: null });
    const save = vi.spyOn(api, "saveAssistConfig");
    renderPanel({ isOwner: true });
    fireEvent.change(await screen.findByLabelText("Mistral API key"), { target: { value: "short" } });
    fireEvent.click(screen.getByText("Save key"));
    expect(await screen.findByText("That doesn't look like a valid key.")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("not configured: other members are told to ask the owner", async () => {
    vi.spyOn(api, "getAssistStatus").mockResolvedValue({ ...configured, configured: false, source: null });
    renderPanel({ isOwner: false });
    expect(await screen.findByText(/Ask the project owner/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mistral API key")).not.toBeInTheDocument();
  });

  it("disabled assistant shows a notice instead of the chat", async () => {
    vi.spyOn(api, "getAssistStatus").mockResolvedValue({ ...configured, enabled: false });
    renderPanel();
    expect(await screen.findByText("The AI assistant is turned off.")).toBeInTheDocument();
  });

  it("still offers the chat if the status call fails", async () => {
    vi.spyOn(api, "getAssistStatus").mockRejectedValue(new Error("network"));
    renderPanel();
    expect(await screen.findByLabelText("Message the assistant")).toBeInTheDocument();
  });

  it("streams its own steps while waiting, then shows the reply's steps", async () => {
    const bus = aiBus();
    const pending = deferred<AIAssistantResponse>();
    const request = vi.spyOn(api, "requestAssistant").mockReturnValue(pending.promise);
    renderPanel({ currentUserId: 1, subscribeAi: bus.subscribeAi });

    await ask("look around");
    const requestId = request.mock.calls[0][1].request_id!;
    expect(requestId).toBeTruthy();

    bus.emit({ op: "AI_STARTED", request_id: requestId, user_id: 1, username: "me", mode: "ask", file_id: 10 });
    bus.emit({ op: "AI_STEP", request_id: requestId, user_id: 1, tool: "read_file", summary: "read_file(file 11)", ok: true });
    // Same user, other tab: not ours, and not "someone else" either
    bus.emit({ op: "AI_STEP", request_id: "other-tab", user_id: 1, tool: "list_files", summary: "list_files()", ok: true });
    bus.emit({ op: "AI_STARTED", request_id: "other-tab", user_id: 1, username: "me", mode: "ask", file_id: 10 });

    const live = await screen.findByLabelText("live agent steps");
    expect(live).toHaveTextContent("read util.py");
    expect(live).not.toHaveTextContent("listed files");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await act(async () => {
      pending.resolve(reply({ response: "Done.", steps: [{ tool: "list_files", summary: "list_files()", ok: true }] }));
    });
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    expect(screen.queryByLabelText("live agent steps")).not.toBeInTheDocument();
    expect(screen.getByLabelText("agent steps")).toHaveTextContent("listed files");
  });

  it("shows other members using the AI and their proposals for this file", async () => {
    const bus = aiBus();
    renderPanel({ currentUserId: 1, subscribeAi: bus.subscribeAi });
    await screen.findByLabelText("Message the assistant");

    bus.emit({ op: "AI_STARTED", request_id: "r2", user_id: 2, username: "alice", mode: "fix", file_id: 10 });
    expect(await screen.findByText("alice is asking the AI (fix, main.py)")).toBeInTheDocument();

    bus.emit({ op: "AI_FINISHED", request_id: "r2", user_id: 2, ok: true, has_proposal: true, file_id: 10 });
    expect(screen.queryByText(/alice is asking/)).not.toBeInTheDocument();
    expect(screen.getByText(/alice got an AI proposal for this file/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("drops another member's activity if AI_FINISHED never arrives", async () => {
    const bus = aiBus();
    renderPanel({ currentUserId: 1, subscribeAi: bus.subscribeAi });
    await screen.findByLabelText("Message the assistant");
    vi.useFakeTimers();
    try {
      bus.emit({ op: "AI_STARTED", request_id: "r3", user_id: 2, username: "bob", mode: "ask", file_id: null });
      expect(screen.getByText("bob is asking the AI (ask)")).toBeInTheDocument();
      act(() => { vi.advanceTimersByTime(60_000); });
      expect(screen.queryByText(/bob is asking/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores a proposal another member got for a different file", async () => {
    const bus = aiBus();
    renderPanel({ currentUserId: 1, subscribeAi: bus.subscribeAi });
    await screen.findByLabelText("Message the assistant");
    bus.emit({ op: "AI_STARTED", request_id: "r4", user_id: 2, username: "alice", mode: "fix", file_id: 11 });
    bus.emit({ op: "AI_FINISHED", request_id: "r4", user_id: 2, ok: true, has_proposal: true, file_id: 11 });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  describe("formatting, selection, New chat and requests from elsewhere", () => {
    it("renders the answer's Markdown, with code blocks", async () => {
      vi.spyOn(api, "requestAssistant").mockResolvedValue(reply({ response: "Use **len**:\n\n```python\nprint(len(xs))\n```" }));
      renderPanel();
      await ask("how?");
      expect(await screen.findByText("len", { selector: "strong" })).toBeInTheDocument();
      expect(screen.getByText("print(len(xs))", { selector: "code" })).toBeInTheDocument();
      expect(screen.getByText("python")).toBeInTheDocument();
    });

    it("sends the selection once, and it can be dropped", async () => {
      const request = vi.spyOn(api, "requestAssistant").mockResolvedValue(reply());
      const onClearSelection = vi.fn();
      const selection = { start_line: 3, end_line: 5, text: "x = 1" };
      renderPanel({ selection, onClearSelection });
      expect(await screen.findByText(/Selection: lines 3–5/)).toBeInTheDocument();

      await ask("what is this?");
      await waitFor(() => expect(request).toHaveBeenCalled());
      expect(request.mock.calls[0][1]).toMatchObject({ selection });
      expect(onClearSelection).toHaveBeenCalled();

      fireEvent.click(screen.getByLabelText("Don't send the selection"));
      expect(onClearSelection).toHaveBeenCalledTimes(2);
    });

    it("New chat forgets the conversation", async () => {
      const request = vi.spyOn(api, "requestAssistant").mockResolvedValue(reply());
      renderPanel();
      await ask("first");
      await screen.findByText("Here you go.");
      fireEvent.click(screen.getByText("New chat"));
      expect(screen.queryByText("Here you go.")).not.toBeInTheDocument();
      await ask("second");
      await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
      expect(request.mock.calls[1][1].history).toEqual([]);
    });

    it("sends a request made elsewhere (Ask AI to fix) exactly once", async () => {
      const request = vi.spyOn(api, "requestAssistant").mockResolvedValue(reply());
      const onProposal = vi.fn();
      const props = { projectId: 1, activeFile: file, files: [file], isOwner: true, onProposal };
      const { rerender } = render(<AssistantPanel {...props} />);
      await screen.findByLabelText("Message the assistant");
      const askRequest = { mode: "fix" as const, prompt: "My run of main.py failed. Fix it.", nonce: 1 };
      rerender(<AssistantPanel {...props} askRequest={askRequest} />);
      await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
      expect(request.mock.calls[0][1]).toMatchObject({ mode: "fix", prompt: "My run of main.py failed. Fix it." });
      expect(screen.getByLabelText("Assistant mode")).toHaveValue("fix");
      rerender(<AssistantPanel {...props} askRequest={askRequest} />);
      await new Promise((r) => setTimeout(r, 20));
      expect(request).toHaveBeenCalledTimes(1);
    });
  });
});
