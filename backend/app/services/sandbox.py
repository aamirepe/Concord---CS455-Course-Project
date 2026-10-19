"""Sandboxed code execution via the Docker SDK.

Each execution produces one short-lived container with:
  - --network none (no internet)
  - --memory 128m
  - --cpus 0.5
  - --pids-limit 64
  - read-only root filesystem
  - workspace mounted read-only
  - cap-drop ALL (no new privileges by default)
  - wall-clock timeout enforced by the worker, not the container

If a script hangs forever, the worker calls container.kill() after the timeout
elapses. The container does NOT trust the code to exit on its own.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from dataclasses import dataclass

import docker
from docker.errors import APIError, ContainerError, ImageNotFound, NotFound

from app.core.config import get_settings
from app.runtime.registry import RuntimeConfig, get_runtime_config

log = logging.getLogger(__name__)

# The sandbox images all create their `runner` user with this fixed uid, so the
# worker (root) can hand it a workspace it may read and, for compile steps, write.
SANDBOX_UID = 10001


def prepare_workspace(path: str) -> None:
    """Give the sandbox user a per-run workspace. ``tempfile`` dirs are 0700 and
    owned by the worker, so without this every run fails with Permission denied."""
    try:
        os.chown(path, SANDBOX_UID, SANDBOX_UID)
    except PermissionError:
        # Not root, i.e. outside the worker container: let the sandbox at least
        # read the code. Compiled languages will fail to write their binary.
        log.warning("cannot chown %s to the sandbox user; workspace is read-only for it", path)
        os.chmod(path, 0o755)  # noqa: S103  # nosec B103 -- per-run code dir another uid must read


STDIN_FILE = ".stdin"
MAX_STDIN_BYTES = 64 * 1024


def write_stdin(workspace: str, stdin: str | None) -> None:
    """Put the program's input next to its code (empty file: the program reads EOF)."""
    with open(os.path.join(workspace, STDIN_FILE), "w", encoding="utf-8") as f:
        f.write((stdin or "")[:MAX_STDIN_BYTES])


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class SandboxRunner:
    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def run(
        self,
        code: str,
        language: str = "python",
        timeout_seconds: int | None = None,
        debug: bool = False,
        workspace_dir: str | None = None,
        stdin: str | None = None,
        execution_id: int | None = None,
    ) -> RunResult:
        """Run `code` in a fresh sandbox container. Returns captured output.

        ``stdin`` is the program's input; without it the program reads EOF
        (``input()`` raises EOFError) rather than waiting forever.

        Note: Build step is now handled by the worker to allow status tracking.
        """
        settings = get_settings()
        timeout = timeout_seconds or settings.sandbox_timeout_seconds

        # Get runtime configuration for the language
        try:
            runtime_config = get_runtime_config(language)
        except ValueError as e:
            log.error("Failed to get runtime config for language %s: %s", language, e)
            # Fallback to Python for backward compatibility
            runtime_config = get_runtime_config("python")

        if workspace_dir:
            tmpdir = workspace_dir
        else:
            with tempfile.TemporaryDirectory(prefix="concord-exec-") as tmpdir:
                # Callers without a workspace pass just the code: it becomes the
                # runtime's main file (e.g. main.py), which the entrypoint runs.
                main_file = os.path.join(tmpdir, f"main{runtime_config.file_extension}")
                with open(main_file, "w", encoding="utf-8") as f:
                    f.write(code)
                write_stdin(tmpdir, stdin)
                prepare_workspace(tmpdir)
                return self._run_with_dir(tmpdir, code, language, timeout, debug, runtime_config, execution_id)

        write_stdin(tmpdir, stdin)
        return self._run_with_dir(tmpdir, code, language, timeout, debug, runtime_config, execution_id)

    def _run_with_dir(
        self,
        tmpdir: str,
        code: str,
        language: str,
        timeout: int,
        debug: bool,
        runtime_config: RuntimeConfig,
        execution_id: int | None = None,
    ) -> RunResult:
        # Prepare entrypoint based on debug mode. ``override`` replaces the
        # image's own ENTRYPOINT (the Python image has one), which would
        # otherwise receive our command as mere arguments.
        override: list[str] | None = None
        if debug and runtime_config.debug_config:
            entrypoint = runtime_config.debug_config["entrypoint"]
            ports = runtime_config.debug_config.get("ports")
        else:
            entrypoint = runtime_config.entrypoint
            ports = None
            if os.path.exists(os.path.join(tmpdir, STDIN_FILE)):
                # The input is passed as a file, never through the shell's
                # command line, so nothing in it is interpreted.
                override = ["sh", "-c", f'exec "$@" < /workspace/{STDIN_FILE}', "sh", *entrypoint]

        started = time.monotonic()
        try:
            container = self.client.containers.run(
                **({"name": f"concord-exec-{execution_id}"} if execution_id is not None else {}),
                image=runtime_config.image,
                entrypoint=override,
                command=None if override else entrypoint,
                network_disabled=(get_settings().sandbox_network == "none"),
                mem_limit=get_settings().sandbox_mem_limit,
                cpu_quota=get_settings().sandbox_cpu_quota,
                pids_limit=get_settings().sandbox_pids_limit,
                read_only=True,
                user="runner",
                working_dir="/workspace",
                volumes={tmpdir: {"bind": "/workspace", "mode": "ro"}},
                ports=ports,
                detach=True,
                remove=False,
                stdout=True,
                stderr=True,
            )
        except ImageNotFound as exc:
            log.error("Sandbox image %s not built; run `docker build -t %s ./sandbox`",
                      runtime_config.image, runtime_config.image)
            raise RuntimeError(f"sandbox image missing: {runtime_config.image}") from exc
        except APIError as exc:
            log.exception("docker run failed")
            raise RuntimeError(f"docker run failed: {exc}") from exc

        timed_out = False
        try:
            result = container.wait(timeout=timeout)
            exit_code = int(result.get("StatusCode", 1))
        except Exception:
            timed_out = True
            try:
                container.kill()
            except APIError:
                log.warning("kill after timeout failed", exc_info=True)
            try:
                result = container.wait(timeout=2)
                exit_code = int(result.get("StatusCode", 137))
            except Exception:
                exit_code = 124

        stdout_bytes = b""
        stderr_bytes = b""
        try:
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
        except APIError:
            log.warning("log capture failed", exc_info=True)

        duration = time.monotonic() - started

        try:
            container.remove(force=True)
        except APIError:
            pass

        return RunResult(
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            duration_seconds=duration,
        )

    def build(self, image: str, workspace_dir: str, build_command: list[str], workdir: str,
              *, execution_id: int | None = None) -> None:
        """Execute a build command in the sandbox.

        Args:
            image: Docker image to use for building
            workspace_dir: Host directory containing source code
            build_command: Build command to execute
            workdir: Working directory inside container for build
        """
        try:
            # Run build in a container with read-write access to workspace
            build_container = self.client.containers.run(
                **({"name": f"concord-build-{execution_id}"} if execution_id is not None else {}),
                image=image,
                command=build_command,
                network_disabled=(get_settings().sandbox_network == "none"),
                mem_limit=get_settings().sandbox_mem_limit,
                cpu_quota=get_settings().sandbox_cpu_quota,
                pids_limit=get_settings().sandbox_pids_limit,
                read_only=False,
                user="runner",
                working_dir=workdir,
                volumes={workspace_dir: {"bind": "/workspace", "mode": "rw"}},
                detach=True,
                remove=False,
                stdout=True,
                stderr=True,
            )

            # Wait for build to complete
            result = build_container.wait()
            exit_code = int(result.get("StatusCode", 1))

            if exit_code != 0:
                # Build failed - get logs for error reporting
                stdout_bytes = build_container.logs(stdout=True, stderr=False)
                stderr_bytes = build_container.logs(stdout=False, stderr=True)
                stdout_str = stdout_bytes.decode("utf-8", errors="replace")
                stderr_str = stderr_bytes.decode("utf-8", errors="replace")
                build_container.remove(force=True)
                raise RuntimeError(
                    f"Build failed with exit code {exit_code}. "
                    f"stdout: {stdout_str}\nstderr: {stderr_str}"
                )

            build_container.remove(force=True)
        except (APIError, ContainerError) as exc:
            log.exception("Build container failed")
            raise RuntimeError(f"Build failed: {exc}") from exc

    def kill_execution(self, execution_id: int) -> None:
        """Remove run, build and debugger containers, including after a worker dies."""
        for prefix in ("concord-exec", "concord-build"):
            try:
                container = self.client.containers.get(f"{prefix}-{int(execution_id)}")
                container.remove(force=True)
            except NotFound:
                continue

    def healthcheck(self) -> bool:
        """Cheap check that the Docker daemon is reachable from the worker."""
        try:
            self.client.ping()
            return True
        except APIError:
            return False

    def validate_code(self, code: str, language: str = "python") -> bool:
        """Validate that code is syntactically correct for the given language.

        Args:
            code: Source code to validate
            language: Programming language (python, nodejs, cpp)

        Returns:
            True if code is syntactically valid, False otherwise
        """
        from app.core.config import get_settings
        settings = get_settings()

        # Check code size limit to prevent DoS via large inputs
        if len(code) > settings.ai_assistant_validation_max_code_size:
            log.warning(f"Code validation rejected: exceeds max size {settings.ai_assistant_validation_max_code_size} chars")
            return False

        # For Python, we can use ast.parse which is faster and doesn't require sandbox
        if language.lower() == "python":
            try:
                import ast
                ast.parse(code)
                return True
            except SyntaxError:
                return False
            except Exception as e:
                log.error(f"Unexpected error during Python AST parsing: {e}")
                return False

        # For other languages, we use the sandbox to run syntax check commands
        settings = get_settings()

        try:
            runtime_config = get_runtime_config(language)
        except ValueError as e:
            log.error("Failed to get runtime config for language %s: %s", language, e)
            # Fallback to Python for backward compatibility
            runtime_config = get_runtime_config("python")

        with tempfile.TemporaryDirectory(prefix="concord-validate-") as tmpdir:
            # Determine file name based on language
            file_name = f"main{runtime_config.file_extension}"
            main_path = os.path.join(tmpdir, file_name)
            with open(main_path, "w", encoding="utf-8") as f:
                f.write(code)

            # Prepare validation command based on language
            if language.lower() == "nodejs":
                # Use node --check for JavaScript validation
                validate_command = ["node", "--check", "/workspace/main.js"]
            elif language.lower() == "cpp":
                # Use g++ -fsyntax-only for C++ validation
                validate_command = ["g++", "-fsyntax-only", "-std=c++17", "/workspace/main.cpp"]
            else:
                # Default to Python validation using ast (already handled above, but just in case)
                try:
                    import ast
                except ImportError:
                    return False
                try:
                    ast.parse(code)
                    return True
                except SyntaxError:
                    return False

            try:
                # Run validation in a container with read-only access
                container = self.client.containers.run(
                    image=runtime_config.image,
                    command=validate_command,
                    network_disabled=(settings.sandbox_network == "none"),
                    mem_limit=settings.sandbox_mem_limit,
                    cpu_quota=settings.sandbox_cpu_quota,
                    pids_limit=settings.sandbox_pids_limit,
                    read_only=True,
                    user="runner",
                    working_dir="/workspace",
                    volumes={tmpdir: {"bind": "/workspace", "mode": "ro"}},
                    detach=True,
                    remove=False,
                    stdout=True,
                    stderr=True,
                )

                # Wait for validation to complete (short timeout for syntax check)
                try:
                    result = container.wait(timeout=10)
                    exit_code = int(result.get("StatusCode", 1))
                except Exception:  # timeout
                    try:
                        container.kill()
                    except APIError:
                        pass
                    exit_code = 124  # timeout exit code

                # Remove container
                try:
                    container.remove(force=True)
                except APIError:
                    pass

                # Exit code 0 means validation passed
                return exit_code == 0

            except Exception as e:
                log.error(f"Error during code validation for {language}: {e}")
                return False


# Singleton — workers hold a single SandboxRunner.
sandbox = SandboxRunner()
