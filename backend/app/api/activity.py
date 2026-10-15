"""Recent changes in a project: which files changed lately, who changed them, lines +/-."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession, get_member
from app.models import File, FileChange, User
from app.schemas import AuthorActivityOut, FileActivityOut

router = APIRouter(prefix="/projects/{project_id}", tags=["activity"])


@router.get("/activity", response_model=list[FileActivityOut])
async def recent_changes(
    project_id: int, user: CurrentUser, db: DbSession, limit: int = Query(5, ge=1, le=20)
) -> list[FileActivityOut]:
    """The ``limit`` most recently changed files, each with per-author totals."""
    await get_member(db, project_id, user.id)

    last_at = func.max(FileChange.created_at).label("last_at")
    recent = (await db.execute(
        select(FileChange.file_id, last_at)
        .where(FileChange.project_id == project_id)
        .group_by(FileChange.file_id)
        .order_by(last_at.desc(), FileChange.file_id.desc())
        .limit(limit)
    )).all()
    file_ids = [r.file_id for r in recent]
    if not file_ids:
        return []

    paths = dict((await db.execute(select(File.id, File.path).where(File.id.in_(file_ids)))).tuples().all())
    author_last = func.max(FileChange.created_at).label("last_at")
    rows = (await db.execute(
        select(
            FileChange.file_id,
            FileChange.user_id,
            User.username,
            func.sum(FileChange.lines_added).label("added"),
            func.sum(FileChange.lines_removed).label("removed"),
            author_last,
        )
        .join(User, User.id == FileChange.user_id)
        .where(FileChange.file_id.in_(file_ids))
        .group_by(FileChange.file_id, FileChange.user_id, User.username)
        .order_by(author_last.desc())
    )).all()

    authors: dict[int, list[AuthorActivityOut]] = {fid: [] for fid in file_ids}
    for r in rows:
        authors[r.file_id].append(AuthorActivityOut(
            user_id=r.user_id, username=r.username, lines_added=r.added, lines_removed=r.removed, last_changed_at=r.last_at,
        ))
    return [
        FileActivityOut(
            file_id=r.file_id,
            path=paths.get(r.file_id, "(deleted)"),
            lines_added=sum(a.lines_added for a in authors[r.file_id]),
            lines_removed=sum(a.lines_removed for a in authors[r.file_id]),
            last_changed_at=r.last_at,
            authors=authors[r.file_id],
        )
        for r in recent
    ]
