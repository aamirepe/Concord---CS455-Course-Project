"""Sandbox security tests.

Each test launches the actual sandbox container with the limits defined in
Settings and asserts that malicious payloads are contained.

These tests require Docker to be available on the host; the worker container
has the Docker socket mounted for this reason.
"""
from __future__ import annotations

import os
import socket
import time

import pytest

from app.services.sandbox import sandbox

# We only run sandbox tests when explicitly enabled (DOCKER_TESTS=1) because they
# require Docker and take a few seconds each. CI runs them; local unit tests skip.
DOCKER_TESTS = os.environ.get("DOCKER_TESTS") == "1"


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_sandbox_image_exists_or_build_needed():
    """If the image isn't built yet, the sandbox surfaces a clear error."""
    res = sandbox.run("print('ok')", timeout_seconds=5)
    assert res.exit_code == 0
    assert "ok" in res.stdout


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_sandbox_blocks_network():
    """Network is disabled. Attempt to reach the internet — should fail quickly."""
    res = sandbox.run(
        "import socket; socket.create_connection(('1.1.1.1', 53), timeout=2)",
        timeout_seconds=5,
    )
    assert res.exit_code != 0
    assert res.timed_out is False


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_sandbox_kills_long_running_code():
    """Wall-clock timeout is enforced by the worker, not the container."""
    res = sandbox.run("import time; time.sleep(60)", timeout_seconds=2)
    assert res.timed_out is True


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_sandbox_blocks_filesystem_write():
    """Read-only root + read-only workspace. Writes must fail."""
    res = sandbox.run("open('/etc/evil', 'w').write('pwned')", timeout_seconds=5)
    assert res.exit_code != 0


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_sandbox_contains_fork_bomb():
    """pid_limit=64 should kill a fork bomb before it can DOS the host."""
    res = sandbox.run(
        "import os, sys\nwhile True: os.fork()",
        timeout_seconds=3,
    )
    # Either it times out (we kill it) or it dies from pids_limit
    assert res.exit_code != 0 or res.timed_out is True


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_program_reads_its_input():
    result = sandbox.run("a = int(input())\nb = int(input())\nprint(a + b)\n", stdin="3\n4\n")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "7"


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_no_input_means_eof_not_a_hang():
    result = sandbox.run("try:\n    input()\nexcept EOFError:\n    print('eof')\n", timeout_seconds=10)
    assert result.stdout.strip() == "eof"
    assert not result.timed_out


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
@pytest.mark.parametrize("language, code", [
    ("python", "print(open('/proc/self/status').read())"),
    ("nodejs", "console.log(require('fs').readFileSync('/proc/self/status', 'utf8'))"),
])
def test_process_has_no_capabilities_or_new_privileges(language, code):
    result = sandbox.run(code, language=language, timeout_seconds=5)
    assert result.exit_code == 0, result.stderr
    fields = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)
    assert fields["CapEff"].strip() == "0000000000000000"
    assert fields["NoNewPrivs"].strip() == "1"


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
def test_cpp_compiler_and_binary_work_without_capabilities(tmp_path):
    import os

    code = '#include <fstream>\n#include <iostream>\nint main() { std::ifstream s("/proc/self/status"); std::cout << s.rdbuf(); }\n'
    (tmp_path / "main.cpp").write_text(code)
    # A disposable compiler workspace must be writable by the container uid.
    os.chmod(tmp_path, 0o777)  # noqa: S103
    sandbox.build("concord-sandbox-cpp:latest", str(tmp_path),
                  ["g++", "-std=c++17", "-o", "/workspace/main.out", "/workspace/main.cpp"], "/workspace")
    result = sandbox.run(code, language="cpp", workspace_dir=str(tmp_path))
    assert result.exit_code == 0, result.stderr
    fields = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)
    assert fields["CapEff"].strip() == "0000000000000000"
    assert fields["NoNewPrivs"].strip() == "1"


@pytest.mark.skipif(not DOCKER_TESTS, reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
async def test_debug_adapter_has_no_capabilities_or_new_privileges(tmp_path):
    from app.debug.docker_adapter import DockerAdapter
    from app.services.sandbox import prepare_workspace

    prepare_workspace(str(tmp_path))
    adapter = DockerAdapter(sandbox.client, "concord-sandbox:latest", str(tmp_path))
    try:
        await adapter.start()
        adapter.container.reload()
        assert adapter.container.attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in adapter.container.attrs["HostConfig"]["SecurityOpt"]
        result = adapter.container.exec_run(["cat", "/proc/1/status"])
        assert result.exit_code == 0
        fields = dict(line.split(":", 1) for line in result.output.decode().splitlines() if ":" in line)
        assert fields["CapEff"].strip() == "0000000000000000"
        assert fields["NoNewPrivs"].strip() == "1"
    finally:
        await adapter.stop()


def test_every_sandbox_run_build_and_validation_request_is_hardened(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from app.services.sandbox import SandboxRunner

    runner = SandboxRunner()
    client = Mock()
    container = client.containers.run.return_value
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b""
    runner._client = client
    monkeypatch.setattr("app.services.sandbox.prepare_workspace", lambda _: None)
    runner.run("print('ok')")
    runner.build("image", str(tmp_path), ["true"], "/workspace")
    assert runner.validate_code("console.log('ok')", "nodejs")
    assert runner.validate_code("int main() {}", "cpp")
    assert client.containers.run.call_count == 4
    for call in client.containers.run.call_args_list:
        assert call.kwargs["cap_drop"] == ["ALL"]
        assert call.kwargs["security_opt"] == ["no-new-privileges"]
