import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import ConsolePanel from "./ConsolePanel";
import { applyRunEvent, isFailed, Run, RunEvent } from "../lib/runs";

function fold(events: RunEvent[]): Run[] {
  return events.reduce(applyRunEvent, [] as Run[]);
}

describe("applyRunEvent", () => {
  it("assembles one run from its events, whatever order they come in", () => {
    const [run] = fold([
      { type: "execution_status", execution_id: 7, status: "running" },
      { op: "EXECUTION_QUEUED", execution_id: 7, file_id: 1, path: "main.py", user_id: 2, username: "bob" },
      { type: "execution_output", execution_id: 7, stdout: "hi\n", stderr: "", exit_code: 0, duration: 0.42 },
      { type: "execution_status", execution_id: 7, status: "completed" },
      { type: "execution_status", execution_id: 7, status: "running" }, // late: ignored
    ]);
    expect(run).toMatchObject({ id: 7, path: "main.py", username: "bob", status: "completed", stdout: "hi\n", exitCode: 0, duration: 0.42 });
  });

  it("keeps runs separate and in order, and ignores events it doesn't know", () => {
    const runs = fold([
      { type: "execution_status", execution_id: 1, status: "running" },
      { type: "execution_status", execution_id: 2, status: "running" },
      { type: "something_else", execution_id: 1 },
      { type: "execution_status", status: "running" } as RunEvent,
    ]);
    expect(runs.map((r) => r.id)).toEqual([1, 2]);
  });

  it("marks runs with input, and collects debugger notes", () => {
    const [run] = fold([
      { type: "local_queued", execution_id: 3, file_id: 1, path: "a.py", username: "alice", with_input: true },
      { type: "debug_started", execution_id: 3 },
      { type: "debug_paused", execution_id: 3, line: 4 },
      { type: "debug_paused", execution_id: 3, line: 5 },
    ]);
    expect(run.withInput).toBe(true);
    expect(run.notes).toEqual(["under the debugger", "paused at a breakpoint"]);
  });

  it("appends a debug session's live output, then takes the final output", () => {
    const events = [
      { type: "execution_output_chunk", execution_id: 4, stream: "stdout", text: "sum" },
      { type: "execution_output_chunk", execution_id: 4, stream: "stdout", text: " 6\n" },
      { type: "execution_output_chunk", execution_id: 4, stream: "stderr", text: "warn\n" },
    ];
    const [live] = fold(events);
    expect([live.stdout, live.stderr]).toEqual(["sum 6\n", "warn\n"]);
    const [done] = fold([...events, { type: "execution_output", execution_id: 4, stdout: "sum 6\n", stderr: "", exit_code: 0 }]);
    expect([done.stdout, done.stderr, done.exitCode]).toEqual(["sum 6\n", "", 0]);
  });

  it("counts timeouts and non-zero exits as failures, not cancellations", () => {
    const base = fold([{ type: "execution_status", execution_id: 1, status: "completed" }])[0];
    expect(isFailed({ ...base, status: "timeout" })).toBe(true);
    expect(isFailed({ ...base, exitCode: 1 })).toBe(true);
    expect(isFailed({ ...base, status: "cancelled", exitCode: 137 })).toBe(false);
    expect(isFailed(base)).toBe(false);
  });
});

describe("ConsolePanel", () => {
  const failed = fold([
    { op: "EXECUTION_QUEUED", execution_id: 9, file_id: 1, path: "main.py", user_id: 1, username: "alice" },
    { type: "execution_output", execution_id: 9, stdout: "", stderr: "NameError: x", exit_code: 1, duration: 0.1 },
    { type: "execution_status", execution_id: 9, status: "failed" },
  ]);
  const props = { runs: failed, onClear: vi.fn(), stdin: "", onStdinChange: vi.fn(), showInput: false, onToggleInput: vi.fn() };

  it("shows each run with who ran it, its output and exit code", () => {
    render(<ConsolePanel {...props} />);
    const run = screen.getByRole("region", { name: "Run 9" });
    expect(run).toHaveTextContent("main.py · run #9 by alice");
    expect(run).toHaveTextContent("NameError: x");
    expect(run).toHaveTextContent("exit code 1 · 0.10 s");
  });

  it("offers Ask AI to fix on a failed run", () => {
    const onAskFix = vi.fn();
    render(<ConsolePanel {...props} onAskFix={onAskFix} />);
    fireEvent.click(screen.getByText("Ask AI to fix"));
    expect(onAskFix).toHaveBeenCalledWith(expect.objectContaining({ id: 9 }));
  });

  it("clears, and edits the program input", () => {
    const onStdinChange = vi.fn();
    render(<ConsolePanel {...props} showInput onStdinChange={onStdinChange} />);
    fireEvent.click(screen.getByText("Clear"));
    expect(props.onClear).toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Program input"), { target: { value: "5\n" } });
    expect(onStdinChange).toHaveBeenCalledWith("5\n");
  });

  it("says how to start when empty", () => {
    render(<ConsolePanel {...props} runs={[]} />);
    expect(screen.getByText("Press Run to see the output here.")).toBeInTheDocument();
    expect(screen.getByText("Clear")).toBeDisabled();
  });
});
