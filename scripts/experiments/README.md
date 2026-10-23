# Workflow experiments

These scripts measure a running Concord pilot through its REST and WebSocket APIs. They report observed behavior rather than asserting a particular scheduling policy or performance target.

Start and seed the stack from the repository root:

```bash
scripts/pilot.sh up
```

Use Python 3.12 with the dependencies in `backend/requirements.txt`, or run the scripts inside the backend container, where `/scripts` is mounted from this repository. For example:

```bash
docker compose exec -T backend python /scripts/experiments/common.py --listen-seconds 2
```

From a host Python environment:

```bash
python scripts/experiments/common.py --api http://localhost:8000 --listen-seconds 2
```

The common helper logs in as the existing `alice` owner account, finds her seeded `Pilot demo` project and files, and records project events. It does not create accounts or projects. An empty event list is valid when nothing happened during the listening interval. Login or project discovery errors usually mean the pilot was not seeded or its accounts were changed.

The pilot accounts are `alice` (owner), `bob` (editor), `dave` (debugger) and `carol` (viewer). Their existing demo password is `pilot-pass-123`. Set `CONCORD_PILOT_PASSWORD` if the local pilot uses another password. Tokens and passwords are not included in reports. `--project` selects an existing project owned by `alice`; `--ws` overrides the WebSocket base derived from `--api`; `--timeout` bounds network operations and event waits.

## Measurements

| Script | Measurement | Command inside the backend container |
|---|---|---|
| `e1_lifecycle.py` | Python and C++ execution status arrival times | `python /scripts/experiments/e1_lifecycle.py` |
| `e2_cancel.py` | Cancel response after 1.5 seconds of running; remaining container lifetime | `python /scripts/experiments/e2_cancel.py` |
| `e3_contention.py` | Start order for editor, debugger and owner submissions while both workers are occupied | `python /scripts/experiments/e3_contention.py` |
| `e4_burst.py` | Queue-wait median, p95 and maximum for five concurrent submissions per round | `python /scripts/experiments/e4_burst.py --rounds 10` |
| `e5_debug_step.py` | Command-to-observer pause latency over 40 debugger `next` steps | `python /scripts/experiments/e5_debug_step.py` |

Prefix these commands with `docker compose exec -T backend` when running from the host. All scripts accept the common URL, project and timeout options. The execution experiments create and edit their own files in the selected pilot project. Use a local pilot without unrelated runs or interactive edits so other activity does not distort the measurements. Reports identify the files and executions involved.

E2 also requires the Docker CLI and access to the daemon running the sandbox containers. Run it from the host Python environment to use the host's Docker CLI; the standard backend image does not include that CLI. If Docker is unavailable, the report must mark container lifetime unavailable; a successful cancel API answer alone does not prove that the container stopped. E3 assumes the pilot's two worker slots and records the observed order. A missing event or timeout is a measurement failure, not evidence of a successful transition. These scripts do not require changes from another feature branch to run.

## JSON output and timing

Each run prints a JSON object with `experiment`, `reported_at` in UTC, and the experiment's measurements. Redirect stdout to a file to retain the result. Project events preserve their wire message, UTC receipt time and `monotonic_s` from the local performance clock. Execution events arrive inside `{"op": "exec_event", "event": {...}}`; the helper exposes the inner event as `payload` while retaining the original message.

Use differences between monotonic timestamps in the same process for arrival and step latency. They are not comparable between machines or separate script runs. Queue wait uses a run's server-side `queued_at` and `started_at` values. The helper checks membership with a socket ping/pong and allows a short subscription warm-up; there is no protocol acknowledgment proving that the Redis forwarder is ready. Retain missing-event diagnostics when interpreting results.

Latency summaries use milliseconds and nearest-rank p95 (`ceil(0.95 * count)`). Empty sample sets report a count of zero and null statistics. Keep the sample count, timeout and missing-event counts with the reported median, p95 and maximum; do not treat a simulated or incomplete run as a live system measurement.
