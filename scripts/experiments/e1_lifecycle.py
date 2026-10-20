"""Measure Python and C++ execution status arrival times."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from common import TERMINAL_STATUSES, Event, PilotClient, argument_parser, print_json


async def run(args) -> dict:
    programs = {
        "python": ("py", "print('lifecycle')\n"),
        "cpp": ("cpp", '#include <iostream>\nint main() { std::cout << "lifecycle\\n"; }\n'),
    }
    result: dict = {"runs": []}
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        await api.login()
        await api.discover(args.project)
        result["project_id"] = api.project_id
        for language, (extension, code) in programs.items():
            item: dict = {"language": language, "status_events": [], "missing_terminal_event": True}
            result["runs"].append(item)
            try:
                file = await api.ensure_file(f"experiment-e1-{uuid4().hex[:12]}.{extension}")
                item.update(file_id=file["id"], path=file["path"])
                await api.replace_text(file["id"], code)
                async with api.project_events() as events:
                    item["submitted_at"] = datetime.now(UTC).isoformat()
                    submitted = time.perf_counter()
                    execution = await api.request("POST", api.project_path("/executions"), json={"file_id": file["id"]})
                    execution_id = execution["id"]
                    item["execution_id"] = execution_id
                    def terminal(event: Event, target: int = execution_id) -> bool:
                        return event.payload.get("execution_id") == target and event.payload.get("status") in TERMINAL_STATUSES
                    try:
                        await events.wait_for(terminal, max_wait_s=args.timeout)
                        item["missing_terminal_event"] = False
                    except (TimeoutError, RuntimeError):
                        pass
                    item["event_reader_error"] = events.error
                    for event in events.events:
                        if event.payload.get("execution_id") != execution_id:
                            continue
                        status = event.payload.get("status")
                        if event.payload.get("op") == "EXECUTION_QUEUED":
                            status = "queued"
                        if status is not None:
                            item["status_events"].append({**asdict(event), "status": status, "after_submit_ms": (event.monotonic_s - submitted) * 1000})
                    item["api_execution"] = await api.request("GET", api.project_path(f"/executions/{execution_id}"))
            except Exception as exc:
                item["error"] = str(exc)
    result["complete"] = all(not item["missing_terminal_event"] and "error" not in item for item in result["runs"])
    return result


if __name__ == "__main__":
    args = argument_parser(__doc__).parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print_json("E1", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
    print_json("E1", report)
    raise SystemExit(0 if report["complete"] else 1)
