import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { OnMount } from "@monaco-editor/react";
import { MonacoBinding } from "y-monaco";
import { connectCollab, CollabConnection, SyncStatus } from "../lib/ws";
import { AIProposal, api, FileMeta, getToken, getUser } from "../lib/api";
import { userColor } from "../lib/colors";
import HistoryModal from "./HistoryModal";
import { cursorStyles } from "../lib/cursors";
import type { EditorSelection } from "./AssistantPanel";
import { monacoLanguage } from "../lib/languages";
import { friendlyError, toast } from "../lib/errors";

const STATUS_LABEL: Record<SyncStatus, string> = {
  connecting: "Connecting…",
  syncing: "Syncing…",
  synced: "✓ All changes synced",
  offline: "Offline: reconnecting…",
  denied: "Disconnected",
};
const STATUS_HELP: Record<SyncStatus, string> = {
  connecting: "Loading the latest version of this file",
  syncing: "Sending your latest edits",
  synced: "Everyone sees your edits, and they are saved automatically every few seconds",
  offline: "Your edits are kept in this tab and will sync when the connection is back. Don't close the tab.",
  denied: "The server refused the connection. You may no longer be a member of this project.",
};

/** Scroll to and select a place in a file (e.g. a search result). */
export type Reveal = { fileId: number; line: number; column: number; length: number; nonce: number };

type EditorProps = {
  project: any;
  file: FileMeta;
  onExecEvent: (event: any) => void;
  onStructuralUpdate?: (msg: any) => void;
  /** An AI edit proposal to review; shown only if it targets this file. */
  proposal?: AIProposal | null;
  /** Whether the current user may edit (editor role or above): gates typing and AI proposals. */
  canApply?: boolean;
  /** Called when the proposal is applied, rejected or can't be applied. */
  onProposalDone?: () => void;
  /** The current selection (null when nothing is selected), for "ask the AI about this". */
  onSelectionChange?: (selection: EditorSelection | null) => void;
  /** Where to jump to once the file has loaded; a new ``nonce`` jumps again. */
  reveal?: Reveal | null;
  /** Breakpoint lines in this file. */
  breakpoints?: number[];
  /** Toggle a breakpoint by clicking left of the line numbers; omitted: read-only. */
  onToggleBreakpoint?: (line: number) => void;
  /** The debugger is paused on this line of this file. */
  pausedLine?: number | null;
};

// Monaco's MouseTargetType.GUTTER_GLYPH_MARGIN: the strip left of the line numbers.
const GUTTER_GLYPH_MARGIN = 2;

