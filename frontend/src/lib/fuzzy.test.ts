import { describe, expect, it } from "vitest";
import { fuzzyMatch, rankFiles } from "./fuzzy";

const f = (id: number, path: string) => ({ id, path });

describe("fuzzyMatch", () => {
  it("needs the characters in order", () => {
    expect(fuzzyMatch("mpy", "main.py")).not.toBeNull();
    expect(fuzzyMatch("ypm", "main.py")).toBeNull();
    expect(fuzzyMatch("", "x")).toEqual({ score: 0, positions: [] });
  });

  it("matches inside the file name before the folders", () => {
    expect(fuzzyMatch("ut", "src/utils.py")!.positions).toEqual([4, 5]);
  });
});

describe("rankFiles", () => {
  const files = [f(1, "src/main.py"), f(2, "tests/test_main.py"), f(3, "maintenance/notes.txt"), f(4, "README.md")];

  it("puts file-name prefix matches first", () => {
    expect(rankFiles(files, "main").map((r) => r.file.id).slice(0, 2)).toEqual([1, 2]);
    expect(rankFiles(files, "readme")[0].file.id).toBe(4);
    expect(rankFiles(files, "zzz")).toEqual([]);
  });

  it("lists recent files first when there is no query", () => {
    expect(rankFiles(files, "", [3, 2]).map((r) => r.file.id)).toEqual([3, 2, 4, 1]);
  });
});
