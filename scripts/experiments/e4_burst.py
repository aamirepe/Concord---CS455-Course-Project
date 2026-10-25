"""Measure server-side queue wait for five concurrent runs per round."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime
from uuid import uuid4

from common import TERMINAL_STATUSES, PilotClient, argument_parser, latency_summary, print_json


def queue_wait_ms(execution: dict) -> float:
    if not execution.get("started_at"):
        raise ValueError("Execution has no started_at timestamp")
    queued = datetime.fromisoformat(execution["queued_at"])
    started = datetime.fromisoformat(execution["started_at"])
    value = (started - queued).total_seconds() * 1000
    if not math.isfinite(value) or value < 0:
        raise ValueError("Execution queue timestamps are out of order")
    return value


async def run(args) -> dict:
    if args.rounds < 1:
        raise ValueError("--rounds must be at least one")
    result: dict = {"requested_rounds": args.rounds, "runs_per_round": 5, "rounds": [], "cleanup": []}
    samples: list[float] = []
    owned_ids: list[int] = []
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        try:
            await api.login()
            await api.discover(args.project)
            result["project_id"] = api.project_id
            active = [item["id"] for item in await api.request("GET", api.project_path("/executions")) if item["status"] not in TERMINAL_STATUSES]
            if active:
                raise RuntimeError(f"Pilot project already has active executions: {active}")
            file = await api.ensure_file(f"experiment-e4-{uuid4().hex[:12]}.py")
            await api.replace_text(file["id"], "import time\ntime.sleep(0.2)\nprint('burst')\n")
            result.update(file_id=file["id"], path=file["path"])
            for number in range(1, args.rounds + 1):
                round_result: dict = {"round": number, "runs": []}
                result["rounds"].append(round_result)

                async def submit_and_observe(index: int, output: dict = round_result) -> None:
                    item: dict = {"submission": index, "submitted_at": datetime.now(UTC).isoformat()}
                    output["runs"].append(item)
                    try:
                        execution = await api.request("POST", api.project_path("/executions"), json={"file_id": file["id"]})
                        target = execution["id"]
                        owned_ids.append(target)
                        item["execution_id"] = target
                        deadline = time.perf_counter() + args.timeout
                        while execution["status"] not in TERMINAL_STATUSES:
                            if time.perf_counter() >= deadline:
                                raise TimeoutError("Execution did not finish before the observation deadline")
                            await asyncio.sleep(min(0.1, max(0, deadline - time.perf_counter())))
                            execution = await api.request("GET", api.project_path(f"/executions/{target}"))
                        item["api_execution"] = execution
                        item["queue_wait_ms"] = queue_wait_ms(execution)
                        samples.append(item["queue_wait_ms"])
                    except Exception as exc:
                        item["error"] = str(exc) or type(exc).__name__

                await asyncio.gather(*(submit_and_observe(index) for index in range(1, 6)))
                if any("error" in item for item in round_result["runs"]):
                    break
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            for target in owned_ids:
                try:
                    current = await api.request("GET", api.project_path(f"/executions/{target}"))
                    if current["status"] not in TERMINAL_STATUSES:
                        current = await api.request("POST", api.project_path(f"/executions/{target}/cancel"))
                    result["cleanup"].append({"execution_id": target, "status": current["status"]})
                except Exception as exc:
                    result["cleanup"].append({"execution_id": target, "error": str(exc)})
    result["queue_wait"] = latency_summary(samples)
    result["missing_samples"] = args.rounds * 5 - len(samples)
    result["timing_basis"] = "Server started_at minus queued_at; HTTP polling detects completion only"
    result["complete"] = "error" not in result and result["missing_samples"] == 0 and not any("error" in item for item in result["cleanup"])
    return result


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be at least one")
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print_json("E4", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
    print_json("E4", report)
    raise SystemExit(0 if report["complete"] else 1)
