"""File CRUD inside a project. The actual live editing happens over WebSocket; these
endpoints bootstrap the file (initial state) and let the frontend read the latest
checkpointed content if no live session is available."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession, RoleRank, at_least, get_member
from app.models import DocumentVersion, File, Folder, Project, ProjectMember, Role, User
from app.realtime.manager import room_manager
from app.schemas import (
    FileCreate,
    FileOut,
    FileSaveOut,
    FileUpdate,
    UserRef,
    VersionOut,
    VersionRestoreOut,
    VersionTextOut,
)

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def create_file(
    project_id: int, payload: FileCreate, user: CurrentUser, db: DbSession
) -> FileOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # RBAC check: editor or higher can create files
    member = None
    if project.owner_id != user.id:
        res = await db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
        member = res.scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to create files in this project")
        if not at_least(member.role, Role.EDITOR):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor role required to create files")

    # Derive full path based on folder
    path = payload.path
    if payload.folder_id:
        folder = await db.get(Folder, payload.folder_id)
        if not folder or folder.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found in project")

        # In a real system, we'd recursively get the parent folder paths
        # For now, we use a simplified approach or store the full path in the folder
        # Since we want a proper tree, we'll assume the frontend provides the relative path from root or we derive it here.
        # For this implementation, we'll trust the provided path but link the folder_id.
        pass

    from app.runtime.registry import language_for_path

    file = File(
        project_id=project_id,
        folder_id=payload.folder_id,
        path=path,
        language=language_for_path(path),
    )
    db.add(file)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A file with that path already exists"
        ) from exc
    await db.refresh(file)

    # Broadcast structural update
    broadcast_payload = json.dumps({
        "op": "WORKSPACE_FILE_CREATED",
        "file": FileOut.model_validate(file).model_dump(mode="json")
    })
    room_manager.broadcast_to_project(project_id, broadcast_payload)

    return FileOut.model_validate(file)


@router.get("", response_model=list[FileOut])
async def list_files(
    project_id: int, user: CurrentUser, db: DbSession
) -> list[FileOut]:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # RBAC check: VIEWERS can list files, but they must be a member
    if project.owner_id != user.id:
        member_res = await db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
        if not member_res.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to list files in this project")

    file_res = await db.execute(select(File).where(File.project_id == project_id).order_by(File.id))
    return [FileOut.model_validate(f) for f in file_res.scalars().all()]


@router.patch("/{file_id}", response_model=FileOut)
async def update_file(
    project_id: int, file_id: int, payload: FileUpdate, user: CurrentUser, db: DbSession
) -> FileOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # RBAC check: editor or higher can rename/move files
    if project.owner_id != user.id:
        res = await db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
        member = res.scalar_one_or_none()
        if not member or not at_least(member.role, Role.EDITOR):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor role required to update files")

    file = await db.get(File, file_id)
    if not file or file.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if payload.path is not None:
        file.path = payload.path
        from app.runtime.registry import language_for_path
        file.language = language_for_path(payload.path)  # follows the new extension

    if "folder_id" in payload.model_fields_set:
        if payload.folder_id is not None:
            folder = await db.get(Folder, payload.folder_id)
            if not folder or folder.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Folder not found in project",
                )
        file.folder_id = payload.folder_id

    await db.commit()
    await db.refresh(file)

    # Broadcast structural update
    broadcast_payload = json.dumps({
        "op": "WORKSPACE_FILE_UPDATED",
        "file": FileOut.model_validate(file).model_dump(mode="json")
    })
    room_manager.broadcast_to_project(project_id, broadcast_payload)

    return FileOut.model_validate(file)


@router.get("/{file_id}/content")
async def get_latest_content(
    project_id: int, file_id: int, user: CurrentUser, db: DbSession
) -> dict:
    """Return the most recent CRDT checkpoint as plain text.

    Live editing is over WebSocket; this endpoint is for cold-load and version history.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # RBAC check: VIEWERS can read content
    if project.owner_id != user.id:
        member_res = await db.execute(
            select(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        )
        if not member_res.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access file content in this project")

    file = await db.get(File, file_id)
    if not file or file.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    version_res = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.file_id == file_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )
    latest = version_res.scalar_one_or_none()
    if not latest:
        return {"file_id": file_id, "text": "", "version": 0}

    # Decode the CRDT bytes back to plain text. We import lazily so the API works
    # even if pycrdt is being mocked in tests.
    from app.realtime.crdt import doc_from_bytes, doc_to_text

    doc = doc_from_bytes(latest.snapshot_bytes)
    return {
        "file_id": file_id,
        "text": doc_to_text(doc),
        "version": latest.version_number,
    }


