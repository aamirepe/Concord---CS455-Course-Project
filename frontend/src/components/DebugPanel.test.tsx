import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import DebugPanel from "./DebugPanel";
import { applyDebugEvent, emptyDebug } from "../lib/debug";

const started = applyDebugEvent(emptyDebug, { type: "debug_started", execution_id: 7, file_id: 10, path: "main.py", username: "dave" });
const paused = applyDebugEvent(started, {
  type: "debug_paused", execution_id: 7, reason: "breakpoint", by: "bob", path: "main.py", file_id: 10, line: 4,
  stack: [{ id: 2, name: "add", line: 4, path: "main.py", file_id: 10 }, { id: 3, name: "<module>", line: 9, path: "main.py", file_id: 10 }],
  scopes: [{ name: "Locals", ref: 4 }, { name: "Globals", ref: 5 }],
  variables: [{ name: "total", value: "1", type: "int", ref: 0 }, { name: "xs", value: "[1, 2]", type: "list", ref: 9 }],
});

function renderPanel(state = paused, canStep = true) {
  const props = { onCommand: vi.fn(), onExpand: vi.fn(), onSelectFrame: vi.fn() };
  const utils = render(<DebugPanel state={state} canStep={canStep} {...props} />);
  return { ...utils, ...props };
}

describe("DebugPanel", () => {
  it("explains how to start when nothing is running", () => {
    renderPanel(emptyDebug);
    expect(screen.getByRole("status")).toHaveTextContent(/click left of a line number/i);
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
  });

  it("shows where it is paused, who stepped, the stack and the variables", () => {
    const { onCommand, onSelectFrame } = renderPanel();
    expect(screen.getByRole("status")).toHaveTextContent("Paused at main.py:4 (breakpoint) · last step by bob");
    expect(screen.getByText("total")).toBeInTheDocument();
    expect(screen.getByText("[1, 2]")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /step over/i }));
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onCommand.mock.calls).toEqual([["next"], ["stop"]]);
    expect(screen.getByRole("button", { name: /pause/i })).toBeDisabled(); // already paused

    fireEvent.click(screen.getByTitle("main.py:9"));
    expect(onSelectFrame).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
  });

  it("expands variables and scopes on demand", () => {
    const { onExpand, rerender } = renderPanel();
    fireEvent.click(screen.getByText("xs"));
    expect(onExpand).toHaveBeenCalledWith(9);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    const loaded = applyDebugEvent(paused, { type: "debug_variables", execution_id: 7, ref: 9, variables: [{ name: "0", value: "1", ref: 0 }] });
    rerender(<DebugPanel state={loaded} canStep onCommand={vi.fn()} onExpand={onExpand} onSelectFrame={vi.fn()} />);
    expect(screen.queryByText("Loading…")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /globals/i }));
    expect(onExpand).toHaveBeenCalledWith(5);
    fireEvent.click(screen.getByRole("button", { name: /locals/i })); // collapse the first scope
    expect(screen.queryByText("total")).toBeNull();
  });

  it("collapses and reopens loaded Locals without requesting adapter variables", () => {
    const { onExpand } = renderPanel();
    const locals = screen.getByRole("button", { name: /locals/i });
    fireEvent.click(locals);
    expect(locals).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("total")).toBeNull();
    fireEvent.click(locals);
    expect(locals).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("total")).toBeInTheDocument();
    expect(onExpand).not.toHaveBeenCalled();
  });

  it("lets people without the debugger role watch but not step", () => {
    renderPanel(paused, false);
    for (const name of [/continue/i, /step over/i, /stop/i]) expect(screen.getByRole("button", { name })).toBeDisabled();
    expect(screen.getByRole("button", { name: /continue/i })).toHaveAttribute("title", "Stepping needs the debugger role");
  });

  it("while running: pause is the only step control", () => {
    renderPanel(started);
    expect(screen.getByRole("button", { name: /pause/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /continue/i })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("dave started debugging main.py");
  });
});
