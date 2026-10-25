"""Cancellation removes the real container in both compilation and execution."""
import os

import pytest

from worker.tests.test_worker_execution import _cancel_real_execution


@pytest.mark.skipif(os.environ.get("DOCKER_TESTS") != "1", reason="set DOCKER_TESTS=1 to run")
@pytest.mark.asyncio
@pytest.mark.parametrize("language,phase", [("cpp", "compiling"), ("cpp", "running")])
async def test_cancel_in_each_phase_leaves_no_container(db, no_op_publish, monkeypatch, language, phase):
    await _cancel_real_execution(db, no_op_publish, monkeypatch, language=language, phase=phase)
