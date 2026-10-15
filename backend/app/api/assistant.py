"""AI Assistant endpoints."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import prompts
from app.agent.runner import AgentResult, Selection, Step, run_agent
from app.agent.tools import AgentContext
from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession, get_member
from app.llm import service as ai_service
from app.llm.manager import llm_manager
from app.models import AIAssistantConfig, AIAssistantRequest, AIProvider, File, Project
from app.realtime.manager import room_manager
from app.realtime.source import get_file_text
from app.schemas.assistant import (
    AIAgentStep,
    AIAssistantConfigCreate,
    AIAssistantConfigOut,
    AIAssistantConfigUpdate,
    AIAssistantRequestCreate,
    AIAssistantRequestOut,
    AIAssistantResponse,
    AIAssistantStatus,
    AIDiffProposal,
)

log = logging.getLogger(__name__)


router = APIRouter(prefix="/projects/{project_id}/assist", tags=["assistant"])


async def _get_project_config(
    project_id: int, db: AsyncSession
) -> AIAssistantConfig:
    """Get AI assistant configuration for a project."""
    result = await db.execute(
        select(AIAssistantConfig).where(AIAssistantConfig.project_id == project_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI assistant not configured for this project",
        )
    return config


@router.post("/config", response_model=AIAssistantConfigOut)
async def configure_assistant(
    project_id: int,
    payload: AIAssistantConfigCreate,
    user: CurrentUser,
    db: DbSession,
) -> AIAssistantConfigOut:
    """Configure AI assistant for a project."""
    if not get_settings().ai_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Assistant is currently disabled",
        )

    await get_member(db, project_id, user.id)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    if project.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owner can configure AI assistant",
        )

    if not llm_manager.validate_provider_config(payload.provider.value, payload.api_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid provider configuration or API key",
        )

    result = await db.execute(
        select(AIAssistantConfig).where(AIAssistantConfig.project_id == project_id)
    )
    existing_config = result.scalar_one_or_none()

    if existing_config:
        existing_config.provider = payload.provider
        existing_config.encrypted_api_key = llm_manager._encrypt_api_key(payload.api_key)
        existing_config.model_name = payload.model_name
        existing_config.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(existing_config)
        config = existing_config
    else:
        config = AIAssistantConfig(
            project_id=project_id,
            provider=payload.provider,
            encrypted_api_key=llm_manager._encrypt_api_key(payload.api_key),
            model_name=payload.model_name,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return AIAssistantConfigOut.model_validate(config)


@router.get("/config", response_model=AIAssistantConfigOut)
async def get_assistant_config(
    project_id: int, user: CurrentUser, db: DbSession
) -> AIAssistantConfigOut:
    """Get AI assistant configuration for a project."""
    if not get_settings().ai_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Assistant is currently disabled",
        )
    await get_member(db, project_id, user.id)

    config = await _get_project_config(project_id, db)
    return AIAssistantConfigOut.model_validate(config)


@router.patch("/config", response_model=AIAssistantConfigOut)
async def update_assistant_config(
    project_id: int,
    payload: AIAssistantConfigUpdate,
    user: CurrentUser,
    db: DbSession,
) -> AIAssistantConfigOut:
    """Update AI assistant configuration for a project."""
    if not get_settings().ai_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Assistant is currently disabled",
        )
    await get_member(db, project_id, user.id)
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    if project.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owner can configure AI assistant",
        )

    config = await _get_project_config(project_id, db)

    update_data = payload.model_dump(exclude_unset=True)
    if update_data.get("api_key"):
        provider = update_data.get("provider", config.provider.value)
        if not llm_manager.validate_provider_config(provider, update_data["api_key"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid API key for provider",
            )
        update_data["encrypted_api_key"] = llm_manager._encrypt_api_key(
            update_data.pop("api_key")
        )
    if "provider" in update_data:
        update_data["provider"] = AIProvider(update_data["provider"])

    for field, value in update_data.items():
        setattr(config, field, value)

    config.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(config)

    return AIAssistantConfigOut.model_validate(config)


@router.get("/status", response_model=AIAssistantStatus)
async def assistant_status(
    project_id: int, user: CurrentUser, db: DbSession
) -> AIAssistantStatus:
    """Whether the assistant is usable here, and where its key comes from."""
    await get_member(db, project_id, user.id)
    return await ai_service.get_status(db, project_id)


def _legacy_prompt(prompt: str, context_code: str | None) -> str:
    """Prompt for providers without tool calling: inline the editor content."""
    if not context_code:
        return prompt
    return (
        "Current content of the user's active file:\n"
        f"```\n{context_code}\n```\n\n"
        f"Question: {prompt}"
    )


def _broadcast_ai(project_id: int, event: dict[str, Any]) -> None:
    """Send an AI progress event to everyone on the project socket.

    Events say who is using the assistant and which tools ran, never the
    prompt, file contents or answer: those only go back to the requester.
    """
    room_manager.broadcast_to_project(project_id, json.dumps(event))


@router.post("", response_model=AIAssistantResponse)
async def request_assistant(
    project_id: int,
    payload: AIAssistantRequestCreate,
    user: CurrentUser,
    db: DbSession,
) -> AIAssistantResponse:
    """Run the coding agent on the user's prompt.

    The agent reads project files and past runs through tools and may return
    one edit proposal; nothing is written to any file here. Applying a
    proposal happens client-side through the CRDT, gated on editor role.
    """
    settings = get_settings()
    if not settings.ai_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Assistant is currently disabled",
        )

    # Only members count against the project's quota.
    await get_member(db, project_id, user.id)

    allowed, rate_limit_info = llm_manager.check_rate_limit(project_id)
    if not allowed:
        retry_after = max(1, int(rate_limit_info["reset_time"]))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"The AI assistant allows {rate_limit_info['limit']} requests per "
                f"{rate_limit_info.get('window', settings.ai_assistant_rate_limit_window)} s per project. Try again in {retry_after} s."
            ),
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(rate_limit_info["limit"]),
                "X-RateLimit-Remaining": str(rate_limit_info["remaining"]),
                "X-RateLimit-Reset": str(rate_limit_info["reset_time"]),
            }
        )

    if payload.file_id is not None:
        file_obj = await db.get(File, payload.file_id)
        if file_obj is None or file_obj.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    resolved = await ai_service.resolve_provider(db, project_id)

    ctx = AgentContext(
        db=db,
        project_id=project_id,
        active_file_id=payload.file_id,
        max_file_chars=settings.ai_agent_max_file_chars,
    )
    history = [t.model_dump() for t in payload.history][-settings.ai_history_turns * 2:]

    request_id = payload.request_id or uuid4().hex
    _broadcast_ai(project_id, {
        "op": "AI_STARTED",
        "request_id": request_id,
        "user_id": user.id,
        "username": user.username,
        "mode": payload.mode,
        "file_id": payload.file_id,
    })

    async def on_step(step: Step) -> None:
        _broadcast_ai(project_id, {
            "op": "AI_STEP",
            "request_id": request_id,
            "user_id": user.id,
            "tool": step.tool,
            "summary": step.summary,
            "ok": step.ok,
        })

    finished: dict[str, Any] = {
        "op": "AI_FINISHED",
        "request_id": request_id,
        "user_id": user.id,
        "ok": False,
        "has_proposal": False,
        "file_id": payload.file_id,
    }
    try:
        try:
            if resolved.provider.supports_tools:
                prompt = payload.prompt
                if payload.file_id is None and payload.context_code:
                    prompt = _legacy_prompt(payload.prompt, payload.context_code)
                result = await run_agent(
                    ctx,
                    resolved.provider,
                    prompt,
                    mode=payload.mode,
                    history=history,
                    max_steps=settings.ai_agent_max_steps,
                    on_step=on_step,
                    selection=Selection(**payload.selection.model_dump()) if payload.selection else None,
                )
            else:
                context = payload.context_code
                if payload.file_id is not None:
                    context = await get_file_text(db, project_id, payload.file_id)
                messages = [{"role": "system", "content": prompts.system_prompt(payload.mode)}]
                messages += [{"role": t["role"], "content": t["text"]} for t in history]
                messages.append({"role": "user", "content": _legacy_prompt(payload.prompt, context)})
                started = time.monotonic()
                reply = await resolved.provider.chat(messages)
                result = AgentResult(
                    answer=reply.content,
                    prompt_tokens=reply.prompt_tokens,
                    completion_tokens=reply.completion_tokens,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
        except Exception as e:
            log.error("AI assistant error for project %d: %s", project_id, e)
            await ai_service.log_request(
                db,
                project_id=project_id,
                user_id=user.id,
                file_id=payload.file_id,
                mode=payload.mode,
                prompt=payload.prompt,
                response="",
                resolved=resolved,
                latency_ms=0,
                error=str(e),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI assistant error: {e}",
            ) from e

        await ai_service.log_request(
            db,
            project_id=project_id,
            user_id=user.id,
            file_id=payload.file_id,
            mode=payload.mode,
            prompt=payload.prompt,
            response=result.answer,
            resolved=resolved,
            latency_ms=result.latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )

        proposal = None
        if result.proposal is not None:
            proposal = AIDiffProposal(**asdict(result.proposal))
        finished.update(ok=True, has_proposal=proposal is not None)
        if proposal is not None:
            finished["file_id"] = proposal.file_id

        return AIAssistantResponse(
            response=result.answer,
            provider=resolved.kind,
            model_used=resolved.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=result.latency_ms,
            proposal=proposal,
            steps=[AIAgentStep(tool=s.tool, summary=s.summary, ok=s.ok) for s in result.steps],
            truncated=result.truncated,
        )
    finally:
        _broadcast_ai(project_id, finished)


@router.get("/history", response_model=list[AIAssistantRequestOut])
async def get_assistant_history(
    project_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: int = 50,
) -> list[AIAssistantRequestOut]:
    """Get AI assistant request history for a project."""
    if not get_settings().ai_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Assistant is currently disabled",
        )
    await get_member(db, project_id, user.id)

    result = await db.execute(
        select(AIAssistantRequest)
        .where(AIAssistantRequest.project_id == project_id)
        .order_by(AIAssistantRequest.created_at.desc())
        .limit(limit)
    )
    requests = result.scalars().all()

    return [AIAssistantRequestOut.model_validate(req) for req in requests]
