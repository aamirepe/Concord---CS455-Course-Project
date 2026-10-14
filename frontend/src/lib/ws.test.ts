import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Client, Server, WebSocket as MockWebSocket } from "mock-socket";
import * as Y from "yjs";
import { connectCollab, connectProject } from "./ws";

// mock-socket gives us a real, event-driven WHATWG-ish WebSocket pair running
// in-process, so these tests exercise ws.ts's actual protocol/framing logic
// (open/message/close lifecycle, JSON vs binary frames, readyState gating)
// instead of asserting against a hand-rolled duck-typed double.

function b64ToBytes(b64: string): Uint8Array {
  return new Uint8Array(atob(b64).split("").map((c) => c.charCodeAt(0)));
}

function toBuffer(update: Uint8Array): ArrayBuffer {
  return update.buffer.slice(update.byteOffset, update.byteOffset + update.byteLength) as ArrayBuffer;
}

async function waitFor(predicate: () => boolean, timeoutMs = 1000): Promise<void> {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeoutMs) throw new Error("waitFor timed out");
    await new Promise((r) => setTimeout(r, 5));
  }
}

describe("connectProject", () => {
  let server: Server;
  const url = "ws://localhost:1234/ws/projects/1?token=tok-1";

  beforeEach(() => {
    vi.stubGlobal("WebSocket", MockWebSocket);
    server = new Server(url);
  });

  afterEach(async () => {
    await server.stop();
    vi.unstubAllGlobals();
  });

  it("builds the correct URL including the encoded token", () => {
    const conn = connectProject({ projectId: 1, token: "tok-1", wsBase: "ws://localhost:1234" });
    expect(conn.ws.url).toBe(url);
    conn.close();
  });

  it("only sends once the socket is OPEN, and is a no-op otherwise", async () => {
    const conn = connectProject({ projectId: 1, token: "tok-1", wsBase: "ws://localhost:1234" });

    const received: unknown[] = [];
    server.on("connection", (socket) => {
      socket.on("message", (data) => received.push(data));
    });

    // Sent before the connection has opened: readyState gating means this is dropped.
    conn.send({ op: "ping" });
    expect(received).toHaveLength(0);

    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);
    conn.send({ op: "ping" });
    await waitFor(() => received.length === 1);
    expect(JSON.parse(received[0] as string)).toEqual({ op: "ping" });

    conn.close();
  });

  it("close() swallows errors", async () => {
    const conn = connectProject({ projectId: 1, token: "tok-1", wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);
    conn.close();
    // Calling close() again on an already-closed socket must not throw.
    expect(() => conn.close()).not.toThrow();
  });

  it("delivers JSON messages, reconnects after a drop and says so", async () => {
    const sockets: Client[] = [];
    server.on("connection", (socket) => sockets.push(socket));
    const conn = connectProject({ projectId: 1, token: "tok-1", wsBase: "ws://localhost:1234", retryMs: 10 });
    const messages: unknown[] = [];
    let reconnects = 0;
    conn.onMessage((m) => messages.push(m));
    conn.onReconnect(() => { reconnects += 1; });

    await waitFor(() => sockets.length === 1);
    expect(reconnects).toBe(0); // the first connect isn't a reconnect
    sockets[0].send(JSON.stringify({ op: "FILE_CHANGED", file_id: 3 }));
    sockets[0].send("not json");
    await waitFor(() => messages.length === 1);
    expect(messages[0]).toEqual({ op: "FILE_CHANGED", file_id: 3 });

    sockets[0].close();
    await waitFor(() => reconnects === 1);
    expect(sockets).toHaveLength(2);
    conn.close();
  });

  it("gives up when refused, and after close()", async () => {
    const sockets: Client[] = [];
    server.on("connection", (socket) => sockets.push(socket));
    const conn = connectProject({ projectId: 1, token: "tok-1", wsBase: "ws://localhost:1234", retryMs: 10 });
    await waitFor(() => sockets.length === 1);
    sockets[0].close({ code: 1008, reason: "not a project member", wasClean: true });
    await new Promise((r) => setTimeout(r, 50));
    expect(sockets).toHaveLength(1);
    conn.close();
  });
});

