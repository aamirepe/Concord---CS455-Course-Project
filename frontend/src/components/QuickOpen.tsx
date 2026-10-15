import { useEffect, useMemo, useRef, useState } from "react";
import { FileMeta } from "../lib/api";
import { rankFiles } from "../lib/fuzzy";

type Props = {
  files: FileMeta[];
  /** Recently opened file ids, most recent first. */
  recent: number[];
  onPick: (file: FileMeta) => void;
  onClose: () => void;
};

const LIMIT = 50;

/** Ctrl/Cmd+P: type part of a file name, Enter to open it. */
export default function QuickOpen({ files, recent, onPick, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const results = useMemo(() => rankFiles(files, query, recent).slice(0, LIMIT), [files, query, recent]);

  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => { setIndex(0); }, [query]);

  const pick = (i: number) => {
    const r = results[i];
    if (r) onPick(r.file);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(index); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-label="Open a file" onMouseDown={onClose}>
      <div className="modal quick-open" onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKey}
          placeholder="Type a file name…"
          aria-label="File name"
          role="combobox"
          aria-expanded="true"
          aria-controls="quick-open-list"
        />
        <ul className="quick-open-list" id="quick-open-list" role="listbox">
          {results.length === 0 && <li className="muted">No matching files</li>}
          {results.map((r, i) => (
            <li
              key={r.file.id}
              role="option"
              aria-selected={i === index}
              className={i === index ? "selected" : ""}
              onMouseEnter={() => setIndex(i)}
              onClick={() => pick(i)}
            >
              <Highlighted text={r.file.path} positions={r.positions} />
              {!query && recent.includes(r.file.id) && <span className="muted"> · recent</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Highlighted({ text, positions }: { text: string; positions: number[] }) {
  if (!positions.length) return <span>{text}</span>;
  const set = new Set(positions);
  return (
    <span>
      {[...text].map((ch, i) => (set.has(i) ? <mark key={i}>{ch}</mark> : <span key={i}>{ch}</span>))}
    </span>
  );
}
