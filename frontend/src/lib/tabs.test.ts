import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadTabs, neighbourAfterClose, saveTabs } from "./tabs";

describe("tabs", () => {
  beforeEach(() => localStorage.clear());

  it("remembers open tabs per project", () => {
    expect(loadTabs(1)).toBeNull();
    saveTabs(1, { open: [3, 4], active: 4 });
    expect(loadTabs(1)).toEqual({ open: [3, 4], active: 4 });
    expect(loadTabs(2)).toBeNull();
  });

  it("ignores damaged data and storage errors", () => {
    localStorage.setItem("concord:tabs:1", "{nope");
    expect(loadTabs(1)).toBeNull();
    localStorage.setItem("concord:tabs:1", JSON.stringify({ open: [1, "x", 2.5], active: "y" }));
    expect(loadTabs(1)).toEqual({ open: [1], active: null });
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("full"); });
    expect(() => saveTabs(1, { open: [], active: null })).not.toThrow();
    spy.mockRestore();
  });

  it("shows the tab to the right after closing, else the one to the left", () => {
    expect(neighbourAfterClose([1, 2, 3], 2)).toBe(3);
    expect(neighbourAfterClose([1, 2, 3], 3)).toBe(2);
    expect(neighbourAfterClose([1], 1)).toBeNull();
  });
});
