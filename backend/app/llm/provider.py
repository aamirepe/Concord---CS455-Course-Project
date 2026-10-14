"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A function call requested by the model."""
    id: str
    name: str
    arguments: str  # raw JSON string, as returned by the provider


@dataclass
class ChatResult:
    """One chat-completion turn."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    # Whether ``chat`` accepts ``tools`` (function calling).
    supports_tools: bool = False

    def __init__(self, api_key: str, **kwargs):
        """Initialize the provider with API key and optional parameters.

        Args:
            api_key: The API key for the provider
            **kwargs: Additional provider-specific configuration
        """
        self.api_key = api_key
        self.config = kwargs

    @abstractmethod
    async def generate_completion(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> str:
        """Generate a completion from the LLM.

        Args:
            prompt: The user prompt
            system_prompt: System prompt to guide behavior
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    def generate_completion_stream(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming completion from the LLM.

        Args:
            prompt: The user prompt
            system_prompt: System prompt to guide behavior
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional provider-specific parameters

        Yields:
            Generated text chunks
        """
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> ChatResult:
        """Run one multi-turn chat completion, optionally with tools.

        The default implementation flattens the conversation into a single
        prompt for providers without native chat/tool support.
        """
        if tools:
            raise NotImplementedError(f"{self.get_provider_name()} does not support tool calling")
        system_prompt = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        prompt = "\n\n".join(
            f"{m['role']}: {m['content']}" for m in messages if m["role"] != "system"
        )
        text = await self.generate_completion(
            prompt, system_prompt, temperature=temperature, max_tokens=max_tokens
        )
        return ChatResult(content=text, finish_reason="stop")

    @abstractmethod
    def validate_api_key(self, api_key: str) -> bool:
        """Validate the API key format.

        Args:
            api_key: The API key to validate

        Returns:
            True if the API key appears valid, False otherwise
        """
        pass

    def get_provider_name(self) -> str:
        """Get the provider name.

        Returns:
            Provider identifier string
        """
        return self.__class__.__name__.replace("Adapter", "").lower()
