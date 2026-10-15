"""Unit tests for LLMManager: API-key encryption and rate-limit fallback.

Previously zero tests existed for this despite it holding the encryption key
for every project's LLM API key and the only rate-limit gate on the AI
assistant feature.
"""
from __future__ import annotations

import pytest

from app.llm.adapters import HuggingFaceAdapter, MistralAdapter
from app.llm.manager import llm_manager


def test_encrypt_decrypt_round_trip():
    encrypted = llm_manager._encrypt_api_key("sk-real-secret-value")
    assert encrypted != "sk-real-secret-value"
    assert llm_manager._decrypt_api_key(encrypted) == "sk-real-secret-value"


def test_get_provider_returns_correct_adapter():
    assert isinstance(llm_manager.get_provider("mistral", "key"), MistralAdapter)
    assert isinstance(llm_manager.get_provider("huggingface", "key"), HuggingFaceAdapter)


def test_get_provider_unknown_raises_value_error():
    with pytest.raises(ValueError):
        llm_manager.get_provider("openai", "key")


def test_validate_provider_config_reflects_adapter_check():
    # MistralAdapter.validate_api_key: len >= 10.
    assert llm_manager.validate_provider_config("mistral", "0123456789") is True
    assert llm_manager.validate_provider_config("mistral", "short") is False
    # HuggingFaceAdapter.validate_api_key: must start with "hf_" and len >= 10.
    assert llm_manager.validate_provider_config("huggingface", "hf_0123456789") is True
    assert llm_manager.validate_provider_config("huggingface", "0123456789") is False


def test_validate_provider_config_unknown_provider_returns_false():
    assert llm_manager.validate_provider_config("openai", "0123456789") is False


def test_check_rate_limit_disabled_always_allows(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_assistant_rate_limit_enabled", False)
    allowed, info = llm_manager.check_rate_limit(project_id=1)
    assert allowed is True
    assert info["limited"] is False


def test_check_rate_limit_fails_open_when_redis_unreachable(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_assistant_rate_limit_enabled", True)
    # No real Redis server is running in the test environment, so the real
    # (lazily-connecting) client's .eval() call raises — check_rate_limit
    # must fail open (allow the request) rather than block on infra being down.
    allowed, info = llm_manager.check_rate_limit(project_id=1)
    assert allowed is True
    assert info["limited"] is False


def test_check_rate_limit_reports_limited_when_over_threshold(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_assistant_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ai_assistant_rate_limit_requests", 5)
    monkeypatch.setattr(settings, "ai_assistant_rate_limit_window", 60)

    class _FakeRedis:
        def eval(self, script: str, numkeys: int, key: str, limit: str, window: str):
            return [6, 42]  # current=6 > limit=5

    monkeypatch.setattr(llm_manager, "_redis_sync", _FakeRedis())

    allowed, info = llm_manager.check_rate_limit(project_id=1)
    assert allowed is False
    assert info["limited"] is True
    assert info["remaining"] == 0
    assert info["reset_time"] == 42
