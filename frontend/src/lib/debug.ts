// The project's debug session as the Debug panel and editor show it,
// assembled from debug_* events (see backend app/debug/session.py).

export type DebugVariable = { name: string; value: string; type?: string | null; ref: number };
export type DebugFrame = { id: number; name: string; line: number; path: string; file_id: number | null };
export type DebugScope = { name: string; ref: number };

export type PausedState = {
  execution_id: number;
  reason: string;
  description?: string | null;
  by?: string | null;
  path: string | null;
  file_id: number | null;
  line: number | null;
  stack: DebugFrame[];
  scopes: DebugScope[];
  variables: DebugVariable[];
};

export type ActiveSession = { execution_id: number; file_id: number; path: string; user_id?: number | null; username?: string | null };

export type DebugState = {
  active: ActiveSession | null;
  paused: PausedState | null;
  /** The frame whose variables are shown (the top one unless someone picked another). */
  frameId: number | null;
  scopes: DebugScope[];
  /** Children of expanded variables and scopes, by reference. */
  children: Record<number, DebugVariable[]>;
  /** The last thing that happened, for the status line. */
  message: string | null;
  lastStopped: { reason: string; exit_code?: number | null; by?: string | null } | null;
};

export const emptyDebug: DebugState = {
  active: null, paused: null, frameId: null, scopes: [], children: {}, message: null, lastStopped: null,
};

type Event = { type?: string; execution_id?: number; [k: string]: unknown };

export function isDebugEvent(e: unknown): e is Event {
  const t = (e as Event)?.type;
  return typeof t === "string" && (t.startsWith("debug_") || t === "breakpoints_updated");
}

/** Fold one event into the state. Events of another session than the active one are ignored. */
export function applyDebugEvent(state: DebugState, e: Event): DebugState {
  const id = e.execution_id;
  switch (e.type) {
    case "debug_started":
      return {
        ...emptyDebug,
        active: {
          execution_id: id as number, file_id: e.file_id as number, path: e.path as string,
          user_id: e.user_id as number | null, username: e.username as string | null,
        },
        message: `${e.username ?? "Someone"} started debugging ${e.path}`,
      };
    case "debug_paused": {
      if (state.active && state.active.execution_id !== id) return state;
      const p = e as unknown as PausedState;
      const active = state.active?.execution_id === id ? state.active
        : { execution_id: id as number, file_id: p.file_id ?? 0, path: p.path ?? "" };
      const firstScope = p.scopes?.[0]?.ref;
      return {
        ...state, active, paused: p, frameId: p.stack?.[0]?.id ?? null, scopes: p.scopes ?? [],
        children: firstScope ? { [firstScope]: p.variables ?? [] } : {},
        message: null,
      };
    }
    case "debug_running":
      if (state.active?.execution_id !== id) return state;
      return { ...state, paused: null, children: {}, message: e.by ? `Running (${e.by})` : "Running" };
    case "debug_frame":
      if (state.paused?.execution_id !== id) return state;
      return {
        ...state, frameId: e.frame_id as number, scopes: (e.scopes as DebugScope[]) ?? [],
        children: (e.scopes as DebugScope[])?.[0]?.ref ? { [(e.scopes as DebugScope[])[0].ref]: (e.variables as DebugVariable[]) ?? [] } : {},
      };
    case "debug_variables":
      if (state.paused?.execution_id !== id) return state;
      return { ...state, children: { ...state.children, [e.ref as number]: (e.variables as DebugVariable[]) ?? [] } };
    case "debug_message":
      return state.active?.execution_id === id ? { ...state, message: e.text as string } : state;
    case "debug_stopped":
      if (state.active && state.active.execution_id !== id) return state;
      return {
        ...emptyDebug,
        lastStopped: { reason: e.reason as string, exit_code: e.exit_code as number | null, by: e.by as string | null },
        message: stoppedMessage(e),
      };
    default:
      return state;
  }
}

function stoppedMessage(e: Event): string {
  switch (e.reason) {
    case "exited": return `The program finished (exit code ${e.exit_code ?? "?"}).`;
    case "stopped": return `${e.by ?? "Someone"} stopped the debugger.`;
    case "idle": return "Stopped: nobody stepped for a few minutes.";
    case "timeout": return "Stopped: the debug session hit its time limit.";
    case "cancelled": return "The debug run was cancelled.";
    default: return `The debugger stopped${e.error ? `: ${e.error}` : "."}`;
  }
}

/** Breakpoint lines by file from a breakpoints_updated event (JSON keys are strings). */
export function breakpointsFromEvent(e: Event): Record<number, number[]> {
  const out: Record<number, number[]> = {};
  for (const [k, v] of Object.entries((e.lines_by_file as Record<string, number[]>) ?? {})) out[Number(k)] = v;
  return out;
}
