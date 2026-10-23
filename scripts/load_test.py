"""Load test: ten concurrent editors and five simultaneous run requests per round.

Ten WebSocket clients edit one file of a running stack at typing speed while,
at the same time, five run requests are submitted at once, round after round.
The script prints one JSON document with

  * edit propagation latency, p50 and p99 (NFR-P1: p99 < 300 ms), and
  * execution queue wait, p50 and p95 (NFR-P2: p95 < 1 s), measured as
    ``started_at - queued_at`` of each run, the same definition as the metrics
    endpoint,

and exits with status 1 if a target is missed, an edit never arrives or a run
does not complete.

It uses the pilot accounts (scripts/pilot.py) and the edit-latency probe
(scripts/latency.py), so it runs wherever the pilot checks run:

    scripts/pilot.sh up
    docker compose exec -T backend python /scripts/load_test.py
    docker compose exec -T backend python /scripts/load_test.py --rounds 5 --duration 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime

from latency import TARGET_P99_MS, measure
from pilot import Api, run_file, set_file_text

QUEUE_WAIT_P95_TARGET_S = 1.0
LOAD_PROJECT = "Load test"
EDIT_FILE = "edit.txt"
RUN_FILE = "run.py"
RUN_SOURCE = 'print("load test")\n'
EDITOR_USERS = ["alice", "bob", "dave"]  # owner, editor, debugger: all may edit and run


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def rounded(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


async def prepare(api: Api) -> tuple[int, int, int]:
    """Sign in, make sure the load-test project exists and return (project, edit file, run file)."""
    await api.login_all()
    project = await api.project("alice", LOAD_PROJECT)
    pid = project["id"]
    await api.add_member(pid, "alice", "bob", "editor")
    await api.add_member(pid, "alice", "dave", "debugger")
    edit_file = await api.file(pid, "alice", EDIT_FILE)
    run_file_ = await api.file(pid, "alice", RUN_FILE)
    await set_file_text(api, pid, edit_file["id"], "alice", "")
    await set_file_text(api, pid, run_file_["id"], "alice", RUN_SOURCE)
    return pid, edit_file["id"], run_file_["id"]


async def run_rounds(api: Api, pid: int, fid: int, rounds: int, runs: int) -> tuple[list[float], list[str]]:
    """Submit ``runs`` executions at the same moment, ``rounds`` times; return queue waits and problems."""
    waits: list[float] = []
    problems: list[str] = []
    for n in range(rounds):
        users = [EDITOR_USERS[i % len(EDITOR_USERS)] for i in range(runs)]
        outcomes = await asyncio.gather(
            *(run_file(api, pid, fid, u, limit=120) for u in users), return_exceptions=True
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                problems.append(f"round {n + 1}: {type(outcome).__name__}: {outcome}")
            elif outcome["status"] != "completed" or not outcome.get("started_at"):
                problems.append(f"round {n + 1}: run {outcome['id']} ended as {outcome['status']}")
            else:
                queued = datetime.fromisoformat(outcome["queued_at"])
                started = datetime.fromisoformat(outcome["started_at"])
                waits.append((started - queued).total_seconds())
        await asyncio.sleep(1)
    return waits, problems


async def load_test(args: argparse.Namespace) -> dict:
    api = Api(args.api, args.ws)
    try:
        pid, edit_fid, run_fid = await prepare(api)
        users = (EDITOR_USERS * args.editors)[: args.editors]
        urls = [f"{args.ws}/ws/projects/{pid}/files/{edit_fid}?token={api.tokens[u]}" for u in users]
        edits_per_client = max(1, int(args.duration / args.interval))

        started = time.monotonic()
        edit_task = asyncio.create_task(measure(urls, edits_per_client=edits_per_client, interval=args.interval))
        await asyncio.sleep(2)  # let the editors connect before the first burst of runs
        waits, run_problems = await run_rounds(api, pid, run_fid, args.rounds, args.runs)
        edits = await edit_task
        elapsed = time.monotonic() - started
    finally:
        await api.close()

    edit_p99 = edits.pct(99)
    wait_p95 = percentile(waits, 95)
    expected_runs = args.rounds * args.runs
    failures = []
    if edit_p99 is None or edit_p99 >= TARGET_P99_MS:
        failures.append(f"edit propagation p99 {rounded(edit_p99, 1)} ms is not below {TARGET_P99_MS:.0f} ms")
    if edits.missing:
        failures.append(f"{edits.missing} of {edits.expected} edit deliveries never arrived")
    failures.extend(edits.errors[:3])
    if wait_p95 is None or wait_p95 >= QUEUE_WAIT_P95_TARGET_S:
        failures.append(f"queue wait p95 {rounded(wait_p95, 3)} s is not below {QUEUE_WAIT_P95_TARGET_S:.0f} s")
    failures.extend(run_problems[:5])

    return {
        "editors": args.editors,
        "runs_per_round": args.runs,
        "rounds": args.rounds,
        "duration_seconds": round(elapsed, 1),
        "edit_propagation": {
            "requirement": "NFR-P1",
            "edits": edits.edits,
            "delivered": len(edits.latencies_ms),
            "expected": edits.expected,
            "p50_ms": rounded(edits.pct(50), 1),
            "p99_ms": rounded(edit_p99, 1),
            "max_ms": rounded(max(edits.latencies_ms) if edits.latencies_ms else None, 1),
            "target_p99_ms": TARGET_P99_MS,
        },
        "queue_wait": {
            "requirement": "NFR-P2",
            "runs": len(waits),
            "expected": expected_runs,
            "p50_s": rounded(percentile(waits, 50), 3),
            "p95_s": rounded(wait_p95, 3),
            "max_s": rounded(max(waits) if waits else None, 3),
            "mean_s": rounded(statistics.fmean(waits) if waits else None, 3),
            "target_p95_s": QUEUE_WAIT_P95_TARGET_S,
        },
        "ok": not failures,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--ws", default="ws://localhost:8000")
    parser.add_argument("--editors", type=int, default=10, help="concurrent editors on one file")
    parser.add_argument("--runs", type=int, default=5, help="run requests submitted at the same moment")
    parser.add_argument("--rounds", type=int, default=3, help="how many times the runs are submitted")
    parser.add_argument("--duration", type=float, default=40, help="seconds the editors keep typing")
    parser.add_argument("--interval", type=float, default=0.2, help="seconds between one editor's edits")
    args = parser.parse_args()
    if args.editors < 2:
        parser.error("--editors must be at least 2: latency needs someone to receive the edits")

    result = asyncio.run(load_test(args))
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        for failure in result["failures"]:
            print(f"  x {failure}", file=sys.stderr)
        return 1
    print("  ok: both latency targets met", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
