import { ApiError, UNAUTHORIZED_EVENT } from "./errors";
import type { ActiveSession, PausedState } from "./debug";
const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type Token = { access_token: string; token_type: string; user: User };
export type User = { id: number; username: string; email: string };
export type Project = { id: number; owner_id: number; name: string; created_at: string };
export type FolderMeta = { id: number; project_id: number; parent_folder_id: number | null; name: string; created_at: string };
export type FileMeta = { id: number; project_id: number; folder_id: number | null; path: string; language: string; created_at: string };
export type Execution = {
  id: number; project_id: number; file_id: number; status: string;
  exit_code: number | null; stdout: string; stderr: string;
  queued_at: string; started_at: string | null; finished_at: string | null; worker_id: string | null;
};

export type ProjectMember = {
  user: User;
  role: string;
};

export type JoinRequest = {
  id: number;
  project_id: number;
  user: User;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
};

export type AssistMode = "ask" | "explain" | "fix" | "generate" | "review";
export type ChatTurn = { role: "user" | "assistant"; text: string };
export type AIProposal = {
  file_id: number | null;
  original_text: string;
  proposed_text: string;
  explanation: string | null;
  syntax_ok: boolean;
};
export type AIAgentStep = { tool: string; summary: string; ok: boolean };
export type AIAssistantResponse = {
  response: string;
  provider: string;
  model_used: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  proposal: AIProposal | null;
  steps: AIAgentStep[];
  truncated: boolean;
};
export type AIAssistantStatus = {
  enabled: boolean;
  configured: boolean;
  source: "project" | "server" | null;
  provider: string | null;
  model: string | null;
};
export type AIAssistRequest = {
  prompt: string;
  mode?: AssistMode;
  file_id?: number | null;
  history?: ChatTurn[];
  context_code?: string;
  // Echoed in the AI_* events on the project socket, so the panel can match
  // live progress to its own pending request.
  request_id?: string;
  selection?: { start_line: number; end_line: number; text: string };
};

// Progress of an AI run, broadcast to everyone on the project socket.
// Carries no prompt, file contents or answer.
export type AIEvent =
  | { op: "AI_STARTED"; request_id: string; user_id: number; username: string; mode: AssistMode; file_id: number | null }
  | { op: "AI_STEP"; request_id: string; user_id: number; tool: string; summary: string; ok: boolean }
  | { op: "AI_FINISHED"; request_id: string; user_id: number; ok: boolean; has_proposal: boolean; file_id: number | null };

export type FileSaveResult = { file_id: number; version: number | null; created: boolean; saved_at: string | null };
export type AuthorActivity = { user_id: number; username: string; lines_added: number; lines_removed: number; last_changed_at: string };
export type SearchMatch = { line: number; column: number; length: number; preview: string; preview_column: number };
export type SearchResult = {
  query: string;
  files: { file_id: number; path: string; matches: SearchMatch[] }[];
  total: number;
  truncated: boolean;
};

export type FileActivity = {
  file_id: number;
  path: string;
  lines_added: number;
  lines_removed: number;
  last_changed_at: string;
  authors: AuthorActivity[];
};
// Sent on the project socket when someone's burst of edits to a file settles (or they save).
export type FileChangedEvent = {
  op: "FILE_CHANGED";
  file_id: number;
  version: number | null;
  changes: { user_id: number; username: string; lines_added: number; lines_removed: number }[];
};

export type VersionMeta = { version: number; created_at: string; saved_by: { id: number; username: string } | null };
export type Snapshot = {
  id: number;
  project_id: number;
  label: string;
  created_at: string;
  created_by_username: string | null;
  file_count: number;
};

const tokenKey = "concord.token";
const userKey = "concord.user";

export function getToken(): string | null {
  return localStorage.getItem(tokenKey);
}

// What a member field holds: digits are a user ID, anything else a username
// for the server to look up.
export function parseMemberRef(input: string): number | string {
  const value = input.trim();
  return /^\d+$/.test(value) ? Number(value) : value;
}

export function getUser(): User | null {
  const u = localStorage.getItem(userKey);
  return u ? JSON.parse(u) : null;
}

