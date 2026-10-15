import { useEffect, useRef, useState } from "react";
import {
  AIAgentStep,
  AIAssistantStatus,
  AIEvent,
  AIProposal,
  AssistMode,
  ChatTurn,
  FileMeta,
  api,
} from "../lib/api";
import Markdown from "./Markdown";
import { friendlyError } from "../lib/errors";

/** What the user has selected in the editor, sent along with their next question. */
export type EditorSelection = { start_line: number; end_line: number; text: string };

/** A request to ask the assistant something from elsewhere in the app (e.g. "Ask AI to fix"). */
export type AskRequest = { mode: AssistMode; prompt: string; nonce: number };

type Message = ChatTurn & {
  steps?: AIAgentStep[];
  note?: string;
  error?: boolean;
};

type Props = {
  projectId: number;
  activeFile: FileMeta;
  files: FileMeta[];
  isOwner: boolean;
  onProposal: (proposal: AIProposal) => void;
  currentUserId?: number | null;
  // Subscribes to AI_* events from the project socket; returns an unsubscribe.
  subscribeAi?: (fn: (e: AIEvent) => void) => () => void;
  selection?: EditorSelection | null;
  onClearSelection?: () => void;
  askRequest?: AskRequest | null;
};

type Activity = { username: string; mode: AssistMode; fileId: number | null };

const MODES: { value: AssistMode; label: string; placeholder: string }[] = [
  { value: "ask", label: "Ask", placeholder: "Ask about this project..." },
  { value: "explain", label: "Explain", placeholder: "What should I explain? (optional)" },
  { value: "fix", label: "Fix last error", placeholder: "Anything to know about the bug? (optional)" },
  { value: "generate", label: "Generate", placeholder: "Describe the code to write..." },
  { value: "review", label: "Review", placeholder: "Anything to focus on? (optional)" },
];

// Modes that work without typing anything: the file itself is the input.
const DEFAULT_PROMPTS: Partial<Record<AssistMode, string>> = {
  explain: "Explain this file.",
  fix: "My last run failed. Find and fix the bug.",
  review: "Review this file.",
};

const TOOL_LABELS: Record<string, string> = {
  list_files: "listed files",
  read_file: "read",
  get_last_execution: "checked last run of",
  check_syntax: "checked syntax",
  propose_edit: "proposed an edit to",
};

// Earlier turns sent back to the model for follow-up questions.
const HISTORY_LIMIT = 12;

// Forget another member's run if its AI_FINISHED never arrives.
const ACTIVITY_TIMEOUT_MS = 60_000;

function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `r${Date.now()}${Math.random().toString(36).slice(2, 10)}`;
}

