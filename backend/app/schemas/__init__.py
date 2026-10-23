"""Pydantic schemas (request/response models)."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator


class _ProjectPathModel(BaseModel):
    @field_validator("path", "name", check_fields=False)
    @classmethod
    def _validate_project_path(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value

        label = "folder name" if info.field_name == "name" else "file path"
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError(f"{label} must not contain control characters")
        if "\\" in value:
            raise ValueError(f"{label} must use forward slashes, not backslashes")
        if value.startswith("/") or (
            len(value) >= 3 and value[0].isalpha() and value[1:3] == ":/"
        ):
            raise ValueError(f"{label} must be relative")
        if info.field_name == "name" and "/" in value:
            raise ValueError("folder name must not contain slashes")

        segments = value.split("/")
        if "" in segments:
            raise ValueError(f"{label} must not contain empty segments")
        if "." in segments:
            raise ValueError(f"{label} must not contain '.' segments")
        if ".." in segments:
            raise ValueError(f"{label} must not contain '..' segments")
        return value

# --- Auth ---


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: EmailStr
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105, RUF100 -- fixed OAuth2 token-type label, not a credential
    user: UserOut

# --- Project ---


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    name: str
    created_at: datetime

# --- Folder ---


class FolderCreate(_ProjectPathModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None


class FolderUpdate(_ProjectPathModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    parent_folder_id: int | None
    name: str
    created_at: datetime

# --- Session ---


class SessionCreate(BaseModel):
    """Open a new collab session for a project. Creator becomes the owner."""


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    created_by: int | None
    status: str
    created_at: datetime


class SessionMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    session_id: int
    user_id: int
    role: str
    joined_at: datetime


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user: UserOut
    role: str


class JoinSessionOut(BaseModel):
    session: SessionOut
    role: str


class RoleUpdate(BaseModel):
    role: Literal["viewer", "editor", "debugger", "owner"]


class MemberAdd(BaseModel):
    # A numeric user ID or a username (the members dialog accepts either).
    user_id: int | str
    role: Literal["viewer", "editor", "debugger", "owner"] = "viewer"

    @field_validator("role", mode="before")
    @classmethod
    def _lowercase_role(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v


class JoinRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    user: UserOut
    status: str
    created_at: datetime
    decided_at: datetime | None = None


class JoinRequestDecision(BaseModel):
    # Ownership is transferred explicitly via the members endpoint, never by approving a request.
    role: Literal["viewer", "editor", "debugger"] = "viewer"

# --- File ---


class FileCreate(_ProjectPathModel):
    path: str = Field(min_length=1, max_length=512)
    folder_id: int | None = None


class FileUpdate(_ProjectPathModel):
    path: str | None = Field(default=None, min_length=1, max_length=512)
    folder_id: int | None = None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    folder_id: int | None
    path: str
    language: str | None = "python"
    created_at: datetime


class FileSaveOut(BaseModel):
    file_id: int
    version: int | None  # None: the file is empty and was never saved
    created: bool  # False: nothing changed since the last saved version
    saved_at: datetime | None


class UserRef(BaseModel):
    id: int
    username: str


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    saved_by: UserRef | None  # None: an autosave


class VersionTextOut(BaseModel):
    version: int
    created_at: datetime
    text: str


class VersionRestoreOut(BaseModel):
    restored_from: int
    version: int | None  # the new version holding the restored text
    changed: bool  # False: the file already had that text


class AuthorActivityOut(BaseModel):
    user_id: int
    username: str
    lines_added: int
    lines_removed: int
    last_changed_at: datetime


class FileActivityOut(BaseModel):
    file_id: int
    path: str
    lines_added: int
    lines_removed: int
    last_changed_at: datetime
    authors: list[AuthorActivityOut]  # most recent first

# --- Snapshot ---


class SnapshotCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    label: str
    created_at: datetime
    created_by_username: str | None = None
    file_count: int = 0


class SnapshotRestoreOut(BaseModel):
    snapshot_id: int
    restored_files: int  # files whose text actually changed
    safety_snapshot_id: int  # the state just before the restore, to undo it

# --- Execution ---


class RunRequest(BaseModel):
    file_id: int
    # The program's input (what it reads from stdin); 64 KiB at most.
    stdin: str | None = Field(None, max_length=64 * 1024)
    # Run under the debugger (Python only, debugger role); stdin is ignored then.
    debug: bool = False

class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int | None
    file_id: int
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None

# --- Breakpoint ---


class BreakpointUpsert(BaseModel):
    file_id: int
    line: int = Field(ge=0)
    enabled: bool = True

class BreakpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_id: int
    line: int
    enabled: bool
    created_by: int | None
    created_at: datetime

# --- Debug ---


class DebugCommand(BaseModel):
    command: Literal["continue", "next", "stepIn", "stepOut", "pause"]

# --- AI Assistant ---


from .assistant import AIAssistantConfigBase as AIAssistantConfigBase  # noqa: E402
from .assistant import AIAssistantConfigCreate as AIAssistantConfigCreate  # noqa: E402
from .assistant import AIAssistantConfigOut as AIAssistantConfigOut  # noqa: E402
from .assistant import AIAssistantConfigUpdate as AIAssistantConfigUpdate  # noqa: E402
from .assistant import AIAssistantRequestBase as AIAssistantRequestBase  # noqa: E402
from .assistant import AIAssistantRequestCreate as AIAssistantRequestCreate  # noqa: E402
from .assistant import AIAssistantRequestOut as AIAssistantRequestOut  # noqa: E402
from .assistant import AIAssistantResponse as AIAssistantResponse  # noqa: E402
