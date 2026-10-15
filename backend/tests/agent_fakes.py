"""Test doubles for the AI agent: a provider that replays scripted turns."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.provider import ChatResult, LLMProvider, ToolCall
from app.models import DocumentVersion
from app.realtime.crdt import doc_to_bytes, new_doc


def call(name: str, call_id: str = "c1", **args: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def tool_turn(*calls: ToolCall, tokens: int = 10) -> ChatResult:
    return ChatResult(tool_calls=list(calls), prompt_tokens=tokens, completion_tokens=tokens)


def answer(text: str, tokens: int = 10) -> ChatResult:
    return ChatResult(content=text, prompt_tokens=tokens, completion_tokens=tokens, finish_reason="stop")


class ScriptedProvider(LLMProvider):
    """Returns the scripted ChatResults in order and records every request."""

    supports_tools = True

    def __init__(self, *turns: ChatResult, supports_tools: bool = True) -> None:
        super().__init__(api_key="scripted-key")
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.supports_tools = supports_tools
        self.model = "scripted-model"

    async def chat(self, messages, tools=None, temperature=0.3, max_tokens=1000) -> ChatResult:
        # Snapshot: the runner keeps appending to the same list.
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.turns:
            raise AssertionError("ScriptedProvider ran out of turns")
        return self.turns.pop(0)

    async def generate_completion(self, prompt, system_prompt, temperature=0.7, max_tokens=1000, **kw):
        raise NotImplementedError

    async def generate_completion_stream(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError
        yield ""

    def validate_api_key(self, api_key: str) -> bool:
        return True

    def tool_results(self) -> list[str]:
        """Contents of every tool message sent back to the model, in order."""
        seen: list[str] = []
        for req in self.requests:
            for m in req["messages"]:
                if m["role"] == "tool" and m["content"] not in seen:
                    seen.append(m["content"])
        return seen


async def set_file_text(db: AsyncSession, file_id: int, text: str, version: int = 1) -> None:
    """Give a file stored content (as a checkpoint) without a live room."""
    db.add(DocumentVersion(file_id=file_id, version_number=version, snapshot_bytes=doc_to_bytes(new_doc(text))))
    await db.commit()
