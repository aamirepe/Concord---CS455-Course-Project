"""Measure 40 debugger next commands as received by another project member."""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from common import (
    TERMINAL_STATUSES,
    Event,
    PilotClient,
    argument_parser,
    latency_summary,
    print_json,
)

STEP_COUNT = 40


async def run(args) -> dict:
    result: dict = {"requested_steps": STEP_COUNT, "debugger": "dave", "observer": "bob", "steps": [], "cleanup": []}
    samples: list[float] = []
    execution_id = file_id = None
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        try:
            await api.login(("alice", "dave", "bob"))
            await api.discover(args.project)
            result["project_id"] = api.project_id
            state = await api.request("GET", api.project_path("/debug"))
            if state.get("active"):
                raise RuntimeError("Pilot project already has an active debug session")
            file = await api.ensure_file(f"experiment-e5-{uuid4().hex[:12]}.py")
            file_id = file["id"]
            # Straight-line code gives every next command another executable line.
            code = "value = 0\n" + "value += 1\n" * (STEP_COUNT + 5) + "print(value)\n"
            await api.replace_text(file_id, code)
            await api.request("PUT", api.project_path(f"/files/{file_id}/breakpoints"), user="dave", json={"lines": [1]})
            result.update(file_id=file_id, path=file["path"])
            async with api.project_events("bob") as observer:
                execution = await api.request("POST", api.project_path("/executions"), user="dave", json={"file_id": file_id, "debug": True})
                execution_id = execution["id"]
                result["execution_id"] = execution_id

                def pause_or_stop(event: Event) -> bool:
                    return event.payload.get("execution_id") == execution_id and event.payload.get("type") in {"debug_paused", "debug_stopped"}

                initial = await observer.wait_for(pause_or_stop, max_wait_s=args.timeout)
                result["initial_event"] = asdict(initial)
                if initial.payload.get("type") != "debug_paused":
                    raise RuntimeError("Debug session ended before its initial pause")
                for number in range(1, STEP_COUNT + 1):
                    after = len(observer.events)
                    sent_at = datetime.now(UTC).isoformat()
                    sent = time.perf_counter()
                    item: dict = {"step": number, "command": "next", "sent_at": sent_at, "sent_monotonic_s": sent}
                    result["steps"].append(item)
                    response = await api.request("POST", api.project_path(f"/debug/{execution_id}/command"), user="dave", json={"command": "next"})
                    item["api_response"] = response
                    if not response.get("delivered"):
                        raise RuntimeError("Debug command had no worker subscriber")
                    try:
                        event = await observer.wait_for(pause_or_stop, after=after, max_wait_s=args.timeout)
                    except (TimeoutError, RuntimeError) as exc:
                        item["error"] = str(exc) or "Timed out waiting for observer pause"
                        raise RuntimeError("Observer did not receive a pause for the step") from None
                    item["observer_event"] = asdict(event)
                    if event.payload.get("type") != "debug_paused":
                        raise RuntimeError("Debug session stopped before all steps were observed")
                    if event.payload.get("by") != "dave" or event.payload.get("reason") != "step":
                        raise RuntimeError("Observer pause is not attributed to dave's step")
                    latency = (event.monotonic_s - sent) * 1000
                    if latency < 0:
                        raise RuntimeError("Observer pause predates the command")
                    item["latency_ms"] = latency
                    samples.append(latency)
                result["observer_error"] = observer.error
                result["events"] = [asdict(event) for event in observer.events if event.payload.get("execution_id") == execution_id]
        except Exception as exc:
            result["error"] = str(exc) or type(exc).__name__
        finally:
            if execution_id is not None:
                try:
                    response = await api.request("POST", api.project_path(f"/debug/{execution_id}/command"), user="dave", json={"command": "stop"})
                    if not response.get("delivered"):
                        raise RuntimeError("Debug stop command had no worker subscriber")
                    result["cleanup"].append({"stop_response": response})
                except Exception as exc:
                    result["cleanup"].append({"stop_error": str(exc)})
                try:
                    response = await api.request("POST", api.project_path(f"/executions/{execution_id}/cancel"), user="dave")
                    result["cleanup"].append({"cancel_response": response})
                except Exception as exc:
                    result["cleanup"].append({"cancel_error": str(exc)})
            if file_id is not None:
                try:
                    await api.request("PUT", api.project_path(f"/files/{file_id}/breakpoints"), user="dave", json={"lines": []})
                    result["cleanup"].append({"breakpoints_cleared": True})
                except Exception as exc:
                    result["cleanup"].append({"breakpoints_error": str(exc)})
    result["step_latency"] = latency_summary(samples)
    result["missing_samples"] = STEP_COUNT - len(samples)
    result["timing_basis"] = "Local command send to bob's receipt of the next attributed debug_paused event; includes REST and event transport"
    execution_cleaned = any(item.get("cancel_response", {}).get("status") in TERMINAL_STATUSES for item in result["cleanup"])
    breakpoints_cleaned = any(item.get("breakpoints_cleared") for item in result["cleanup"])
    result["complete"] = "error" not in result and len(samples) == STEP_COUNT and execution_cleaned and breakpoints_cleaned
    return result


if __name__ == "__main__":
    args = argument_parser(__doc__).parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print_json("E5", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
    print_json("E5", report)
    raise SystemExit(0 if report["complete"] else 1)
