import { useEffect, useRef } from "react";
import { FINISHED, isFailed, Run } from "../lib/runs";

type Props = {
  runs: Run[];
  onClear: () => void;
  onAskFix?: (run: Run) => void;
  stdin: string;
  onStdinChange: (value: string) => void;
  showInput: boolean;
  onToggleInput: () => void;
};

const time = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** The console: one block per run with its status, output and exit code. */
export default function ConsolePanel({ runs, onClear, onAskFix, stdin, onStdinChange, showInput, onToggleInput }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [runs]);

  return (
    <div className="console">
      <div className="console-bar">
        <strong>Console</strong>
        <button className={`secondary small ${showInput ? "on" : ""}`} onClick={onToggleInput} aria-pressed={showInput}
          title="Text your program reads from standard input (input(), cin, stdin) on the next Run">
          Input{stdin ? " •" : ""}
        </button>
        <button className="secondary small" onClick={onClear} disabled={runs.length === 0}>Clear</button>
      </div>
      {showInput && (
        <textarea
          aria-label="Program input"
          className="console-input"
          placeholder={"Input for the next Run, one value per line.\nLeft empty, reading input gets end-of-file."}
          value={stdin}
          onChange={(e) => onStdinChange(e.target.value)}
        />
      )}
      <div className="console-runs" ref={scroller}>
        {runs.length === 0 && <div style={{ opacity: 0.5 }}>Press Run to see the output here.</div>}
        {runs.map((r) => (
          <section key={r.id} className={`run ${r.status}`} aria-label={`Run ${r.id}`}>
            <div className="run-head">
              ▶ {r.path ?? "file"} · run #{r.id}{r.username ? ` by ${r.username}` : ""} · {time(r.startedAt)}
              {r.withInput && <span className="run-tag">with input</span>}
              <span className={`run-status ${isFailed(r) ? "bad" : r.status === "completed" ? "good" : ""}`}>{r.status}</span>
            </div>
            {r.stdout && <pre className="run-out">{r.stdout}</pre>}
            {r.stderr && <pre className="run-err">{r.stderr}</pre>}
            {r.notes.map((n, i) => <pre key={i} className="run-note">{n}</pre>)}
            {FINISHED.has(r.status) && (
              <div className="run-foot">
                {r.exitCode != null && <>exit code {r.exitCode}</>}
                {r.duration != null && <> · {r.duration.toFixed(2)} s</>}
                {isFailed(r) && onAskFix && (
                  <button className="small" onClick={() => onAskFix(r)} style={{ marginLeft: 8 }}>Ask AI to fix</button>
                )}
              </div>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
