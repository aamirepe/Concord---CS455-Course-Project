// Runs shown in the console, assembled from execution events.

/** One run of a file, assembled from the events about it. */
export type Run = {
  id: number;
  fileId: number | null;
  path: string | null;
  username: string | null;
  status: string;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  duration: number | null;
  startedAt: number;
  withInput: boolean;
  notes: string[]; // debugger events
};

/** Events the console understands: worker events (exec_event) and EXECUTION_QUEUED. */
export type RunEvent =
  | { op: "EXECUTION_QUEUED"; execution_id: number; file_id: number; path: string | null; user_id: number; username: string }
  | { type: "execution_status"; execution_id: number; status: string }
  | { type: "execution_output"; execution_id: number; stdout: string; stderr: string; exit_code?: number | null; duration?: number | null }
  | { type: "local_queued"; execution_id: number; file_id: number; path: string; username: string | null; with_input: boolean }
  | { type: string; execution_id?: number; [k: string]: unknown };

export const FINISHED = new Set(["completed", "failed", "timeout", "cancelled"]);

function blank(id: number): Run {
  return {
    id, fileId: null, path: null, username: null, status: "queued", stdout: "", stderr: "",
    exitCode: null, duration: null, startedAt: Date.now(), withInput: false, notes: [],
  };
}

/** Fold one event into the list of runs (oldest first). Unknown runs are added as they appear. */
export function applyRunEvent(runs: Run[], event: RunEvent): Run[] {
  const id = (event as { execution_id?: unknown }).execution_id;
  if (typeof id !== "number") return runs;
  const idx = runs.findIndex((r) => r.id === id);
  const run = idx === -1 ? blank(id) : runs[idx];
  let next: Run = run;
  if ("op" in event && event.op === "EXECUTION_QUEUED") {
    const q = event as Extract<RunEvent, { op: "EXECUTION_QUEUED" }>;
    next = { ...run, fileId: q.file_id, path: q.path ?? run.path, username: q.username };
  } else if ("type" in event) {
    const e = event as Record<string, unknown>;
    if (e.type === "local_queued") {
      next = {
        ...run, fileId: e.file_id as number, path: e.path as string,
        username: run.username ?? (e.username as string | null), withInput: Boolean(e.with_input),
      };
    } else if (e.type === "execution_status") {
      // A late "running" must not undo a finished status.
      next = FINISHED.has(run.status) && !FINISHED.has(e.status as string) ? run : { ...run, status: e.status as string };
    } else if (e.type === "execution_output") {
      next = {
        ...run,
        stdout: (e.stdout as string) ?? "",
        stderr: (e.stderr as string) ?? "",
        exitCode: (e.exit_code as number | null | undefined) ?? run.exitCode,
        duration: (e.duration as number | null | undefined) ?? run.duration,
      };
    } else if (e.type === "execution_output_chunk") {
      // Live output of a debug session; the final execution_output replaces it.
      const text = String(e.text ?? "");
      next = e.stream === "stderr" ? { ...run, stderr: run.stderr + text } : { ...run, stdout: run.stdout + text };
    } else if (e.type === "debug_started") {
      next = { ...run, notes: [...run.notes, "under the debugger"] };
    } else if (e.type === "debug_paused" && !run.notes.includes("paused at a breakpoint")) {
      next = { ...run, notes: [...run.notes, "paused at a breakpoint"] };
    } else {
      return runs;
    }
  }
  if (idx === -1) return [...runs, next];
  const copy = [...runs];
  copy[idx] = next;
  return copy;
}

export const isFailed = (r: Run) => r.status === "failed" || r.status === "timeout" || (r.exitCode != null && r.exitCode !== 0 && r.status !== "cancelled");
