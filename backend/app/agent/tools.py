"""Tools the coding agent may call.

Every tool is read-only except ``propose_edit``, which does not write either:
it records a proposal that a human (editor+) accepts or rejects in the UI,
where it is applied through the CRDT like any other edit.

Handlers never raise for bad input from the model; they return an error
string so the model can correct itself on the next step.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Execution, File
from app.realtime.source import get_file_text


@dataclass
class Proposal:
    file_id: int
    original_text: str
    proposed_text: str
    explanation: str
    syntax_ok: bool


@dataclass
class AgentContext:
    db: AsyncSession
    project_id: int
    active_file_id: int | None
    max_file_chars: int
    proposal: Proposal | None = None
    # When set, used instead of the stored text for the active file
    # (the editor's unsaved content sent by clients that don't send file_id).
    active_text_override: str | None = None
    files_read: set[int] = field(default_factory=set)


class ToolError(Exception):
    """Raised inside a handler; its message is returned to the model."""


def check_python_syntax(code: str) -> str | None:
    """Return None if ``code`` parses, else a one-line error description."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    return None


async def _project_file(ctx: AgentContext, file_id: Any) -> File:
    try:
        fid = int(file_id)
    except (TypeError, ValueError):
        raise ToolError(f"file_id must be an integer, got {file_id!r}") from None
    file_obj = await ctx.db.get(File, fid)
    if file_obj is None or file_obj.project_id != ctx.project_id:
        raise ToolError(f"no file with id {fid} in this project; call list_files")
    return file_obj


async def file_text(ctx: AgentContext, file_obj: File) -> str:
    if file_obj.id == ctx.active_file_id and ctx.active_text_override is not None:
        return ctx.active_text_override
    return await get_file_text(ctx.db, ctx.project_id, file_obj.id)


# --- handlers ---------------------------------------------------------------

async def list_files(ctx: AgentContext) -> str:
    res = await ctx.db.execute(
        select(File).where(File.project_id == ctx.project_id).order_by(File.path)
    )
    files = [
        {"id": f.id, "path": f.path, "language": f.language, "active": f.id == ctx.active_file_id}
        for f in res.scalars()
    ]
    return json.dumps(files)


async def read_file(ctx: AgentContext, file_id: Any) -> str:
    file_obj = await _project_file(ctx, file_id)
    text = await file_text(ctx, file_obj)
    ctx.files_read.add(file_obj.id)
    header = f"# {file_obj.path} ({file_obj.language}, {text.count(chr(10)) + 1} lines)\n"
    if len(text) > ctx.max_file_chars:
        return header + text[: ctx.max_file_chars] + "\n... [truncated]"
    return header + text


async def last_execution(ctx: AgentContext, file_obj: File) -> dict[str, Any] | None:
    """Summary of the file's latest run (output tails), or None if it never ran."""
    res = await ctx.db.execute(
        select(Execution)
        .where(Execution.file_id == file_obj.id)
        .order_by(Execution.queued_at.desc(), Execution.id.desc())
        .limit(1)
    )
    execution = res.scalar_one_or_none()
    if execution is None:
        return None
    return {
        "file": file_obj.path,
        "status": str(execution.status),
        "exit_code": execution.exit_code,
        "stdout_tail": (execution.stdout or "")[-2000:],
        "stderr_tail": (execution.stderr or "")[-4000:],
    }


async def get_last_execution(ctx: AgentContext, file_id: Any = None) -> str:
    fid = file_id if file_id is not None else ctx.active_file_id
    if fid is None:
        raise ToolError("no active file; pass file_id")
    file_obj = await _project_file(ctx, fid)
    summary = await last_execution(ctx, file_obj)
    if summary is None:
        return f"{file_obj.path} has not been run yet."
    return json.dumps(summary)


async def check_syntax(ctx: AgentContext, code: str, language: str = "python") -> str:
    if language.lower() != "python":
        return f"Syntax checking is only available for python, not {language}."
    err = check_python_syntax(code)
    return "OK: parses cleanly." if err is None else err


