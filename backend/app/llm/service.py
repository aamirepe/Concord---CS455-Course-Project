"""Pick the LLM provider for a project and record what it was asked.

Key resolution order: the project's own config (set by its owner) wins; if
the project has none, the server-wide ``MISTRAL_API_KEY`` is used; otherwise
the assistant is "not configured" for that project.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.manager import llm_manager
from app.llm.provider import LLMProvider
from app.models import AIAssistantConfig, AIAssistantRequest, AIProvider
from app.schemas.assistant import AIAssistantStatus

log = logging.getLogger(__name__)


@dataclass
class ResolvedProvider:
    provider: LLMProvider
    kind: AIProvider
    model: str
    source: Literal["project", "server"]


async def _project_config(db: AsyncSession, project_id: int) -> AIAssistantConfig | None:
    res = await db.execute(
        select(AIAssistantConfig).where(AIAssistantConfig.project_id == project_id)
    )
    return res.scalar_one_or_none()


async def get_status(db: AsyncSession, project_id: int) -> AIAssistantStatus:
    settings = get_settings()
    config = await _project_config(db, project_id)
    if config is not None:
        return AIAssistantStatus(
            enabled=settings.ai_assistant_enabled and config.is_active,
            configured=True,
            source="project",
            provider=config.provider,
            model=config.model_name,
        )
    if settings.mistral_api_key:
        return AIAssistantStatus(
            enabled=settings.ai_assistant_enabled,
            configured=True,
            source="server",
            provider=AIProvider.MISTRAL,
            model=settings.mistral_model,
        )
    return AIAssistantStatus(
        enabled=settings.ai_assistant_enabled,
        configured=False,
        source=None,
        provider=None,
        model=None,
    )


async def resolve_provider(db: AsyncSession, project_id: int) -> ResolvedProvider:
    """Return a ready-to-call provider, or raise a 4xx/5xx HTTPException."""
    settings = get_settings()
    config = await _project_config(db, project_id)

    if config is not None:
        if not config.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI assistant is disabled for this project",
            )
        try:
            api_key = llm_manager._decrypt_api_key(config.encrypted_api_key)
        except Exception as e:
            log.error("Failed to decrypt API key for project %d: %s", project_id, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="AI assistant configuration error",
            ) from e
        kind = config.provider
        model = config.model_name
        source: Literal["project", "server"] = "project"
    elif settings.mistral_api_key:
        api_key = settings.mistral_api_key
        kind = AIProvider.MISTRAL
        model = settings.mistral_model
        source = "server"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI assistant not configured for this project.",
        )

    try:
        provider = llm_manager.get_provider(kind.value, api_key, model=model)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ResolvedProvider(
        provider=provider,
        kind=kind,
        model=model or getattr(provider, "model", None) or provider.get_provider_name(),
        source=source,
    )


async def log_request(
    db: AsyncSession,
    *,
    project_id: int,
    user_id: int,
    file_id: int | None,
    mode: str,
    prompt: str,
    response: str,
    resolved: ResolvedProvider,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
) -> AIAssistantRequest:
    row = AIAssistantRequest(
        project_id=project_id,
        user_id=user_id,
        file_id=file_id,
        mode=mode,
        prompt=prompt,
        response=response,
        provider=resolved.kind,
        model_used=resolved.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=(
            (prompt_tokens or 0) + (completion_tokens or 0)
            if prompt_tokens is not None or completion_tokens is not None
            else None
        ),
        latency_ms=latency_ms,
        is_successful=error is None,
        error_message=error,
    )
    db.add(row)
    await db.commit()
    return row
