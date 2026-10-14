"""Common role and status enums."""
from __future__ import annotations

import enum


class SessionStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class Role(enum.StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    DEBUGGER = "debugger"
    OWNER = "owner"


ROLE_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.DEBUGGER: 2, Role.OWNER: 3}