export default function CollabEditor({
  project, file, onExecEvent, onStructuralUpdate,
  proposal: incomingProposal = null, canApply = true, onProposalDone, onSelectionChange, reveal = null,
  breakpoints = [], onToggleBreakpoint, pausedLine = null,
}: EditorProps) {
  const onToggleBreakpointRef = useRef(onToggleBreakpoint);
  onToggleBreakpointRef.current = onToggleBreakpoint;
  const decorationIds = useRef<string[]>([]);
  const onSelectionChangeRef = useRef(onSelectionChange);
  onSelectionChangeRef.current = onSelectionChange;
  const editorRef = useRef<any>(null);
  const connRef = useRef<CollabConnection | null>(null);
  const bindingRef = useRef<MonacoBinding | null>(null);
  const onExecEventRef = useRef(onExecEvent);
  const onStructuralUpdateRef = useRef(onStructuralUpdate);
  const [presence, setPresence] = useState<{ user_id: number; username: string; color: string }[]>([]);
  const [status, setStatus] = useState<SyncStatus>("connecting");
  const [bound, setBound] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveNote, setSaveNote] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [cursorCss, setCursorCss] = useState("");
  const currentText = useCallback(() => connRef.current?.doc.getText("monaco").toString() ?? "", []);
  const proposal =
    incomingProposal && (incomingProposal.file_id == null || incomingProposal.file_id === file.id)
      ? incomingProposal
      : null;
  const closeProposal = () => onProposalDone?.();

  // Keep the event ref up to date so the WS listener always uses the latest callback
  useEffect(() => {
    onExecEventRef.current = onExecEvent;
  }, [onExecEvent]);

  useEffect(() => {
    onStructuralUpdateRef.current = onStructuralUpdate;
  }, [onStructuralUpdate]);

  const user = useMemo(() => {
    const tok = getToken();
    if (!tok) return null;
    try {
      const payload = JSON.parse(atob(tok.split(".")[1]));
      // The token carries only the id; the name comes from the login response.
      const username = getUser()?.username || payload.username || `user-${payload.sub}`;
      return { id: payload.sub, username, color: userColor(Number(payload.sub)) };
    } catch {
      return null;
    }
  }, []);

  useEffect(() => {
    if (!user) return;

    const wsBase = (import.meta.env.VITE_WS_URL as string | undefined) || "ws://127.0.0.1:8000";
    const conn = connectCollab({
      projectId: project.id,
      fileId: file.id,
      token: getToken()!,
      user,
      wsBase,
    });
    connRef.current = conn;
    setStatus(conn.status());
    setSaveNote(null);
    const offStatus = conn.onStatus(setStatus);

    conn.awareness.on("change", () => {
      const seen = new Map<number, { user_id: number; username: string; color: string }>();
      conn.awareness.getStates().forEach((state: any) => {
        if (state?.user_id) seen.set(state.user_id, state);
      });
      setPresence(Array.from(seen.values()));
      setCursorCss(cursorStyles(conn.awareness.getStates(), conn.doc.clientID));
    });

    // Set local awareness state so others can see our cursor, name, and color
    // (after subscribing, so our own avatar shows too).
    conn.awareness.setLocalState({
      user_id: user.id,
      username: user.username,
      color: user.color,
    });

    const offMessage = conn.onMessage((msg) => {
      if (msg.op === "exec_event") {
        onExecEventRef.current(msg.event);
      } else if (msg.op === "project_presence" || msg.op?.startsWith("WORKSPACE_")) {
        onStructuralUpdateRef.current?.(msg);
      }
    });

    // Tell the project which file we're in (relayed to the file tree), once connected.
    let cancelled = false;
    // Bind the editor only once the server's copy is in the doc: typing into
    // an empty doc before that would be merged in front of the real content.
    conn.whenSynced.then(() => {
      if (cancelled) return;
      conn.send({ op: "project_presence", user_id: user.id, file_id: file.id });
      if (editorRef.current) setupBinding(editorRef.current, conn);
    });

    return () => {
      cancelled = true;
      offStatus();
      offMessage();
      if (bindingRef.current) {
        bindingRef.current.destroy();
        bindingRef.current = null;
        setBound(false);
      }
      conn.close();
      connRef.current = null;
    };
  }, [project.id, file.id, user?.id]); // Stable dependencies: only reconnect on project/file/user change

  // Jump to a search result once the file's text is in the editor.
  useEffect(() => {
    const editor = editorRef.current;
    if (!reveal || !bound || !editor || reveal.fileId !== file.id) return;
    const range = {
      startLineNumber: reveal.line,
      startColumn: reveal.column,
      endLineNumber: reveal.line,
      endColumn: reveal.column + reveal.length,
    };
    editor.revealLineInCenter?.(reveal.line);
    editor.setSelection?.(range);
    editor.focus?.();
  }, [reveal?.nonce, bound, file.id]); // eslint-disable-line react-hooks/exhaustive-deps -- once per request

  // Breakpoint dots in the margin, and the line the debugger is paused on.
  const bpKey = breakpoints.join(",");
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor?.deltaDecorations) return;
    const decorations = breakpoints.map((line) => ({
      range: { startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 1 },
      options: { glyphMarginClassName: "bp-glyph", glyphMarginHoverMessage: { value: "Breakpoint" }, stickiness: 1 },
    }));
    if (pausedLine) {
      decorations.push({
        range: { startLineNumber: pausedLine, startColumn: 1, endLineNumber: pausedLine, endColumn: 1 },
        options: {
          isWholeLine: true, className: "debug-current-line", glyphMarginClassName: breakpoints.includes(pausedLine) ? "bp-glyph debug-arrow" : "debug-arrow",
          stickiness: 1,
        } as never,
      });
    }
    decorationIds.current = editor.deltaDecorations(decorationIds.current, decorations);
  }, [bpKey, pausedLine, bound, file.id]); // eslint-disable-line react-hooks/exhaustive-deps -- bpKey stands for breakpoints

  // Jump to where the debugger paused.
  useEffect(() => {
    if (pausedLine && bound) editorRef.current?.revealLineInCenterIfOutsideViewport?.(pausedLine);
  }, [pausedLine, bound]);

  // Warn before closing the tab while edits haven't reached the server.
  useEffect(() => {
    if (status !== "offline" && status !== "syncing") return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  const save = async () => {
    const conn = connRef.current;
    if (!conn || !canApply || saving) return;
    setSaving(true);
    try {
      if (!(await conn.flush(5000))) {
        setSaveNote("Can't save while offline. Your edits are kept and sync when the connection is back.");
        return;
      }
      const r = await api.saveFile(project.id, file.id);
      const at = r.saved_at ? new Date(r.saved_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
      setSaveNote(
        r.version == null ? "Nothing to save yet"
          : r.created ? `Saved as version ${r.version}${at ? ` at ${at}` : ""}`
            : `No changes since version ${r.version}`
      );
    } catch (e) {
      setSaveNote(`Save failed. ${friendlyError(e)}`);
    } finally {
      setSaving(false);
    }
  };
  const saveRef = useRef(save);
  saveRef.current = save;

  // Ctrl/Cmd+S saves instead of opening the browser's "Save page" dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        saveRef.current();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const setupBinding = (editor: any, conn: CollabConnection) => {
    if (bindingRef.current) {
      bindingRef.current.destroy();
    }

    const model = editor.getModel();
    if (!model) return;

    bindingRef.current = new MonacoBinding(
      conn.doc.getText("monaco"),
      model,
      new Set([editor]),
      conn.awareness
    );
    setBound(true);
  };

  const applyBlockedReason = !canApply
    ? "Only editors can apply changes"
    : proposal && !proposal.syntax_ok
      ? "This change would not parse"
      : null;

  const applyProposal = () => {
    if (!proposal || !connRef.current || applyBlockedReason) return;
    const ytext = connRef.current.doc.getText("monaco");
    const currentText = ytext.toString();
    const index = currentText.indexOf(proposal.original_text);

    if (index !== -1) {
      // One transaction so collaborators receive a single replace, not a
      // delete and an insert that could interleave with their edits.
      connRef.current.doc.transact(() => {
        ytext.delete(index, proposal.original_text.length);
        ytext.insert(index, proposal.proposed_text);
      });
    } else {
      toast("Could not find the original text to replace. The file may have changed since the AI read it.", "error");
    }
    closeProposal();
  };

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    const glyph = monaco?.editor?.MouseTargetType?.GUTTER_GLYPH_MARGIN ?? GUTTER_GLYPH_MARGIN;
    editor.onMouseDown?.((e: { target: { type: number; position?: { lineNumber: number } | null } }) => {
      if (e.target.type === glyph && e.target.position && onToggleBreakpointRef.current) {
        onToggleBreakpointRef.current(e.target.position.lineNumber);
      }
    });
    let timer: ReturnType<typeof setTimeout> | undefined;
    editor.onDidChangeCursorSelection?.(() => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const sel = editor.getSelection();
        const model = editor.getModel();
        if (!sel || !model || sel.isEmpty()) {
          onSelectionChangeRef.current?.(null);
          return;
        }
        // A selection ending at column 1 doesn't really include that last line.
        const end = sel.endColumn === 1 && sel.endLineNumber > sel.startLineNumber ? sel.endLineNumber - 1 : sel.endLineNumber;
        onSelectionChangeRef.current?.({
          start_line: sel.startLineNumber,
          end_line: end,
          text: model.getValueInRange(sel).slice(0, 8000),
        });
      }, 200);
    });
    const conn = connRef.current;
    if (conn && conn.status() !== "connecting" && !bindingRef.current) {
      setupBinding(editor, conn);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {proposal && (
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0,0,0,0.8)", zIndex: 100, padding: 20,
          display: "flex", flexDirection: "column", gap: 16, color: "var(--fg)",
          overflowY: "auto", backdropFilter: "blur(4px)"
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>AI Proposal</h3>
            <button className="secondary" onClick={closeProposal}>Close</button>
          </div>
          <p style={{ opacity: 0.8, fontStyle: "italic" }}>{proposal.explanation}</p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, height: "60%" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 12, opacity: 0.6 }}>Original</span>
              <pre style={{
                background: "var(--panel)", padding: 12, borderRadius: 4,
                overflow: "auto", fontSize: 12, border: "1px solid var(--border)",
                color: "#ffaaaa"
              }}>{proposal.original_text}</pre>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 12, opacity: 0.6 }}>Proposed</span>
              <pre style={{
                background: "var(--panel)", padding: 12, borderRadius: 4,
                overflow: "auto", fontSize: 12, border: "1px solid var(--border)",
                color: "#aaffaa"
              }}>{proposal.proposed_text}</pre>
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8 }}>
            {applyBlockedReason && <span style={{ fontSize: 12, color: "var(--bad)" }}>{applyBlockedReason}</span>}
            <button className="secondary" onClick={closeProposal}>Reject</button>
            <button onClick={applyProposal} disabled={!!applyBlockedReason}>Accept Change</button>
          </div>
        </div>
      )}
      <style data-testid="cursor-styles">{cursorCss}</style>
      <div className="presence-bar" style={{ padding: 4, gap: 6 }}>
        {presence.map((p) => (
          <span key={p.user_id} className="presence-dot" title={p.username || "Unknown"} style={{ background: p.color }}>
            {(p.username || "??").slice(0, 2)}
          </span>
        ))}
        {!canApply && (
          <span role="note" style={{ fontSize: 11, opacity: 0.7 }}>Read-only: viewers can't edit</span>
        )}
        <span role="status" className={`sync-status ${status}`} title={STATUS_HELP[status]}>
          {STATUS_LABEL[status]}{saveNote && status === "synced" ? ` · ${saveNote}` : ""}
        </span>
        {(status === "offline" || status === "denied") && saveNote && <span className="sync-status offline">{saveNote}</span>}
        <button
          className="secondary save-button"
          onClick={() => setHistoryOpen(true)}
          disabled={status === "connecting" || status === "denied"}
          title="See earlier versions of this file, compare and restore them"
        >
          History
        </button>
        {canApply && (
          <button
            className="secondary save-button"
            onClick={save}
            disabled={saving || status === "connecting" || status === "denied"}
            title="Save a version now (Ctrl+S). Edits also sync and save automatically."
          >
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </div>
      {historyOpen && (
        <HistoryModal
          projectId={project.id}
          file={file}
          currentText={currentText}
          canRestore={canApply}
          onClose={() => setHistoryOpen(false)}
        />
      )}
      <div className="editor-host">
        <Editor
          height="100%"
          // One model per file, so switching files doesn't reuse the last file's text or undo history.
          path={`concord/${project.id}/${file.id}/${file.path}`}
          language={monacoLanguage(file.path, file.language)}
          theme="vs-dark"
          onMount={handleMount}
          options={{
            fontSize: 13,
            minimap: { enabled: false },
            glyphMargin: true,
            // Room above line 1 for other people's name tags.
            padding: { top: 16 },
            // The server also drops edits from viewers; this just stops them typing.
            // Until the file has loaded, typing would go nowhere.
            readOnly: !canApply || !bound,
            readOnlyMessage: {
              value: !canApply ? "You're a viewer in this project, so you can't edit." : "Loading the file…",
            },
          }}
        />
      </div>
    </div>
  );
}
