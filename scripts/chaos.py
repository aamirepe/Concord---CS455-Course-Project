"""Kill the claiming worker and verify durable recovery and sandbox cleanup.

Run only against a disposable local stack. Select its Compose project explicitly.
The chosen file is temporarily replaced with a long program, then restored. The
worker is restarted even when verification fails. JSON reports actual timings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time

import docker
import httpx
from docker.errors import NotFound
from pilot import Api, set_file_text


def worker_for_execution(client, project: str, worker_id: str):
    workers = client.containers.list(filters={"label": [
        f"com.docker.compose.project={project}", "com.docker.compose.service=worker",
    ]})
    matches = [worker for worker in workers
               if worker_id.startswith(worker.attrs["Config"]["Hostname"] + "-")]
    if len(matches) != 1:
        raise RuntimeError("Could not uniquely identify the claiming worker in the selected Compose project")
    return matches[0]


def container_exists(client, name: str) -> bool:
    try:
        client.containers.get(name)
        return True
    except NotFound:
        return False


async def exercise(args) -> dict:
    api = Api(args.api, args.ws or args.api.replace("http", "ws", 1))
    api.tokens["chaos"] = args.token
    client = docker.from_env()
    worker = None
    killed = False
    original = None
    run = None
    route = f"/projects/{args.project_id}/executions"
    try:
        files_response = await api.http.get(f"/projects/{args.project_id}/files", headers=api.h("chaos"))
        files_response.raise_for_status()
        if not any(file["id"] == args.file_id and file["language"] == "python" for file in files_response.json()):
            raise ValueError("Choose a Python file in the selected project")
        original = await api.text(args.project_id, "chaos", args.file_id)
        await set_file_text(api, args.project_id, args.file_id, "chaos", "import time\nwhile True: time.sleep(1)\n")
        response = await api.http.post(route, json={"file_id": args.file_id}, headers=api.h("chaos"))
        response.raise_for_status()
        run = response.json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = await api.http.get(f"{route}/{run['id']}", headers=api.h("chaos"))
            response.raise_for_status()
            run = response.json()
            if run["status"] == "running" and container_exists(client, f"concord-exec-{run['id']}"):
                break
            if run["status"] in {"failed", "completed", "cancelled", "timeout"}:
                raise RuntimeError(f"Run ended before worker kill: {run['status']}")
            await asyncio.sleep(0.05)
        else:
            raise TimeoutError("Run did not reach RUNNING within 30 seconds")
        worker = worker_for_execution(client, args.compose_project, run["worker_id"])
        # The container ID is obtained from Docker after checking its Compose labels.
        await asyncio.to_thread(subprocess.run, ["docker", "kill", worker.id], check=True, capture_output=True)
        killed = True
        killed_at = time.monotonic()
        deadline = killed_at + args.timeout
        while time.monotonic() < deadline:
            response = await api.http.get(f"{route}/{run['id']}", headers=api.h("chaos"))
            response.raise_for_status()
            run = response.json()
            if run["status"] in {"failed", "completed", "cancelled", "timeout"}:
                break
            await asyncio.sleep(0.5)
        if run["status"] != "failed":
            raise RuntimeError(f"Expected sweeper failure, got {run['status']}")
        recovery_seconds = time.monotonic() - killed_at
        response = await api.http.get("/metrics/events/recent?limit=10000", headers=api.h("chaos"))
        response.raise_for_status()
        events = [event for event in response.json()["events"] if event["execution_id"] == run["id"]]
        if not any(event["event_type"] == "failed" and event["payload"].get("by") == "sweeper" for event in events):
            raise AssertionError("Missing FAILED audit event attributed to sweeper")
        # The sweeper commits its failure before performing best-effort Docker cleanup.
        cleanup_deadline = time.monotonic() + 2
        while time.monotonic() < cleanup_deadline and any(  # noqa: ASYNC110 - poll an external Docker daemon
            container_exists(client, f"{prefix}-{run['id']}") for prefix in ("concord-exec", "concord-build")
        ):
            await asyncio.sleep(0.05)
        remaining = [prefix for prefix in ("concord-exec", "concord-build")
                     if container_exists(client, f"{prefix}-{run['id']}")]
        if remaining:
            raise AssertionError(f"Sandbox containers left behind: {remaining}")
        return {"execution_id": run["id"], "status": run["status"],
                "recovery_seconds": round(recovery_seconds, 3), "sweeper_event": True,
                "containers_remaining": [], "passed": True}
    finally:
        try:
            if run is not None and run["status"] not in {"failed", "completed", "cancelled", "timeout"}:
                await api.http.post(f"{route}/{run['id']}/cancel", headers=api.h("chaos"))
        finally:
            try:
                if original is not None:
                    await set_file_text(api, args.project_id, args.file_id, "chaos", original)
            finally:
                try:
                    if worker is not None and killed:
                        await asyncio.to_thread(worker.start)
                finally:
                    client.close()
                    await api.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--ws")
    parser.add_argument("--token", default=os.environ.get("CONCORD_TOKEN"))
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--file-id", type=int, required=True, help="A disposable Python file in the project")
    parser.add_argument("--timeout", type=float, default=420)
    args = parser.parse_args()
    if not args.token:
        parser.error("set CONCORD_TOKEN or pass --token")
    try:
        result = asyncio.run(exercise(args))
    except (AssertionError, ValueError, RuntimeError, TimeoutError, httpx.HTTPError, docker.errors.DockerException, subprocess.CalledProcessError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
