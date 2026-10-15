"""LLM Provider Manager for handling provider selection and API keys."""

from __future__ import annotations

import base64
import logging
import os
from typing import cast

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .adapters import HuggingFaceAdapter, MistralAdapter
from .provider import LLMProvider

# Redis imports for rate limiting
try:
    import redis.asyncio as aioredis
    from redis import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None  # type: ignore
    Redis = None  # type: ignore

log = logging.getLogger(__name__)


class LLMManager:
    """Manages LLM providers and API keys."""

    def __init__(self):
        self._providers: dict[str, type[LLMProvider]] = {
            "mistral": MistralAdapter,
            "huggingface": HuggingFaceAdapter,
        }
        # In a real implementation, this would come from environment or a secure vault
        self._encryption_key = self._get_or_create_encryption_key()
        self._cipher = Fernet(self._encryption_key)

        # Redis for rate limiting
        self._redis_sync: Redis | None = None
        self._async_redis: aioredis.Redis | None = None
        if REDIS_AVAILABLE:
            try:
                from app.core.config import get_settings
                self._redis_sync = Redis.from_url(get_settings().redis_url, decode_responses=False)
                self._async_redis = aioredis.from_url(get_settings().redis_url, decode_responses=False)
            except Exception as e:
                log.warning(f"Failed to initialize Redis for rate limiting: {e}")
                self._redis_sync = None
                self._async_redis = None

    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for API key storage."""
        # For simplicity, we'll derive a key from an environment variable
        # In production, this should be properly managed
        key_env = os.getenv("LLM_ENCRYPTION_KEY")
        if key_env:
            # Derive a proper Fernet key from the environment variable
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b'concord-llm-salt',  # In production, use a random salt stored securely
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(key_env.encode()))
            return key
        else:
            # Generate a key for development - NOT SECURE FOR PRODUCTION
            key = Fernet.generate_key()
            log.warning(
                "LLM_ENCRYPTION_KEY not set, using ephemeral key. "
                "API keys will not persist across restarts!"
            )
            return key

    def _encrypt_api_key(self, api_key: str) -> str:
        """Encrypt an API key for storage."""
        encrypted = self._cipher.encrypt(api_key.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def _decrypt_api_key(self, encrypted_api_key: str) -> str:
        """Decrypt an API key from storage."""
        try:
            decoded = base64.urlsafe_b64decode(encrypted_api_key.encode())
            decrypted = self._cipher.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            log.error(f"Failed to decrypt API key: {e}")
            raise ValueError("Invalid encrypted API key") from e

    def get_provider(
        self, provider_name: str, api_key: str, model: str | None = None
    ) -> LLMProvider:
        """Get a provider instance.

        Args:
            provider_name: Name of the provider (mistral, huggingface, etc.)
            api_key: API key for the provider
            model: Model name; the adapter's default when omitted

        Returns:
            Initialized provider instance

        Raises:
            ValueError: If provider is not supported
        """
        provider_name = provider_name.lower()
        if provider_name not in self._providers:
            raise ValueError(
                f"Unsupported LLM provider: {provider_name}. "
                f"Supported providers: {list(self._providers.keys())}"
            )

        provider_class = self._providers[provider_name]
        if model:
            return provider_class(api_key=api_key, model=model)
        return provider_class(api_key=api_key)

    def validate_provider_config(self, provider_name: str, api_key: str) -> bool:
        """Validate a provider configuration.

        Args:
            provider_name: Name of the provider
            api_key: API key to validate

        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            provider = self.get_provider(provider_name, api_key)
            return provider.validate_api_key(api_key)
        except Exception:
            return False

    def get_supported_providers(self) -> list[str]:
        """Get list of supported provider names.

        Returns:
            List of provider identifiers
        """
        return list(self._providers.keys())

    def check_rate_limit(self, project_id: int) -> tuple[bool, dict]:
        """Check if the project has exceeded the rate limit for AI assistant requests.

        A fixed-window counter in Redis: the first request starts a window of
        ``ai_assistant_rate_limit_window`` seconds.

        Args:
            project_id: The project to check

        Returns:
            Tuple of (allowed: bool, info: dict with limit details)
        """
        from app.core.config import get_settings
        settings = get_settings()

        # If rate limiting is disabled, always allow
        if not settings.ai_assistant_rate_limit_enabled:
            return True, {
                "limit": settings.ai_assistant_rate_limit_requests,
                "window": settings.ai_assistant_rate_limit_window,
                "remaining": settings.ai_assistant_rate_limit_requests,
                "reset_time": 0,
                "limited": False
            }

        # If Redis is not available, allow the request (fail open)
        if not REDIS_AVAILABLE or self._redis_sync is None:
            log.warning("Redis not available for rate limiting, allowing request")
            return True, {
                "limit": settings.ai_assistant_rate_limit_requests,
                "window": settings.ai_assistant_rate_limit_window,
                "remaining": settings.ai_assistant_rate_limit_requests,
                "reset_time": 0,
                "limited": False
            }

        try:
            # Use a Redis key specific to this session
            key = f"ai_assistant:rate_limit:{project_id}"
            limit = settings.ai_assistant_rate_limit_requests
            window = settings.ai_assistant_rate_limit_window

            # Use Redis INCR with EXPIRE for sliding window
            # We'll use a Lua script for atomic increment and expire setting
            lua_script = """
            local current = redis.call('INCR', KEYS[1])
            if current == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[2])
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {current, ttl}
            """

            # Use synchronous Redis client for rate limiting
            # redis-py types eval() as Awaitable[str] | str for both clients; the sync
            # client returns the script's table as a list of ints.
            result = cast(
                list[int],
                self._redis_sync.eval(lua_script, 1, key, str(limit), str(window)),
            )
            current = int(result[0])
            ttl = int(result[1])

            remaining = max(0, limit - current)
            limited = current > limit

            return not limited, {
                "limit": limit,
                "window": window,
                "remaining": remaining,
                "reset_time": ttl,
                "limited": limited
            }
        except Exception as e:
            log.error(f"Error checking rate limit for session {project_id}: {e}")
            # Fail open - allow request if rate limiting check fails
            return True, {
                "limit": settings.ai_assistant_rate_limit_requests,
                "window": settings.ai_assistant_rate_limit_window,
                "remaining": settings.ai_assistant_rate_limit_requests,
                "reset_time": 0,
                "limited": False,
                "error": str(e)
            }


# Global instance
llm_manager = LLMManager()
