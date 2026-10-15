import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { api, SearchResult } from "../lib/api";
import { friendlyError } from "../lib/errors";

export type SearchPanelHandle = { focus: () => void };

type Props = {
  projectId: number;
  /** Open a result: the file, and where the match is. */
  onOpen: (fileId: number, line: number, column: number, length: number) => void;
};

const DEBOUNCE_MS = 300;

/** Find text in every file of the project, including edits not saved yet. */
const SearchPanel = forwardRef<SearchPanelHandle, Props>(function SearchPanel({ projectId, onOpen }, ref) {
  const [query, setQuery] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [word, setWord] = useState(false);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({ focus: () => { inputRef.current?.focus(); inputRef.current?.select(); } }), []);

  useEffect(() => {
    if (!query.trim()) {
      setResult(null);
      setError(null);
      return;
    }
    let stale = false;
    const t = setTimeout(() => {
      setLoading(true);
      api.search(projectId, query, { case: matchCase, word })
        .then((r) => { if (!stale) { setResult(r); setError(null); } })
        .catch((e) => { if (!stale) setError(friendlyError(e)); })
        .finally(() => { if (!stale) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { stale = true; clearTimeout(t); };
  }, [projectId, query, matchCase, word]);

  const toggle = (fileId: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) next.delete(fileId);
      else next.add(fileId);
      return next;
    });

  return (
    <div className="search-panel">
      <div className="search-row">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search all files"
          aria-label="Search all files"
          maxLength={200}
        />
        <button className={`toggle ${matchCase ? "on" : ""}`} aria-pressed={matchCase} title="Match case" onClick={() => setMatchCase((v) => !v)}>Aa</button>
        <button className={`toggle ${word ? "on" : ""}`} aria-pressed={word} title="Whole word" onClick={() => setWord((v) => !v)}>ab</button>
      </div>
      {error && <div className="search-note error">{error}</div>}
      {loading && !result && <div className="search-note">Searching…</div>}
      {result && (
        <div className="search-note">
          {result.total === 0 ? "No results" : `${result.total}${result.truncated ? "+" : ""} result${result.total === 1 ? "" : "s"} in ${result.files.length} file${result.files.length === 1 ? "" : "s"}`}
          {result.truncated && " (showing the first 500)"}
        </div>
      )}
      <div className="search-results">
        {result?.files.map((f) => (
          <div key={f.file_id} className="search-file">
            <div className="search-file-header" onClick={() => toggle(f.file_id)} title={f.path}>
              <span>{collapsed.has(f.file_id) ? "▸" : "▾"} {f.path}</span>
              <span className="search-count">{f.matches.length}</span>
            </div>
            {!collapsed.has(f.file_id) && f.matches.map((m, i) => (
              <button
                key={i}
                className="search-hit"
                onClick={() => onOpen(f.file_id, m.line, m.column, m.length)}
                title={`Line ${m.line}`}
              >
                <span className="search-line">{m.line}</span>
                <span className="search-preview">
                  {m.preview.slice(0, m.preview_column).trimStart()}
                  <mark>{m.preview.slice(m.preview_column, m.preview_column + m.length)}</mark>
                  {m.preview.slice(m.preview_column + m.length)}
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
});

export default SearchPanel;
