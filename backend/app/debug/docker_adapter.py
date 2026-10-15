"""Run debugpy's adapter in a sandbox container and talk to it over stdio."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import Any

from app.core.config import get_settings

log = logging.getLogger(__name__)

STDOUT, STDERR = 1, 2


class DockerAdapter:
    """The adapter's stdin/stdout as asyncio streams.

    Docker multiplexes a non-TTY container's stdout and stderr on the attach
    socket (8-byte frame headers); ``reader`` gets just stdout, which is the
    DAP stream. stdin is written raw.
    """

    def __init__(self, client: Any, image: str, workspace: str) -> None:
        self.client = client
        self.image = image
        self.workspace = workspace
        self.container: Any = None
        self.reader = asyncio.StreamReader()
        self.writer: Any = None
        self.stderr: list[str] = []
        self._raw: Any = None
        self._demux_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        settings = get_settings()

        def create() -> tuple[Any, Any]:
            container = self.client.containers.create(
                image=self.image,
                entrypoint=["python", "-m", "debugpy.adapter"],
                command=None,
                stdin_open=True,
                tty=False,
                network_disabled=(settings.sandbox_network == "none"),
                mem_limit=settings.sandbox_mem_limit,
                cpu_quota=settings.sandbox_cpu_quota,
                pids_limit=settings.sandbox_pids_limit,
                read_only=True,
                user="runner",
                working_dir="/workspace",
                volumes={self.workspace: {"bind": "/workspace", "mode": "ro"}},
            )
            # Attach before starting so no output is missed.
            sock = self.client.api.attach_socket(
                container.id, params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            container.start()
            return container, sock

        self.container, sock = await asyncio.to_thread(create)
        raw = getattr(sock, "_sock", sock)
        if not isinstance(raw, socket.socket):
            raise RuntimeError(f"unexpected attach socket type {type(raw).__name__}")
        raw.setblocking(False)
        self._raw = raw
        mux_reader, self.writer = await asyncio.open_connection(sock=raw)
        self._demux_task = asyncio.create_task(self._demux(mux_reader))

    async def _demux(self, mux: asyncio.StreamReader) -> None:
        try:
            while True:
                header = await mux.readexactly(8)
                data = await mux.readexactly(int.from_bytes(header[4:8], "big"))
                if header[0] == STDOUT:
                    self.reader.feed_data(data)
                elif header[0] == STDERR:
                    text = data.decode("utf-8", "replace")
                    self.stderr.append(text)
                    log.debug("debug adapter stderr: %s", text.rstrip())
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.reader.feed_eof()

    async def stop(self) -> None:
        if self.writer is not None:
            with contextlib.suppress(Exception):
                self.writer.close()
        if self._demux_task is not None:
            self._demux_task.cancel()
        container = self.container
        if container is None:
            return

        def kill() -> None:
            with contextlib.suppress(Exception):
                container.kill()
            with contextlib.suppress(Exception):
                container.remove(force=True)

        await asyncio.to_thread(kill)
