# Concord — API Specification

All REST endpoints accept and return JSON. Auth is via `Authorization: Bearer <token>`.

Errors are `{"detail": "<sentence>"}` (422 validation errors: a list of
`{loc, msg}`). The web app turns them into plain messages (`frontend/src/lib/errors.ts`);
a 401 on any endpoint except `/auth/*` logs the user out and returns them to
the login page ("Your session expired").

## Auth

### `POST /auth/signup`
Body: `{username, email, password}` → 201 `{access_token, user}`

### `POST /auth/login`
Body: `{username, password}` → 200 `{access_token, user}`

## Projects

### `POST /projects`
Body: `{name}` → 201 `Project`

### `GET /projects` → `[Project]`

### `GET /projects/{project_id}` (members) → `Project`

### `POST /projects/{project_id}/join` → `{status, role}`
Enter a project you're already a member of. Non-members get 403 and can request access instead.

### `POST /projects/{project_id}/join-requests` → 201 `JoinRequest`
Ask the owner for access. 409 if already a member or a request is already pending; a rejected user may ask again.

### `GET /projects/{project_id}/join-requests` (owner only) → `[JoinRequest]`
Pending requests, oldest first.

### `POST /projects/{project_id}/join-requests/{request_id}/approve` (owner only) → `ProjectMember`
Body: `{"role": "viewer" | "editor" | "debugger"}` (default `viewer`; `owner` is not grantable here). 409 if already decided.

### `POST /projects/{project_id}/join-requests/{request_id}/reject` (owner only) → `JoinRequest`
409 if already decided.

`JoinRequest` = `{id, project_id, user: User, status: "pending" | "approved" | "rejected", created_at, decided_at}`

## Members and invitations

Roles rank `viewer` < `editor` < `debugger` < `owner`; "editor+" below means
editor or higher. Viewers can read and follow along, editors can edit, create,
rename and run, and debuggers can also debug.

### `GET /projects/{project_id}/members` (members) → `[ProjectMember]`
`ProjectMember` = `{user: User, role}`.

### `POST /projects/{project_id}/members` (owner only) → 201 `{message}`
Body: `{user_id: <id or username>, role?: "viewer" | "editor" | "debugger" | "owner"}`
(default `viewer`, any case). Adding someone who is already a member changes
their role. 404 if the user doesn't exist, 400 for the project owner (their
role can't change), 422 for a bad body.

### `PATCH /projects/{project_id}/members/{user_id}` (owner only) → `ProjectMember`
Body: `{role}`. 404 if not a member, 400 for the project owner.

### `DELETE /projects/{project_id}/members/{user_id}` (owner only) → 204
400 for the project owner, 404 if not a member.

### `POST /invitations?project_id=N&role=R` (editor+) → 201 `{token, invite_url}`
`role` is one of the four roles and may not rank above the inviter's own. The
token is valid for 7 days.

### `POST /invitations/accept/{token}` → `{message}`
Adds the caller with the invited role and uses up the token. 404 for an
unknown token, 410 once expired, 409 if the caller is already a member (the
token stays valid for someone else).

## Search

### `GET /projects/{project_id}/search?q=&case=false&word=false` (members) → `SearchResult`
Plain-text search (not a regular expression) over every file's current text,
live edits included. `case`: match case; `word`: whole words only.
`{query, total, truncated, files: [{file_id, path, matches: [{line, column, length, preview, preview_column}]}]}`
with 1-based `line`/`column`; at most 500 matches (`truncated` says there were
more); `preview` is the line, cut to 200 characters around the match.

## Files

### `POST /projects/{project_id}/files` → 201 `File`
Body: `{path}`. `language` follows the extension: `python` (.py), `nodejs`
(.js), `cpp` (.c/.cc/.cpp/.cxx), anything else `plaintext` — editable but not
runnable (running it returns 400).

### `GET /projects/{project_id}/files` → `[File]`

### `PATCH /projects/{project_id}/files/{file_id}` (editor+) → `File`
Body: `{path?, folder_id?}`. Renames or moves a file; `language` follows the new
extension.

### `GET /projects/{project_id}/folders` (members) → `[Folder]`
`Folder` = `{id, project_id, parent_folder_id, name, created_at}`.

### `POST /projects/{project_id}/folders` (editor+) → 201 `Folder`
Body: `{name, parent_id?}`. 400 if `parent_id` isn't a folder of this project.

