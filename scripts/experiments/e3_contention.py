"""Observe editor, debugger and owner start order with both worker slots occupied."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from common import TERMINAL_STATUSES, Event, PilotClient, argument_parser, print_json


async def run(args) -> dict:
    result: dict = {"blockers": [], "contenders": [], "cleanup": [], "contention_valid": False, "contention_issues": []}
    owned_ids: list[int] = []
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        try:
            await api.login(("alice", "bob", "dave"))
            await api.discover(args.project)
            result["project_id"] = api.project_id
            active = [run["id"] for run in await api.request("GET", api.project_path("/executions")) if run["status"] not in TERMINAL_STATUSES]
            if active:
                raise RuntimeError(f"Pilot project already has active executions: {active}")
            files = {}
            for name in ("blocker-0", "blocker-1", "editor", "debugger", "owner"):
                file = await api.ensure_file(f"experiment-e3-{uuid4().hex[:12]}-{name}.py")
                code = "import time\nwhile True:\n    time.sleep(0.1)\n" if name.startswith("blocker") else f"print({name!r})\n"
                await api.replace_text(file["id"], code)
                files[name] = file
            result["files"] = files
            async with api.project_events() as events:
                for index in range(2):
                    execution = await api.request("POST", api.project_path("/executions"), json={"file_id": files[f"blocker-{index}"]["id"]})
                    execution_id = execution["id"]
                    owned_ids.append(execution_id)
                    def is_running(event: Event, target: int = execution_id) -> bool:
                        return event.payload.get("execution_id") == target and event.payload.get("status") == "running"
                    running = await events.wait_for(is_running, max_wait_s=args.timeout)
                    result["blockers"].append({"execution_id": execution_id, "running_event": asdict(running)})
                states = [await api.request("GET", api.project_path(f"/executions/{item['execution_id']}")) for item in result["blockers"]]
                result["both_blockers_running"] = all(state["status"] == "running" for state in states)
                if not result["both_blockers_running"]:
                    raise RuntimeError("The two blockers were not running together; contention setup incomplete")

                async def submit(role: str, user: str) -> dict:
                    submitted = time.perf_counter()
                    submitted_at = datetime.now(UTC).isoformat()
                    execution = await api.request("POST", api.project_path("/executions"), user=user, json={"file_id": files[role]["id"]})
                    owned_ids.append(execution["id"])
                    item = {"role": role, "user": user, "execution_id": execution["id"], "submitted_at": submitted_at, "submitted_monotonic_s": submitted}
                    result["contenders"].append(item)
                    return item

                submissions = await asyncio.gather(
                    submit("editor", "bob"), submit("debugger", "dave"), submit("owner", "alice"),
                    return_exceptions=True,
                )
                if any(isinstance(item, BaseException) for item in submissions):
                    raise RuntimeError("A contender submission failed; see submitted contenders and cleanup")
                result["blockers_after_submissions"] = [
                    await api.request("GET", api.project_path(f"/executions/{item['execution_id']}"))
                    for item in result["blockers"]
                ]
                for state in result["blockers_after_submissions"]:
                    if state["status"] != "running":
                        result["contention_issues"].append(f"Blocker {state['id']} was {state['status']} after contender submissions")
                release_started = time.perf_counter()
                result["release_started_monotonic_s"] = release_started
                result["blocker_release"] = []
                for blocker in result["blockers"]:
                    response = await api.request("POST", api.project_path(f"/executions/{blocker['execution_id']}/cancel"))
                    result["blocker_release"].append(response)
                    if response["status"] != "cancelled":
                        result["contention_issues"].append(f"Blocker {blocker['execution_id']} returned {response['status']} when released")

                async def observe(item: dict) -> None:
                    target = item["execution_id"]
                    try:
                        started = await events.wait_for(
                            lambda event: event.payload.get("execution_id") == target
                            and event.payload.get("status") == "running", max_wait_s=args.timeout,
                        )
                        item["running_event"] = asdict(started)
                        item["start_received_monotonic_s"] = started.monotonic_s
                        await events.wait_for(
                            lambda event: event.payload.get("execution_id") == target
                            and event.payload.get("status") in TERMINAL_STATUSES, max_wait_s=args.timeout,
                        )
                        item["api_execution"] = await api.request("GET", api.project_path(f"/executions/{target}"))
                    except Exception as exc:
                        item["event_error"] = str(exc) or "Timed out waiting for execution event"

                await asyncio.gather(*(observe(item) for item in result["contenders"]))
                started = [item for item in result["contenders"] if "start_received_monotonic_s" in item]
                result["start_order"] = [item["role"] for item in sorted(started, key=lambda item: item["start_received_monotonic_s"])]
                result["order_basis"] = "Receipt order of RUNNING events; debugger refers to dave's role, not a debug session"
                result["events"] = [asdict(event) for event in events.events if event.payload.get("execution_id") in owned_ids]
                blocker_ids = {item["execution_id"] for item in result["blockers"]}
                contender_ids = {item["execution_id"] for item in result["contenders"]}
                premature = [event for event in events.events if event.monotonic_s < release_started and (
                    (event.payload.get("execution_id") in blocker_ids and event.payload.get("status") in TERMINAL_STATUSES)
                    or (event.payload.get("execution_id") in contender_ids and event.payload.get("status") == "running")
                )]
                result["premature_events"] = [asdict(event) for event in premature]
                if premature:
                    result["contention_issues"].append("A blocker finished or a contender started before intentional blocker release")
                result["contention_valid"] = not result["contention_issues"]
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            for execution_id in owned_ids:
                try:
                    current = await api.request("GET", api.project_path(f"/executions/{execution_id}"))
                    if current["status"] not in TERMINAL_STATUSES:
                        current = await api.request("POST", api.project_path(f"/executions/{execution_id}/cancel"))
                    result["cleanup"].append({"execution_id": execution_id, "status": current["status"]})
                except Exception as exc:
                    result["cleanup"].append({"execution_id": execution_id, "error": str(exc)})
    result["complete"] = "error" not in result and result["contention_valid"] and len(result.get("start_order", [])) == 3 and not any("event_error" in item for item in result["contenders"])
    return result


if __name__ == "__main__":
    args = argument_parser(__doc__).parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print_json("E3", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
    print_json("E3", report)
    raise SystemExit(0 if report["complete"] else 1)
