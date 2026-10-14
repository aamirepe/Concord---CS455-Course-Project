"""AI Assistant models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models import Project
    from app.models.user import User


class AIProvider(enum.StrEnum):
    """Supported AI providers."""
    MISTRAL = "mistral"
    HUGGINGFACE = "huggingface"


# Store the lowercase values, as the Postgres ``aiprovider`` type defines them.
# SQLAlchemy's default is the member *names* ("MISTRAL"), which Postgres rejects.
_provider_enum = Enum(
    AIProvider, name="aiprovider", values_callable=lambda e: [m.value for m in e]
)
    # Future providers can be added here


class AIAssistantConfig(Base):
    """AI Assistant configuration per project."""
    __tablename__ = "ai_assistant_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider: Mapped[AIProvider] = mapped_column(
        _provider_enum, nullable=False
    )
    # Store encrypted API key
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional: model name or other provider-specific config
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Configuration flags
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship(back_populates="ai_config")


class AIAssistantRequest(Base):
    """Log of AI Assistant requests for auditing and rate limiting."""
    __tablename__ = "ai_assistant_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The file the request was about, if any
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    # Agent mode: ask | explain | fix | generate | review
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The prompt sent to the AI
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # The AI's response
    response: Mapped[str] = mapped_column(Text, nullable=False)
    # Which provider was used
    provider: Mapped[AIProvider] = mapped_column(
        _provider_enum, nullable=False
    )
    # Model used
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Token usage (if available from provider)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Latency in milliseconds
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether the request was successful
    is_successful: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Error message if failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    project: Mapped[Project] = relationship()
    user: Mapped[User] = relationship()
