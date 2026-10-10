# Concord — Database Design

## ER diagram (text form)

```
users ──┐
        ├── projects ── files ── document_versions
        │       │
        │       └── sessions ── session_members ── (back to users)
        │              │
        │              ├── executions ── execution_events
        │              │       └── debug_sessions
        │              ├── snapshots ── snapshot_files (back to files)
        │              └── breakpoints (back to files)
```

## Tables

| Table | Purpose | Key constraints |
|---|---|---|
| users | Login + identity | unique(username), unique(email) |
| projects | Owned by a user | FK owner_id → users.id |
| sessions | A live collab session for a project | FK project_id |
| session_members | Role per user per session | unique(session_id, user_id) |
| files | Files in a project | unique(project_id, path) |
| document_versions | Periodic CRDT checkpoints | one per file per checkpoint |
| snapshots | User-labeled checkpoints | FK session_id |
| snapshot_files | Per-file content of a snapshot | FK snapshot_id, FK file_id |
| executions | One row per code run | FK session_id, FK file_id, status enum |
| execution_events | Append-only audit log | FK execution_id |
| debug_sessions | Active debug session for an execution | FK execution_id (unique) |
| breakpoints | Per-file breakpoints | FK file_id |

## Indexes
- users.username, users.email
- projects.owner_id
- sessions.project_id
- session_members.session_id, .user_id
- files.project_id
- document_versions.file_id
- executions.session_id, .status
- execution_events.execution_id, .event_type
- breakpoints.file_id

## Why no per-keystroke writes
The single most important DB design decision: the `files` table does not hold
the live text. The Yjs CRDT (in-memory in the collab gateway + every client)
is the source of truth for live state. Postgres receives:
- A new `document_versions` row every N seconds while a session is active
- A `snapshots` row only when a user explicitly saves one
- A `snapshot_files` row per file in that snapshot

This is the only way to make per-keystroke latency fit in the 300ms budget
without burning the database.

## Migration policy
- All schema changes go through Alembic (`alembic/versions/`).
- Forward-only migrations in production; never edit a committed migration.
- New tables get a corresponding model in `app/models/`.