export default function AssistantPanel({
  projectId, activeFile, files, isOwner, onProposal, currentUserId, subscribeAi,
  selection = null, onClearSelection, askRequest = null,
}: Props) {
  const [status, setStatus] = useState<AIAssistantStatus | null>(null);
  const [mode, setMode] = useState<AssistMode>("ask");
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [keyError, setKeyError] = useState<string | null>(null);
  // Steps of our own pending request, streamed over the project socket.
  const [liveSteps, setLiveSteps] = useState<AIAgentStep[]>([]);
  const pendingId = useRef<string | null>(null);
  // Other members' runs in progress, by request id.
  const [others, setOthers] = useState<Record<string, Activity>>({});
  const [othersNote, setOthersNote] = useState<string | null>(null);
  const othersRef = useRef(others);
  othersRef.current = others;
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    let cancelled = false;
    api.getAssistStatus(projectId)
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch(() => { if (!cancelled) setStatus(null); });
    return () => { cancelled = true; };
  }, [projectId]);

  const pathOf = (id: number | null | undefined) => files.find((f) => f.id === id)?.path;
  const activeFileId = activeFile.id;

  useEffect(() => {
    if (!subscribeAi) return;
    const pending = timers.current;
    const forget = (id: string) => {
      clearTimeout(pending.get(id));
      pending.delete(id);
      setOthers((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    };
    const unsubscribe = subscribeAi((e) => {
      if (e.request_id === pendingId.current) {
        if (e.op === "AI_STEP") {
          setLiveSteps((prev) => [...prev, { tool: e.tool, summary: e.summary, ok: e.ok }]);
        }
        return;
      }
      if (e.user_id === currentUserId) return; // our own run in another tab
      if (e.op === "AI_STARTED") {
        setOthers((prev) => ({ ...prev, [e.request_id]: { username: e.username, mode: e.mode, fileId: e.file_id } }));
        pending.set(e.request_id, setTimeout(() => forget(e.request_id), ACTIVITY_TIMEOUT_MS));
      } else if (e.op === "AI_FINISHED") {
        const who = othersRef.current[e.request_id]?.username ?? "Someone";
        if (e.has_proposal && e.file_id === activeFileId) {
          setOthersNote(`${who} got an AI proposal for this file. If they accept it, the change will appear here.`);
        }
        forget(e.request_id);
      }
    });
    return () => {
      unsubscribe();
      pending.forEach(clearTimeout);
      pending.clear();
    };
  }, [subscribeAi, currentUserId, activeFileId]);

  function stepLabel(step: AIAgentStep): string {
    const label = TOOL_LABELS[step.tool] ?? step.tool;
    const id = /file (\d+)/.exec(step.summary)?.[1];
    const path = id ? pathOf(Number(id)) ?? `file ${id}` : "";
    return `${label}${path ? ` ${path}` : ""}${step.ok ? "" : " (failed)"}`;
  }

  async function send(sendMode: AssistMode = mode, typed: string = prompt) {
    const text = typed.trim() || DEFAULT_PROMPTS[sendMode] || "";
    if (!text || loading) return;
    const history: ChatTurn[] = messages
      .filter((m) => !m.error)
      .slice(-HISTORY_LIMIT)
      .map(({ role, text }) => ({ role, text }));
    setPrompt("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);
    setLiveSteps([]);
    const requestId = newRequestId();
    pendingId.current = requestId;
    try {
      const sentSelection = selection;
      onClearSelection?.();
      const res = await api.requestAssistant(projectId, {
        prompt: text,
        mode: sendMode,
        file_id: activeFile.id,
        history,
        request_id: requestId,
        ...(sentSelection ? { selection: sentSelection } : {}),
      });
      let note: string | undefined;
      if (res.proposal) {
        onProposal(res.proposal);
        const target = res.proposal.file_id;
        note = target != null && target !== activeFile.id
          ? `Proposed a change to ${pathOf(target) ?? `file ${target}`}. Open it to review.`
          : "Proposed a change. Review it in the editor.";
      } else if (res.truncated) {
        note = "Stopped early (step limit).";
      }
      setMessages((prev) => [...prev, { role: "assistant", text: res.response, steps: res.steps, note }]);
    } catch (e) {
      const text = friendlyError(e);
      setMessages((prev) => [...prev, { role: "assistant", text, error: true }]);
    } finally {
      pendingId.current = null;
      setLiveSteps([]);
      setLoading(false);
    }
  }

  // "Ask AI to fix" and similar buttons elsewhere send a request through here.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    if (!askRequest) return;
    setMode(askRequest.mode);
    sendRef.current(askRequest.mode, askRequest.prompt);
  }, [askRequest?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps -- one send per request

  async function saveKey() {
    if (apiKey.trim().length < 10) {
      setKeyError("That doesn't look like a valid key.");
      return;
    }
    try {
      await api.saveAssistConfig(projectId, { provider: "mistral", api_key: apiKey.trim() });
      setApiKey("");
      setKeyError(null);
      setStatus(await api.getAssistStatus(projectId));
    } catch (e) {
      setKeyError(friendlyError(e));
    }
  }

  const header = (
    <div style={{ padding: 12, borderBottom: "1px solid var(--border)", fontWeight: "bold", display: "flex", justifyContent: "space-between" }}>
      <span>AI Assistant</span>
      <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {messages.length > 0 && !loading && (
          <button className="secondary small" onClick={() => setMessages([])} title="Start over; the assistant forgets this conversation">
            New chat
          </button>
        )}
        {status?.model && <span style={{ fontWeight: "normal", fontSize: 11, opacity: 0.6 }}>{status.model}</span>}
      </span>
    </div>
  );

  if (status && !status.configured) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {header}
        <div style={{ padding: 12, fontSize: 13, display: "flex", flexDirection: "column", gap: 8 }}>
          {isOwner ? (
            <>
              <span>Add a Mistral API key to turn on the assistant for this project.</span>
              <input
                type="password"
                aria-label="Mistral API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Mistral API key"
                style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", padding: 8, borderRadius: 4 }}
              />
              <button onClick={saveKey}>Save key</button>
              {keyError && <span style={{ color: "var(--bad)" }}>{keyError}</span>}
            </>
          ) : (
            <span>The AI assistant isn't set up for this project. Ask the project owner to add an API key.</span>
          )}
        </div>
      </div>
    );
  }

  if (status && !status.enabled) {
    return (
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {header}
        <div style={{ padding: 12, fontSize: 13, opacity: 0.7 }}>The AI assistant is turned off.</div>
      </div>
    );
  }

  const current = MODES.find((m) => m.value === mode)!;
  const activity = Object.values(others);
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {header}
      {(activity.length > 0 || othersNote) && (
        <div role="status" style={{ padding: "6px 12px", fontSize: 11, opacity: 0.8, borderBottom: "1px solid var(--border)" }}>
          {activity.map((a, i) => {
            const path = pathOf(a.fileId);
            return <div key={i}>{a.username} is asking the AI ({a.mode}{path ? `, ${path}` : ""})</div>;
          })}
          {othersNote && (
            <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span>{othersNote}</span>
              <button aria-label="Dismiss" onClick={() => setOthersNote(null)} style={{ padding: "0 4px", fontSize: 11 }}>×</button>
            </div>
          )}
        </div>
      )}
      <div style={{ flexGrow: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ alignSelf: msg.role === "user" ? "flex-end" : "flex-start", maxWidth: "85%" }}>
            {msg.steps && msg.steps.length > 0 && (
              <ul aria-label="agent steps" style={{ margin: "0 0 4px", paddingLeft: 16, fontSize: 11, opacity: 0.6 }}>
                {msg.steps.map((s, j) => <li key={j}>{stepLabel(s)}</li>)}
              </ul>
            )}
            <div style={{
              background: msg.role === "user" ? "var(--accent)" : "var(--bg)",
              color: msg.error ? "var(--bad)" : "var(--fg)",
              padding: 8, borderRadius: 8, fontSize: 13, wordBreak: "break-word",
              whiteSpace: msg.role === "assistant" && !msg.error ? "normal" : "pre-wrap",
            }}>
              {msg.role === "assistant" && !msg.error ? <Markdown text={msg.text} /> : msg.text}
            </div>
            {msg.note && <div style={{ fontSize: 11, marginTop: 4, opacity: 0.8 }}>{msg.note}</div>}
          </div>
        ))}
        {loading && (
          <div style={{ alignSelf: "flex-start", maxWidth: "85%" }}>
            {liveSteps.length > 0 && (
              <ul aria-label="live agent steps" style={{ margin: "0 0 4px", paddingLeft: 16, fontSize: 11, opacity: 0.6 }}>
                {liveSteps.map((s, j) => <li key={j}>{stepLabel(s)}</li>)}
              </ul>
            )}
            <div style={{ background: "var(--bg)", padding: 8, borderRadius: 8, fontSize: 13, opacity: 0.6 }}>AI is thinking...</div>
          </div>
        )}
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 8 }}>
        <select
          aria-label="Assistant mode"
          value={mode}
          onChange={(e) => setMode(e.target.value as AssistMode)}
          style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", padding: 6, borderRadius: 4 }}
        >
          {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        {selection && (
          <span className="selection-chip" title={selection.text.slice(0, 300)}>
            Selection: {selection.start_line === selection.end_line ? `line ${selection.start_line}` : `lines ${selection.start_line}–${selection.end_line}`}
            <button aria-label="Don't send the selection" onClick={() => onClearSelection?.()}>✕</button>
          </span>
        )}
        <div style={{ display: "flex", gap: 8 }}>
          <input
            aria-label="Message the assistant"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={current.placeholder}
            style={{ flexGrow: 1, background: "var(--bg)", border: "1px solid var(--border)", color: "var(--fg)", padding: 8, borderRadius: 4 }}
          />
          <button onClick={() => send()} disabled={loading} style={{ padding: "8px 12px" }}>Send</button>
        </div>
      </div>
    </div>
  );
}
