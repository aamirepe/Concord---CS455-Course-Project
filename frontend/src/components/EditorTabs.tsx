import { FileMeta } from "../lib/api";
import Avatars from "./Avatars";

type Props = {
  tabs: FileMeta[];
  activeId: number | null;
  onSelect: (file: FileMeta) => void;
  onClose: (file: FileMeta) => void;
  /** Files someone else changed since you last looked: file id -> who. */
  modified: Record<number, string>;
  /** Who is in which file: user id -> file id. */
  presence: Record<number, number>;
  names: Record<number, string>;
  me?: number;
};

export default function EditorTabs({ tabs, activeId, onSelect, onClose, modified, presence, names, me }: Props) {
  if (!tabs.length) return null;
  return (
    <div className="editor-tabs" role="tablist" aria-label="Open files">
      {tabs.map((f) => {
        const here = Object.entries(presence)
          .filter(([uid, fid]) => fid === f.id && Number(uid) !== me)
          .map(([uid]) => Number(uid));
        const name = f.path.split("/").pop() || f.path;
        return (
          <div
            key={f.id}
            role="tab"
            aria-selected={f.id === activeId}
            className={`editor-tab ${f.id === activeId ? "active" : ""}`}
            title={f.path}
            onClick={() => onSelect(f)}
            onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); onClose(f); } }}
          >
            <span className="editor-tab-name">{name}</span>
            {modified[f.id] && <span className="modified-dot" title={`Changed by ${modified[f.id]}`}>●</span>}
            <Avatars userIds={here} names={names} max={2} />
            <button
              className="editor-tab-close"
              aria-label={`Close ${name}`}
              title="Close (Ctrl+Alt+W)"
              onClick={(e) => { e.stopPropagation(); onClose(f); }}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}
