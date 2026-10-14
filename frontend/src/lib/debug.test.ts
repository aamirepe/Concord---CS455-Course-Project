import { describe, expect, it } from "vitest";
import { applyDebugEvent, breakpointsFromEvent, emptyDebug, isDebugEvent } from "./debug";

const paused = {
  type: "debug_paused", execution_id: 7, reason: "breakpoint", by: null, path: "helper.py", file_id: 11, line: 3,
  stack: [{ id: 2, name: "add", line: 3, path: "helper.py", file_id: 11 }, { id: 3, name: "<module>", line: 6, path: "main.py", file_id: 10 }],
  scopes: [{ name: "Locals", ref: 4 }, { name: "Globals", ref: 5 }],
  variables: [{ name: "a", value: "0", type: "int", ref: 0 }],
};

describe("applyDebugEvent", () => {
  it("follows a session from start to finish", () => {
    let s = applyDebugEvent(emptyDebug, { type: "debug_started", execution_id: 7, file_id: 10, path: "main.py", username: "dave" });
    expect(s.active).toMatchObject({ execution_id: 7, path: "main.py", username: "dave" });
    expect(s.message).toBe("dave started debugging main.py");

    s = applyDebugEvent(s, paused);
    expect(s.paused?.line).toBe(3);
    expect(s.frameId).toBe(2);
    expect(s.children).toEqual({ 4: paused.variables });

    s = applyDebugEvent(s, { type: "debug_variables", execution_id: 7, ref: 5, variables: [{ name: "xs", value: "[1]", ref: 9 }] });
    expect(Object.keys(s.children)).toEqual(["4", "5"]);
    s = applyDebugEvent(s, { type: "debug_variables", execution_id: 99, ref: 6, variables: [] });
    expect(s.children[6]).toBeUndefined(); // another session

    s = applyDebugEvent(s, { type: "debug_frame", execution_id: 7, frame_id: 3, scopes: [{ name: "Locals", ref: 8 }], variables: [] });
    expect([s.frameId, s.scopes[0].ref, s.children]).toEqual([3, 8, { 8: [] }]);

    s = applyDebugEvent(s, { type: "debug_running", execution_id: 7, by: "bob" });
    expect([s.paused, s.message]).toEqual([null, "Running (bob)"]);
    s = applyDebugEvent(s, { type: "debug_message", execution_id: 7, text: "next failed" });
    expect(s.message).toBe("next failed");

    s = applyDebugEvent(s, { type: "debug_stopped", execution_id: 7, reason: "exited", exit_code: 0 });
    expect(s.active).toBeNull();
    expect(s.message).toBe("The program finished (exit code 0).");
  });

  it("ignores pauses from another execution while a session is active", () => {
    const active = applyDebugEvent(emptyDebug, { type: "debug_started", execution_id: 7, file_id: 10, path: "main.py" });
    const foreignPause = { ...paused, execution_id: 8 };
    expect(applyDebugEvent(active, foreignPause)).toBe(active);
    const stopped = applyDebugEvent(active, paused);
    expect(applyDebugEvent(stopped, foreignPause)).toBe(stopped);
  });

  it("explains why a session ended", () => {
    const end = (reason: string, extra = {}) => applyDebugEvent(emptyDebug, { type: "debug_stopped", execution_id: 1, reason, ...extra }).message;
    expect(end("stopped", { by: "dave" })).toBe("dave stopped the debugger.");
    expect(end("idle")).toMatch(/nobody stepped/);
    expect(end("timeout")).toMatch(/time limit/);
    expect(end("cancelled")).toMatch(/cancelled/);
    expect(end("error", { error: "boom" })).toBe("The debugger stopped: boom");
  });

  it("a pause without a start (joined late) still makes the session active; others' events are ignored", () => {
    const s = applyDebugEvent(emptyDebug, paused);
    expect(s.active?.execution_id).toBe(7);
    expect(applyDebugEvent(s, { type: "debug_running", execution_id: 8 })).toBe(s);
    expect(applyDebugEvent(s, { type: "debug_stopped", execution_id: 8, reason: "exited" })).toBe(s);
    expect(applyDebugEvent(s, { type: "debug_frame", execution_id: 8, frame_id: 1 })).toBe(s);
    expect(applyDebugEvent(s, { type: "debug_message", execution_id: 8, text: "x" })).toBe(s);
    expect(applyDebugEvent(s, { type: "something_else" })).toBe(s);
  });
});

describe("helpers", () => {
  it("recognises debugger events and reads breakpoint updates", () => {
    expect(isDebugEvent({ type: "debug_paused" })).toBe(true);
    expect(isDebugEvent({ type: "breakpoints_updated" })).toBe(true);
    expect(isDebugEvent({ type: "execution_status" })).toBe(false);
    expect(isDebugEvent(null)).toBe(false);
    expect(breakpointsFromEvent({ lines_by_file: { "10": [3, 5], "11": [] } })).toEqual({ 10: [3, 5], 11: [] });
    expect(breakpointsFromEvent({})).toEqual({});
  });
});
