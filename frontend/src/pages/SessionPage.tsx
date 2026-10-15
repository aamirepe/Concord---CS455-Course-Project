import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, AIEvent, AIProposal, Project, Execution, FileActivity, FileChangedEvent, FileMeta, FolderMeta, getToken, getUser, JoinRequest, parseMemberRef, ProjectMember } from "../lib/api";
import { connectProject } from "../lib/ws";
import CollabEditor from "../components/Editor";
import FileTree from "../components/FileTree";
import ErrorBoundary from "../components/ErrorBoundary";
import AssistantPanel, { AskRequest, EditorSelection } from "../components/AssistantPanel";
import RecentChanges from "../components/RecentChanges";
import SnapshotsPanel from "../components/SnapshotsPanel";
import ConsolePanel from "../components/ConsolePanel";
import { applyRunEvent, Run, RunEvent } from "../lib/runs";
import { friendlyError, showError, toast } from "../lib/errors";
import { isDebuggable, isRunnable } from "../lib/languages";
import { applyDebugEvent, breakpointsFromEvent, DebugFrame, DebugState, emptyDebug, isDebugEvent } from "../lib/debug";
import DebugPanel from "../components/DebugPanel";
import { loadTabs, neighbourAfterClose, saveTabs } from "../lib/tabs";
import EditorTabs from "../components/EditorTabs";
import QuickOpen from "../components/QuickOpen";
import SearchPanel, { SearchPanelHandle } from "../components/SearchPanel";
import type { Reveal } from "../components/Editor";

// Insert or replace by id. A new file or folder reaches its creator twice, from the
// REST response and from the project socket, in either order.
function upsertById<T extends { id: number }>(list: T[], item: T): T[] {
  const idx = list.findIndex(x => x.id === item.id);
  if (idx === -1) return [...list, item];
  const next = [...list];
  next[idx] = item;
  return next;
}

