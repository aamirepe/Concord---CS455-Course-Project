import { useCallback, useEffect, useState } from "react";
import { api, Snapshot } from "../lib/api";
import { timeAgo } from "../lib/time";
import { friendlyError } from "../lib/errors";

type Props = {
  projectId: number;
  canEdit: boolean;
};

/** Project snapshots: a named copy of every file, and restoring one for everyone. */
export default function SnapshotsPanel({ projectId, canEdit }: Props) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [label, setLabel] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.listSnapshots(projectId).then(setSnapshots).catch(() => { /* keep the list */ });
  }, [projectId]);
  useEffect(load, [load]);

  async function create() {
    if (!label?.trim()) return;
    setBusy(true);
    try {
      const s = await api.createSnapshot(projectId, label.trim());
      setNote(`Snapshot "${s.label}" saved (${s.file_count} files).`);
      setLabel(null);
      load();
    } catch (e) {
      setNote(`Snapshot failed. ${friendlyError(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function restore(s: Snapshot) {
    if (!window.confirm(`Restore "${s.label}"? This replaces ${s.file_count} files for everyone. A snapshot of the current state is taken first, so you can undo it.`)) return;
    setBusy(true);
    try {
      const r = await api.restoreSnapshot(projectId, s.id);
      setNote(r.restored_files ? `Restored ${r.restored_files} file${r.restored_files === 1 ? "" : "s"} from "${s.label}".` : "Nothing to restore: the files already match.");
      load();
    } catch (e) {
      setNote(`Restore failed. ${friendlyError(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Snapshots" className="recent-changes snapshots">
      <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
        <h3 style={{ fontSize: 14, margin: 0 }}>Snapshots</h3>
        {canEdit && label === null && (
          <button className="secondary small" style={{ marginLeft: "auto" }} onClick={() => setLabel("")}>+ Snapshot</button>
        )}
      </div>
      {label !== null && (
        <form onSubmit={(e) => { e.preventDefault(); create(); }} style={{ display: "flex", gap: 4, marginBottom: 6 }}>
          <input
            autoFocus
            aria-label="Snapshot name"
            placeholder="e.g. before refactor"
            value={label}
            maxLength={255}
            onChange={(e) => setLabel(e.target.value)}
            style={{ flex: 1, minWidth: 0, fontSize: 12, padding: 4 }}
          />
          <button type="submit" className="small" disabled={busy || !label.trim()}>Save</button>
          <button type="button" className="secondary small" onClick={() => setLabel(null)}>✕</button>
        </form>
      )}
      {note && <div role="status" style={{ fontSize: 11, opacity: 0.8, marginBottom: 6 }}>{note}</div>}
      {snapshots.length === 0 ? (
        <div style={{ fontSize: 12, opacity: 0.6 }}>None yet. A snapshot keeps every file as it is now.</div>
      ) : (
        <ul>
          {snapshots.map((s) => (
            <li key={s.id} className="snapshot-row">
              <span className="snapshot-label" title={s.label}>{s.label}</span>
              <span className="when">{s.created_by_username ?? "?"} · {timeAgo(s.created_at)} · {s.file_count} files</span>
              {canEdit && (
                <button className="link" onClick={() => restore(s)} disabled={busy} aria-label={`Restore ${s.label}`}>Restore</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