describe("connectCollab", () => {
  let server: Server;
  const url = "ws://localhost:1234/ws/projects/1/files/2?token=tok-1";
  const user = { id: 1, username: "alice", color: "#fff" };

  beforeEach(() => {
    vi.stubGlobal("WebSocket", MockWebSocket);
    server = new Server(url);
  });

  afterEach(async () => {
    await server.stop();
    vi.unstubAllGlobals();
  });

  it("builds the correct URL", () => {
    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    expect(conn.ws.url).toBe(url);
    conn.close();
  });

  it("sends a hello message as soon as the connection opens", async () => {
    const received: unknown[] = [];
    server.on("connection", (socket) => {
      socket.on("message", (data) => received.push(JSON.parse(data as string)));
    });

    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => received.some((m: any) => m.op === "hello"));

    conn.close();
  });

  it("keeps an edit made before the socket opens and sends it once the server's copy has arrived", async () => {
    const received: unknown[] = [];
    server.on("connection", (socket) => {
      socket.on("message", (data) => received.push(JSON.parse(data as string)));
      socket.send(toBuffer(Y.encodeStateAsUpdate(new Y.Doc())));
    });

    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    // The mock socket hasn't had a chance to open yet (opening is asynchronous).
    conn.doc.getText("monaco").insert(0, "hello");

    await waitFor(() => received.some((m: any) => m.op === "update"));
    const updateMsg = received.find((m: any) => m.op === "update") as { op: string; data: string };

    // Round-trip the captured frame through a second, independent doc to confirm
    // the base64/JSON encoding actually carries a valid Yjs update.
    const mirror = new Y.Doc();
    Y.applyUpdate(mirror, b64ToBytes(updateMsg.data));
    expect(mirror.getText("monaco").toString()).toBe("hello");

    conn.close();
  });

  it("applies an inbound binary CRDT update to the local doc", async () => {
    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);

    // Build a real update from an independent doc and push it from the "server".
    const source = new Y.Doc();
    source.getText("monaco").insert(0, "from server");
    const update = Y.encodeStateAsUpdate(source);

    server.emit("message", update.buffer.slice(update.byteOffset, update.byteOffset + update.byteLength));

    await waitFor(() => conn.doc.getText("monaco").toString() === "from server");

    conn.close();
  });

  it("does not re-broadcast a remote-origin update back to the server", async () => {
    const received: unknown[] = [];
    server.on("connection", (socket) => {
      socket.on("message", (data) => received.push(JSON.parse(data as string)));
    });

    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);
    await waitFor(() => received.some((m: any) => m.op === "hello"));
    received.length = 0;

    const source = new Y.Doc();
    source.getText("monaco").insert(0, "remote text");
    const update = Y.encodeStateAsUpdate(source);
    server.emit("message", update.buffer.slice(update.byteOffset, update.byteOffset + update.byteLength));

    await waitFor(() => conn.doc.getText("monaco").toString() === "remote text");
    // Give any (incorrect) re-broadcast a chance to arrive before asserting absence.
    await new Promise((r) => setTimeout(r, 50));
    expect(received.some((m: any) => m.op === "update")).toBe(false);

    conn.close();
  });

  it("applies an inbound awareness_update message", async () => {
    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);

    const remoteDoc = new Y.Doc();
    // Import lazily to keep this test self-contained about what it needs from y-protocols.
    const { Awareness, encodeAwarenessUpdate } = await import("y-protocols/awareness");
    const remoteAwareness = new Awareness(remoteDoc);
    remoteAwareness.setLocalState({ user_id: 99, username: "bob", color: "#000" });
    const encoded = encodeAwarenessUpdate(remoteAwareness, [remoteAwareness.clientID]);
    const b64 = btoa(String.fromCharCode(...encoded));

    server.emit("message", JSON.stringify({ op: "awareness_update", data: b64 }));

    await waitFor(() => conn.awareness.getStates().size >= 2);
    const states = Array.from(conn.awareness.getStates().values());
    expect(states.some((s: any) => s.username === "bob")).toBe(true);

    conn.close();
  });

  it("applies an inbound presence message by client_id", async () => {
    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);

    server.emit(
      "message",
      JSON.stringify({
        op: "presence",
        states: [{ client_id: 42, user_id: 7, username: "carol", color: "#abc" }],
      })
    );

    await waitFor(() => conn.awareness.getStates().has(42));
    expect(conn.awareness.getStates().get(42)).toMatchObject({ user_id: 7, username: "carol", color: "#abc" });

    conn.close();
  });

  it("forwards local awareness changes upstream as base64 awareness_update frames", async () => {
    const received: unknown[] = [];
    server.on("connection", (socket) => {
      socket.on("message", (data) => received.push(JSON.parse(data as string)));
    });

    const conn = connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234" });
    await waitFor(() => conn.ws.readyState === MockWebSocket.OPEN);

    conn.awareness.setLocalState({ user_id: user.id, username: user.username, color: user.color });

    await waitFor(() => received.some((m: any) => m.op === "awareness_update"));
  });
});

