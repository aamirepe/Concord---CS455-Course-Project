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
