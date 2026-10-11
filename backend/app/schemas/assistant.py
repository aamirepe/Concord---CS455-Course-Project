"""AI Assistant schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_assistant import AIProvider


class AIAssistantConfigBase(BaseModel):
    """Base AI assistant configuration."""
    provider: AIProvider
    model_name: str | None = Field(None, max_length=100)


class AIAssistantConfigCreate(AIAssistantConfigBase):
    """Create AI assistant configuration."""
    api_key: str = Field(min_length=10)


class AIAssistantConfigUpdate(BaseModel):
    """Update AI assistant configuration."""
    provider: AIProvider | None = None
    model_name: str | None = Field(None, max_length=100)
    api_key: str | None = Field(None, min_length=10)
    is_active: bool | None = None


class AIAssistantConfigOut(AIAssistantConfigBase):
    """AI assistant configuration response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AIAssistantRequestBase(BaseModel):
    """Base AI assistant request."""
    prompt: str = Field(min_length=1, max_length=8000)  # Reasonable limit


AgentMode = Literal["ask", "explain", "fix", "generate", "review"]


class AIChatTurn(BaseModel):
    """One earlier turn of the conversation, sent back for context."""
    role: Literal["user", "assistant"]
    text: str = Field(max_length=8000)


class AISelection(BaseModel):
    """The part of the active file the user had selected."""
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=8000)


class AIAssistantRequestCreate(AIAssistantRequestBase):
    """Create AI assistant request."""
    mode: AgentMode = "ask"
    file_id: int | None = Field(None, description="The file open in the editor")
    history: list[AIChatTurn] = Field(default_factory=list, max_length=50)
    context_code: str | None = Field(
        None,
        description="Fallback editor content, used only when file_id is not given",
    )
    selection: AISelection | None = Field(None, description="What the user had selected in the active file")
    request_id: str | None = Field(
        None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Client-chosen id echoed in the AI_* progress events on the project socket",
    )


class AIAssistantRequestOut(BaseModel):
    """AI assistant request/response log."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    user_id: int
    file_id: int | None = None
    mode: str | None = None
    prompt: str
    response: str
    provider: AIProvider
    model_used: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int | None
    is_successful: bool
    error_message: str | None
    created_at: datetime


class AIDiffProposal(BaseModel):
    """A proposed change to a file: replace ``original_text`` with ``proposed_text``."""
    file_id: int | None = None
    original_text: str
    proposed_text: str
    explanation: str | None = None
    syntax_ok: bool = True


class AIAgentStep(BaseModel):
    """One tool call the agent made while answering."""
    tool: str
    summary: str
    ok: bool


class AIAssistantStatus(BaseModel):
    """Whether the assistant can be used in this project (never includes the key)."""
    enabled: bool
    configured: bool
    source: Literal["project", "server"] | None
    provider: AIProvider | None
    model: str | None


class AIAssistantResponse(BaseModel):
    """AI assistant response."""
    response: str
    provider: AIProvider
    model_used: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    proposal: AIDiffProposal | None = None
    steps: list[AIAgentStep] = Field(default_factory=list)
    truncated: bool = False