### `PATCH /projects/{project_id}/folders/{folder_id}` (editor+) → `Folder`
Body: `{name?, parent_id?}`. 400 if the new parent isn't in this project, or is
the folder itself or one of its subfolders.

### `DELETE /projects/{project_id}/folders/{folder_id}` (editor+) → 204
Deletes the folder and its subfolders. Files inside are kept and move to the project root.

Folder changes are broadcast on the project socket as
`WORKSPACE_FOLDER_CREATED`, `WORKSPACE_FOLDER_UPDATED` and `WORKSPACE_FOLDER_DELETED`.

### `GET /projects/{project_id}/files/{file_id}/content` → `{file_id, text, version}`

Old autosaves are thinned every 10 minutes: every Save, the latest version and
the last hour are kept; older autosaves keep one per 10 minutes for a day,
then one per day. Version numbers therefore have gaps.

### `POST /projects/{project_id}/files/{file_id}/save` (editor+) → `{file_id, version, created, saved_at}`
Writes the live document as a new version now (the checkpointer would within
seconds anyway) and records the saver's change in recent changes right away.
`created: false` means nothing changed since `version`; `version: null` means
the file is empty and was never saved.

### `GET /projects/{project_id}/activity?limit=5` → `[FileActivity]`
The `limit` (1–20) most recently changed files, newest first:
`{file_id, path, lines_added, lines_removed, last_changed_at, authors: [{user_id, username, lines_added, lines_removed, last_changed_at}]}`.

Lines are counted like `git diff --stat` (a line edited in place is one removed
and one added) over a burst of edits: a burst ends once the file has been idle
for 5 s, after a minute, on Save, or when someone else starts editing after a
pause of a second or more. Only when two people type in the same file at the
same moment is a burst shared, in proportion to the characters each typed.

## Executions

### `POST /projects/{project_id}/executions` (editor+) → 202 `Execution`
Body: `{file_id, stdin?}`. `stdin` (up to 64 KiB) is what the program reads from
standard input; without it, reading input gets end-of-file (Python's `input()`
raises `EOFError`) instead of waiting forever. Also sends `EXECUTION_QUEUED` on
the project socket.

### `GET /projects/{project_id}/executions` → `[Execution]`

### `GET /projects/{project_id}/executions/{id}` → `Execution`

### `POST /projects/{project_id}/executions/{id}/cancel` (editor+) → `Execution`

## Versions

Every file has versions: autosaves (every few seconds while it's being
edited) and saves (Save button, or a restore).

### `GET /projects/{project_id}/files/{file_id}/versions?limit=100&saves_only=false` → `[{version, created_at, saved_by}]`
Newest first. `saved_by` is `{id, username}` for a save, `null` for an autosave.

### `GET /projects/{project_id}/files/{file_id}/versions/{n}` → `{version, created_at, text}`

### `POST /projects/{project_id}/files/{file_id}/versions/{n}/restore` (editor+) → `{restored_from, version, changed}`
Makes version `n`'s text the file's content as an edit by the caller: people
with the file open see it change like any edit, it shows in recent changes,
and it's saved as a new version (so the text before the restore stays in the
history). `changed: false` means the file already had that text.

## Snapshots

A snapshot is a named copy of every file in the project.

### `POST /projects/{project_id}/snapshots` (editor+) → 201 `Snapshot`
Body: `{label}`. Takes each file's live content, or its latest version if
nobody has it open; files that were never edited are skipped.
`Snapshot` is `{id, project_id, label, created_at, created_by_username, file_count}`.

### `GET /projects/{project_id}/snapshots` → `[Snapshot]`
Newest first.

### `POST /projects/{project_id}/snapshots/{id}/restore` (editor+) → `{snapshot_id, restored_files, safety_snapshot_id}`
First takes a snapshot of the current state ("Before restoring '<label>'",
returned as `safety_snapshot_id`, so a restore can be undone), then restores
each file like a version restore. `restored_files` counts the files that changed.

## Breakpoints

### `GET /projects/{project_id}/breakpoints?file_id=N` (members) → `[Breakpoint]`
404 if the file isn't in this project.

### `PUT /projects/{project_id}/files/{file_id}/breakpoints` (debugger or owner) → `[Breakpoint]`
Body: `{lines: [int]}` replaces the file's breakpoints; `[]` clears them. Sends
`breakpoints_updated` to everyone, and the new lines to a running debug session.

