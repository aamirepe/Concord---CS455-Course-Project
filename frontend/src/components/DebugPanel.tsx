import { useEffect, useState } from "react";
import { DebugFrame, DebugState, DebugVariable } from "../lib/debug";

type Props = {
  state: DebugState;
  /** Debugger role: may step. Everyone else can watch and expand variables. */
  canStep: boolean;
  onCommand: (command: "continue" | "next" | "stepIn" | "stepOut" | "pause" | "stop") => void;
  /** Load the children of a variable or scope. */
  onExpand: (ref: number) => void;
  /** Show a stack frame: open its file at its line, and its variables. */
  onSelectFrame: (frame: DebugFrame) => void;
};

const BUTTONS = [
  { command: "continue", label: "▶ Continue", key: "F5", whenPaused: true },
  { command: "next", label: "⤼ Step over", key: "F10", whenPaused: true },
  { command: "stepIn", label: "↓ Step into", key: "F11", whenPaused: true },
  { command: "stepOut", label: "↑ Step out", key: "Shift+F11", whenPaused: true },
  { command: "pause", label: "⏸ Pause", key: "F6", whenPaused: false },
] as const;

export default function DebugPanel({ state, canStep, onCommand, onExpand, onSelectFrame }: Props) {
  const { active, paused } = state;
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // A new pause shows fresh variables; old expansions don't apply.
  useEffect(() => { setExpanded(new Set()); }, [paused, state.frameId]);

  const toggle = (ref: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(ref)) next.delete(ref);
      else {
        next.add(ref);
        if (ref > 0 && !state.children[ref]) onExpand(ref);
      }
      return next;
    });
  };

  let status: string;
  if (paused) {
    status = `Paused at ${paused.path ?? "?"}:${paused.line ?? "?"} (${paused.reason})`;
    if (paused.by) status += ` · last step by ${paused.by}`;
  } else if (active) {
    status = state.message ?? `Running: ${active.username ?? "someone"} is debugging ${active.path}`;
  } else {
    status = state.message ?? "Not debugging. Click left of a line number to set a breakpoint, then press Debug (Python files).";
  }

  return (
    <div className="debug-panel">
      <div className="debug-toolbar" role="toolbar" aria-label="Debugger controls">
        {BUTTONS.map((b) => (
          <button
            key={b.command}
            className="secondary small"
            disabled={!active || !canStep || (b.whenPaused ? !paused : !!paused)}
            onClick={() => onCommand(b.command)}
            title={canStep ? `${b.label.slice(2)} (${b.key})` : "Stepping needs the debugger role"}
          >
            {b.label}
          </button>
        ))}
        <button className="secondary small danger" disabled={!active || !canStep} onClick={() => onCommand("stop")} title="Stop debugging (Shift+F5)">
          ■ Stop
        </button>
      </div>
      <div className={`debug-status ${paused ? "paused" : ""}`} role="status">{status}</div>
      {paused && (
        <div className="debug-columns">
          <div className="debug-stack">
            <div className="debug-heading">Call stack</div>
            {paused.stack.map((f) => (
              <button
                key={f.id}
                className={`debug-frame ${f.id === state.frameId ? "selected" : ""}`}
                onClick={() => onSelectFrame(f)}
                title={`${f.path}:${f.line}`}
              >
                <span>{f.name}</span>
                <span className="muted">{f.path}:{f.line}</span>
              </button>
            ))}
          </div>
          <div className="debug-vars">
            {state.scopes.map((scope, i) => {
              const open = i === 0 ? !expanded.has(-scope.ref) : expanded.has(scope.ref);
              return (
                <div key={scope.ref}>
                  <button
                    className="debug-heading debug-scope"
                    aria-expanded={open}
                    onClick={() => (i === 0 ? toggle(-scope.ref) : toggle(scope.ref))}
                  >
                    {open ? "▾" : "▸"} {scope.name}
                  </button>
                  {open && <VariableList vars={state.children[scope.ref]} depth={1} state={state} expanded={expanded} toggle={toggle} />}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function VariableList({ vars, depth, state, expanded, toggle }: {
  vars: DebugVariable[] | undefined;
  depth: number;
  state: DebugState;
  expanded: Set<number>;
  toggle: (ref: number) => void;
}) {
  if (!vars) return <div className="debug-var muted" style={{ paddingLeft: depth * 12 }}>Loading…</div>;
  if (!vars.length) return <div className="debug-var muted" style={{ paddingLeft: depth * 12 }}>(none)</div>;
  return (
    <>
      {vars.map((v) => (
        <div key={`${v.name}-${v.ref}`}>
          <div
            className={`debug-var ${v.ref ? "expandable" : ""}`}
            style={{ paddingLeft: depth * 12 }}
            onClick={v.ref ? () => toggle(v.ref) : undefined}
            title={v.type ?? undefined}
          >
            <span className="debug-caret">{v.ref ? (expanded.has(v.ref) ? "▾" : "▸") : ""}</span>
            <span className="debug-name">{v.name}</span>
            <span className="debug-value">{v.value}</span>
          </div>
          {v.ref > 0 && expanded.has(v.ref) && depth < 6 && (
            <VariableList vars={state.children[v.ref]} depth={depth + 1} state={state} expanded={expanded} toggle={toggle} />
          )}
        </div>
      ))}
    </>
  );
}
