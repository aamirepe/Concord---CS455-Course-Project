"""Chaos test: kill a worker mid-execution and verify the system recovers.

The way to run this:
  1. Submit a slow execution job (e.g. `time.sleep(60)`)
  2. Kill the worker service: `docker compose kill -s SIGKILL worker`
  3. Verify the execution row's status goes to FAILED (via the orphan sweep,
     backend/app/realtime/orphan_sweep.py - runs every 60s, marks executions
     stuck in STARTING/RUNNING for more than 5 minutes as FAILED)
  4. `docker compose up -d worker` to bring the replicas back

This is the "Feature 14 chaos" test, written as a script so it can be invoked
manually during the demo. Expect this to take 5-6 minutes end to end: the
orphan sweep only acts once an execution has been stuck for 5+ minutes.
"""
from __future__ import annotations

import argparse
import json
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--file-id", type=int, required=True)
    args = parser.parse_args()

    h = {"Authorization": f"Bearer {args.token}"}

    # We can't run code via REST (execution runs whatever's already persisted
    # for this file_id, not code in the request body) - for the chaos demo,
    # push a `time.sleep(60)` edit through the collab WebSocket first, then
    # submit and watch.
    r = httpx.post(
        f"{args.api}/projects/{args.project_id}/executions",
        json={"file_id": args.file_id},
        headers=h,
    )
    r.raise_for_status()
    execution = r.json()
    print(json.dumps(execution, indent=2))
    print("Now kill the worker with:  docker compose kill -s SIGKILL worker")
    print("(bring it back afterwards with:  docker compose up -d worker)")
    print("Then re-fetch (polling for up to 6 minutes - the orphan sweep")
    print("needs the execution stuck for 5+ minutes before it acts):")
    for _ in range(190):
        time.sleep(2)
        r = httpx.get(
            f"{args.api}/projects/{args.project_id}/executions/{execution['id']}",
            headers=h,
        )
        r.raise_for_status()
        cur = r.json()
        print(f"  status={cur['status']} exit={cur['exit_code']}")
        if cur["status"] in {"completed", "failed", "timeout", "cancelled"}:
            print("Final:", json.dumps(cur, indent=2))
            return
    print("Did not terminate within ~6.3 minutes - system may be hung.")


if __name__ == "__main__":
    main()