### `PUT /projects/{project_id}/breakpoints[?file_id=N…]` (debugger or owner) → `[Breakpoint]`
Older form. Body: `[{file_id, line, enabled}]`; replaces the breakpoints of every
file in the body, and clears files named in `file_id` that have no entries.

## Debugger (Python)

A debug session is a run under debugpy. The worker runs debugpy's adapter
inside the usual sandbox (no network) and talks to it over the container's
stdin/stdout. There is one session per project at a time. It ends when the program exits, on
**stop**, after 10 minutes, or after 3 minutes paused with nobody stepping
(`SANDBOX_DEBUG_TIMEOUT_SECONDS`, `SANDBOX_DEBUG_IDLE_SECONDS`). Set
`ENABLE_DEBUGGER=false` to turn it off.

### `POST /projects/{project_id}/executions` with `{"file_id": N, "debug": true}` (debugger or owner) → 202 `Execution`
Also `POST /projects/{project_id}/debug/start?file_id=N`. It takes every enabled breakpoint in
the project; `stdin` is ignored. Errors: 400 not a Python file, 403 not the
debugger role, 409 someone else is debugging in this project, 503 the debugger
is off or Redis is down.

### `GET /projects/{project_id}/debug` (members) → `{active, paused}`
The running session (`{execution_id, file_id, path, user_id, username}`) and its
last `debug_paused` event, or `null`s: for someone who opens the project mid-session.

