"""Auth endpoints: signup + login."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import DbSession
from app.core.login_limiter import (
    lockout_seconds_remaining,
    record_login_failure,
    reset_login_failures,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas import TokenOut, UserCreate, UserLogin, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def signup(payload: UserCreate, db: DbSession) -> TokenOut:
    existing = await db.execute(
        select(User.id)
        .where((User.username == payload.username) | (User.email == payload.email))
        .limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username or email already in use"
        )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLogin, db: DbSession) -> TokenOut:
    retry_after = await lockout_seconds_remaining(payload.username)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    res = await db.execute(select(User).where(User.username == payload.username))
    user = res.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        await record_login_failure(payload.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )

    await reset_login_failures(payload.username)
    token = create_access_token(user.id)
    return TokenOut(access_token=token, user=UserOut.model_validate(user))
