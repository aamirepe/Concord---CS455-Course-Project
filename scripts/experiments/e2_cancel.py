"""Cancel a loop after RUNNING and observe its container independently."""
from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from common import PilotClient, argument_parser, print_json


class DockerWatch:
    def __init__(self, marker: str, image: str) -> None:
        self.marker = marker
        self.image = image
        self.executable = shutil.which("docker")
        self.stop = asyncio.Event()
        self.container_id: str | None = None
        self.last_alive: float | None = None
        self.first_absent: float | None = None
        self.error: str | None = None
        self.baseline: set[str] = set()

    async def command(self, *arguments: str) -> str:
        if self.executable is None:
            raise RuntimeError("Docker CLI unavailable")
        process = await asyncio.create_subprocess_exec(
            self.executable, *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(3):
                stdout, _ = await process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"Docker {arguments[0]} returned exit code {process.returncode}")
            return stdout.decode()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def ids(self) -> set[str]:
        output = await self.command("ps", "--no-trunc", "--filter", f"ancestor={self.image}", "--format", "{{.ID}}")
        return set(output.split())

    async def prepare(self) -> None:
        try:
            self.baseline = await self.ids()
        except (OSError, RuntimeError, TimeoutError) as exc:
            self.error = str(exc)

    async def observe(self) -> None:
        if self.error:
            return
        try:
            while not self.stop.is_set():
                poll_started = time.perf_counter()
                current = await self.ids()
                poll_finished = time.perf_counter()
                if self.container_id is None:
                    for candidate in sorted(current - self.baseline):
                        logs = await self.command("logs", "--tail", "20", candidate)
                        if self.marker in logs.splitlines():
                            self.container_id = candidate
                            break
                if self.container_id is not None:
                    if self.container_id not in current:
                        self.first_absent = poll_finished
                        return
                    self.last_alive = poll_started
                await asyncio.sleep(0.1)
        except (OSError, RuntimeError, TimeoutError) as exc:
            self.error = str(exc)

    def report(self, cancel_at: float | None) -> dict:
        before_cancel = self.first_absent is not None and cancel_at is not None and self.first_absent < cancel_at
        lower_ms = upper_ms = None
        if cancel_at is not None and self.first_absent is not None and not before_cancel:
            upper_ms = (self.first_absent - cancel_at) * 1000
            if self.last_alive is not None:
                lower_ms = max(0, (self.last_alive - cancel_at) * 1000)
        measured = self.container_id is not None and upper_ms is not None and self.error is None
        return {
            "available": measured, "container_id": self.container_id,
            "attribution": "unique stdout marker" if self.container_id else None,
            "last_seen_alive_after_cancel_ms": lower_ms if measured else None,
            "first_absent_after_cancel_ms": upper_ms if measured else None,
            "ended_before_cancel": before_cancel,
            "disappearance_not_observed": self.container_id is not None and self.first_absent is None,
            "error": self.error,
            "resolution": "100 ms polling plus Docker command duration; interval bounds, not an exact stop time",
        }


async def run(args) -> dict:
    marker = f"concord-e2-{uuid4().hex}"
    watch = DockerWatch(marker, args.image)
    result: dict = {"cancel_delay_target_s": 1.5}
    execution_id = None
    cancel_at = None
    cancel_confirmed = False
    async with PilotClient(args.api, args.ws, args.timeout) as api:
        await watch.prepare()
        monitor = asyncio.create_task(watch.observe())
        try:
            await api.login()
            await api.discover(args.project)
            file = await api.ensure_file(f"experiment-e2-{uuid4().hex[:12]}.py")
            await api.replace_text(file["id"], f"import time\nprint({marker!r}, flush=True)\nwhile True:\n    time.sleep(0.1)\n")
            result.update(project_id=api.project_id, file_id=file["id"], path=file["path"])
            async with api.project_events() as events:
                execution = await api.request("POST", api.project_path("/executions"), json={"file_id": file["id"]})
                execution_id = execution["id"]
                result["execution_id"] = execution_id
                running = await events.wait_for(
                    lambda event: event.payload.get("execution_id") == execution_id
                    and event.payload.get("status") == "running", max_wait_s=args.timeout,
                )
                result["running_event"] = asdict(running)
                await asyncio.sleep(max(0, running.monotonic_s + 1.5 - time.perf_counter()))
                cancel_at = time.perf_counter()
                result["cancel_requested_at"] = datetime.now(UTC).isoformat()
                response = await api.http.post(api.project_path(f"/executions/{execution_id}/cancel"), headers=api.headers("alice"))
                result["actual_delay_after_running_s"] = cancel_at - running.monotonic_s
                try:
                    body = response.json()
                except ValueError:
                    body = response.text[:1000]
                result["cancel_api"] = {"status_code": response.status_code, "body": body}
                cancel_confirmed = response.status_code == 200
                try:
                    await asyncio.wait_for(asyncio.shield(monitor), timeout=args.timeout)
                except TimeoutError:
                    result["container_observation_timed_out"] = True
                result["api_execution"] = await api.request("GET", api.project_path(f"/executions/{execution_id}"))
                result["events"] = [asdict(event) for event in events.events if event.payload.get("execution_id") == execution_id]
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if execution_id is not None and not cancel_confirmed:
                try:
                    await api.request("POST", api.project_path(f"/executions/{execution_id}/cancel"))
                    result["cleanup_cancel_requested"] = True
                except Exception as exc:
                    result["cleanup_error"] = str(exc)
            watch.stop.set()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
    result["container"] = watch.report(cancel_at)
    result["complete"] = "error" not in result and result.get("cancel_api", {}).get("status_code") == 200 and result["container"]["available"]
    return result


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--image", default="concord-sandbox:latest", help="Python sandbox image used by the Docker daemon")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception as exc:
        print_json("E2", {"ok": False, "error": str(exc)})
        raise SystemExit(1) from None
    print_json("E2", report)
    raise SystemExit(0 if report["complete"] else 1)
