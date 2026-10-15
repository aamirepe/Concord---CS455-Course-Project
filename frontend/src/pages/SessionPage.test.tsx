import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SessionPage from "./SessionPage";
import { api, setToken } from "../lib/api";
import Toasts from "../components/Toasts";

// SessionPage is the largest page in the app (~500 lines); full line-by-line
// coverage is disproportionate for this phase. Scope: a smoke-render test,
// the regression test for the addProjectMember string/number fix, and the
// onStructuralUpdate WS-message-driven state updates.

const navigateMock = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigateMock, useParams: () => ({ projectId: "1" }) };
});

// vi.hoisted forces this mock fn to exist before any import is resolved, so
// the very first test in the file sees the same configured instance that
// mockConnectProject() sets up (a plain `vi.fn()` returned from the vi.mock
// factory intermittently missed its queued return value on the file's first
// render otherwise).
const { connectProjectMock } = vi.hoisted(() => ({ connectProjectMock: vi.fn() }));
vi.mock("../lib/ws", () => ({
  connectProject: connectProjectMock,
}));

type EditorProps = React.ComponentProps<typeof import("../components/Editor").default>;
type PanelProps = React.ComponentProps<typeof import("../components/AssistantPanel").default>;
const { editorProps, panelProps } = vi.hoisted(() => ({
  editorProps: { current: null as unknown as EditorProps },
  panelProps: { current: null as unknown as PanelProps },
}));
vi.mock("../components/Editor", () => ({
  default: (props: EditorProps) => {
    editorProps.current = props;
    return <div data-testid="fake-collab-editor" />;
  },
}));
vi.mock("../components/AssistantPanel", () => ({
  default: (props: PanelProps) => {
    panelProps.current = props;
    return <div data-testid="fake-assistant-panel" />;
  },
}));

const project = { id: 1, owner_id: 1, name: "demo-project", created_at: "now" };
const someToken = { access_token: "tok-1", token_type: "bearer", user: { id: 1, username: "alice", email: "a@example.com" } };
const file1 = { id: 10, project_id: 1, folder_id: null, path: "main.py", language: "python", created_at: "now" };

function mockConnectProject() {
  let messageHandler: ((msg: unknown) => void) | null = null;
  let reconnectHandler: (() => void) | null = null;
  const conn = {
    onMessage: vi.fn((cb: (msg: unknown) => void) => { messageHandler = cb; return () => {}; }),
    onReconnect: vi.fn((cb: () => void) => { reconnectHandler = cb; return () => {}; }),
    send: vi.fn(),
    close: vi.fn(),
  };
  connectProjectMock.mockReturnValue(conn as unknown as ReturnType<typeof import("../lib/ws").connectProject>);
  return {
    emit: (msg: unknown) => messageHandler?.(msg),
    reconnect: () => reconnectHandler?.(),
  };
}

