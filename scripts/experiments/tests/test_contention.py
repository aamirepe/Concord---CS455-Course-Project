"""Run with python -m unittest discover -s scripts/experiments/tests."""
from __future__ import annotations

import sys
import time
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import e3_contention
from common import TERMINAL_STATUSES, Event, EventLog


class ScriptedPilot:
    def __init__(self, fault: str | None = None) -> None:
        self.fault = fault
        self.project_id = 7
        self.files: dict[int, dict] = {}
        self.runs: dict[int, dict] = {}
        self.events = EventLog()
        self.released: set[int] = set()
        self.started: set[int] = set()
        self.closed = False
        self.early_start_before_last_submission = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def login(self, users):
        assert users == ("alice", "bob", "dave")

    async def discover(self, _name):
        pass

    def project_path(self, suffix=""):
        return f"/projects/7{suffix}"

    async def ensure_file(self, path):
        file = {"id": len(self.files) + 1, "path": path}
        self.files[file["id"]] = file
        return file

    async def replace_text(self, file_id, code):
        self.files[file_id]["text"] = code

    @asynccontextmanager
    async def project_events(self):
        yield self.events

    def emit(self, target, status):
        self.events.events.append(Event(
            datetime.now(UTC).isoformat(), time.perf_counter(),
            {"op": "exec_event", "event": {"type": "execution_status", "execution_id": target, "status": status}},
        ))

    def start(self, target):
        if target in self.started:
            return
        self.started.add(target)
        if target >= 102 and len(self.runs) < 5:
            self.early_start_before_last_submission = True
        self.emit(target, "running")
        self.runs[target]["status"] = "completed"
        self.emit(target, "completed")

    async def request(self, method, path, *, user="alice", **kwargs):
        if path == self.project_path("/executions"):
            if method == "GET":
                return []
            target = 100 + len(self.runs)
            self.runs[target] = {"id": target, "status": "queued", "file_id": kwargs["json"]["file_id"]}
            if target < 102:
                self.runs[target]["status"] = "running"
                self.emit(target, "running")
            elif target == 102:
                self.emit(999, "running")  # Another execution must not invalidate this measurement.
                if self.fault in {"timeout", "silent_timeout", "stale_snapshot", "one_timeout"}:
                    for blocker in ((100,) if self.fault == "one_timeout" else (100, 101)):
                        self.runs[blocker]["status"] = "timeout"
                        if self.fault in {"timeout", "one_timeout"}:
                            self.emit(blocker, "timeout")
                if self.fault in {"timeout", "early_start", "one_timeout"}:
                    self.start(target)
            return self.runs[target].copy()
        target = int(path.split("/executions/")[1].split("/")[0])
        if path.endswith("/cancel"):
            if target < 102:
                self.released.add(target)
            if self.runs[target]["status"] not in TERMINAL_STATUSES:
                self.runs[target]["status"] = "cancelled"
                self.emit(target, "cancelled")
            if len(self.released) == 2:
                for contender in (104, 102, 103):
                    self.start(contender)
        if method == "GET" and target < 102 and self.fault == "stale_snapshot":
            return {**self.runs[target], "status": "running"}
        return self.runs[target].copy()


class ContentionTests(unittest.IsolatedAsyncioTestCase):
    async def measure(self, fault=None):
        pilot = ScriptedPilot(fault)
        args = SimpleNamespace(api="http://simulated", ws=None, timeout=0.05, project="Pilot demo")
        with patch.object(e3_contention, "PilotClient", return_value=pilot):
            result = await e3_contention.run(args)
        self.assertTrue(pilot.closed)
        self.assertEqual(len(result["cleanup"]), 5)
        self.assertTrue(all(run["status"] in TERMINAL_STATUSES for run in pilot.runs.values()))
        self.assertEqual(set(result["start_order"]), {"owner", "editor", "debugger"})
        return pilot, result

    async def test_intentional_release_preserves_observed_order(self):
        pilot, result = await self.measure()
        self.assertFalse(pilot.early_start_before_last_submission)
        self.assertTrue(result["complete"])
        self.assertEqual(result["start_order"], ["owner", "editor", "debugger"])

    async def test_timeouts_during_submissions_are_incomplete(self):
        pilot, result = await self.measure("timeout")
        self.assertTrue(pilot.early_start_before_last_submission)
        self.assertEqual([run["status"] for run in result["blocker_release"]], ["timeout", "timeout"])
        self.assertFalse(result["complete"])

    async def test_missing_timeout_events_do_not_hide_lost_blockers(self):
        _, result = await self.measure("silent_timeout")
        self.assertFalse(result["complete"])

    async def test_cancel_responses_detect_loss_despite_stale_snapshots(self):
        _, result = await self.measure("stale_snapshot")
        self.assertFalse(result["complete"])

    async def test_early_start_invalidates_even_if_api_reports_running(self):
        pilot, result = await self.measure("early_start")
        self.assertTrue(pilot.early_start_before_last_submission)
        self.assertEqual([run["status"] for run in result["blocker_release"]], ["cancelled", "cancelled"])
        self.assertFalse(result["complete"])

    async def test_losing_one_blocker_is_enough(self):
        _, result = await self.measure("one_timeout")
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
