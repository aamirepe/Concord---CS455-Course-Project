"""The agent loop: ask the model, run the tools it calls, repeat."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent import prompts
from app.agent.tools import AgentContext, Proposal, file_text, last_execution, run_tool, tool_specs
from app.llm.provider import LLMProvider
from app.models import File

log = logging.getLogger(__name__)

# Returned when the step budget runs out before the model gives an answer.
TRUNCATED_ANSWER = (
    "I ran out of steps before finishing. Try a narrower question, "
    "or ask me to continue."
)


@dataclass
class Step:
    tool: str
    summary: str
    ok: bool


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    proposal: Proposal | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    truncated: bool = False


def _summarize(name: str, args_json: str) -> str:
    """Short human-readable description of a tool call for the UI."""
    try:
        args: dict[str, Any] = json.loads(args_json or "{}")
    except ValueError:
        args = {}
    if "file_id" in args:
        return f"{name}(file {args['file_id']})"
    return f"{name}()"


# How much of the active file goes into the first message. Longer files are
# cut off there and the agent reads the rest with read_file.
PRELOAD_CHARS = 12000


@dataclass
class Selection:
    start_line: int
    end_line: int
    text: str


def _fenced(text: str, lang: str = "") -> str:
    """Wrap ``text`` in a code fence longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


async def _user_message(ctx: AgentContext, prompt: str, mode: str = "ask", selection: Selection | None = None) -> str:
    """The user's prompt, preceded by the context the agent would otherwise
    fetch with tools first: the active file, its last run when fixing, and
    what the user selected. Saves round trips (free Mistral keys allow about
    one request per second)."""
    file_obj = None
    if ctx.active_file_id is not None:
        file_obj = await ctx.db.get(File, ctx.active_file_id)
        if file_obj is not None and file_obj.project_id != ctx.project_id:
            file_obj = None
    if file_obj is None:
        return prompt

    text = await file_text(ctx, file_obj)
    ctx.files_read.add(file_obj.id)
    lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    parts = [
        f"[Active file: {file_obj.path} (id {file_obj.id}, {file_obj.language}, {lines} lines). "
        "Its current content:]\n" + _fenced(text[:PRELOAD_CHARS], file_obj.language or "")
    ]
    if len(text) > PRELOAD_CHARS:
        parts.append(f"[The file was cut off after {PRELOAD_CHARS} characters; call read_file for all of it.]")
    if mode == "fix":
        last = await last_execution(ctx, file_obj)
        parts.append(
            "[Last run of this file:]\n" + _fenced(json.dumps(last, indent=1), "json")
            if last else "[This file has not been run yet.]"
        )
    if selection is not None:
        parts.append(
            f"[The user selected lines {selection.start_line}-{selection.end_line}:]\n"
            + _fenced(selection.text[:8000], file_obj.language or "")
        )
    parts.append(prompt)
    return "\n\n".join(parts)


async def run_agent(
    ctx: AgentContext,
    provider: LLMProvider,
    prompt: str,
    mode: str = "ask",
    history: list[dict[str, str]] | None = None,
    max_steps: int = 6,
    on_step: Callable[[Step], Awaitable[None]] | None = None,
    selection: Selection | None = None,
) -> AgentResult:
    """Run the agent. ``on_step`` is called after each tool call, for live
    progress; if it raises, the error is logged and the run carries on."""
    started = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompts.system_prompt(mode)}]
    for turn in history or []:
        messages.append({"role": turn["role"], "content": turn["text"]})
    messages.append({"role": "user", "content": await _user_message(ctx, prompt, mode, selection)})

    tools = tool_specs(mode)
    result = AgentResult(answer="")

    for _ in range(max_steps):
        reply = await provider.chat(messages, tools=tools)
        result.prompt_tokens += reply.prompt_tokens
        result.completion_tokens += reply.completion_tokens

        if not reply.tool_calls:
            result.answer = reply.content
            break

        messages.append({
            "role": "assistant",
            "content": reply.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in reply.tool_calls
            ],
        })
        for tc in reply.tool_calls:
            output, ok = await run_tool(ctx, mode, tc.name, tc.arguments)
            step = Step(tool=tc.name, summary=_summarize(tc.name, tc.arguments), ok=ok)
            result.steps.append(step)
            if on_step is not None:
                try:
                    await on_step(step)
                except Exception:
                    log.exception("on_step callback failed")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": output,
            })
    else:
        result.truncated = True
        result.answer = TRUNCATED_ANSWER

    result.proposal = ctx.proposal
    result.latency_ms = int((time.monotonic() - started) * 1000)
    return result
