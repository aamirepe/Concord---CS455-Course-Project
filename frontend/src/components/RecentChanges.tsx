import { FileActivity } from "../lib/api";
import { timeAgo } from "../lib/time";

type Props = {
  activity: FileActivity[];
  onOpen: (fileId: number) => void;
  /** For tests: what "now" is. */
  now?: number;
};

/** The most recently changed files, with who changed each and by how many lines. */
export default function RecentChanges({ activity, onOpen, now }: Props) {
  return (
    <section aria-label="Recent changes" className="recent-changes">
      <h3 style={{ fontSize: 14, margin: "0 0 6px" }}>Recent changes</h3>
      {activity.length === 0 ? (
        <div style={{ fontSize: 12, opacity: 0.6 }}>No edits yet.</div>
      ) : (
        <ul>
          {activity.map((f) => (
            <li key={f.file_id}>
              <button className="link" onClick={() => onOpen(f.file_id)} title={f.path}>
                {f.path.split("/").pop()}
              </button>
              <span className="when">{timeAgo(f.last_changed_at, now)}</span>
              <ul>
                {f.authors.map((a) => (
                  <li key={a.user_id}>
                    {a.username} <span className="added">+{a.lines_added}</span>{" "}
                    <span className="removed">−{a.lines_removed}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
