"""Run workspaces must be usable by the sandbox's non-root `runner` user.

tempfile dirs are 0700 and owned by the worker (root), so before this every
run in Docker failed with "can't open file '/workspace/main.py': Permission denied".
"""
from __future__ import annotations

import os
import stat

from app.services import sandbox as sandbox_module
from app.services.sandbox import SANDBOX_UID, prepare_workspace


def test_workspace_is_given_to_the_sandbox_user(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sandbox_module.os, "chown", lambda p, u, g: calls.append((p, u, g)))
    prepare_workspace(str(tmp_path))
    assert calls == [(str(tmp_path), SANDBOX_UID, SANDBOX_UID)]


def test_without_root_the_workspace_is_made_readable(tmp_path, monkeypatch):
    def denied(*a):
        raise PermissionError

    monkeypatch.setattr(sandbox_module.os, "chown", denied)
    os.chmod(tmp_path, 0o700)
    prepare_workspace(str(tmp_path))
    assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o755


def test_sandbox_images_use_the_same_uid():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "sandbox"
    for dockerfile in ("Dockerfile", "Dockerfile.cpp", "Dockerfile.nodejs"):
        assert f"{SANDBOX_UID} " in (root / dockerfile).read_text(), dockerfile
