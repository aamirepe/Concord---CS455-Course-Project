"""Edit propagation latency over the real WebSocket protocol (NFR1: <300 ms p99).

Each client is a separate user connected to the same file, speaking the same
protocol as the browser: base64 JSON ``{"op": "update"}`` up, binary CRDT
updates down. Every edit inserts a unique token; each *other* client records
when that token shows up in its copy of the document. Latency is therefore
"user A types, user B sees it", measured on one clock.

Used by ``scripts/load_test.py --wire`` and the pilot's "Edit latency" check.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import statistics
import time
from dataclasses import dataclass, field

import websockets
from pycrdt import Doc, Text

TEXT_TYPE = "monaco"
TOKEN_RE = re.compile(r"\[(\d+):(\d+)\]")
TARGET_P99_MS = 300.0


@dataclass
class LatencyResult:
    clients: int
    edits: int
    expected: int  # edits x (clients - 1) deliveries
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def pct(self, p: float) -> float | None:
        if not self.latencies_ms:
            return None
        xs = sorted(self.latencies_ms)
        return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

    @property
    def missing(self) -> int:
        return self.expected - len(self.latencies_ms)

    @property
    def ok(self) -> bool:
        p99 = self.pct(99)
        return not self.errors and self.missing == 0 and p99 is not None and p99 < TARGET_P99_MS

    def summary(self) -> dict:
        r = lambda v: round(v, 1) if v is not None else None  # noqa: E731
        return {
            "clients": self.clients,
            "edits": self.edits,
            "delivered": len(self.latencies_ms),
            "expected": self.expected,
            "p50_ms": r(self.pct(50)),
            "p95_ms": r(self.pct(95)),
            "p99_ms": r(self.pct(99)),
            "max_ms": r(max(self.latencies_ms) if self.latencies_ms else None),
            "mean_ms": r(statistics.fmean(self.latencies_ms) if self.latencies_ms else None),
            "target_p99_ms": TARGET_P99_MS,
            "ok": self.ok,
            "errors": self.errors[:3],
        }


async def measure(
    urls: list[str],
    edits_per_client: int = 50,
    interval: float = 0.05,
    settle: float = 3.0,
) -> LatencyResult:
    """Connect one client per URL (one per user) and have each type
    ``edits_per_client`` tokens, about one every ``interval`` seconds."""
    n = len(urls)
    result = LatencyResult(clients=n, edits=n * edits_per_client, expected=n * edits_per_client * (n - 1))
    sent_at: dict[tuple[int, int], float] = {}
    ready = asyncio.Barrier(n) if hasattr(asyncio, "Barrier") else None
    all_sent = asyncio.Event()
    senders_done = 0

    async def client(idx: int, url: str) -> None:
        nonlocal senders_done
        async with websockets.connect(url, max_size=None) as ws:
            doc = Doc()
            text = doc.get(TEXT_TYPE, type=Text)
            while True:  # the first binary frame is the whole document
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                if isinstance(msg, bytes):
                    doc.apply_update(msg)
                    break

            def on_change(event) -> None:
                now = time.monotonic()
                for op in getattr(event, "delta", []) or []:
                    ins = op.get("insert") if isinstance(op, dict) else None
                    if not isinstance(ins, str):
                        continue
                    for m in TOKEN_RE.finditer(ins):
                        key = (int(m.group(1)), int(m.group(2)))
                        if key[0] != idx and key in sent_at:
                            result.latencies_ms.append((now - sent_at[key]) * 1000)

            subscription = text.observe(on_change)

            async def receive() -> None:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        doc.apply_update(msg)

            rx = asyncio.create_task(receive())
            if ready is not None:
                await ready.wait()
            else:
                await asyncio.sleep(0.5)
            await asyncio.sleep(random.random() * interval)  # noqa: S311 -- jitter, not crypto
            for seq in range(edits_per_client):
                before = doc.get_state()
                token = f"[{idx}:{seq}]"
                pos = random.randint(0, len(text))  # noqa: S311 -- anywhere, like real typing
                text.insert(pos, token)
                update = doc.get_update(before)
                sent_at[(idx, seq)] = time.monotonic()
                await ws.send(json.dumps({"op": "update", "data": base64.b64encode(update).decode()}))
                await asyncio.sleep(interval * random.uniform(0.5, 1.5))  # noqa: S311
            senders_done += 1
            if senders_done == n:
                all_sent.set()
            try:
                await asyncio.wait_for(all_sent.wait(), timeout=60)
                await asyncio.sleep(settle)  # let the last edits arrive
            finally:
                rx.cancel()
                text.unobserve(subscription)  # pycrdt objects must be freed on this thread

    outcomes = await asyncio.gather(*(client(i, u) for i, u in enumerate(urls)), return_exceptions=True)
    result.errors = [f"{type(o).__name__}: {o}" for o in outcomes if isinstance(o, BaseException)]
    return result