export function setToken(t: Token | null) {
  if (t) {
    localStorage.setItem(tokenKey, t.access_token);
    localStorage.setItem(userKey, JSON.stringify(t.user));
  } else {
    localStorage.removeItem(tokenKey);
    localStorage.removeItem(userKey);
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const tok = getToken();
  if (tok) headers.set("Authorization", `Bearer ${tok}`);
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "", "Can't reach the server");
  }
  if (!res.ok) {
    const text = await res.text();
    const retry = Number(res.headers.get("Retry-After"));
    if (res.status === 401 && tok && !path.startsWith("/auth/")) {
      // The token expired or was revoked: log out and let the app send the
      // user to the login page.
      setToken(null);
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw new ApiError(res.status, res.statusText, text, retry > 0 ? retry : null);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  signup: (u: { username: string; email: string; password: string }) =>
    req<Token>("/auth/signup", { method: "POST", body: JSON.stringify(u) }),
  login: (u: { username: string; password: string }) =>
    req<Token>("/auth/login", { method: "POST", body: JSON.stringify(u) }),

  listProjects: () => req<Project[]>("/projects"),
  createProject: (name: string) => req<Project>("/projects", { method: "POST", body: JSON.stringify({ name }) }),
  joinProject: (projectId: number) => req<{ status: string; role: string }>(`/projects/${projectId}/join`, { method: "POST" }),
  // userId: a numeric user ID or a username; the server accepts either.
  addProjectMember: (projectId: number, userId: number | string, role: string) =>
    req<{ message: string }>(`/projects/${projectId}/members`, { method: "POST", body: JSON.stringify({ user_id: userId, role }) }),
  listProjectMembers: (projectId: number) => req<ProjectMember[]>(`/projects/${projectId}/members`),
  removeProjectMember: (projectId: number, userId: number) =>
    req<void>(`/projects/${projectId}/members/${userId}`, { method: "DELETE" }),
  updateProjectMemberRole: (projectId: number, userId: number, role: string) =>
    req<ProjectMember>(`/projects/${projectId}/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) }),
  requestJoin: (projectId: number) =>
    req<JoinRequest>(`/projects/${projectId}/join-requests`, { method: "POST" }),
  listJoinRequests: (projectId: number) => req<JoinRequest[]>(`/projects/${projectId}/join-requests`),
  approveJoinRequest: (projectId: number, requestId: number, role: string) =>
    req<ProjectMember>(`/projects/${projectId}/join-requests/${requestId}/approve`, { method: "POST", body: JSON.stringify({ role }) }),
  rejectJoinRequest: (projectId: number, requestId: number) =>
    req<JoinRequest>(`/projects/${projectId}/join-requests/${requestId}/reject`, { method: "POST" }),

  getProject: (projectId: number) => req<Project>(`/projects/${projectId}`),

  listFiles: (projectId: number) => req<FileMeta[]>(`/projects/${projectId}/files`),
  listFolders: (projectId: number) => req<FolderMeta[]>(`/projects/${projectId}/folders`),
  createFolder: (projectId: number, name: string, parentId: number | null = null) =>
    req<FolderMeta>(`/projects/${projectId}/folders`, { method: "POST", body: JSON.stringify({ name, parent_id: parentId }) }),
  updateFolder: (projectId: number, folderId: number, payload: { name?: string; parent_id?: number }) =>
    req<FolderMeta>(`/projects/${projectId}/folders/${folderId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  createFile: (projectId: number, path: string, folderId: number | null = null) =>
    req<FileMeta>(`/projects/${projectId}/files`, { method: "POST", body: JSON.stringify({ path, folder_id: folderId }) }),
  getFileContent: (projectId: number, fileId: number) =>
    req<{ file_id: number; text: string; version: number }>(`/projects/${projectId}/files/${fileId}/content`),
  saveFile: (projectId: number, fileId: number) =>
    req<FileSaveResult>(`/projects/${projectId}/files/${fileId}/save`, { method: "POST" }),
  listVersions: (projectId: number, fileId: number, savesOnly = false) =>
    req<VersionMeta[]>(`/projects/${projectId}/files/${fileId}/versions?saves_only=${savesOnly}`),
  getVersion: (projectId: number, fileId: number, version: number) =>
    req<{ version: number; created_at: string; text: string }>(`/projects/${projectId}/files/${fileId}/versions/${version}`),
  restoreVersion: (projectId: number, fileId: number, version: number) =>
    req<{ restored_from: number; version: number | null; changed: boolean }>(
      `/projects/${projectId}/files/${fileId}/versions/${version}/restore`, { method: "POST" }
    ),
  listSnapshots: (projectId: number) => req<Snapshot[]>(`/projects/${projectId}/snapshots`),
  createSnapshot: (projectId: number, label: string) =>
    req<Snapshot>(`/projects/${projectId}/snapshots`, { method: "POST", body: JSON.stringify({ label }) }),
  restoreSnapshot: (projectId: number, snapshotId: number) =>
    req<{ snapshot_id: number; restored_files: number; safety_snapshot_id: number }>(
      `/projects/${projectId}/snapshots/${snapshotId}/restore`, { method: "POST" }
    ),
  search: (projectId: number, q: string, opts: { case?: boolean; word?: boolean } = {}) =>
    req<SearchResult>(`/projects/${projectId}/search?${new URLSearchParams({ q, case: String(!!opts.case), word: String(!!opts.word) })}`),
  getActivity: (projectId: number, limit = 5) => req<FileActivity[]>(`/projects/${projectId}/activity?limit=${limit}`),
  updateFile: (projectId: number, fileId: number, payload: { path?: string; folder_id?: number }) =>
    req<FileMeta>(`/projects/${projectId}/files/${fileId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  run: (projectId: number, fileId: number, stdin?: string) =>
    req<Execution>(`/projects/${projectId}/executions`, { method: "POST", body: JSON.stringify({ file_id: fileId, ...(stdin ? { stdin } : {}) }) }),
  /** Start a debug session of a Python file (debugger role). */
  debug: (projectId: number, fileId: number) =>
    req<Execution>(`/projects/${projectId}/executions`, { method: "POST", body: JSON.stringify({ file_id: fileId, debug: true }) }),
  debugState: (projectId: number) =>
    req<{ active: ActiveSession | null; paused: PausedState | null }>(`/projects/${projectId}/debug`),
  debugCommand: (projectId: number, executionId: number, command: string, extra: { ref?: number; frame_id?: number } = {}) =>
    req<{ delivered: boolean }>(`/projects/${projectId}/debug/${executionId}/command`, { method: "POST", body: JSON.stringify({ command, ...extra }) }),
  setBreakpoints: (projectId: number, fileId: number, lines: number[]) =>
    req<{ id: number; line: number }[]>(`/projects/${projectId}/files/${fileId}/breakpoints`, { method: "PUT", body: JSON.stringify({ lines }) }),
  listExecutions: (projectId: number) => req<Execution[]>(`/projects/${projectId}/executions`),
  getExecution: (projectId: number, executionId: number) =>
    req<Execution>(`/projects/${projectId}/executions/${executionId}`),
  cancelExecution: (projectId: number, executionId: number) =>
    req<Execution>(`/projects/${projectId}/executions/${executionId}/cancel`, { method: "POST", body: JSON.stringify({}) }),

  listBreakpoints: (projectId: number, fileId: number) =>
    req<{ id: number; file_id: number; line: number; enabled: boolean; created_by: number | null; created_at: string }[]>(
      `/projects/${projectId}/breakpoints?file_id=${fileId}`
    ),
  requestAssistant: (projectId: number, payload: AIAssistRequest) =>
    req<AIAssistantResponse>(`/projects/${projectId}/assist`, { method: "POST", body: JSON.stringify(payload) }),
  getAssistStatus: (projectId: number) => req<AIAssistantStatus>(`/projects/${projectId}/assist/status`),
  saveAssistConfig: (projectId: number, payload: { provider: "mistral" | "huggingface"; api_key: string; model_name?: string | null }) =>
    req<{ id: number; project_id: number; provider: string; model_name: string | null; is_active: boolean }>(
      `/projects/${projectId}/assist/config`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
  metricsSummary: () => req<{ status_counts: Record<string, number>; avg_runtime_seconds: number }>("/metrics/executions/summary"),
  metricsRecent: () => req<{ events: { id: number; event_type: string; created_at: string; payload: unknown }[] }>(`/metrics/events/recent`),
};
