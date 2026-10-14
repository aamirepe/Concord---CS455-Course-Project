"""Concrete implementations of LLM providers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .provider import ChatResult, LLMProvider, ToolCall

log = logging.getLogger(__name__)


class MistralAdapter(LLMProvider):
    """Mistral AI adapter (chat completions + function calling)."""

    supports_tools = True
    DEFAULT_MODEL = "codestral-latest"
    # The free tier allows ~1 request/second; 429s and transient 5xxs are
    # retried this many times before giving up.
    MAX_RETRIES = 2
    RETRY_BASE_DELAY = 1.0

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.base_url = "https://api.mistral.ai/v1"
        self.model = kwargs.get("model") or self.DEFAULT_MODEL

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _post_with_retry(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(self.MAX_RETRIES + 1):
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=body,
                timeout=30.0,
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.MAX_RETRIES:
                response.raise_for_status()
                return response
            delay = self.RETRY_BASE_DELAY * (2 ** attempt)
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = min(float(retry_after), 10.0)
                except ValueError:
                    pass
            log.warning("Mistral API %s, retrying in %.1fs", response.status_code, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> ChatResult:
        """One chat-completion turn, optionally offering ``tools``."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await self._post_with_retry(client, body)
            except httpx.HTTPStatusError as e:
                log.error(f"Mistral API error: {e.response.status_code} - {e.response.text}")
                raise
        result = response.json()
        choice = result["choices"][0]
        message = choice["message"]
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments") or "{}",
            )
            for tc in message.get("tool_calls") or []
        ]
        # Mistral may return arguments as an already-decoded object.
        for tc in tool_calls:
            if not isinstance(tc.arguments, str):
                tc.arguments = json.dumps(tc.arguments)
        usage = result.get("usage") or {}
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason"),
        )

    async def generate_completion(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> str:
        """Generate a single completion using Mistral AI."""
        result = await self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result.content

    async def generate_completion_stream(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming completion using Mistral AI."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": True,
                        **kwargs
                    },
                    timeout=30.0
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]  # Remove "data: " prefix
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if chunk["choices"][0]["delta"].get("content"):
                                    yield chunk["choices"][0]["delta"]["content"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                # Skip malformed chunks
                                continue
            except httpx.HTTPStatusError as e:
                log.error(f"Mistral API error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                log.error(f"Error calling Mistral API: {e}")
                raise

    def validate_api_key(self, api_key: str) -> bool:
        """Validate Mistral API key format.

        Mistral API keys typically start with something like 'sk-' or similar.
        For now, we'll do basic validation.
        """
        return bool(api_key and len(api_key) >= 10)


class HuggingFaceAdapter(LLMProvider):
    """Hugging Face Inference API adapter for free models."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.base_url = "https://api-inference.huggingface.co/models"
        self.model = kwargs.get("model", "HuggingFaceH4/zephyr-7b-beta")

    async def generate_completion(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> str:
        """Generate a completion using Hugging Face Inference API."""
        # Combine system and user prompts for HF format
        full_prompt = f"<|system|>\n{system_prompt}</s>\n<|user|>\n{prompt}</s>\n<|assistant|>\n"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/{self.model}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "inputs": full_prompt,
                        "parameters": {
                            "temperature": temperature,
                            "max_new_tokens": max_tokens,
                            "return_full_text": False,
                            **kwargs
                        }
                    },
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                # HF returns a list of generated texts on success; a non-list
                # body (e.g. {"error": "model is loading"}) is a failure, not
                # a completion to hand back to the user.
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("generated_text", "")
                raise RuntimeError(f"HuggingFace API error: {result}")
            except httpx.HTTPStatusError as e:
                log.error(f"Hugging Face API error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                log.error(f"Error calling Hugging Face API: {e}")
                raise

    async def generate_completion_stream(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs
    ) -> AsyncIterator[str]:
        """Generate a streaming completion using Hugging Face.

        Note: Hugging Face Inference API doesn't consistently support streaming
        for all models, so we'll simulate it by yielding the complete response.
        """
        response = await self.generate_completion(
            prompt, system_prompt, temperature, max_tokens, **kwargs
        )
        # Simulate streaming by yielding chunks of the response
        words = response.split()
        for i in range(0, len(words), 5):  # Yield 5 words at a time
            chunk = " ".join(words[i:i+5])
            if i + 5 < len(words):
                chunk += " "
            yield chunk

    def validate_api_key(self, api_key: str) -> bool:
        """Validate Hugging Face API key format.

        HF tokens typically start with 'hf_'.
        """
        return bool(api_key and api_key.startswith("hf_") and len(api_key) >= 10)