export default function SessionPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const pid = Number(projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [files, setFiles] = useState<FileMeta[]>([]);
  const [folders, setFolders] = useState<FolderMeta[]>([]);
  const [active, setActive] = useState<FileMeta | null>(null);
  const [activeFolder, setActiveFolder] = useState<FolderMeta | null>(null);
  const [presence, setPresence] = useState<Record<number, number>>({});
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [projectMembers, setProjectMembers] = useState<ProjectMember[]>([]);
  const [membersModal, setMembersModal] = useState<"list" | "add" | null>(null);
  const [memberForm, setMemberForm] = useState({ userId: "", role: "viewer" });
  const [joinRequests, setJoinRequests] = useState<JoinRequest[]>([]);
  const [requestRoles, setRequestRoles] = useState<Record<number, string>>({});
  const [exec, setExec] = useState<Execution | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [stdin, setStdin] = useState("");
  const [showInput, setShowInput] = useState(false);
  const [proposal, setProposal] = useState<AIProposal | null>(null);
  const [activity, setActivity] = useState<FileActivity[]>([]);
  const [selection, setSelection] = useState<EditorSelection | null>(null);
  const [askRequest, setAskRequest] = useState<AskRequest | null>(null);
  // Files someone else changed since you last opened them: file id -> who.
  const [modified, setModified] = useState<Record<number, string>>({});
  // Open tabs (file ids, in order) and recently opened files, most recent first.
  const [tabIds, setTabIds] = useState<number[]>([]);
  const [recent, setRecent] = useState<number[]>([]);
  const tabsRestored = useRef(false);
  const [sidebarView, setSidebarView] = useState<"files" | "search">("files");
  const [quickOpen, setQuickOpen] = useState(false);
  // Where to scroll the editor to (from a search result), once that file is open.
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const searchRef = useRef<SearchPanelHandle>(null);
  // The project's debug session, breakpoint lines by file, and which bottom tab shows.
  const [debug, setDebug] = useState<DebugState>(emptyDebug);
  const [breakpoints, setBreakpoints] = useState<Record<number, number[]>>({});
  const [bottomTab, setBottomTab] = useState<"console" | "debug">("console");
  const bottomTabRef = useRef(bottomTab);
  // A debug event that may switch the bottom tab or the open file (handled in an effect).
  const [pendingFollow, setPendingFollow] = useState<{ type?: string; file_id?: unknown } | null>(null);
  bottomTabRef.current = bottomTab;
  const activeIdRef = useRef<number | null>(null);
  activeIdRef.current = active?.id ?? null;
  const [modal, setModal] = useState<{
    mode: "create" | "rename";
    type: "file" | "folder";
    name: string;
    targetId?: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      navigate("/login");
      return;
    }
    if (!pid || isNaN(pid)) {
      setError("Invalid project ID");
      return;
    }

    async function loadProjectData() {
      try {
        const p = await api.getProject(pid);

        if (!p) {
          setError("Project not found");
          return;
        }

        setProject(p);

        Promise.all([
          api.listProjectMembers(pid).then(m => {
            setMembers(m || []);
          }).catch(e => console.error("SessionPage: Failed to load members:", e)),
          api.listExecutions(pid).catch(e => console.error("SessionPage: Failed to load executions:", e))
        ]);

        const [fs, flds] = await Promise.all([
          api.listFiles(p.id),
          api.listFolders(p.id)
        ]);
        setFiles(fs || []);
        setFolders(flds || []);
        // Reopen the tabs this browser had open for the project.
        const all = fs || [];
        const saved = loadTabs(p.id);
        const open = (saved?.open ?? []).filter((id) => all.some((f) => f.id === id));
        const first = all.find((f) => f.id === saved?.active && open.includes(f.id)) ?? all.find((f) => f.id === open[0]) ?? all[0];
        if (first) {
          setActive(first);
          setTabIds(open.includes(first.id) ? open : [...open, first.id]);
          setRecent([first.id]);
        }
        tabsRestored.current = true;
      } catch (err) {
        console.error("SessionPage: ERROR in loadProjectData:", err);
        setError(friendlyError(err));
      }
    }

    loadProjectData();
  }, [pid, navigate]);

  const onStructuralUpdate = useCallback((msg: any) => {
    const { op, file, folder, folder_id, user_id, file_id } = msg;
    if (op === "WORKSPACE_FILE_CREATED") {
      setFiles(prev => upsertById(prev, file));
      setActive(prev => prev || file);
    } else if (op === "WORKSPACE_FILE_UPDATED") {
      setFiles(prev => {
        const idx = prev.findIndex(f => f.id === file.id);
        if (idx !== -1) {
          const next = [...prev];
          next[idx] = file;
          return next;
        }
        return [...prev, file];
      });
    } else if (op === "WORKSPACE_FILE_DELETED") {
      setFiles(prev => prev.filter(f => f.id !== file_id));
      setTabIds(prev => prev.filter(id => id !== file_id));
      setActive(prev => prev?.id === file_id ? null : prev);
    } else if (op === "WORKSPACE_FOLDER_CREATED" || op === "WORKSPACE_FOLDER_UPDATED") {
      setFolders(prev => {
        const idx = prev.findIndex(f => f.id === folder.id);
        if (idx !== -1) {
          const next = [...prev];
          next[idx] = folder;
          return next;
        }
        return [...prev, folder];
      });
    } else if (op === "WORKSPACE_FOLDER_DELETED") {
      setFolders(prev => prev.filter(f => f.id !== folder_id));
    } else if (op === "project_presence") {
      setPresence(prev => {
        if (file_id == null) {
          // They left a file; ignore it if they have since opened another one.
          if (msg.left_file_id != null && prev[user_id] !== msg.left_file_id) return prev;
          const next = { ...prev };
          delete next[user_id];
          return next;
        }
        return { ...prev, [user_id]: file_id };
      });
    }
  }, []);

  const loadActivity = useCallback(() => {
    if (!pid || isNaN(pid)) return;
    api.getActivity(pid).then(setActivity).catch(() => { /* the panel just stays as it was */ });
  }, [pid]);

  useEffect(() => {
    if (project) loadActivity();
  }, [project, loadActivity]);

  const loadDebug = useCallback(() => {
    if (!pid || isNaN(pid)) return;
    api.debugState(pid)
      .then(({ active: a, paused }) => {
        if (!a) { setDebug((prev) => (prev.active ? emptyDebug : prev)); return; }
        let st: DebugState = applyDebugEvent(emptyDebug, { type: "debug_started", execution_id: a.execution_id, file_id: a.file_id, path: a.path, username: a.username });
        if (paused) st = applyDebugEvent(st, { ...paused, type: "debug_paused" });
        setDebug(st);
      })
      .catch(() => { /* no debugger: nothing to show */ });
  }, [pid]);

  useEffect(() => {
    if (project) loadDebug();
  }, [project, loadDebug]);


  const onFileChanged = useCallback((msg: FileChangedEvent) => {
    const me = getUser()?.id;
    const others = msg.changes.filter((c) => c.user_id !== me).map((c) => c.username);
    if (others.length > 0 && msg.file_id !== activeIdRef.current) {
      setModified((prev) => ({ ...prev, [msg.file_id]: others.join(", ") }));
    }
    loadActivity();
  }, [loadActivity]);

  function openFile(f: FileMeta) {
    if (f.id !== active?.id) setSelection(null);
    setActive(f);
    setTabIds((prev) => (prev.includes(f.id) ? prev : [...prev, f.id]));
    setRecent((prev) => [f.id, ...prev.filter((id) => id !== f.id)].slice(0, 20));
    // New files then go next to the open one.
    setActiveFolder(folders.find((d) => d.id === f.folder_id) ?? null);
    setModified((prev) => {
      if (!(f.id in prev)) return prev;
      const next = { ...prev };
      delete next[f.id];
      return next;
    });
  }

  function closeTab(f: FileMeta) {
    const next = neighbourAfterClose(tabIds, f.id);
    setTabIds((prev) => prev.filter((id) => id !== f.id));
    if (active?.id !== f.id) return;
    const nf = files.find((x) => x.id === next);
    if (nf) openFile(nf);
    else {
      setActive(null);
      setSelection(null);
    }
  }

  /** Open a file and put the cursor on a match (search results). */
  function openAt(fileId: number, line: number, column = 1, length = 0) {
    const f = files.find((x) => x.id === fileId);
    if (!f) return;
    openFile(f);
    setReveal({ fileId, line, column, length, nonce: Date.now() });
  }

  // The open file was closed elsewhere (deleted): show another open tab.
  useEffect(() => {
    if (active || !tabIds.length) return;
    const f = files.find((x) => x.id === tabIds[tabIds.length - 1]);
    if (f) setActive(f);
  }, [active, tabIds, files]);

  // A rename updates the file list; keep the open file's name and language in step.
  useEffect(() => {
    if (!active) return;
    const f = files.find((x) => x.id === active.id);
    if (f && f !== active && (f.path !== active.path || f.language !== active.language)) setActive(f);
  }, [files, active]);

  useEffect(() => {
    if (project && tabsRestored.current) saveTabs(project.id, { open: tabIds, active: active?.id ?? null });
  }, [project, tabIds, active?.id]);

  // Ctrl/Cmd+P: open a file by name. Ctrl/Cmd+Shift+F: search all files.
  // Ctrl/Cmd+Alt+W: close the tab (plain Ctrl+W closes the browser tab).
  const closeTabRef = useRef(closeTab);
  closeTabRef.current = closeTab;
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "p" && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        setQuickOpen(true);
      } else if (k === "f" && e.shiftKey && !e.altKey) {
        e.preventDefault();
        setSidebarView("search");
        setTimeout(() => searchRef.current?.focus(), 0);
      } else if ((k === "w" || e.code === "KeyW") && e.altKey) {
        e.preventDefault();
        if (activeRef.current) closeTabRef.current(activeRef.current);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const names: Record<number, string> = Object.fromEntries(members.map((m) => [m.user.id, m.user.username]));
  const me = getUser()?.id;
  const tabs = tabIds.map((id) => files.find((f) => f.id === id)).filter((f): f is FileMeta => !!f);

  const onExecEvent = useCallback((event: RunEvent) => {
    if (isDebugEvent(event)) {
      if (event.type === "breakpoints_updated") {
        const lines = breakpointsFromEvent(event);
        setBreakpoints((prev) => ({ ...prev, ...lines }));
        return;
      }
      setDebug((prev) => applyDebugEvent(prev, event));
      if (event.type === "debug_started" || event.type === "debug_paused") setPendingFollow(event);
    }
    setRuns((prev) => applyRunEvent(prev, event));
    if ("type" in event && event.type === "execution_status") {
      const { execution_id, status } = event as { execution_id: number; status: string };
      setExec((cur) => (cur && cur.id === execution_id ? { ...cur, status } : cur));
    }
  }, []);

  // AI progress events from the project socket, fanned out to the assistant panel.
  const aiListeners = useRef(new Set<(e: AIEvent) => void>());
  const subscribeAi = useCallback((fn: (e: AIEvent) => void) => {
    aiListeners.current.add(fn);
    return () => { aiListeners.current.delete(fn); };
  }, []);

  useEffect(() => {
    if (!pid || isNaN(pid)) return;
    if (!project) return;

    const wsBase = (import.meta.env.VITE_WS_URL as string | undefined) || "ws://127.0.0.1:8000";
    const projConn = connectProject({
      projectId: pid,
      token: getToken()!,
      wsBase,
    });

    projConn.onMessage((msg) => {
      if (msg.op?.startsWith("WORKSPACE_") || msg.op === "project_presence") {
        onStructuralUpdate(msg);
      } else if (msg.op === "FILE_CHANGED") {
        onFileChanged(msg as FileChangedEvent);
      } else if (msg.op === "EXECUTION_QUEUED") {
        onExecEvent(msg as RunEvent);
      } else if (msg.op?.startsWith("AI_")) {
        aiListeners.current.forEach((fn) => fn(msg as AIEvent));
      }
    });
    // Events sent while we were disconnected are gone; reload what they'd have changed.
    projConn.onReconnect(() => {
      loadActivity();
      loadDebug();
      Promise.all([api.listFiles(pid), api.listFolders(pid)])
        .then(([fs, flds]) => { setFiles(fs || []); setFolders(flds || []); })
        .catch(() => { /* keep what we have */ });
    });

    return () => {
      projConn.close();
    };
  }, [pid, project, onStructuralUpdate, onFileChanged, loadActivity, loadDebug, onExecEvent]);


  function askAiToFix(run: Run) {
    const f = files.find((x) => x.id === run.fileId);
    if (f && f.id !== active?.id) openFile(f);
    setAskRequest({
      mode: "fix",
      prompt: `My run of ${run.path ?? "this file"} failed${run.exitCode != null ? ` (exit code ${run.exitCode})` : ""}. Find and fix the bug.`,
      nonce: Date.now(),
    });
  }

  const noteQueued = (e: Execution, file: FileMeta, withInput: boolean) =>
    onExecEvent({ type: "local_queued", execution_id: e.id, file_id: file.id, path: file.path, username: getUser()?.username ?? null, with_input: withInput });

  async function runActive() {
    if (!active || !project) return;
    try {
      const e = await api.run(project.id, active.id, stdin || undefined);
      setExec(e);
      noteQueued(e, active, !!stdin);

      // FALLBACK: Poll for status if WebSocket is slow/broken
      const pollInterval = setInterval(async () => {
        try {
          const current = await api.getExecution(project.id, e.id);
          if (current.status !== "queued" && current.status !== "starting") {
            setExec(current);
            // If it finished, we can stop polling
            if (["completed", "failed", "timeout", "cancelled"].includes(current.status)) {
              clearInterval(pollInterval);
              // In case the live events were missed (e.g. a reconnect).
              onExecEvent({ type: "execution_output", execution_id: e.id, stdout: current.stdout, stderr: current.stderr, exit_code: current.exit_code });
              onExecEvent({ type: "execution_status", execution_id: e.id, status: current.status });
            }
          }
        } catch (err) {
          console.error("Polling error:", err);
        }
      }, 2000);

      // Stop polling if execution is cancelled or replaced
      return () => clearInterval(pollInterval);
    } catch (e) {
      showError(e);
    }
  }

  async function debugActive() {
    if (!active || !project) return;
    try {
      const e = await api.debug(project.id, active.id);
      setExec(e);
      noteQueued(e, active, false);
      setBottomTab("debug");
    } catch (e) {
      showError(e);
    }
  }

  // Breakpoint saves run one after another, and debug commands wait for them:
  // the backend forwards a save to the running session before it replies, so
  // "remove the breakpoint, then Continue" can't overtake the removal and stop
  // at the old breakpoint.
  const breakpointSaves = useRef<Promise<unknown>>(Promise.resolve());

  function debugCommand(command: string, extra: { ref?: number; frame_id?: number } = {}) {
    const id = debug.active?.execution_id ?? debug.paused?.execution_id;
    if (!project || id == null) return;
    const projectId = project.id;
    breakpointSaves.current
      .then(() => api.debugCommand(projectId, id, command, extra))
      .catch(showError);
  }

  function selectFrame(frame: DebugFrame) {
    if (frame.file_id != null) openAt(frame.file_id, frame.line);
    debugCommand("frame", { frame_id: frame.id });
  }

  async function toggleBreakpoint(line: number) {
    if (!project || !active) return;
    const fileId = active.id;
    const before = breakpoints[fileId] ?? [];
    const next = before.includes(line) ? before.filter((l) => l !== line) : [...before, line].sort((a, b) => a - b);
    setBreakpoints((prev) => ({ ...prev, [fileId]: next }));
    const projectId = project.id;
    const save = breakpointSaves.current.then(() => api.setBreakpoints(projectId, fileId, next));
    breakpointSaves.current = save.catch(() => undefined);
    try {
      await save;
    } catch (e) {
      setBreakpoints((prev) => ({ ...prev, [fileId]: before }));
      showError(e);
    }
  }

  async function cancel() {
    if (!exec || !project) return;
    try {
      await api.cancelExecution(project.id, exec.id);
    } catch (e) {
      showError(e);
    }
  }

  async function loadProjectMembers() {
    if (!project) return;
    try {
      const pm = await api.listProjectMembers(project.id);
      setProjectMembers(pm || []);
      setMembers(pm || []);
    } catch (e) {
      console.error("Failed to load project members:", e);
    }
  }

  const isOwner = !!project && project.owner_id === getUser()?.id;
  const currentUser = getUser();
  const myRole = isOwner ? "owner" : members.find((m) => m.user.id === currentUser?.id)?.role;
  // Viewers can ask the assistant but not apply its edits.
  const canEdit = myRole === "owner" || myRole === "editor" || myRole === "debugger";
  const canDebug = myRole === "owner" || myRole === "debugger";

  // Breakpoints of the file on screen.
  useEffect(() => {
    if (!project || !active || breakpoints[active.id]) return;
    const fileId = active.id;
    api.listBreakpoints(project.id, fileId)
      .then((bps) => setBreakpoints((prev) => ({ ...prev, [fileId]: bps.filter((b) => b.enabled).map((b) => b.line) })))
      .catch(() => { /* shown as none */ });
  }, [project, active, breakpoints]);

  // When the program pauses in another file, show that file (if you're watching the Debug tab).
  useEffect(() => {
    if (!pendingFollow) return;
    setPendingFollow(null);
    if (pendingFollow.type === "debug_started" || bottomTabRef.current === "debug") setBottomTab("debug");
    const fid = pendingFollow.file_id;
    if (typeof fid === "number" && fid !== active?.id && bottomTabRef.current === "debug") {
      const f = files.find((x) => x.id === fid);
      if (f) openFile(f);
    }
  }, [pendingFollow]); // eslint-disable-line react-hooks/exhaustive-deps -- runs once per event

  // Debugger keys, while a session runs: F5 continue, F10 over, F11 into, Shift+F11 out, F6 pause, Shift+F5 stop.
  const debugCommandRef = useRef(debugCommand);
  debugCommandRef.current = debugCommand;
  const debugRef = useRef(debug);
  debugRef.current = debug;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const d = debugRef.current;
      if (!d.active || !canDebug) return;
      const paused = !!d.paused;
      const map: Record<string, string | undefined> = {
        F5: e.shiftKey ? "stop" : paused ? "continue" : undefined,
        F10: paused ? "next" : undefined,
        F11: paused ? (e.shiftKey ? "stepOut" : "stepIn") : undefined,
        F6: paused ? undefined : "pause",
      };
      const command = map[e.key];
      if (command) {
        e.preventDefault();
        debugCommandRef.current(command);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [canDebug]);

  async function loadJoinRequests() {
    if (!project || !isOwner) return;
    try {
      setJoinRequests(await api.listJoinRequests(project.id));
    } catch (e) {
      console.error("Failed to load join requests:", e);
    }
  }

  useEffect(() => {
    loadJoinRequests();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, isOwner]);

  async function handleApproveRequest(requestId: number) {
    if (!project) return;
    try {
      await api.approveJoinRequest(project.id, requestId, requestRoles[requestId] || "viewer");
      await Promise.all([loadProjectMembers(), loadJoinRequests()]);
    } catch (e) {
      showError(e);
    }
  }

  async function handleRejectRequest(requestId: number) {
    if (!project) return;
    try {
      await api.rejectJoinRequest(project.id, requestId);
      await loadJoinRequests();
    } catch (e) {
      showError(e);
    }
  }

  async function handleCreate() {
    if (!project || !modal) return;
    try {
      // No folder selected means the project root.
      if (modal.type === "file") {
        const f = await api.createFile(project.id, modal.name, activeFolder?.id ?? null);
        setFiles(prev => upsertById(prev, f));
        openFile(f);
      } else {
        const f = await api.createFolder(project.id, modal.name, activeFolder?.id ?? null);
        setFolders(prev => upsertById(prev, f));
      }
      setModal(null);
    } catch (e) {
      showError(e);
    }
  }

  async function handleRename() {
    if (!project || !modal || !modal.targetId) return;
    try {
      if (modal.type === "file") {
        const f = await api.updateFile(project.id, modal.targetId, { path: modal.name });
        setFiles(prev => {
          const idx = prev.findIndex(f => f.id === modal.targetId);
          if (idx !== -1) {
            const next = [...prev];
            next[idx] = f;
            return next;
          }
          return [...prev, f];
        });
        if (active?.id === modal.targetId) setActive(f);
      } else {
        const f = await api.updateFolder(project.id, modal.targetId, { name: modal.name });
        setFolders(prev => {
          const idx = prev.findIndex(f => f.id === modal.targetId);
          if (idx !== -1) {
            const next = [...prev];
            next[idx] = f;
            return next;
          }
          return [...prev, f];
        });
      }
      setModal(null);
    } catch (e) {
      showError(e);
    }
  }

  async function handleAddMember() {
    if (!project) return;

    if (memberForm.userId.trim() === "") {
      toast("Please enter a User ID or Username.", "error");
      return;
    }
    const userId = parseMemberRef(memberForm.userId);

    try {
      await api.addProjectMember(project.id, userId, memberForm.role);
      await loadProjectMembers();
      setMembersModal(null);
      setMemberForm({ userId: "", role: "viewer" });
    } catch (e) {
      showError(e);
    }
  }

  async function handleRemoveMember(userId: number) {
    if (!project) return;
    try {
      await api.removeProjectMember(project.id, userId);
      await loadProjectMembers();
    } catch (e) {
      showError(e);
    }
  }

  async function handleUpdateRole(userId: number, role: string) {
    if (!project) return;
    try {
      await api.updateProjectMemberRole(project.id, userId, role);
      await loadProjectMembers();
    } catch (e) {
      showError(e);
    }
  }

  if (error) return (
    <div style={{ padding: 24, background: "var(--bg)", height: "100vh" }}>
      <p style={{ color: "var(--bad)" }}>Couldn't open this project. {error}</p>
      <button onClick={() => navigate("/")}>Back to projects</button>
    </div>
  );
  if (!project) return <div style={{ padding: 24, background: "var(--bg)", height: "100vh", opacity: 0.7 }}>Loading project…</div>;

  return (
    <ErrorBoundary>
      <div style={{ display: "flex", flexDirection: "column", height: "100vh", width: "100vw", overflow: "hidden", background: "var(--bg)", color: "var(--fg)", boxSizing: "border-box" }}>
        <header className="topbar" style={{ flexShrink: 0, height: 40, display: "flex", alignItems: "center", padding: "0 12px", background: "var(--panel)", borderBottom: "1px solid var(--border)" }}>
          <strong style={{ marginRight: 12 }}>Concord</strong>
          <span style={{ opacity: 0.7 }}>Project: {project.name} · ID #{project.id}</span>
          <button
            className="secondary small"
            style={{ marginLeft: 8, marginRight: "auto" }}
            title="Copy the project ID to share with someone who wants to request access"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(String(project.id));
                toast(`Project ID ${project.id} copied.`, "success");
              } catch {
                toast(`Project ID: ${project.id}`, "info");
              }
            }}
          >
            Copy ID
          </button>
          {currentUser && (
            <span className="whoami" title={currentUser.email}>
              You: <strong>{currentUser.username}</strong>{myRole ? ` · ${myRole}` : ""} (user #{currentUser.id})
            </span>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {members.map((m) => (
              <span key={m.user.id} className={`role-pill ${m.role}`} style={{ fontSize: 10 }} title={`${m.user.username} (${m.role}, user #${m.user.id})`}>
                {m.user.username} · {m.role}
              </span>
            ))}
            {project.owner_id === (getUser() || {id: -1})?.id && (
              <button
                onClick={async () => {
                  await Promise.all([loadProjectMembers(), loadJoinRequests()]);
                  setMembersModal("list");
                }}
                className="secondary"
                style={{ marginLeft: 12 }}
              >
                Members{joinRequests.length > 0 ? ` (${joinRequests.length} pending)` : ""}
              </button>
            )}
            <button onClick={() => navigate("/")} className="secondary" style={{ marginLeft: 12 }}>Back</button>
          </div>
        </header>
        <div style={{ display: "flex", flexGrow: 1, overflow: "hidden" }}>
          <aside className="sidebar" style={{ width: 240, background: "var(--panel)", borderRight: "1px solid var(--border)", padding: 8, overflowY: "auto", zIndex: 10 }}>
            <div className="sidebar-switch" role="tablist" aria-label="Sidebar">
              <button role="tab" aria-selected={sidebarView === "files"} className={sidebarView === "files" ? "on" : ""} onClick={() => setSidebarView("files")}>Files</button>
              <button role="tab" aria-selected={sidebarView === "search"} className={sidebarView === "search" ? "on" : ""} onClick={() => setSidebarView("search")} title="Search all files (Ctrl+Shift+F)">Search</button>
            </div>
            {sidebarView === "search" ? (
              <SearchPanel ref={searchRef} projectId={project.id} onOpen={openAt} />
            ) : (<>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginBottom: 12 }}>
              <button onClick={() => setModal({ mode: "create", type: "file", name: "" })} className="secondary" style={{ cursor: "pointer" }}>+ New file</button>
              <button onClick={() => setModal({ mode: "create", type: "folder", name: "" })} className="secondary" style={{ cursor: "pointer" }}>+ New folder</button>
            </div>
            <FileTree
              files={files}
              folders={folders}
              activeFileId={active?.id || null}
              activeFolderId={activeFolder?.id || null}
              onSelectFile={openFile}
              onSelectRoot={() => setActiveFolder(null)}
              onSelectFolder={(f) => setActiveFolder(f)}
              onRename={(type, item) => {
                setModal({
                  mode: "rename",
                  type,
                  name: type === "file" ? (item as FileMeta).path : (item as FolderMeta).name,
                  targetId: (item as any).id,
                });
              }}
              presence={presence}
              modified={modified}
              names={names}
              me={me}
            />
            {active && (
              <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                <button onClick={runActive} disabled={!isRunnable(active) || (!!exec && (exec.status === "queued" || exec.status === "starting" || exec.status === "running"))}
                  title={isRunnable(active) ? "Run this file" : "Only Python, JavaScript and C/C++ files can be run"} style={{ width: "100%", marginBottom: 4 }}>
                  Run
                </button>
                <button onClick={debugActive}
                  disabled={!canDebug || !isDebuggable(active) || !!debug.active || (!!exec && (exec.status === "queued" || exec.status === "starting" || exec.status === "running"))}
                  title={!canDebug ? "Debugging needs the debugger role" : !isDebuggable(active) ? "Only Python files can be debugged" : debug.active ? `${debug.active.username ?? "Someone"} is debugging ${debug.active.path}` : "Run under the debugger; it stops at your breakpoints"}
                  className="secondary" style={{ width: "100%", marginBottom: 4 }}>
                  Debug
                </button>
                <button onClick={cancel} disabled={!exec || ["completed", "failed", "timeout", "cancelled"].includes(exec.status)} className="secondary" style={{ width: "100%" }}>
                  Cancel
                </button>
                {exec && <div style={{ marginTop: 8, fontSize: 12, opacity: 0.7 }}>status: {exec.status}</div>}
              </div>
            )}
            <RecentChanges
              activity={activity}
              onOpen={(id) => {
                const f = files.find((x) => x.id === id);
                if (f) openFile(f);
              }}
            />
            <SnapshotsPanel projectId={project.id} canEdit={canEdit} />
            </>)}
          </aside>
          <main className="main" style={{ flexGrow: 1, position: "relative", overflow: "hidden" }}>
            {active ? (
              <div style={{ display: "flex", flexDirection: "row", height: "100%" }}>
                <div style={{ flexGrow: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
                  <EditorTabs
                    tabs={tabs}
                    activeId={active.id}
                    onSelect={openFile}
                    onClose={closeTab}
                    modified={modified}
                    presence={presence}
                    names={names}
                    me={me}
                  />
                  <CollabEditor
                    project={project}
                    file={active}
                    onExecEvent={onExecEvent}
                    onStructuralUpdate={onStructuralUpdate}
                    proposal={proposal}
                    canApply={canEdit}
                    onProposalDone={() => setProposal(null)}
                    onSelectionChange={setSelection}
                    reveal={reveal?.fileId === active.id ? reveal : null}
                    breakpoints={breakpoints[active.id] ?? []}
                    onToggleBreakpoint={canDebug ? toggleBreakpoint : undefined}
                    pausedLine={debug.paused?.file_id === active.id ? debug.paused.line : null}
                  />
                  <div className="bottom-panel" style={{ height: 220, borderTop: "1px solid var(--border)", background: "var(--panel)", padding: 8, overflow: "hidden" }}>
                    <div className="bottom-tabs" role="tablist" aria-label="Bottom panel">
                      <button role="tab" aria-selected={bottomTab === "console"} className={bottomTab === "console" ? "on" : ""} onClick={() => setBottomTab("console")}>Console</button>
                      <button role="tab" aria-selected={bottomTab === "debug"} className={bottomTab === "debug" ? "on" : ""} onClick={() => setBottomTab("debug")}>
                        Debug{debug.active && <span className="dot" title={debug.paused ? "Paused" : "Running"}>●</span>}
                      </button>
                    </div>
                    {bottomTab === "console" ? (
                      <ConsolePanel
                        runs={runs}
                        onClear={() => setRuns([])}
                        onAskFix={askAiToFix}
                        stdin={stdin}
                        onStdinChange={setStdin}
                        showInput={showInput}
                        onToggleInput={() => setShowInput((v) => !v)}
                      />
                    ) : (
                      <DebugPanel
                        state={debug}
                        canStep={canDebug}
                        onCommand={(c) => debugCommand(c)}
                        onExpand={(ref) => debugCommand("variables", { ref })}
                        onSelectFrame={selectFrame}
                      />
                    )}
                  </div>
                </div>
                <aside style={{ width: 300, background: "var(--panel)", borderLeft: "1px solid var(--border)", overflow: "hidden" }}>
                  <AssistantPanel
                    projectId={project.id}
                    activeFile={active}
                    files={files}
                    isOwner={isOwner}
                    onProposal={setProposal}
                    currentUserId={getUser()?.id ?? null}
                    subscribeAi={subscribeAi}
                    selection={selection}
                    onClearSelection={() => setSelection(null)}
                    askRequest={askRequest}
                  />
                </aside>
              </div>
            ) : (
              <div style={{ padding: 24, display: "flex", alignItems: "center", justifyContent: "center", height: "100%", opacity: 0.5 }}>
                <div style={{ textAlign: "center", lineHeight: 1.8 }}>
                  <div>Select a file to open it.</div>
                  <div className="shortcut-hint"><kbd>Ctrl</kbd>+<kbd>P</kbd> open a file · <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>F</kbd> search all files · <kbd>Ctrl</kbd>+<kbd>S</kbd> save</div>
                </div>
              </div>
            )}
          </main>
        </div>
        {quickOpen && (
          <QuickOpen
            files={files}
            recent={recent}
            onPick={(f) => { openFile(f); setQuickOpen(false); }}
            onClose={() => setQuickOpen(false)}
          />
        )}
        {modal && (
          <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: "rgba(0,0,0,0.7)", display: "flex",
            alignItems: "center", justifyContent: "center", zIndex: 1000
          }}>
            <div style={{
              background: "var(--panel)", padding: 24, borderRadius: 8,
              border: "1px solid var(--border)", width: 300, display: "flex",
              flexDirection: "column", gap: 12
            }}>
              <h3 style={{ margin: 0 }}>{modal.mode === "create" ? `Create ${modal.type === "file" ? "File" : "Folder"}` : `Rename ${modal.type === "file" ? "File" : "Folder"}`}</h3>
              <input
                autoFocus
                placeholder={modal.type === "file" ? "file_name.py" : "folder_name"}
                value={modal.name}
                onChange={(e) => setModal({ ...modal, name: e.target.value })}
                style={{ padding: 8, background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", borderRadius: 4 }}
              />
              {modal.mode === "create" && (
                <span style={{ fontSize: 12, opacity: 0.7 }}>
                  In: {activeFolder ? activeFolder.name : "project root"}
                </span>
              )}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
                <button className="secondary" onClick={() => setModal(null)}>Cancel</button>
                <button onClick={modal.mode === "create" ? handleCreate : handleRename}>
                  {modal.mode === "create" ? "Create" : "Rename"}
                </button>
              </div>
            </div>
          </div>
        )}

        {membersModal && (
          <div style={{
            position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: "rgba(0,0,0,0.7)", display: "flex",
            alignItems: "center", justifyContent: "center", zIndex: 1000
          }}>
            <div style={{
              background: "var(--panel)", padding: 24, borderRadius: 8,
              border: "1px solid var(--border)", width: 400, display: "flex",
              flexDirection: "column", gap: 12
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h3 style={{ margin: 0 }}>Project Members</h3>
                <button
                  onClick={() => setMembersModal(membersModal === "list" ? "add" : "list")}
                  className="secondary"
                  style={{ fontSize: 12 }}
                >
                  {membersModal === "list" ? "+ Add Member" : "Back to List"}
                </button>
              </div>

              {membersModal === "list" ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 400, overflowY: "auto" }}>
                  {isOwner && joinRequests.length > 0 && (
                    <section aria-label="Pending join requests" style={{ display: "flex", flexDirection: "column", gap: 6, paddingBottom: 8, borderBottom: "1px solid var(--border)" }}>
                      <strong style={{ fontSize: 12 }}>Pending requests</strong>
                      {joinRequests.map((r) => (
                        <div key={r.id} style={{
                          display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6,
                          padding: 8, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4
                        }}>
                          <strong>{r.user.username}</strong>
                          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                            <select
                              aria-label={`Role for ${r.user.username}`}
                              value={requestRoles[r.id] || "viewer"}
                              onChange={e => setRequestRoles(prev => ({ ...prev, [r.id]: e.target.value }))}
                              style={{ fontSize: 10, padding: "2px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--fg)" }}
                            >
                              <option value="viewer">Viewer</option>
                              <option value="editor">Editor</option>
                              <option value="debugger">Debugger</option>
                            </select>
                            <button onClick={() => handleApproveRequest(r.id)} style={{ fontSize: 10 }}>Approve</button>
                            <button onClick={() => handleRejectRequest(r.id)} className="secondary" style={{ fontSize: 10, color: "var(--bad)" }}>Reject</button>
                          </div>
                        </div>
                      ))}
                    </section>
                  )}
                  {projectMembers.map((m) => (
                    <div key={m.user.id} style={{
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                      padding: 8, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4
                    }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <strong>{m.user.username}</strong>
                        <span className={`role-pill ${m.role}`} style={{ fontSize: 10 }}>{m.role}</span>
                        {project?.owner_id === (getUser() || {id: -1})?.id && m.user.id !== project?.owner_id && (
                          <select
                            value={m.role?.toLowerCase()}
                            onChange={e => handleUpdateRole(m.user.id, e.target.value)}
                            style={{ fontSize: 10, padding: "2px", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--fg)" }}
                          >
                            <option value="viewer">Viewer</option>
                            <option value="editor">Editor</option>
                            <option value="debugger">Debugger</option>
                            <option value="owner">Owner</option>
                          </select>
                        )}
                      </div>
                      {m.user.id !== project?.owner_id && (
                        <button
                          onClick={() => handleRemoveMember(m.user.id)}
                          className="secondary"
                          style={{ fontSize: 10, color: "var(--bad)" }}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  ))}
                  {projectMembers.length === 0 && <div style={{ textAlign: "center", opacity: 0.5 }}>No members found.</div>}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div>
                    <label style={{ display: "block", fontSize: 12, marginBottom: 4 }}>User ID or Username</label>
                    <input
                      autoFocus
                      placeholder="e.g. 123 or username"
                      value={memberForm.userId}
                      onChange={(e) => setMemberForm({ ...memberForm, userId: e.target.value })}
                      style={{ width: "100%", padding: 8, background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", borderRadius: 4 }}
                    />
                  </div>
                  <div>
                    <label style={{ display: "block", fontSize: 12, marginBottom: 4 }}>Role</label>
                    <select
                      value={memberForm.role}
                      onChange={(e) => setMemberForm({ ...memberForm, role: e.target.value })}
                      style={{ width: "100%", padding: 8, background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", borderRadius: 4 }}
                    >
                      <option value="viewer">Viewer</option>
                      <option value="editor">Editor</option>
                      <option value="debugger">Debugger</option>
                      <option value="owner">Owner</option>
                    </select>
                  </div>
                  <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
                    <button className="secondary" onClick={() => setMembersModal("list")}>Cancel</button>
                    <button onClick={handleAddMember}>Add Member</button>
                  </div>
                </div>
              )}

              <button
                onClick={() => setMembersModal(null)}
                className="secondary"
                style={{ marginTop: 12 }}
              >
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </ErrorBoundary>
  );
}
