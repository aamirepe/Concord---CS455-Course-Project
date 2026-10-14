# How Concord Works — A Beginner's Guide

This is the "explain it like I'm new" version. Read this first if you want
to understand what each piece does before running anything.

## The big picture

Concord is a website where multiple people can:

1. Open the same file at the same time
2. Type in it together, with no overwriting each other
3. Press a button to run that file as a Python program
4. Step through it with a debugger, together

It's built out of five independent services that talk to each other:

```
Browser (React)  ←→  Backend (FastAPI)  ←→  Postgres (the database)
        ↕                    ↕
   WebSocket             Redis (the message queue)
                            ↕
                         Worker  →  Docker (sandbox)
```

When you understand these five pieces and how data flows between them, you
understand the whole app.

## The five services, one at a time

### 1. The Browser (the frontend)

Built with **React** (a JavaScript framework for building UIs) and
**TypeScript** (JavaScript with type checking). The big code editor you see
is **Monaco** — the same editor VS Code uses, but loaded as a library.

The browser holds:
- **The UI** (buttons, sidebars, the editor itself)
- **A copy of the file's text** (so you can see it without a network round trip)
- **A copy of the Yjs CRDT state** (more on this in a second)

When you type, two things happen:
- Your screen updates immediately (because the editor already has the text)
- A tiny "diff" describing your change gets sent over a WebSocket to the backend

### 2. The Backend (FastAPI)

**FastAPI** is a Python web framework. It serves two completely different
kinds of traffic:

- **REST API** — the normal "GET this URL, get JSON back" style. Used for
  things that don't need to be live: logging in, creating projects, listing
  files, etc.
- **WebSocket** — a persistent connection that stays open. Used for the
  live editing. Both sides can send messages at any time.

The backend is also where all the **business logic** lives:
- Auth (signing up, logging in, JWT tokens)
- Permissions (is this user allowed to do X?)
- The "room registry" (which WebSocket connections are editing which file)
- The execution API (you click "Run" → it queues a job)
- The snapshot API (save and restore versions)

### 3. Postgres (the database)