### `POST /projects/{project_id}/debug/{execution_id}/command` → 202 `{delivered}`
Body: `{command, ref?, frame_id?}`. `continue`, `next`, `stepIn`, `stepOut`,
`pause` and `stop` need the debugger role; `variables` (`ref`: expand a variable
or scope) and `frame` (`frame_id`: show that stack frame's variables) are open
to every member. 409 if that session has ended.

### Debug events (`exec_event` on the sockets, to every member)
| `event.type` | Fields |
|---|---|
| `debug_started` | `execution_id, file_id, path, user_id, username` |
| `debug_paused` | `reason` (breakpoint, step, pause), `path, file_id, line`, `stack: [{id, name, path, file_id, line}]`, `scopes: [{name, ref}]`, `variables` (first scope: `[{name, value, type, ref}]`), `by` (who stepped last) |
| `debug_running` | `by` |
| `debug_variables` | `ref, variables` |
| `debug_frame` | `frame_id, scopes, variables` |
| `debug_message` | `text` (e.g. a step the adapter refused) |
| `execution_output_chunk` | `stream` (stdout/stderr), `text`: the program's output as it happens |
| `debug_stopped` | `reason` (exited, stopped, idle, timeout, cancelled, error), `exit_code, by, error` |
| `breakpoints_updated` | `lines_by_file: {file_id: [lines]}` |

## Metrics

### `GET /metrics/executions/summary` → `{status_counts, avg_runtime_seconds}`

### `GET /metrics/executions/last_hour` → `{last_hour: int}`

### `GET /metrics/events/recent?limit=N` → `{events: [...]}`

## AI assistant

All routes need project membership. A project uses its own key if the owner
configured one, otherwise the server-wide `MISTRAL_API_KEY`.

### `GET /projects/{project_id}/assist/status` → `{enabled, configured, source, provider, model}`
`source` is `"project"`, `"server"` or `null` (not configured). Never returns a key.

### `POST /projects/{project_id}/assist` → `AIAssistantResponse`
Runs the coding agent. Body:
```json
{ "prompt": "My last run failed", "mode": "fix", "file_id": 3,
  "history": [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}],
  "context_code": null, "request_id": "3f2a9c",
  "selection": {"start_line": 4, "end_line": 9, "text": "..."} }
```
`mode` is one of `ask` (default), `explain`, `fix`, `generate`, `review`.
`file_id` is the file open in the editor. Its live content (up to 12,000
characters) goes into the agent's first message, with the last run's output
in `fix` mode and the `selection` (optional; what the user had selected, up to
8,000 characters) if given, so the agent doesn't spend tool calls fetching them.
`context_code` is only used when `file_id` is omitted. `request_id` (optional,
`[A-Za-z0-9_-]{1,64}`) is echoed in the `AI_*` progress events on the project
socket (see below); the server makes one up if it is missing. Response:
```json
{ "response": "markdown answer", "provider": "mistral", "model_used": "codestral-latest",
  "prompt_tokens": 812, "completion_tokens": 140, "total_tokens": 952, "latency_ms": 2300,
  "steps": [{"tool": "read_file", "summary": "read_file(file 3)", "ok": true}],
  "proposal": {"file_id": 3, "original_text": "len(x)", "proposed_text": "len(xs)",
               "explanation": "...", "syntax_ok": true},
  "truncated": false }
```
`proposal` is `null` unless the agent proposed an edit. Nothing is written
server-side; the client applies it through the CRDT (editor+ only).
Errors: 400 not configured / disabled for project, 404 file not in project,
429 rate limited (per project, members only; `Retry-After` and `X-RateLimit-*` headers, and a `detail` that says when to retry), 502 provider error, 503 disabled globally.

### `POST /projects/{project_id}/assist/config` (owner) → `AIAssistantConfig`
`{provider: "mistral"|"huggingface", api_key, model_name?}`. The key is stored encrypted.

### `GET|PATCH /projects/{project_id}/assist/config` → `AIAssistantConfig`
PATCH (owner) accepts `provider`, `model_name`, `api_key`, `is_active`.

### `GET /projects/{project_id}/assist/history?limit=50` → `[AIAssistantRequest]`
Audit log: prompt, response, mode, file_id, tokens, latency, success/error.

## WebSocket

### `ws://host/ws/projects/{project_id}/files/{file_id}?token=<jwt>`

Frames:

| Type | Direction | Purpose |
|---|---|---|
| binary | server → client | The first frame is the full document; later ones are other people's updates |
| text | client → server | `{"op":"update","data":"<base64 Yjs update>"}` — a local edit (editor+) |
| text | server → client | `{"op":"ack"}` — one per applied `update` |
| text | client → server | `{"op":"hello"}` — optional; the full state is sent on connect anyway |
| text | client → server | `{"op":"sync_step1","sv":"<base64>"}` — state vector; the server replies with the diff |
| text | client → server | `{"op":"awareness_update","data":"<base64>"}` — cursor / presence, relayed to the others |
| text | client → server | `{"op":"project_presence"}` — "I opened this file", relayed to the project socket |
| text | both | `{"op":"ping"}` / `{"op":"pong"}` — keep-alive; the client answers the server's pings |
| text | server → client | `{"op":"exec_event","event":{...}}` — execution status / output / debug state |
| text | server → client | `{"op":"error","message":"..."}` |

**Reconnecting.** The browser keeps its document when the connection drops
and reconnects with backoff (0.5 s doubling to 5 s; not after close code 1008,
which means no access). On reconnect, it diffs the server's first frame
against its own document and sends only what the server is missing, so edits
made offline or lost in flight arrive exactly once. Close code 1008 = not a
member / bad token.

### `ws://host/ws/projects/{project_id}?token=<jwt>` (project socket)

Project-wide events for every member with the project open: `WORKSPACE_*` file
and folder changes, and progress of AI assistant runs. AI events carry no
prompt, file contents or answer; the requester gets those in the REST response.

| Message | When |
|---|---|
| `{"op":"AI_STARTED","request_id","user_id","username","mode","file_id"}` | a member's request passed the checks and the agent starts |
| `{"op":"AI_STEP","request_id","user_id","tool","summary","ok"}` | after each tool call (`read_file`, `propose_edit`, ...) |
| `{"op":"AI_FINISHED","request_id","user_id","ok","has_proposal","file_id"}` | always, also on errors (`ok: false`); `file_id` is the proposal's file if there is one |
| `{"op":"FILE_CHANGED","file_id","version","changes":[{"user_id","username","lines_added","lines_removed"}]}` | a burst of edits to a file was recorded (see `/activity`) |
| `{"op":"project_presence","user_id","file_id"}` | a member opened a file |
| `{"op":"project_presence","user_id","file_id":null,"left_file_id"}` | a member closed their last connection to `left_file_id` (ignore it if they have since opened another file) |
| `{"op":"EXECUTION_QUEUED","execution_id","file_id","path","user_id","username"}` | a member pressed Run (the console shows who) |

This socket reconnects on its own too; after a reconnect the app reloads the
file list and recent changes, since events sent while it was down are gone.

These are sent in-process (like `WORKSPACE_*`), so they reach sockets on the
same backend instance as the REST request; that is the single-backend compose setup.

## Error model

All REST errors return:
```json
{ "detail": "human-readable reason" }
```

Standard codes used: 400 (validation), 401 (unauth), 403 (forbidden / wrong role), 404 (not found), 409 (conflict), 500 (server error).
