import * as Y from "yjs";
import {
  Awareness,
  applyAwarenessUpdate,
  encodeAwarenessUpdate,
  removeAwarenessStates,
} from "y-protocols/awareness";

export type CollabMessage =
  | { op: "hello" }
  | { op: "sync_step1"; sv: string }
  | { op: "awareness"; state: { user_id: number; username: string; cursor?: { line: number; col: number }; color: string } }
  | { op: "awareness_update"; data: string }
  | { op: "exec_event"; event: any }
  | { op: "presence"; states: any[] }
  | { op: "project_presence"; user_id: number; file_id: number | null; left_file_id?: number }
  | { op: "error"; message: string }
  | { op: "ack" };

// We tag local edits with this origin so we don't echo them back into the doc
// when the server forwards the same update to other clients in this room.
const REMOTE_ORIGIN = Symbol("concord:remote");

/** A JSON message from the server; ``op`` says which (see gateway.py for the list). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- payloads vary by op; consumers narrow on op
export type ServerMessage = { op: string; [field: string]: any };
type PresenceState = { client_id?: number; user_id: number; username: string; color: string; cursor?: unknown };

export type ProjectConnection = {
  /** The current socket; replaced on every reconnect. */
  readonly ws: WebSocket;
  /** Called with each JSON message (WORKSPACE_*, AI_*, FILE_CHANGED, ...). */
  onMessage: (fn: (msg: ServerMessage) => void) => () => void;
  /** Called after a *re*connect, to refetch whatever was missed while it was down. */
  onReconnect: (fn: () => void) => () => void;
  send: (msg: unknown) => void;
  close: () => void;
};

/** Project-wide events. Reconnects on its own if the connection drops. */
export function connectProject(opts: {
  projectId: number;
  token: string;
  wsBase: string;
  /** Delay before the first reconnect attempt; doubles up to 5 s. */
  retryMs?: number;
}): ProjectConnection {
  const url = `${opts.wsBase}/ws/projects/${opts.projectId}?token=${encodeURIComponent(opts.token)}`;
  const messageListeners = new Set<(msg: ServerMessage) => void>();
  const reconnectListeners = new Set<() => void>();
  let ws: WebSocket;
  let closed = false;
  let attempts = 0;
  let opened = false;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    ws = new WebSocket(url);
    ws.addEventListener("open", () => {
      attempts = 0;
      if (opened) reconnectListeners.forEach((fn) => fn());
      opened = true;
    });
    ws.addEventListener("message", (event) => {
      if (typeof event.data !== "string") return;
      try {
        const msg = JSON.parse(event.data);
        messageListeners.forEach((fn) => fn(msg));
      } catch { /* ignore non-JSON */ }
    });
    ws.addEventListener("close", (event) => {
      if (closed || event.code === 1008) return;
      const delay = Math.min((opts.retryMs ?? 500) * 2 ** attempts, 5000);
      attempts += 1;
      retryTimer = setTimeout(connect, delay);
    });
  };
  connect();

  return {
    get ws() {
      return ws;
    },
    onMessage(fn) {
      messageListeners.add(fn);
      return () => { messageListeners.delete(fn); };
    },
    onReconnect(fn) {
      reconnectListeners.add(fn);
      return () => { reconnectListeners.delete(fn); };
    },
    send(msg: unknown) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(typeof msg === "string" ? msg : JSON.stringify(msg));
      }
    },
    close() {
      closed = true;
      clearTimeout(retryTimer);
      try { ws.close(); } catch { /* noop */ }
    },
  };
}

/**
 * Where a file's edits stand, for the editor's status line:
 * - connecting: waiting for the server's copy (first load or reconnect)
 * - syncing:    connected, some local edits not yet acknowledged
 * - synced:     the server has everything typed here
 * - offline:    connection lost; edits are kept and sent on reconnect
 * - denied:     the server refused us (not a member, bad token); no retry
 */
export type SyncStatus = "connecting" | "syncing" | "synced" | "offline" | "denied";

