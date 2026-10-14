import { userColor } from "../lib/colors";

type Props = {
  userIds: number[];
  /** user id -> username */
  names: Record<number, string>;
  /** Show at most this many, then "+N". */
  max?: number;
};

/** Small coloured initials for the people in a file. */
export default function Avatars({ userIds, names, max = 3 }: Props) {
  if (!userIds.length) return null;
  const shown = userIds.slice(0, max);
  const label = userIds.map((id) => names[id] ?? `user ${id}`).join(", ");
  return (
    <span className="presence-avatars" title={`Here: ${label}`} aria-label={`Here: ${label}`}>
      {shown.map((id) => (
        <span key={id} className="avatar-mini" style={{ background: userColor(id) }}>
          {(names[id] ?? "?").charAt(0).toUpperCase()}
        </span>
      ))}
      {userIds.length > max && <span className="avatar-more">+{userIds.length - max}</span>}
    </span>
  );
}