describe("SessionPage", () => {
  beforeEach(() => {
    localStorage.clear();
    navigateMock.mockClear();
    setToken(someToken);
    vi.spyOn(api, "getProject").mockResolvedValue(project);
    vi.spyOn(api, "listProjectMembers").mockResolvedValue([]);
    vi.spyOn(api, "listExecutions").mockResolvedValue([]);
    vi.spyOn(api, "listFiles").mockResolvedValue([file1]);
    vi.spyOn(api, "listFolders").mockResolvedValue([]);
    vi.spyOn(api, "listJoinRequests").mockResolvedValue([]);
    vi.spyOn(api, "getActivity").mockResolvedValue([]);
    vi.spyOn(api, "listSnapshots").mockResolvedValue([]);
    vi.spyOn(api, "debugState").mockResolvedValue({ active: null, paused: null });
    vi.spyOn(api, "listBreakpoints").mockResolvedValue([]);
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the project ID, the current user and their role, and copies the ID", async () => {
    mockConnectProject();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<><SessionPage /><Toasts /></>, { wrapper: MemoryRouter });

    expect(await screen.findByText(/Project: demo-project · ID #1/)).toBeInTheDocument();
    const whoami = screen.getByText(/^You:/);
    expect(whoami).toHaveTextContent("You: alice · owner (user #1)");
    expect(whoami).toHaveAttribute("title", "a@example.com");

    fireEvent.click(screen.getByRole("button", { name: "Copy ID" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("1"));
    expect(await screen.findByText("Project ID 1 copied.")).toBeInTheDocument();
  });

  it("renders the project name and file tree without throwing", async () => {
    mockConnectProject();
    render(<SessionPage />, { wrapper: MemoryRouter });

    expect(await screen.findByText(/demo-project/)).toBeInTheDocument();
    expect(within(document.querySelector(".file-tree-container") as HTMLElement).getByText(/main\.py/)).toBeInTheDocument();
    expect(screen.getByTestId("fake-collab-editor")).toBeInTheDocument();
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
  });

  it("regression: handleAddMember sends a NUMBER to api.addProjectMember, not the raw input string", async () => {
    mockConnectProject();
    vi.spyOn(api, "addProjectMember").mockResolvedValue({ message: "added" });

    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByText(/demo-project/);
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /members/i }));
    fireEvent.click(await screen.findByRole("button", { name: /add member/i }));
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. 123 or username/i), { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: /^add member$/i }));

    await waitFor(() => expect(api.addProjectMember).toHaveBeenCalledWith(1, 42, "viewer"));
    // Explicitly assert it's a number, not the string "42" — this is what the
    // bug produced before the fix (Part A.4 of the phase 5 plan).
    expect(typeof (api.addProjectMember as any).mock.calls[0][1]).toBe("number");
  });

  it("sends a username as text so the server can look it up", async () => {
    mockConnectProject();
    vi.spyOn(api, "addProjectMember").mockResolvedValue({ message: "added" });

    render(<><SessionPage /><Toasts /></>, { wrapper: MemoryRouter });
    await screen.findByText(/demo-project/);
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /members/i }));
    fireEvent.click(await screen.findByRole("button", { name: /add member/i }));
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. 123 or username/i), { target: { value: " test_admin " } });
    fireEvent.click(screen.getByRole("button", { name: /^add member$/i }));

    await waitFor(() => expect(api.addProjectMember).toHaveBeenCalledWith(1, "test_admin", "viewer"));
  });

  it("onStructuralUpdate: WORKSPACE_FILE_CREATED adds the new file to the tree", async () => {
    const harness = mockConnectProject();
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByRole("tab", { name: /main\.py/ });
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    harness.emit({ op: "WORKSPACE_FILE_CREATED", file: { id: 11, project_id: 1, folder_id: null, path: "new_file.py", language: "python", created_at: "now" } });

    expect(await screen.findByText(/new_file\.py/)).toBeInTheDocument();
  });

  it("onStructuralUpdate: WORKSPACE_FILE_DELETED removes the file from the tree", async () => {
    const harness = mockConnectProject();
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByRole("tab", { name: /main\.py/ });
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    harness.emit({ op: "WORKSPACE_FILE_DELETED", file_id: file1.id });

    await waitFor(() => expect(screen.queryAllByText(/main\.py/)).toHaveLength(0));
  });

  it("onStructuralUpdate: WORKSPACE_FOLDER_CREATED adds the new folder to the tree", async () => {
    const harness = mockConnectProject();
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByRole("tab", { name: /main\.py/ });
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    harness.emit({ op: "WORKSPACE_FOLDER_CREATED", folder: { id: 20, project_id: 1, parent_folder_id: null, name: "src", created_at: "now" } });

    expect(await screen.findByText(/src/)).toBeInTheDocument();
  });

  it("owner sees pending join requests and approves one with the chosen role", async () => {
    mockConnectProject();
    const bob = { id: 2, username: "bob", email: "b@example.com" };
    const pending = [{ id: 5, project_id: 1, user: bob, status: "pending" as const, created_at: "now", decided_at: null }];
    // Pending on page load and when the modal opens; gone after approval.
    vi.spyOn(api, "listJoinRequests")
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(pending)
      .mockResolvedValue([]);
    vi.spyOn(api, "approveJoinRequest").mockResolvedValue({ user: bob, role: "editor" });

    render(<SessionPage />, { wrapper: MemoryRouter });
    fireEvent.click(await screen.findByRole("button", { name: /members \(1 pending\)/i }));

    fireEvent.change(await screen.findByLabelText("Role for bob"), { target: { value: "editor" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(api.approveJoinRequest).toHaveBeenCalledWith(1, 5, "editor"));
    await waitFor(() => expect(screen.queryByLabelText("Role for bob")).not.toBeInTheDocument());
  });

  it("owner can reject a pending join request", async () => {
    mockConnectProject();
    const pending = [{ id: 6, project_id: 1, user: { id: 3, username: "carol", email: "c@example.com" }, status: "pending" as const, created_at: "now", decided_at: null }];
    vi.spyOn(api, "listJoinRequests").mockResolvedValue(pending);
    vi.spyOn(api, "rejectJoinRequest").mockResolvedValue({ ...pending[0], status: "rejected", decided_at: "now" });

    render(<SessionPage />, { wrapper: MemoryRouter });
    fireEvent.click(await screen.findByRole("button", { name: /members \(1 pending\)/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => expect(api.rejectJoinRequest).toHaveBeenCalledWith(1, 6));
  });

  it("non-owners never fetch join requests", async () => {
    mockConnectProject();
    setToken({ ...someToken, user: { id: 99, username: "zoe", email: "z@example.com" } });

    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByText(/demo-project/);

    expect(api.listJoinRequests).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /members/i })).not.toBeInTheDocument();
  });

  describe("AI assistant wiring", () => {
    const proposal = {
      file_id: 10, original_text: "a", proposed_text: "b", explanation: "why", syntax_ok: true,
    };

    it("passes a proposal from the assistant panel to the editor, applicable by the owner", async () => {
      mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByTestId("fake-assistant-panel");
      expect(panelProps.current).toMatchObject({ projectId: 1, isOwner: true, activeFile: file1 });

      act(() => panelProps.current.onProposal(proposal));
      await waitFor(() => expect(editorProps.current.proposal).toEqual(proposal));
      expect(editorProps.current.canApply).toBe(true);

      act(() => editorProps.current.onProposalDone?.());
      await waitFor(() => expect(editorProps.current.proposal).toBeNull());
    });

    it("viewers get proposals but cannot apply them", async () => {
      mockConnectProject();
      setToken({ ...someToken, user: { id: 2, username: "bob", email: "b@example.com" } });
      vi.spyOn(api, "listProjectMembers").mockResolvedValue([
        { user: { id: 2, username: "bob", email: "b@example.com" }, role: "viewer" },
      ]);
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByTestId("fake-assistant-panel");
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

      await waitFor(() => expect(api.listProjectMembers).toHaveBeenCalled());
      await act(async () => {}); // let the members state settle
      expect(panelProps.current.isOwner).toBe(false);
      act(() => panelProps.current.onProposal(proposal));
      await waitFor(() => expect(editorProps.current.proposal).toEqual(proposal));
      expect(editorProps.current.canApply).toBe(false);
    });

    it("editors can apply", async () => {
      mockConnectProject();
      setToken({ ...someToken, user: { id: 3, username: "carol", email: "c@example.com" } });
      vi.spyOn(api, "listProjectMembers").mockResolvedValue([
        { user: { id: 3, username: "carol", email: "c@example.com" }, role: "editor" },
      ]);
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByTestId("fake-assistant-panel");
      await waitFor(() => expect(editorProps.current.canApply).toBe(true));
    });
  });

  it("forwards AI_* project-socket events to the assistant panel", async () => {
    const { emit } = mockConnectProject();
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByTestId("fake-assistant-panel");
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    expect(panelProps.current.currentUserId).toBe(1);
    const seen: unknown[] = [];
    let unsubscribe = () => {};
    act(() => { unsubscribe = panelProps.current.subscribeAi!((e) => seen.push(e)); });

    const step = { op: "AI_STEP", request_id: "r1", user_id: 2, tool: "read_file", summary: "read_file(file 10)", ok: true };
    act(() => emit(step));
    act(() => emit({ op: "WORKSPACE_FILE_DELETED", file_id: 999 }));
    expect(seen).toEqual([step]);

    unsubscribe();
    act(() => emit({ ...step, request_id: "r2" }));
    expect(seen).toHaveLength(1);
  });

  it("shows a new folder once when the socket event arrives before the create response", async () => {
    const harness = mockConnectProject();
    const folder = { id: 20, project_id: 1, parent_folder_id: null, name: "src", created_at: "now" };
    let respond: (f: typeof folder) => void = () => {};
    vi.spyOn(api, "createFolder").mockReturnValue(new Promise(resolve => { respond = resolve; }));
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByRole("tab", { name: /main\.py/ });
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    fireEvent.click(screen.getByText("+ New folder"));
    fireEvent.change(screen.getByPlaceholderText("folder_name"), { target: { value: "src" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    act(() => harness.emit({ op: "WORKSPACE_FOLDER_CREATED", folder }));
    await act(async () => respond(folder));

    await waitFor(() => expect(screen.queryByPlaceholderText("folder_name")).not.toBeInTheDocument());
    expect(document.querySelectorAll(".folder-name")).toHaveLength(1);
  });

  it("shows a new file once when the socket event arrives before the create response", async () => {
    const harness = mockConnectProject();
    const created = { ...file1, id: 11, path: "notes.py" };
    let respond: (f: typeof created) => void = () => {};
    vi.spyOn(api, "createFile").mockReturnValue(new Promise(resolve => { respond = resolve; }));
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByRole("tab", { name: /main\.py/ });
    await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

    fireEvent.click(screen.getByText("+ New file"));
    fireEvent.change(screen.getByPlaceholderText("file_name.py"), { target: { value: "notes.py" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    act(() => harness.emit({ op: "WORKSPACE_FILE_CREATED", file: created }));
    await act(async () => respond(created));

    await waitFor(() => expect(screen.queryByPlaceholderText("file_name.py")).not.toBeInTheDocument());
    const nodes = [...document.querySelectorAll(".file-node")].filter(n => n.textContent?.includes("notes.py"));
    expect(nodes).toHaveLength(1);
  });

  it("creates a new file in the project root when no folder is selected", async () => {
    mockConnectProject();
    const created = { ...file1, id: 11, path: "notes.py" };
    const createFile = vi.spyOn(api, "createFile").mockResolvedValue(created);
    render(<SessionPage />, { wrapper: MemoryRouter });
    await screen.findByText(/demo-project/);

    fireEvent.click(screen.getByText("+ New file"));
    expect(screen.getByText("In: project root")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("file_name.py"), { target: { value: "notes.py" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createFile).toHaveBeenCalledWith(1, "notes.py", null));
    expect(window.alert).not.toHaveBeenCalled();
  });

  describe("recent changes and modified files", () => {
    const file2 = { ...file1, id: 11, path: "utils.py" };
    const activity = [{
      file_id: 11, path: "utils.py", lines_added: 13, lines_removed: 3, last_changed_at: new Date().toISOString(),
      authors: [
        { user_id: 2, username: "bob", lines_added: 12, lines_removed: 3, last_changed_at: new Date().toISOString() },
        { user_id: 1, username: "alice", lines_added: 1, lines_removed: 0, last_changed_at: new Date().toISOString() },
      ],
    }];

    it("lists who changed which file by how many lines, and opens the file on click", async () => {
      mockConnectProject();
      vi.mocked(api.listFiles).mockResolvedValue([file1, file2]);
      vi.mocked(api.getActivity).mockResolvedValue(activity);
      render(<SessionPage />, { wrapper: MemoryRouter });

      const panel = await screen.findByRole("region", { name: "Recent changes" });
      await waitFor(() => expect(panel).toHaveTextContent("bob +12 −3"));
      expect(panel).toHaveTextContent("alice +1 −0");
      expect(panel).toHaveTextContent("just now");

      fireEvent.click(within(panel).getByRole("button", { name: "utils.py" }));
      await waitFor(() => expect(editorProps.current.file.id).toBe(11));
    });

    it("marks a file someone else changed until you open it, and refreshes the list", async () => {
      const { emit } = mockConnectProject();
      vi.mocked(api.listFiles).mockResolvedValue([file1, file2]);
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByText(/utils\.py/);
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
      const fetches = vi.mocked(api.getActivity).mock.calls.length;

      const change = { user_id: 2, username: "bob", lines_added: 2, lines_removed: 0 };
      act(() => emit({ op: "FILE_CHANGED", file_id: 11, version: 4, changes: [change] }));
      const dot = await screen.findByLabelText("modified");
      expect(dot).toHaveAttribute("title", "Changed by bob since you last opened it");
      await waitFor(() => expect(vi.mocked(api.getActivity).mock.calls.length).toBeGreaterThan(fetches));

      fireEvent.click(screen.getByText(/utils\.py/));
      await waitFor(() => expect(screen.queryByLabelText("modified")).not.toBeInTheDocument());
    });

    it("does not mark files for your own edits or for the file you have open", async () => {
      const { emit } = mockConnectProject();
      vi.mocked(api.listFiles).mockResolvedValue([file1, file2]);
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByText(/utils\.py/);
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

      act(() => emit({ op: "FILE_CHANGED", file_id: 11, version: 4, changes: [{ user_id: 1, username: "alice", lines_added: 1, lines_removed: 0 }] }));
      act(() => emit({ op: "FILE_CHANGED", file_id: 10, version: 4, changes: [{ user_id: 2, username: "bob", lines_added: 1, lines_removed: 0 }] }));
      expect(screen.queryByLabelText("modified")).not.toBeInTheDocument();
    });

    it("shows who is in which file from project_presence", async () => {
      const { emit } = mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /main\.py/ });
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
      act(() => emit({ op: "project_presence", user_id: 2, file_id: 10 }));
      const inTree = () => document.querySelector(".file-tree-container .presence-avatars");
      await waitFor(() => expect(inTree()).toBeTruthy());
      expect(document.querySelector(".editor-tab .presence-avatars")).toBeTruthy();

      // Leaving another file doesn't remove them; leaving this one does.
      act(() => emit({ op: "project_presence", user_id: 2, file_id: null, left_file_id: 99 }));
      expect(inTree()).toBeTruthy();
      act(() => emit({ op: "project_presence", user_id: 2, file_id: null, left_file_id: 10 }));
      await waitFor(() => expect(inTree()).toBeNull());
    });

    it("does not show yourself as present", async () => {
      const { emit } = mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /main\.py/ });
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
      act(() => emit({ op: "project_presence", user_id: 1, file_id: 10 }));
      expect(document.querySelector(".presence-avatars")).toBeNull();
    });

    it("reloads the file list and recent changes after the project socket reconnects", async () => {
      const { reconnect } = mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /main\.py/ });
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
      vi.mocked(api.listFiles).mockResolvedValue([file1, file2]);
      const fetches = vi.mocked(api.getActivity).mock.calls.length;

      act(() => reconnect());
      expect(await screen.findByText(/utils\.py/)).toBeInTheDocument();
      expect(vi.mocked(api.getActivity).mock.calls.length).toBeGreaterThan(fetches);
    });
  });

  describe("console", () => {
    it("runs with the program input and groups the run's events", async () => {
      const { emit } = mockConnectProject();
      const run = vi.spyOn(api, "run").mockResolvedValue({
        id: 42, project_id: 1, file_id: 10, status: "queued", exit_code: null, stdout: "", stderr: "",
        queued_at: "now", started_at: null, finished_at: null, worker_id: null,
      });
      vi.spyOn(api, "getExecution").mockResolvedValue({
        id: 42, project_id: 1, file_id: 10, status: "running", exit_code: null, stdout: "", stderr: "",
        queued_at: "now", started_at: null, finished_at: null, worker_id: null,
      });
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByText(/demo-project/);
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());

      fireEvent.click(screen.getByText("Input"));
      fireEvent.change(screen.getByLabelText("Program input"), { target: { value: "3\n4\n" } });
      fireEvent.click(screen.getByRole("button", { name: "Run" }));
      await waitFor(() => expect(run).toHaveBeenCalledWith(1, 10, "3\n4\n"));

      act(() => emit({ op: "EXECUTION_QUEUED", execution_id: 42, file_id: 10, path: "main.py", user_id: 1, username: "alice" }));
      act(() => editorProps.current.onExecEvent({ type: "execution_output", execution_id: 42, stdout: "7\n", stderr: "", exit_code: 0 }));
      act(() => editorProps.current.onExecEvent({ type: "execution_status", execution_id: 42, status: "completed" }));
      const block = await screen.findByRole("region", { name: "Run 42" });
      expect(block).toHaveTextContent("main.py · run #42 by alice");
      expect(block).toHaveTextContent("with input");
      expect(block).toHaveTextContent("7");
    });

    it("Ask AI to fix sends a fix request to the assistant", async () => {
      mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByTestId("fake-assistant-panel");
      act(() => editorProps.current.onExecEvent({ type: "execution_output", execution_id: 5, stdout: "", stderr: "boom", exit_code: 1 }));
      act(() => editorProps.current.onExecEvent({ type: "execution_status", execution_id: 5, status: "failed" }));
      fireEvent.click(await screen.findByText("Ask AI to fix"));
      await waitFor(() => expect(panelProps.current.askRequest).toMatchObject({ mode: "fix" }));
      expect(panelProps.current.askRequest?.prompt).toContain("(exit code 1)");
    });

    it("passes the editor selection to the assistant, and drops it on file switch", async () => {
      mockConnectProject();
      vi.mocked(api.listFiles).mockResolvedValue([file1, { ...file1, id: 11, path: "utils.py" }]);
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByTestId("fake-assistant-panel");
      const selection = { start_line: 1, end_line: 2, text: "x" };
      act(() => editorProps.current.onSelectionChange?.(selection));
      await waitFor(() => expect(panelProps.current.selection).toEqual(selection));
      fireEvent.click(screen.getByText(/utils\.py/));
      await waitFor(() => expect(panelProps.current.selection).toBeNull());
    });
  });

  describe("tabs, quick open and search", () => {
    const file2 = { ...file1, id: 11, path: "lib/util.py" };
    const file3 = { ...file1, id: 12, path: "notes.txt", language: "plaintext" };

    async function renderWithFiles() {
      vi.spyOn(api, "listFiles").mockResolvedValue([file1, file2, file3]);
      const harness = mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /main\.py/ });
      await waitFor(() => expect(connectProjectMock).toHaveBeenCalled());
      return harness;
    }
    const tree = () => within(document.querySelector(".file-tree-container") as HTMLElement);

    it("opens files as tabs, closes them, and remembers them for next time", async () => {
      await renderWithFiles();
      fireEvent.click(tree().getByText(/util\.py/));
      expect(screen.getAllByRole("tab", { name: /\.py|\.txt/ })).toHaveLength(2);
      expect(editorProps.current.file.id).toBe(11);
      fireEvent.click(tree().getByText(/notes\.txt/));
      expect(editorProps.current.file.id).toBe(12);
      expect(screen.getByRole("button", { name: /^run$/i })).toBeDisabled(); // not code

      fireEvent.click(screen.getByRole("tab", { name: /util\.py/ }));
      expect(editorProps.current.file.id).toBe(11);
      fireEvent.click(screen.getByLabelText("Close util.py"));
      expect(editorProps.current.file.id).toBe(12); // the tab to the right
      expect(JSON.parse(localStorage.getItem("concord:tabs:1")!)).toEqual({ open: [10, 12], active: 12 });

      // Ctrl+Alt+W closes the current tab.
      fireEvent.keyDown(window, { key: "w", ctrlKey: true, altKey: true });
      expect(editorProps.current.file.id).toBe(10);
    });

    it("reopens the saved tabs", async () => {
      localStorage.setItem("concord:tabs:1", JSON.stringify({ open: [11, 12, 999], active: 12 }));
      vi.spyOn(api, "listFiles").mockResolvedValue([file1, file2, file3]);
      mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /notes\.txt/ });
      expect(screen.getAllByRole("tab").filter((t) => t.className.includes("editor-tab"))).toHaveLength(2);
      expect(editorProps.current.file.id).toBe(12);
    });

    it("a deleted file's tab goes away and another tab is shown", async () => {
      const { emit } = await renderWithFiles();
      fireEvent.click(tree().getByText(/util\.py/));
      act(() => emit({ op: "WORKSPACE_FILE_DELETED", file_id: 11 }));
      await waitFor(() => expect(editorProps.current.file.id).toBe(10));
      expect(screen.queryByRole("tab", { name: /util\.py/ })).toBeNull();
    });

    it("Ctrl+P opens a file by name", async () => {
      await renderWithFiles();
      fireEvent.keyDown(window, { key: "p", ctrlKey: true });
      const box = await screen.findByLabelText("File name");
      fireEvent.change(box, { target: { value: "util" } });
      fireEvent.keyDown(box, { key: "Enter" });
      expect(editorProps.current.file.id).toBe(11);
      expect(screen.queryByLabelText("File name")).toBeNull();
    });

    it("Ctrl+Shift+F searches, and a result opens the file at the match", async () => {
      vi.spyOn(api, "search").mockResolvedValue({
        query: "helper", total: 1, truncated: false,
        files: [{ file_id: 11, path: "lib/util.py", matches: [{ line: 7, column: 3, length: 6, preview: "  helper()", preview_column: 2 }] }],
      });
      await renderWithFiles();
      fireEvent.keyDown(window, { key: "F", ctrlKey: true, shiftKey: true });
      const box = await screen.findByLabelText("Search all files");
      await waitFor(() => expect(box).toHaveFocus());
      fireEvent.change(box, { target: { value: "helper" } });
      fireEvent.click(await screen.findByTitle("Line 7"));
      expect(editorProps.current.file.id).toBe(11);
      expect(editorProps.current.reveal).toMatchObject({ fileId: 11, line: 7, column: 3, length: 6 });

      fireEvent.click(screen.getByRole("tab", { name: "Files" }));
      expect(tree().getByText(/util\.py/)).toBeInTheDocument();
    });
  });

  describe("debugger", () => {
    const helper = { ...file1, id: 11, path: "helper.py" };
    const pausedEvent = {
      type: "debug_paused", execution_id: 50, reason: "breakpoint", by: null, path: "helper.py", file_id: 11, line: 2,
      stack: [{ id: 2, name: "add", line: 2, path: "helper.py", file_id: 11 }, { id: 3, name: "<module>", line: 5, path: "main.py", file_id: 10 }],
      scopes: [{ name: "Locals", ref: 4 }], variables: [{ name: "total", value: "3", ref: 0 }],
    };

    async function renderAs(role: "owner" | "viewer") {
      vi.spyOn(api, "listFiles").mockResolvedValue([file1, helper]);
      if (role === "viewer") {
        vi.spyOn(api, "getProject").mockResolvedValue({ ...project, owner_id: 99 });
        vi.spyOn(api, "listProjectMembers").mockResolvedValue([{ user: someToken.user, role: "viewer" }]);
      }
      mockConnectProject();
      render(<SessionPage />, { wrapper: MemoryRouter });
      await screen.findByRole("tab", { name: /main\.py/ });
      await waitFor(() => expect(editorProps.current).toBeTruthy());
    }

    it("breakpoints load per file and toggle from the editor margin", async () => {
      vi.spyOn(api, "listBreakpoints").mockResolvedValue([
        { id: 1, file_id: 10, line: 3, enabled: true, created_by: 1, created_at: "now" },
        { id: 2, file_id: 10, line: 4, enabled: false, created_by: 1, created_at: "now" },
      ]);
      const put = vi.spyOn(api, "setBreakpoints").mockResolvedValue([]);
      await renderAs("owner");
      await waitFor(() => expect(editorProps.current.breakpoints).toEqual([3]));
      act(() => { void editorProps.current.onToggleBreakpoint!(1); });
      expect(editorProps.current.breakpoints).toEqual([1, 3]); // shown right away
      await waitFor(() => expect(put).toHaveBeenCalledWith(1, 10, [1, 3]));

      put.mockRejectedValueOnce(new Error("nope"));
      act(() => { void editorProps.current.onToggleBreakpoint!(3); });
      await waitFor(() => expect(editorProps.current.breakpoints).toEqual([1, 3])); // put back

      // Someone else's change arrives as an event.
      act(() => editorProps.current.onExecEvent({ type: "breakpoints_updated", lines_by_file: { "10": [7] } }));
      expect(editorProps.current.breakpoints).toEqual([7]);
    });

    it("Debug starts a session; pauses show in the panel and follow the program to its file", async () => {
      const start = vi.spyOn(api, "debug").mockResolvedValue({ id: 50, project_id: 1, file_id: 10, status: "queued", exit_code: null, stdout: "", stderr: "", queued_at: "now", started_at: null, finished_at: null, worker_id: null });
      const command = vi.spyOn(api, "debugCommand").mockResolvedValue({ delivered: true });
      await renderAs("owner");
      fireEvent.click(screen.getByRole("button", { name: /^debug$/i }));
      await waitFor(() => expect(start).toHaveBeenCalledWith(1, 10));
      expect(screen.getByRole("tab", { name: /debug/i })).toHaveAttribute("aria-selected", "true");

      act(() => editorProps.current.onExecEvent({ type: "debug_started", execution_id: 50, file_id: 10, path: "main.py", username: "alice" }));
      act(() => editorProps.current.onExecEvent(pausedEvent));
      await waitFor(() => expect(editorProps.current.file.id).toBe(11)); // followed into helper.py
      expect(editorProps.current.pausedLine).toBe(2);
      expect(screen.getByText(/Paused at helper\.py:2/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: /step over/i }));
      await waitFor(() => expect(command).toHaveBeenLastCalledWith(1, 50, "next", {}));
      fireEvent.keyDown(window, { key: "F5" });
      await waitFor(() => expect(command).toHaveBeenLastCalledWith(1, 50, "continue", {}));
      fireEvent.click(screen.getByTitle("main.py:5"));
      await waitFor(() => expect(command).toHaveBeenLastCalledWith(1, 50, "frame", { frame_id: 3 }));
      await waitFor(() => expect(editorProps.current.file.id).toBe(10));
      expect(screen.getByRole("button", { name: /^debug$/i })).toBeDisabled(); // one session at a time

      act(() => editorProps.current.onExecEvent({ type: "debug_stopped", execution_id: 50, reason: "exited", exit_code: 0 }));
      expect(screen.getByText("The program finished (exit code 0).")).toBeInTheDocument();
    });

    it("Continue waits for a breakpoint change that is still being saved", async () => {
      vi.spyOn(api, "debugState").mockResolvedValue({
        active: { execution_id: 50, file_id: 10, path: "main.py", username: "alice" },
        paused: { ...pausedEvent, path: "main.py", file_id: 10, line: 3 } as never,
      });
      vi.spyOn(api, "listBreakpoints").mockResolvedValue([
        { id: 1, file_id: 10, line: 3, enabled: true, created_by: 1, created_at: "now" },
      ]);
      const command = vi.spyOn(api, "debugCommand").mockResolvedValue({ delivered: true });
      let finishSave!: () => void;
      const put = vi.spyOn(api, "setBreakpoints").mockImplementationOnce(
        () => new Promise((resolve) => { finishSave = () => resolve([]); }),
      ).mockResolvedValue([]);
      await renderAs("owner");
      await waitFor(() => expect(editorProps.current.breakpoints).toEqual([3]));

      act(() => { void editorProps.current.onToggleBreakpoint!(3); }); // remove it
      act(() => { void editorProps.current.onToggleBreakpoint!(5); }); // and add another
      fireEvent.keyDown(window, { key: "F5" });
      await new Promise((r) => setTimeout(r, 20));
      expect(put).toHaveBeenCalledTimes(1); // the second save waits for the first
      expect(command).not.toHaveBeenCalled(); // so does Continue

      act(() => finishSave());
      await waitFor(() => expect(command).toHaveBeenCalledWith(1, 50, "continue", {}));
      expect(put.mock.calls.map((c) => c[2])).toEqual([[], [5]]);
      expect(put.mock.invocationCallOrder[1]).toBeLessThan(command.mock.invocationCallOrder[0]);
    });

    it("someone opening the project mid-session sees the pause; viewers can't step or set breakpoints", async () => {
      vi.spyOn(api, "debugState").mockResolvedValue({
        active: { execution_id: 50, file_id: 10, path: "main.py", username: "dave" },
        paused: pausedEvent as never,
      });
      await renderAs("viewer");
      fireEvent.click(screen.getByRole("tab", { name: /debug/i }));
      expect(await screen.findByText(/Paused at helper\.py:2/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
      expect(editorProps.current.onToggleBreakpoint).toBeUndefined();
      expect(screen.getByRole("button", { name: /^debug$/i })).toBeDisabled();
    });
  });
});
