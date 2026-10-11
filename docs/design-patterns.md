# Concord — Design Patterns (Feature 15)

This document explains *where* each pattern lives and *why* it was chosen.
Patterns are listed only when they materially help, not for decoration.

## State pattern — execution status

**Where:** `app/models/execution.py::ExecutionStatus` + `ALLOWED_TRANSITIONS` +
`can_transition()`.

**Why:** The execution lifecycle (`QUEUED → STARTING → RUNNING → COMPLETED|FAILED|TIMEOUT|CANCELLED`)
is exactly a finite state machine, and the workers and the REST cancel endpoint
both need to *enforce* that we never move from `COMPLETED` back to `RUNNING`.
Encoding the allowed transitions in a single dictionary makes the rule
auditable, testable in isolation (`test_execution_state_machine.py`), and
impossible to bypass from any code path that goes through `_transition()`.

**Cost:** 30 lines of code, no runtime overhead.

## Observer pattern — WebSocket broadcast + Redis pub/sub

**Where:** `app/realtime/manager.py::RoomManager.broadcast` and
`app/realtime/execution_queue.py::publish_session_event` /
`subscribe_session_events`.

**Why:** Sessions are intrinsically publish/subscribe: every connected client
"observes" edit events, execution events, and debug events. Implementing this
with a manual observer list (instead of an event bus) is what lets us keep
the code small while still being testable.

The cross-process version (workers → backend gateway → clients) is also an
observer, just over Redis pub/sub instead of in-process iteration. Same
pattern, different transport.

## Adapter pattern — DAP client wrapper

**Where:** `app/debug/dap.py::DapClient`.

**Why:** debugpy speaks the Debug Adapter Protocol (DAP) over a TCP socket
with a custom Content-Length framed JSON message format. Our application
wants simple `set_breakpoints(file, lines)`, `next(thread_id)`, etc. The
DapClient translates between the two. Without this adapter, every consumer
of the debugger would have to know DAP's request/response correlation rules,
event types, and lifecycle. The adapter contains all of that.

**Demonstrably justified:** the surface is meaningfully smaller than the
underlying protocol (10 methods vs. DAP's 60+).

## Strategy (deferred)

The CRDT library selection (Yjs vs. Automerge) is a Strategy choice that
v1 commits to Yjs. If a future iteration needs a different merge algorithm
(for example, for binary blobs where CRDTs are too expensive), the
`realtime/crdt.py` module is the seam where you'd plug the alternative
implementation. For v1 a single strategy is sufficient; this is documented
in the report so the design intent is explicit.

## Factory (deferred)

A multi-language execution factory would replace `app/services/sandbox.py`
with a strategy/factory pair where `pick_runtime("python3")` returns a
PythonRunner, `pick_runtime("node")` returns a NodeRunner, etc. v1 is
Python-only by design (Feature 17 deliberately makes a multi-file change
later, not a multi-language one).