async def propose_edit(
    ctx: AgentContext, file_id: Any, original: str, proposed: str, explanation: str = ""
) -> str:
    file_obj = await _project_file(ctx, file_id)
    if not original:
        raise ToolError("original must be the exact existing text to replace (non-empty)")
    text = await file_text(ctx, file_obj)
    count = text.count(original)
    if count == 0:
        raise ToolError(
            "original was not found verbatim in the file. Call read_file and copy the "
            "exact lines, including indentation."
        )
    if count > 1:
        raise ToolError(
            f"original appears {count} times; include more surrounding lines so it is unique."
        )
    new_text = text.replace(original, proposed, 1)
    syntax_error = check_python_syntax(new_text) if file_obj.language == "python" else None
    ctx.proposal = Proposal(
        file_id=file_obj.id,
        original_text=original,
        proposed_text=proposed,
        explanation=explanation,
        syntax_ok=syntax_error is None,
    )
    if syntax_error:
        return (
            f"Proposal recorded, but the file would not parse afterwards: {syntax_error}. "
            "Fix it with another propose_edit call."
        )
    return "Proposal recorded. The user will review and apply it; do not repeat the code in full."


# --- registry ---------------------------------------------------------------

Handler = Callable[..., Awaitable[str]]

_FILE_ID = {"type": "integer", "description": "File id from list_files"}

TOOLS: dict[str, tuple[Handler, dict[str, Any]]] = {
    "list_files": (list_files, {
        "description": "List the project's files with their ids and languages.",
        "parameters": {"type": "object", "properties": {}},
    }),
    "read_file": (read_file, {
        "description": "Read the current content of a file.",
        "parameters": {
            "type": "object",
            "properties": {"file_id": _FILE_ID},
            "required": ["file_id"],
        },
    }),
    "get_last_execution": (get_last_execution, {
        "description": (
            "Get the result of the most recent run of a file: status, exit code, "
            "and the tail of stdout/stderr. Defaults to the active file."
        ),
        "parameters": {"type": "object", "properties": {"file_id": _FILE_ID}},
    }),
    "check_syntax": (check_syntax, {
        "description": "Check whether a code snippet parses (python only).",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "language": {"type": "string", "default": "python"},
            },
            "required": ["code"],
        },
    }),
    "propose_edit": (propose_edit, {
        "description": (
            "Propose replacing an exact, unique span of a file with new text. "
            "The user reviews and applies it. This is the only way to change code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "original": {
                    "type": "string",
                    "description": "Exact existing text to replace, copied verbatim from read_file",
                },
                "proposed": {"type": "string", "description": "Replacement text"},
                "explanation": {"type": "string", "description": "One sentence: why"},
            },
            "required": ["file_id", "original", "proposed", "explanation"],
        },
    }),
}

# Tools each mode may use. "review" and "explain" never edit.
MODE_TOOLS: dict[str, list[str]] = {
    "ask": list(TOOLS),
    "explain": ["list_files", "read_file", "get_last_execution"],
    "review": ["list_files", "read_file", "get_last_execution", "check_syntax"],
    "fix": list(TOOLS),
    "generate": list(TOOLS),
}


def tool_specs(mode: str) -> list[dict[str, Any]]:
    """Tool definitions in the OpenAI/Mistral function-calling format."""
    return [
        {"type": "function", "function": {"name": name, **TOOLS[name][1]}}
        for name in MODE_TOOLS.get(mode, list(TOOLS))
    ]


async def run_tool(ctx: AgentContext, mode: str, name: str, raw_args: str) -> tuple[str, bool]:
    """Execute one tool call. Returns (result text for the model, ok)."""
    if name not in MODE_TOOLS.get(mode, list(TOOLS)):
        return f"Error: tool {name!r} is not available in {mode} mode.", False
    handler = TOOLS[name][0]
    try:
        args = json.loads(raw_args) if raw_args else {}
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as e:
        return f"Error: invalid arguments JSON ({e}).", False
    try:
        return await handler(ctx, **args), True
    except ToolError as e:
        return f"Error: {e}", False
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}", False