A normal relational database. It holds:
- **Users** (username, email, hashed password)
- **Projects** (a project belongs to a user)
- **Sessions** (a "live coding room" tied to a project)
- **Session members** (who's in each session, with what role)
- **Files** (the files in each project)
- **Executions** (each time someone pressed "Run")
- **Snapshots** (saved versions of a file)

**Important design choice:** the live file text is NOT in Postgres. The
Yjs CRDT keeps that in memory on the backend and in every browser. Postgres
only gets **periodic checkpoints** (every 5 seconds while a session is
active) and **manual snapshots** (when you click "Save snapshot"). This is
the single biggest performance decision in the app — writing to Postgres on
every keystroke would be way too slow.

### 4. Redis (the message queue)

A super-fast in-memory key-value store. We use it for two things:

- **Job queue**: when you press "Run", the backend doesn't run the code
  itself. It pushes a job onto a Redis list. A separate **worker** process
  pulls jobs off the list and runs them. This way, ten users pressing "Run"
  at once doesn't block anyone.
- **Pub/sub** (publish/subscribe): when a worker finishes running your code,
  it publishes a message to a Redis channel. The backend subscribes to that
  channel and forwards the result to every connected client in your session.
  This is how the live output panel updates in everyone's browser at once.

### 5. The Worker + Docker (the sandbox)

A worker is just another Python process. It runs in a loop:

```
while True:
    job = redis.get_next_job()    # blocks until one is available
    result = run_job_in_docker(job)
    save_result_to_postgres(result)
    publish_result_to_redis(result)
```

For each job, it spins up a fresh **Docker container** — a tiny isolated
Linux box — runs the user's Python code in it, captures stdout/stderr, and
shuts it down. The container is locked down:

- No network access (`--network none`)
- Limited to 128 MB of RAM
- Limited to 0.5 CPU cores
- Limited to 64 processes (kills fork bombs)
- Read-only filesystem
- A non-root user
- A wall-clock timeout enforced by the worker (not the container — we don't
  trust the code to exit on its own)

If the code tries to do `import os; os.system("rm -rf /")`, the worst it can
do is wreck its own throwaway container, which gets deleted seconds later.

## The "magic" — how do 5 people type in the same file without overwriting each other?

This is the part most people are surprised by. The answer is a data
structure called a **CRDT** (Conflict-free Replicated Data Type).

A normal text file is a string. If you and I both have the string `"hello"`,
and I insert `"X"` at position 0 to get `"Xhello"`, and at the same time
you insert `"Y"` at position 0 to get `"Yhello"` — whose version wins?
There's no good answer with plain strings.

A CRDT solves this by attaching a unique ID to every character. Instead of
storing `"hello"`, it stores something like:
```
(char='h', id=(client1, clock=1))
(char='e', id=(client1, clock=2))
(char='l', id=(client1, clock=3))
(char='l', id=(client1, clock=4))
(char='o', id=(client1, clock=5))
```

When two edits happen at the same time and then get exchanged, the receiving
side sorts all the characters by their ID and rebuilds the string. The
result is **deterministic** — every replica in the world, given the same
set of operations, will produce the exact same final string. No central
arbiter needed.

The library we use is called **Yjs** (in the browser) and **pycrdt** (on
the server). They're the same data structure, just written in two languages.
The browser and the server converge on the same text automatically.

What you have to build yourself is the **plumbing around the CRDT**:
- The room registry that maps a `(session, file)` pair to a CRDT document
- The WebSocket code that ships updates between clients
- The persistence layer that periodically writes the CRDT state to Postgres

That's what the collab gateway does.

## The execution flow, end to end

Let's trace what happens when User A clicks "Run" while editing
`main.py` in a session with User B:

1. **Browser** sends a REST request: `POST /sessions/42/executions` with
   `{file_id: 7}`. The JWT in the Authorization header identifies User A.

2. **Backend** checks: is User A allowed to run code in session 42? (Yes,
   they're the owner.) Creates a row in the `executions` table with
   `status='queued'`. Returns the new row to the browser.

3. **Backend** pushes a job onto the Redis queue:
   ```
   { execution_id: 99, session_id: 42, file_id: 7, source_text: "print('hi')" }
   ```

4. **Browser** is also subscribed (via WebSocket) to events on session 42.
   It sees the "queued" status come back as an event.

5. **Worker** wakes up (it was blocked waiting for jobs), pulls the job,
   updates the `executions` row to `status='starting'`, then `status='running'`.

6. **Worker** starts a Docker container with the source text mounted as
   `/workspace/main.py`. The container runs `python /workspace/main.py`.

7. **Worker** captures stdout/stderr. Updates the `executions` row to
   `status='completed'`, `stdout='hi\n'`, `exit_code=0`.

8. **Worker** publishes a message to Redis channel
   `concord:exec:42`:
   ```
   { type: 'execution_output', execution_id: 99, stdout: 'hi\n', ... }
   ```

9. **Backend** is subscribed to that channel. It forwards the message as a
   WebSocket text frame to every client connected to session 42.

10. **Both User A's and User B's browser** receive the message and update
    their "Console" panel to show "hi".

## The permission system

Every mutating operation — editing a file, running code, setting a breakpoint,
stepping the debugger, restoring a snapshot, changing someone's role —
checks the user's role first.

There are four roles, in increasing privilege:

| Role | Can do |
|---|---|
| viewer | read code, watch runs, no edits |
| editor | everything viewer can + edit + run + snapshots |
| debugger | everything editor can + breakpoints + step |
| owner | everything + manage members and roles |

When you join a session, you start as a viewer. The owner can promote you.

The role check happens in two places:
- On REST endpoints, as a FastAPI dependency
- On WebSocket messages, in the message handler before any state change

So even if a malicious client crafts a custom WebSocket message, the server
rejects it.

## The debugger

The debugger uses a real piece of technology: **debugpy**, the same engine
VS Code uses, which speaks the **Debug Adapter Protocol (DAP)** over a TCP
socket.

Instead of writing our own bytecode-stepping debugger (which would be a
months-long project), we:

1. Launch the user's code inside the sandbox with
   `python -m debugpy --listen 0.0.0.0:5678 --wait-for-client main.py`
2. Connect to it from the worker with a tiny DAP client
3. Translate "set breakpoint on line 7" → DAP `setBreakpoints`
4. Translate "step over" → DAP `next`
5. When debugpy sends back a `stopped` event (we hit a breakpoint), the
   worker queries the variables and call stack and publishes them to Redis
6. The backend forwards that to every connected client, who all see the
   same paused state simultaneously

This is the **Adapter pattern**: the app code talks to our clean
`DapClient` interface, and the interface hides all of DAP's quirks.

## Why each design decision was made

A few things you might wonder about:

**"Why not write to Postgres on every keystroke?"** — Because that would
take maybe 10-30ms per write. We want <300ms end-to-end edit propagation.
The CRDT is in memory everywhere; we only need to durably store it every
5 seconds or so.

**"Why Docker and not a real VM?"** — Docker is much faster to start
(seconds vs. minutes), and Linux namespaces + cgroups give us enough
isolation for untrusted student code. If you needed to defend against
malicious nation-state code, you'd want gVisor or Firecracker.

**"Why Yjs and not a database with locking?"** — Because optimistic local
edits with merge are the natural model for human collaboration. With
locking, every keystroke would block while waiting for everyone else's
keystroke. The CRDT lets everyone type freely and reconciles the result.

**"Why JWT and not session cookies?"** — Because the WebSocket connection
also needs authentication and a cookie is awkward in that context. JWT
in the Authorization header (and the WebSocket query string) is uniform.

## Where to read next

- `docs/architecture.md` — diagrams and a more compact version of this
- `docs/api.md` — every REST endpoint and WebSocket message
- `docs/database.md` — the schema and why we made it look the way it does
- `docs/design-patterns.md` — where State, Observer, and Adapter live
- `docs/setup-dev.md` — getting it running
- `docs/HOW_TO_RUN.md` — the actual step-by-step run guide