export type CollabConnection = {
  /** The current socket; replaced on every reconnect. */
  readonly ws: WebSocket;
  doc: Y.Doc;
  awareness: Awareness;
  status: () => SyncStatus;
  /** Called on every status change; returns an unsubscribe function. */
  onStatus: (fn: (s: SyncStatus) => void) => () => void;
  /** Called with each JSON message from the server (exec_event, WORKSPACE_*, ...). */
  onMessage: (fn: (msg: ServerMessage) => void) => () => void;
  /** Resolves once the server's copy has been loaded into ``doc`` (the first time). */
  whenSynced: Promise<void>;
  /** Resolves when every local edit is acknowledged, or false after ``timeoutMs``. */
  flush: (timeoutMs?: number) => Promise<boolean>;
  send: (msg: CollabMessage | Uint8Array) => void;
  close: () => void;
};

const toB64 = (bytes: Uint8Array) => {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
};
const fromB64 = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

function hasContent(update: Uint8Array): boolean {
  const { structs, ds } = Y.decodeUpdate(update);
  return structs.length > 0 || ds.clients.size > 0;
}

/**
 * Live connection to one file's document.
 *
 * The ``Y.Doc`` outlives the socket: if the connection drops, local edits stay
 * in the doc, and on reconnect the server's first frame (its full state) tells
 * us what it is missing, which we send back as one update. So nothing typed
 * while offline, or in flight when the connection died, is lost.
 */
