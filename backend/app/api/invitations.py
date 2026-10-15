"""Invitation API."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, RoleRank, at_least
from app.models import Invitation, Project, ProjectMember, Role

router = APIRouter(prefix="/invitations", tags=["invitations"])

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    project_id: int,
    role: Literal["viewer", "editor", "debugger", "owner"],
    user: CurrentUser,
    db: DbSession,
) -> dict:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # RBAC: editor or higher can invite, and never to a role above their own
    # (an editor minting an "owner" invite for a second account would escalate).
    if project.owner_id != user.id:
        res = await db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
        member = res.scalar_one_or_none()
        if not member or not at_least(member.role, Role.EDITOR):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions to invite")
        if RoleRank[role] > RoleRank[member.role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You cannot invite someone to a role above your own"
            )

    token = secrets.token_urlsafe(32)
    invitation = Invitation(
        project_id=project_id,
        token=token,
        role=role,
        expires_at=datetime.now(UTC) + timedelta(days=7)
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)

    return {"token": token, "invite_url": f"/join?token={token}"}

@router.post("/accept/{token}")
async def accept_invitation(token: str, user: CurrentUser, db: DbSession):
    res = await db.execute(select(Invitation).where(Invitation.token == token))
    inv = res.scalar_one_or_none()

    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    # SQLite (used in unit tests) drops tzinfo on read even for
    # DateTime(timezone=True) columns; Postgres (production) preserves it.
    # Normalize so this comparison works against either backend.
    expires_at = inv.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation expired")

    # Grant access to the project.
    project = await db.get(Project, inv.project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # A second membership row would make every permission check for this user
    # fail (get_member expects one row), so existing members keep their role
    # and the invitation stays unused.
    if project.owner_id == user.id or await db.scalar(
        select(ProjectMember.id).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You are already a member of this project")

    # Add user to project
    member = ProjectMember(project_id=project.id, user_id=user.id, role=inv.role)
    db.add(member)

    # Delete invitation
    await db.delete(inv)

    await db.commit()
    return {"message": "Joined project successfully"}
