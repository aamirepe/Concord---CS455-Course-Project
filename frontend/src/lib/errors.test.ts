import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, dismissToast, friendlyError, parseDetail, subscribeToasts, toast, Toast } from "./errors";

describe("parseDetail", () => {
  it("reads FastAPI's string detail", () => {
    expect(parseDetail('{"detail":"File not found"}')).toBe("File not found");
  });

  it("joins validation errors with their field", () => {
    const body = JSON.stringify({ detail: [{ loc: ["body", "path"], msg: "field required" }, { loc: [], msg: "bad" }] });
    expect(parseDetail(body)).toBe("path: field required; bad");
  });

  it("keeps short plain text but not HTML error pages", () => {
    expect(parseDetail("not a member")).toBe("not a member");
    expect(parseDetail("<html>502</html>")).toBeNull();
    expect(parseDetail("")).toBeNull();
    expect(parseDetail('{"other":1}')).toBeNull();
  });
});

describe("friendlyError", () => {
  const err = (status: number, body = "", retry: number | null = null) => new ApiError(status, "X", body, retry);

  it("keeps the old message shape for code that checks the status prefix", () => {
    expect(err(404, "nope").message).toBe("404 X: nope");
  });

  it("explains each kind of failure in a sentence", () => {
    expect(friendlyError(err(0))).toMatch(/can't reach the server/i);
    expect(friendlyError(new TypeError("Failed to fetch"))).toMatch(/can't reach the server/i);
    expect(friendlyError(err(401))).toMatch(/session expired/i);
    expect(friendlyError(err(401, '{"detail":"Invalid credentials"}'))).toBe("Invalid credentials");
    expect(friendlyError(err(403, '{"detail":"Editor role required."}'))).toBe("You don't have permission to do that: editor role required");
    expect(friendlyError(err(403))).toBe("You don't have permission to do that.");
    expect(friendlyError(err(404))).toMatch(/^Not found/);
    expect(friendlyError(err(409, '{"detail":"Path already exists"}'))).toBe("Path already exists");
    expect(friendlyError(err(422, JSON.stringify({ detail: [{ loc: ["body", "name"], msg: "too long" }] })))).toBe("Please check the input (name: too long).");
    expect(friendlyError(err(422))).toBe("Please check the input.");
    expect(friendlyError(err(429, "", 12))).toBe("Too many requests. Try again in 12 s.");
    expect(friendlyError(err(429))).toMatch(/wait a moment/i);
    expect(friendlyError(err(429, '{"detail":"Slow down."}'))).toBe("Slow down.");
    expect(friendlyError(err(502, '{"detail":"AI assistant error: boom"}'))).toBe("AI assistant error: boom");
    expect(friendlyError(err(503))).toMatch(/unavailable/i);
    expect(friendlyError(err(500, "Internal Server Error"))).toMatch(/went wrong on the server/i);
    expect(friendlyError(err(418))).toBe("418 X: ");
    expect(friendlyError(new Error("plain"))).toBe("plain");
    expect(friendlyError("text")).toBe("text");
  });
});

describe("toasts", () => {
  afterEach(() => vi.useRealTimers());

  it("shows, de-duplicates, dismisses and expires messages", () => {
    vi.useFakeTimers();
    let seen: Toast[] = [];
    const unsubscribe = subscribeToasts((t) => { seen = t; });
    const id = toast("Saved", "success");
    expect(toast("Saved", "success")).toBe(id);
    toast("Broke", "error");
    expect(seen.map((t) => t.text)).toEqual(["Saved", "Broke"]);
    dismissToast(id);
    expect(seen.map((t) => t.text)).toEqual(["Broke"]);
    vi.advanceTimersByTime(8000);
    expect(seen).toEqual([]);
    unsubscribe();
  });

  it("keeps at most four", () => {
    let seen: Toast[] = [];
    const unsubscribe = subscribeToasts((t) => { seen = t; });
    for (let i = 0; i < 6; i++) toast(`m${i}`);
    expect(seen.map((t) => t.text)).toEqual(["m2", "m3", "m4", "m5"]);
    seen.forEach((t) => dismissToast(t.id));
    unsubscribe();
  });
});
