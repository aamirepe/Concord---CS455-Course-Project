import { useEffect, useState } from "react";
import { dismissToast, subscribeToasts, Toast } from "../lib/errors";

/** Messages from `toast()` / `showError()`, bottom-right, newest last. */
export default function Toasts() {
  const [items, setItems] = useState<Toast[]>([]);
  useEffect(() => subscribeToasts(setItems), []);
  if (!items.length) return null;
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
          <span>{t.text}</span>
          <button className="toast-close" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