describe("connectCollab: sync status and reconnecting", () => {
  let server: Server;
  const url = "ws://localhost:1234/ws/projects/1/files/2?token=tok-1";
  const user = { id: 1, username: "alice", color: "#fff" };
  // What the fake server has; it sends this in full to each new connection,
  // applies the client's updates and acks them, like the real gateway.
  let serverDoc: Y.Doc;
  let sockets: Client[];
  let received: { op: string; data?: string }[];
  let ack: boolean;

  beforeEach(() => {
    vi.stubGlobal("WebSocket", MockWebSocket);
    serverDoc = new Y.Doc();
    sockets = [];
    received = [];
    ack = true;
    server = new Server(url);
    server.on("connection", (socket) => {
      sockets.push(socket);
      socket.on("message", (data) => {
        const msg = JSON.parse(data as string);
        received.push(msg);
        if (msg.op === "update") {
          Y.applyUpdate(serverDoc, b64ToBytes(msg.data));
          if (ack) socket.send(JSON.stringify({ op: "ack" }));
        }
      });
      socket.send(toBuffer(Y.encodeStateAsUpdate(serverDoc)));
    });
  });

  afterEach(async () => {
    await server.stop();
    vi.unstubAllGlobals();
  });

  const open = () => connectCollab({ projectId: 1, fileId: 2, token: "tok-1", user, wsBase: "ws://localhost:1234", retryMs: 10 });

  it("goes connecting -> synced, and syncing until an edit is acknowledged", async () => {
    serverDoc.getText("monaco").insert(0, "saved text");
    const conn = open();
    const seen: string[] = [];
    conn.onStatus((s) => seen.push(s));
    expect(conn.status()).toBe("connecting");

    await conn.whenSynced;
    expect(conn.doc.getText("monaco").toString()).toBe("saved text");
    expect(conn.status()).toBe("synced");
    // Nothing local to send back: a viewer opening a file sends no update.
    expect(received.some((m) => m.op === "update")).toBe(false);

    ack = false;
    conn.doc.getText("monaco").insert(0, "x");
    expect(conn.status()).toBe("syncing");
    const flushed = conn.flush(50);
    expect(await flushed).toBe(false); // no ack yet

    sockets[0].send(JSON.stringify({ op: "ack" }));
    await waitFor(() => conn.status() === "synced");
    expect(await conn.flush()).toBe(true);
    expect(seen).toEqual(["synced", "syncing", "synced"]);
    conn.close();
  });

  it("reconnects after the connection drops and sends the edits made while offline", async () => {
    const conn = open();
    await conn.whenSynced;
    conn.doc.getText("monaco").insert(0, "before ");
    await waitFor(() => serverDoc.getText("monaco").toString() === "before ");

    sockets[0].close();
    await waitFor(() => conn.status() === "offline");
    conn.doc.getText("monaco").insert(7, "offline");
    expect(serverDoc.getText("monaco").toString()).toBe("before "); // not sent yet

    await waitFor(() => sockets.length === 2);
    await waitFor(() => conn.status() === "synced");
    expect(serverDoc.getText("monaco").toString()).toBe("before offline");
    conn.close();
  });

  it("re-announces its cursor ahead of the removal the server sends when a socket drops", async () => {
    // The server tells everyone else a dropped cursor is gone, at one clock tick
    // past the last it saw, and Yjs ignores anything not newer than that. So a
    // reconnecting client has to announce itself at least two ticks ahead, or
    // its cursor stays invisible until the next move.
    const { Awareness, applyAwarenessUpdate } = await import("y-protocols/awareness");
    const announcedClocks = (clientId: number) =>
      received
        .filter((m) => m.op === "awareness_update")
        .map((m) => {
          // A frame sent before the client has any state says "nobody", which has no clock.
          const probe = new Awareness(new Y.Doc());
          applyAwarenessUpdate(probe, b64ToBytes(m.data!), "probe");
          return probe.meta.get(clientId)?.clock;
        })
        .filter((clock): clock is number => clock !== undefined);

    const conn = open();
    await conn.whenSynced;
    conn.awareness.setLocalState({ user_id: user.id, username: user.username, color: user.color });
    await waitFor(() => announcedClocks(conn.doc.clientID).length > 0);
    const before = announcedClocks(conn.doc.clientID).at(-1)!;
    const seen = announcedClocks(conn.doc.clientID).length;

    sockets[0].close();
    await waitFor(() => sockets.length === 2);
    await waitFor(() => announcedClocks(conn.doc.clientID).length > seen);

    expect(announcedClocks(conn.doc.clientID).at(-1)!).toBeGreaterThanOrEqual(before + 2);
    conn.close();
  });

  it("picks up what others wrote while it was offline", async () => {
    const conn = open();
    await conn.whenSynced;
    sockets[0].close();
    await waitFor(() => conn.status() === "offline");
    serverDoc.getText("monaco").insert(0, "bob typed this");

    await waitFor(() => conn.doc.getText("monaco").toString() === "bob typed this");
    conn.close();
  });

  it("stops retrying when the server refuses the connection", async () => {
    const conn = open();
    await conn.whenSynced;
    sockets[0].close({ code: 1008, reason: "not a project member", wasClean: true });
    await waitFor(() => conn.status() === "denied");
    await new Promise((r) => setTimeout(r, 50));
    expect(sockets).toHaveLength(1);
    conn.close();
  });

  it("does not reconnect after close()", async () => {
    const conn = open();
    await conn.whenSynced;
    conn.close();
    await new Promise((r) => setTimeout(r, 50));
    expect(sockets).toHaveLength(1);
  });

  it("answers the server's ping, and passes other JSON messages to onMessage", async () => {
    const conn = open();
    const messages: { op: string }[] = [];
    conn.onMessage((m) => messages.push(m));
    await conn.whenSynced;

    sockets[0].send(JSON.stringify({ op: "ping" }));
    await waitFor(() => received.some((m) => m.op === "pong"));
    sockets[0].send(JSON.stringify({ op: "exec_event", event: { status: "running" } }));
    await waitFor(() => messages.some((m) => m.op === "exec_event"));
    conn.close();
  });
});
