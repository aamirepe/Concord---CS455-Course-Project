"""FastAPI dependencies: auth, role checks, DB session."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_session
from app.models import ProjectMember, Role, User

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    try:
        payload = decode_token(creds.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user_id = int(payload["sub"])
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_member(db: AsyncSession, project_id: int, user_id: int) -> ProjectMember:
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
    )
    res = await db.execute(stmt)
    member = res.scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project"
        )
    return member


def require_role(min_role: Role):
    """FastAPI dependency factory: enforces ROLE_RANK >= min_role."""

    async def _check(
        project_id: int,
        user: CurrentUser,
        db: DbSession,
    ) -> ProjectMember:
        member = await get_member(db, project_id, user.id)
        if RoleRank[member.role] < RoleRank[min_role.value]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role.value} role or higher",
            )
        return member

    return _check


# Local lookup that doesn't require the enum object to be a string.
RoleRank: dict[str, int] = {r.value: i for i, r in enumerate(Role.__members__.values())}


def at_least(role: Role | str, min_role: Role) -> bool:
    """True if ``role`` ranks at or above ``min_role`` (viewer < editor < debugger < owner)."""
    return RoleRank[role] >= RoleRank[min_role.value]
