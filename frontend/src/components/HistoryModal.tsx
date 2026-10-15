import { useEffect, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { api, FileMeta, VersionMeta } from "../lib/api";
import { friendlyError } from "../lib/errors";

type Props = {
  projectId: number;
  file: FileMeta;
  /** The file's text right now (from the live document). */
  currentText: () => string;
  canRestore: boolean;
  onClose: () => void;
};

const fmt = (iso: string) =>
  new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });

/** A file's versions, a diff of one against the current text, and restoring it. */
export default function HistoryModal({ projectId, file, currentText, canRestore, onClose }: Props) {
  const [savesOnly, setSavesOnly] = useState(false);
  const [versions, setVersions] = useState<VersionMeta[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [current, setCurrent] = useState(currentText);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Each effect ignores a response that arrives after its inputs changed, so
  // flipping "Saves only" quickly can't leave the older list on screen.
  useEffect(() => {
    let stale = false;
    setVersions(null);
    api.listVersions(projectId, file.id, savesOnly)
      .then((vs) => {
        if (stale) return;
        setVersions(vs);
        setSelected((s) => (s != null && vs.some((v) => v.version === s) ? s : vs[0]?.version ?? null));
      })
      .catch((e) => { if (!stale) setError(friendlyError(e)); });
    return () => { stale = true; };
  }, [projectId, file.id, savesOnly]);

  useEffect(() => {
    if (selected == null) return;
    let stale = false;
    setText(null);
    setCurrent(currentText());
    api.getVersion(projectId, file.id, selected)
      .then((v) => { if (!stale) setText(v.text); })
      .catch((e) => { if (!stale) setError(friendlyError(e)); });
    return () => { stale = true; };
  }, [projectId, file.id, selected, currentText]);

  async function restore() {
    if (selected == null) return;
    if (!window.confirm(`Restore version ${selected} of ${file.path}? Everyone with the file open sees the change; the current text stays in the history.`)) return;
    setBusy(true);
    try {
      await api.restoreVersion(projectId, file.id, selected);
      onClose();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-label={`History of ${file.path}`}>
      <div className="modal history-modal">
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>History: {file.path}</h3>
          <label style={{ fontSize: 12, marginLeft: "auto" }}>
            <input type="checkbox" checked={savesOnly} onChange={(e) => setSavesOnly(e.target.checked)} /> Saves only
          </label>
          <button className="secondary" onClick={onClose}>Close</button>
        </div>
        {error && <div style={{ color: "var(--bad)", fontSize: 12 }}>{error}</div>}
        <div className="history-body">
          <ul className="version-list" aria-label="Versions">
            {versions === null && <li style={{ opacity: 0.6 }}>Loading…</li>}
            {versions?.length === 0 && <li style={{ opacity: 0.6 }}>{savesOnly ? "No saves yet." : "No versions yet."}</li>}
            {versions?.map((v) => (
              <li key={v.version}>
                <button className={v.version === selected ? "active" : ""} onClick={() => setSelected(v.version)}>
                  <strong>v{v.version}</strong> · {v.saved_by ? `Saved by ${v.saved_by.username}` : "autosave"}
                  <div className="when">{fmt(v.created_at)}</div>
                </button>
              </li>
            ))}
          </ul>
          <div className="history-diff">
            <div className="diff-labels">
              <span>{selected != null ? `Version ${selected}` : ""}</span>
              <span>Now</span>
            </div>
            {text === null ? (
              <div style={{ padding: 12, opacity: 0.6 }}>{selected == null ? "" : "Loading…"}</div>
            ) : (
              <DiffEditor
                height="100%"
                original={text}
                modified={current}
                language={file.language || "python"}
                theme="vs-dark"
                options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12 }}
              />
            )}
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, alignItems: "center" }}>
          {text !== null && text === current && <span style={{ fontSize: 12, opacity: 0.7 }}>Same as now</span>}
          {canRestore && (
            <button onClick={restore} disabled={busy || selected == null || text === null || text === current}>
              {busy ? "Restoring…" : "Restore this version"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