@router.post("/{file_id}/save", response_model=FileSaveOut)
async def save_file(
    project_id: int, file_id: int, user: CurrentUser, db: DbSession
) -> FileSaveOut:
    """Write the live document as a new version now, instead of within a few seconds.

    Edits already sync to everyone as you type; Save is for "this is a good
    point": it makes a version you can go back to and records your changes
    in the project's recent-changes list right away.
    """
    member = await get_member(db, project_id, user.id)
    if RoleRank[member.role] < RoleRank[Role.EDITOR.value]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor role required to save files")
    file = await db.get(File, file_id)
    if not file or file.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    from app.realtime.checkpoint import save_room

    room = room_manager.get_room(project_id, file_id)
    if room is not None and room.hydrated:
        result = await save_room(room, user.id)
        return FileSaveOut(file_id=file_id, version=result.version, created=result.created, saved_at=result.saved_at)

    # Nobody has it open, so the latest version already has everything.
    latest = (await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.file_id == file_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    return FileSaveOut(
        file_id=file_id,
        version=latest.version_number if latest else None,
        created=False,
        saved_at=latest.created_at if latest else None,
    )


async def _project_file(db, project_id: int, file_id: int) -> File:
    file = await db.get(File, file_id)
    if not file or file.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


async def _version(db, file_id: int, number: int) -> DocumentVersion:
    version = (await db.execute(
        select(DocumentVersion).where(DocumentVersion.file_id == file_id, DocumentVersion.version_number == number)
    )).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return version


@router.get("/{file_id}/versions", response_model=list[VersionOut])
async def list_versions(
    project_id: int,
    file_id: int,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=500),
    saves_only: bool = False,
) -> list[VersionOut]:
    """The file's versions, newest first: autosaves (every few seconds of editing) and saves."""
    await get_member(db, project_id, user.id)
    await _project_file(db, project_id, file_id)
    query = (
        select(DocumentVersion.version_number, DocumentVersion.created_at, User.id, User.username)
        .outerjoin(User, User.id == DocumentVersion.saved_by)
        .where(DocumentVersion.file_id == file_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(limit)
    )
    if saves_only:
        query = query.where(DocumentVersion.saved_by.is_not(None))
    return [
        VersionOut(version=n, created_at=at, saved_by=UserRef(id=uid, username=name) if uid is not None else None)
        for n, at, uid, name in (await db.execute(query)).tuples()
    ]


@router.get("/{file_id}/versions/{number}", response_model=VersionTextOut)
async def get_version(
    project_id: int, file_id: int, number: int, user: CurrentUser, db: DbSession
) -> VersionTextOut:
    await get_member(db, project_id, user.id)
    await _project_file(db, project_id, file_id)
    version = await _version(db, file_id, number)

    from app.realtime.crdt import doc_from_bytes, doc_to_text

    return VersionTextOut(
        version=number, created_at=version.created_at, text=doc_to_text(doc_from_bytes(version.snapshot_bytes))
    )


@router.post("/{file_id}/versions/{number}/restore", response_model=VersionRestoreOut)
async def restore_version(
    project_id: int, file_id: int, number: int, user: CurrentUser, db: DbSession
) -> VersionRestoreOut:
    """Bring an old version back, as an edit by you that everyone with the file open sees."""
    member = await get_member(db, project_id, user.id)
    if RoleRank[member.role] < RoleRank[Role.EDITOR.value]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor role required to restore")
    await _project_file(db, project_id, file_id)
    version = await _version(db, file_id, number)

    from app.realtime.checkpoint import save_room
    from app.realtime.crdt import doc_from_bytes, doc_to_text
    from app.realtime.rooms import open_room, replace_text

    room = await open_room(project_id, file_id)
    changed = await replace_text(room, doc_to_text(doc_from_bytes(version.snapshot_bytes)), user.id)
    result = await save_room(room, user.id)
    return VersionRestoreOut(restored_from=number, version=result.version, changed=changed)
