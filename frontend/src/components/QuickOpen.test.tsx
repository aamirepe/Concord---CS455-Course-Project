import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import QuickOpen from "./QuickOpen";
import { FileMeta } from "../lib/api";

const file = (id: number, path: string): FileMeta => ({ id, project_id: 1, folder_id: null, path, language: "python", created_at: "now" });
const files = [file(1, "main.py"), file(2, "lib/util.py"), file(3, "notes.txt")];

describe("QuickOpen", () => {
  it("filters as you type and opens the highlighted file with Enter", () => {
    const onPick = vi.fn();
    render(<QuickOpen files={files} recent={[3]} onPick={onPick} onClose={vi.fn()} />);
    const input = screen.getByLabelText("File name");
    expect(input).toHaveFocus();
    expect(screen.getAllByRole("option")[0]).toHaveTextContent("notes.txt · recent");

    fireEvent.change(input, { target: { value: "ut" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith(files[1]);
  });

  it("moves with the arrow keys, clicks, and closes with Escape or outside", () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(<QuickOpen files={files} recent={[]} onPick={onPick} onClose={onClose} />);
    const input = screen.getByLabelText("File name");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getAllByRole("option")[0]);
    expect(onPick).toHaveBeenCalledTimes(1);

    fireEvent.change(input, { target: { value: "zzz" } });
    expect(screen.getByText("No matching files")).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onPick).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
