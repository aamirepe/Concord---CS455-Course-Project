// Errors from the API, turned into sentences people can act on, and a tiny
// toast bus so any component can show one without prop drilling.

export class ApiError extends Error {
  /** HTTP status; 0 when the server couldn't be reached at all. */
  readonly status: number;
  /** The server's explanation (FastAPI's `detail`), if it gave one. */
  readonly detail: string | null;
  /** Seconds to wait before retrying (from `Retry-After`), if given. */
  readonly retryAfter: number | null;

  constructor(status: number, statusText: string, body: string, retryAfter: number | null = null) {
    // The message keeps the old "404 Not Found: <body>" shape: code that
    // checks `message.startsWith("403")` keeps working.
    super(status ? `${status} ${statusText}: ${body}` : body);
    this.name = "ApiError";
    this.status = status;
    this.detail = parseDetail(body);
    this.retryAfter = retryAfter;
  }
}

type ValidationItem = { loc?: unknown[]; msg?: string };

/** FastAPI sends `{"detail": "text"}` or `{"detail": [{loc, msg}, ...]}`. */
export function parseDetail(body: string): string | null {
  if (!body) return null;
  try {
    const data = JSON.parse(body) as { detail?: unknown };
    const d = data?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      return (d as ValidationItem[])
        .map((item) => {
          const field = (item.loc ?? []).filter((p) => p !== "body").join(".");
          return field ? `${field}: ${item.msg ?? "invalid"}` : item.msg ?? "invalid";
        })
        .join("; ");
    }
    return null;
  } catch {
    // Plain text bodies are shown as they are, if short enough to be a message.
    return body.length <= 200 && !body.trimStart().startsWith("<") ? body : null;
  }
}

/** A sentence to show the user for any thrown value. */
export function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail;
    switch (true) {
      case e.status === 0:
        return "Can't reach the server. Check your connection and try again.";
      case e.status === 401:
        return d && /password|credential|username/i.test(d) ? d : "Your session expired. Please log in again.";
      case e.status === 403:
        return d ? `You don't have permission to do that: ${lower(d)}` : "You don't have permission to do that.";
      case e.status === 404:
        return d ? `${d}. It may have been deleted, or you no longer have access.` : "Not found. It may have been deleted, or you no longer have access.";
      case e.status === 422:
        return d ? `Please check the input (${d}).` : "Please check the input.";
      case e.status === 429:
        if (d) return d;
        return e.retryAfter
          ? `Too many requests. Try again in ${e.retryAfter} s.`
          : "Too many requests. Wait a moment and try again.";
      case e.status === 502 || e.status === 503:
        // Upstream trouble (e.g. the AI provider) or a disabled feature: the server says which.
        return d ?? "The service is unavailable right now. Please try again shortly.";
      case e.status >= 500:
        return "Something went wrong on the server. Please try again.";
      default:
        return d ?? e.message;
    }
  }
  if (e instanceof TypeError) return "Can't reach the server. Check your connection and try again.";
  if (e instanceof Error) return e.message;
  return String(e);
}

function lower(s: string): string {
  const t = s.replace(/\.$/, "");
  return t.charAt(0).toLowerCase() + t.slice(1);
}

// --- toasts ------------------------------------------------------------------

export type ToastKind = "error" | "info" | "success";
export type Toast = { id: number; kind: ToastKind; text: string };

let nextId = 1;
let toasts: Toast[] = [];
const listeners = new Set<(t: Toast[]) => void>();

function emit() {
  for (const l of listeners) l(toasts);
}

export function subscribeToasts(fn: (t: Toast[]) => void): () => void {
  listeners.add(fn);
  fn(toasts);
  return () => {
    listeners.delete(fn);
  };
}

export function dismissToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

/** Show a message for a few seconds (errors stay longer). Same text isn't stacked. */
export function toast(text: string, kind: ToastKind = "info"): number {
  const existing = toasts.find((t) => t.text === text && t.kind === kind);
  if (existing) return existing.id;
  const id = nextId++;
  toasts = [...toasts.slice(-3), { id, kind, text }];
  emit();
  setTimeout(() => dismissToast(id), kind === "error" ? 8000 : 4000);
  return id;
}

/** Shorthand for `catch (e) { showError(e) }`. */
export function showError(e: unknown): void {
  toast(friendlyError(e), "error");
}

export const UNAUTHORIZED_EVENT = "concord:unauthorized";
