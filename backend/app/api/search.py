"""Find text across a project's files (the live text, including unsaved edits)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, get_member
from app.models import File
from app.realtime.source import get_file_text

router = APIRouter(prefix="/projects/{project_id}", tags=["search"])

MAX_MATCHES = 500
PREVIEW_CHARS = 200


class SearchMatch(BaseModel):
    line: int  # 1-based
    column: int  # 1-based
    length: int
    preview: str  # the line, cut to PREVIEW_CHARS around the match
    preview_column: int  # where the match starts inside ``preview`` (0-based)


class SearchFile(BaseModel):
    file_id: int
    path: str
    matches: list[SearchMatch]


class SearchOut(BaseModel):
    query: str
    files: list[SearchFile]
    total: int
    truncated: bool


def _preview(line: str, start: int, length: int) -> tuple[str, int]:
    if len(line) <= PREVIEW_CHARS:
        return line, start
    lo = max(0, min(start - 40, len(line) - PREVIEW_CHARS))
    return line[lo:lo + PREVIEW_CHARS], start - lo


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units, which is what Monaco columns and JavaScript ``slice`` count."""
    return len(text.encode("utf-16-le")) // 2


def search_text(text: str, pattern: re.Pattern[str], limit: int) -> list[SearchMatch]:
    out: list[SearchMatch] = []
    for n, line in enumerate(text.splitlines(), start=1):
        for m in pattern.finditer(line):
            if m.end() == m.start():
                continue
            preview, col = _preview(line, m.start(), m.end() - m.start())
            # Python counts code points; the browser counts UTF-16 units, which differ
            # for characters outside the Basic Multilingual Plane (emoji and the like).
            out.append(SearchMatch(line=n, column=_utf16_len(line[:m.start()]) + 1,
                                   length=_utf16_len(m.group()),
                                   preview=preview, preview_column=_utf16_len(preview[:col])))
            if len(out) >= limit:
                return out
    return out


@router.get("/search", response_model=SearchOut)
async def search_project(
    project_id: int,
    user: CurrentUser,
    db: DbSession,
    q: str = Query(..., min_length=1, max_length=200),
    case: bool = Query(False, description="match case"),
    word: bool = Query(False, description="whole words only"),
) -> SearchOut:
    """Plain-text search (no regular expressions: a pattern can't stall the server)."""
    await get_member(db, project_id, user.id)
    body = re.escape(q)
    if word:
        body = rf"(?<!\w){body}(?!\w)"
    pattern = re.compile(body, 0 if case else re.IGNORECASE)

    files = (await db.execute(
        select(File).where(File.project_id == project_id).order_by(File.path)
    )).scalars().all()
    found: list[SearchFile] = []
    total = 0
    truncated = False
    for f in files:
        matches = search_text(await get_file_text(db, project_id, f.id), pattern, MAX_MATCHES - total + 1)
        if not matches:
            continue
        if total + len(matches) > MAX_MATCHES:
            matches = matches[:MAX_MATCHES - total]
            truncated = True
        total += len(matches)
        if matches:
            found.append(SearchFile(file_id=f.id, path=f.path, matches=matches))
        if truncated:
            break
    return SearchOut(query=q, files=found, total=total, truncated=truncated)
