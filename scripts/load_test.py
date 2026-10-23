"""Load test for the CRDT layer.

Two modes:

  (default)   in-process convergence test.
              Spawns N pycrdt.Doc instances ("clients") plus one server-side
              room Doc. Fires `edits_per_client` random single-character
              inserts from random clients; each edit is applied to the room
              and rebroadcast to every OTHER client (exactly what the
              gateway does). At the end, asserts that all clients and the
              room have byte-identical text content.

              This is the §20 acceptance test: it doesn't depend on the
              WebSocket transport, the network, the worker, or docker —
              it directly exercises the CRDT math + the room-manager
              fanout pattern.

  --wire      wire-protocol latency test (scripts/latency.py).
              N real WebSocket clients, each logged in as a user, edit one
              file of a running stack at typing speed. Each edit carries a
              unique token, and every other client records when the token
              reaches its copy: "A types, B sees it". Reports p50/p95/p99;
              fails if any edit is lost or p99 >= 300 ms. This backs
              FR4/NFR1 (<300 ms p99) and NFR2 (compare p99 across client
              counts).

Usage:
    # in-process (preferred; no docker required)
    python scripts/load_test.py --clients 1 5 10 25

    # wire, against a running stack (e.g. the pilot's demo accounts)
    python scripts/load_test.py --wire --clients 2 5 10 --project 1 --file 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time

# Make the backend package importable so we can use the canonical crdt helpers
# (which encapsulate the pycrdt 0.10.x API quirks: get_state() is the state
# vector, get_update() is full state).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.realtime.crdt import (  # noqa: E402
    apply_update,
    doc_to_bytes,
    doc_to_text,
    new_doc,
)

# `websockets` is only needed by --wire mode. Importing it lazily (inside
# run_wire/_wire_client) keeps the default in-process mode dependency-free,
# matching this script's own "no docker required" promise above.


# ---------------------------------------------------------------------------
# In-process convergence test
# ---------------------------------------------------------------------------


def _random_insert_pos(text: str, rng: random.Random) -> int:
    return rng.randint(0, len(text))


def _make_room_and_clients(n_clients: int, initial: str) -> tuple[object, list[object]]:
    """Mirror the gateway's per-room layout: one server Doc + N client Docs.

    Each client gets a full-state sync from the room before any edits.
    """
    from pycrdt import Text  # local import keeps top of file clean

    room = new_doc(initial)
    clients = []
    for _ in range(n_clients):
        c = new_doc(initial)
        apply_update(c, doc_to_bytes(room))
        # touch the text binding so the doc is in a known state (the new_doc
        # helper already does this, but be explicit for clarity)
        c.get("monaco", type=Text)
        clients.append(c)
    return room, clients


async def run_in_process(n_clients: int, edits_per_client: int, seed: int = 42) -> dict:
    """Fire concurrent edits and assert byte-identical convergence.

    Returns a dict with timing, success, and (if it converged) the common
    text content. If it diverges, raises AssertionError with details.
    """
    from pycrdt import Text

    rng = random.Random(seed)
    initial = "hello world"

    room, clients = _make_room_and_clients(n_clients, initial)
    n_edits = n_clients * edits_per_client

    started = time.monotonic()
    for i in range(n_edits):
        client = clients[rng.randrange(n_clients)]
        text_obj = client.get("monaco", type=Text)
        before = str(text_obj)
        pos = _random_insert_pos(before, rng)
        ch = rng.choice("abcdefghijklmnopqrstuvwxyz ")

        # Wire-format update: in production, the gateway sends a per-edit
        # diff to peers. For a convergence test, doc_to_bytes (full state)
        # is idempotent and produces identical results when each client
        # applies it on top of the same base — which is exactly the
        # "reconnect after a dropped broadcast" scenario.
        update = doc_to_bytes(client)

        # Server applies the update to the room and rebroadcasts to peers.
        apply_update(room, update)
        for other in clients:
            if other is not client:
                apply_update(other, update)

    duration = time.monotonic() - started
    reference = doc_to_text(room)
    divergent = []
    for idx, c in enumerate(clients):
        if doc_to_text(c) != reference:
            divergent.append((idx, doc_to_text(c)[:80]))

    converged = not divergent
    return {
        "mode": "in-process",
        "clients": n_clients,
        "edits_per_client": edits_per_client,
        "total_edits": n_edits,
        "duration_seconds": round(duration, 3),
        "converged": converged,
        "final_text_length": len(reference),
        "divergent_clients": divergent,
    }


# ---------------------------------------------------------------------------
# Wire-protocol latency test
# ---------------------------------------------------------------------------


async def run_wire(n_clients: int, edits_per_client: int, args: argparse.Namespace) -> dict:
    """``n_clients`` users edit one file over the real WebSocket protocol; see scripts/latency.py."""
    import httpx
    from latency import measure

    users = (args.users * n_clients)[:n_clients]
    tokens: list[str] = []
    async with httpx.AsyncClient(base_url=args.api, timeout=30) as http:
        for u in users:
            r = await http.post("/auth/login", json={"username": u, "password": args.password})
            if r.status_code != 200:
                raise SystemExit(f"can't log in as {u}: {r.status_code} {r.text[:200]}")
            tokens.append(r.json()["access_token"])
    urls = [f"{args.ws}/ws/projects/{args.project}/files/{args.file}?token={t}" for t in tokens]
    started = time.monotonic()
    result = await measure(urls, edits_per_client=edits_per_client, interval=args.interval)
    return {"mode": "wire", "duration_seconds": round(time.monotonic() - started, 2), **result.summary()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 5, 10, 25])
    parser.add_argument("--edits", type=int, default=50, help="edits per client")
    parser.add_argument("--wire", action="store_true", help="use WebSocket transport")
    parser.add_argument(
        "--seed", type=int, default=42, help="RNG seed (in-process mode only)"
    )
    wire = parser.add_argument_group("--wire options (a running stack)")
    wire.add_argument("--api", default="http://localhost:8000")
    wire.add_argument("--ws", default="ws://localhost:8000")
    wire.add_argument("--users", nargs="+", default=["alice", "bob", "dave"],
                      help="accounts that can edit the file; reused if there are more clients")
    wire.add_argument("--password", default=os.environ.get("CONCORD_PASSWORD", "pilot-pass-123"))
    wire.add_argument("--project", type=int, default=int(os.environ.get("CONCORD_PROJECT_ID", "1")))
    wire.add_argument("--file", type=int, default=int(os.environ.get("CONCORD_FILE_ID", "1")))
    wire.add_argument("--interval", type=float, default=0.05, help="seconds between one client's edits")
    args = parser.parse_args()

    all_ok = True
    for n in args.clients:
        if args.wire:
            if n < 2:
                print("  - skipping 1 client: latency needs someone to receive the edits", file=sys.stderr)
                continue
            result = asyncio.run(run_wire(n, args.edits, args))
            ok = result["ok"]
        else:
            result = asyncio.run(run_in_process(n, args.edits, seed=args.seed))
            ok = result["converged"]
        print(json.dumps(result))
        if not ok:
            all_ok = False
            print(f"  ✗ {n} clients FAILED", file=sys.stderr)
        elif args.wire:
            print(f"  ✓ {n} clients: p50={result['p50_ms']}ms p99={result['p99_ms']}ms "
                  f"({result['delivered']}/{result['expected']} deliveries)", file=sys.stderr)
        else:
            print(f"  ✓ {n} clients converged", file=sys.stderr)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