export function connectCollab(opts: {
  projectId: number;
  fileId: number;
  token: string;
  user: { id: number; username: string; color: string };
  wsBase: string;
  /** Delay before the first reconnect attempt; doubles up to 5 s. */
  retryMs?: number;
}): CollabConnection {
  const url = `${opts.wsBase}/ws/projects/${opts.projectId}/files/${opts.fileId}?token=${encodeURIComponent(opts.token)}`;
  const doc = new Y.Doc();
  const awareness = new Awareness(doc);
  const statusListeners = new Set<(s: SyncStatus) => void>();
  const messageListeners = new Set<(msg: ServerMessage) => void>();
  const flushWaiters = new Set<() => void>();

  let ws: WebSocket;
  let status: SyncStatus = "connecting";
  let closed = false;
  let synced = false; // the current socket has delivered the server's state
  let unacked = 0;
  let attempts = 0;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let pingTimer: ReturnType<typeof setInterval> | undefined;
  let resolveSynced!: () => void;
  const whenSynced = new Promise<void>((r) => { resolveSynced = r; });

  const setStatus = (next: SyncStatus) => {
    if (next === status) return;
    status = next;
    statusListeners.forEach((fn) => fn(next));
    if (next === "synced") {
      flushWaiters.forEach((fn) => fn());
      flushWaiters.clear();
    }
  };
  const settle = () => setStatus(unacked > 0 ? "syncing" : "synced");

  const sendUpdate = (update: Uint8Array) => {
    unacked += 1;
    ws.send(JSON.stringify({ op: "update", data: toB64(update) }));
    settle();
  };

  const sendAwareness = () => {
    if (ws.readyState !== WebSocket.OPEN) return;
    const update = encodeAwarenessUpdate(awareness, [awareness.clientID]);
    ws.send(JSON.stringify({ op: "awareness_update", data: toB64(update) }));
  };

  // When a socket drops, the server tells everyone else this cursor is gone, at
  // one clock tick past the last one it saw, and Yjs ignores anything that isn't
  // newer. Announcing two ticks ahead on reconnect puts the cursor back at once
  // instead of after the next move or the ~15 s renewal.
  let everConnected = false;
  const reannounceAwareness = () => {
    const state = awareness.getLocalState();
    if (!state) return sendAwareness();
    awareness.setLocalState({ ...state });
    awareness.setLocalState({ ...state });
  };

  const connect = () => {
    synced = false;
    unacked = 0;
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    ws.addEventListener("open", () => {
      attempts = 0;
      ws.send(JSON.stringify({ op: "hello" }));
      if (everConnected) reannounceAwareness();
      else sendAwareness();
      everConnected = true;
      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: "ping" }));
      }, 10000);
    });

    ws.addEventListener("message", (event) => {
      if (event.data instanceof ArrayBuffer) {
        const update = new Uint8Array(event.data);
        Y.applyUpdate(doc, update, REMOTE_ORIGIN);
        if (!synced) {
          // The first frame is the server's whole document: send back whatever
          // we have that it doesn't (edits made offline or lost in flight).
          synced = true;
          const missing = Y.encodeStateAsUpdate(doc, Y.encodeStateVectorFromUpdate(update));
          if (hasContent(missing)) sendUpdate(missing);
          settle();
          resolveSynced();
        }
        return;
      }
      if (typeof event.data !== "string") return;
      let msg: ServerMessage;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      if (msg.op === "ack") {
        unacked = Math.max(0, unacked - 1);
        if (synced) settle();
      } else if (msg.op === "ping") {
        // Answering keeps the connection alive even when this tab is in the
        // background and the browser throttles our own ping timer.
        ws.send(JSON.stringify({ op: "pong" }));
      } else if (msg.op === "awareness_update") {
        applyAwarenessUpdate(awareness, fromB64(msg.data), REMOTE_ORIGIN);
      } else if (msg.op === "presence" && Array.isArray(msg.states)) {
        // Full-roster snapshot (as opposed to the incremental deltas that
        // arrive via awareness_update): write directly into the states map
        // and emit "change" ourselves, since Awareness has no public API
        // for injecting a remote peer's state outside of applyAwarenessUpdate.
        const updatedClientIds: number[] = [];
        msg.states.forEach((s: PresenceState) => {
          if (typeof s.client_id === "number") {
            awareness.states.set(s.client_id, {
              user_id: s.user_id,
              username: s.username,
              color: s.color,
              cursor: s.cursor,
            });
            updatedClientIds.push(s.client_id);
          }
        });
        if (updatedClientIds.length > 0) {
          awareness.emit("change", [{ added: [], updated: updatedClientIds, removed: [] }, REMOTE_ORIGIN]);
        }
      }
      messageListeners.forEach((fn) => fn(msg));
    });

    ws.addEventListener("close", (event) => {
      clearInterval(pingTimer);
      // Other people's cursors are stale until we hear from them again.
      const others = Array.from(awareness.getStates().keys()).filter((id) => id !== doc.clientID);
      removeAwarenessStates(awareness, others, REMOTE_ORIGIN);
      if (closed) return;
      if (event.code === 1008) {
        setStatus("denied");
        return;
      }
      setStatus("offline");
      const delay = Math.min((opts.retryMs ?? 500) * 2 ** attempts, 5000);
      attempts += 1;
      retryTimer = setTimeout(connect, delay);
    });
  };

  // Forward local doc edits upstream. Before the (re)connection has synced they
  // just stay in the doc; the sync above sends them.
  doc.on("update", (update: Uint8Array, origin: unknown) => {
    if (origin === REMOTE_ORIGIN) return;
    if (synced && ws.readyState === WebSocket.OPEN) sendUpdate(update);
  });

  awareness.on("update", ({ added, updated, removed }: { added: number[]; updated: number[]; removed: number[] }, origin: unknown) => {
    if (origin === REMOTE_ORIGIN) return;
    if ([...added, ...updated, ...removed].includes(doc.clientID)) sendAwareness();
  });

  connect();

  return {
    get ws() {
      return ws;
    },
    doc,
    awareness,
    status: () => status,
    onStatus(fn) {
      statusListeners.add(fn);
      return () => { statusListeners.delete(fn); };
    },
    onMessage(fn) {
      messageListeners.add(fn);
      return () => { messageListeners.delete(fn); };
    },
    whenSynced,
    flush(timeoutMs = 5000) {
      if (status === "synced") return Promise.resolve(true);
      return new Promise<boolean>((resolve) => {
        const done = () => { clearTimeout(timer); resolve(true); };
        const timer = setTimeout(() => { flushWaiters.delete(done); resolve(false); }, timeoutMs);
        flushWaiters.add(done);
      });
    },
    send(msg: CollabMessage | Uint8Array) {
      if (ws.readyState !== WebSocket.OPEN) return;
      ws.send(msg instanceof Uint8Array ? msg : JSON.stringify(msg));
    },
    close() {
      closed = true;
      clearTimeout(retryTimer);
      clearInterval(pingTimer);
      // Tells the others our cursor is gone (sent while the socket is still open).
      awareness.setLocalState(null);
      awareness.destroy();
      try { ws.close(); } catch { /* noop */ }
    },
  };
}
