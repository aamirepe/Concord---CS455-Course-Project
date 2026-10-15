"""Project CRUD."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.deps import CurrentUser, DbSession, get_member
from app.models import (
    File,
    JoinRequestStatus,
    Project,
    ProjectJoinRequest,
    ProjectMember,
    Role,
    User,
)
from app.schemas import (
    JoinRequestDecision,
    JoinRequestOut,
    MemberAdd,
    ProjectCreate,
    ProjectMemberOut,
    ProjectOut,
    RoleUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, user: CurrentUser, db: DbSession) -> ProjectOut:
    # 1. Create the Project
    project = Project(owner_id=user.id, name=payload.name)
    db.add(project)
    await db.flush()

    # 2. Assign the user as the OWNER of this project
    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=Role.OWNER,
    )
    db.add(member)

    # 3. Bootstrap with a default main.py file
    starter_file = File(
        project_id=project.id,
        path="main.py",
        language="python",
    )
    db.add(starter_file)

    await db.commit()
    await db.refresh(project)
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, db: DbSession) -> list[ProjectOut]:
    # Get projects where user is owner OR a member
    stmt = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where((Project.owner_id == user.id) | (ProjectMember.user_id == user.id))
        .distinct()
        .order_by(Project.id)
    )
    res = await db.execute(stmt)
    return [ProjectOut.model_validate(p) for p in res.scalars().all()]


@router.post("/{project_id}/join", status_code=status.HTTP_200_OK)
async def join_project(project_id: int, user: CurrentUser, db: DbSession):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Check if user is already a member
    member_res = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )
    member = member_res.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project. You can request access from the owner."
        )

    return {"status": "success", "role": member.role}


async def _get_owned_project(db: AsyncSession, project_id: int, user: User) -> Project:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the project owner can review join requests"
        )
    return project


async def _get_pending_request(db: AsyncSession, project_id: int, request_id: int) -> ProjectJoinRequest:
    res = await db.execute(
        select(ProjectJoinRequest)
        .options(joinedload(ProjectJoinRequest.user))
        .where(ProjectJoinRequest.id == request_id, ProjectJoinRequest.project_id == project_id)
    )
    join_request = res.scalar_one_or_none()
    if not join_request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Join request not found")
    if join_request.status != JoinRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Join request is already {join_request.status}"
        )
    return join_request


@router.post(
    "/{project_id}/join-requests", response_model=JoinRequestOut, status_code=status.HTTP_201_CREATED
)
async def request_to_join(project_id: int, user: CurrentUser, db: DbSession) -> JoinRequestOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    member_res = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
    )
    if member_res.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You are already a member of this project")

    pending_res = await db.execute(
        select(ProjectJoinRequest).where(
            ProjectJoinRequest.project_id == project_id,
            ProjectJoinRequest.user_id == user.id,
            ProjectJoinRequest.status == JoinRequestStatus.PENDING,
        )
    )
    if pending_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You already have a pending request for this project"
        )

    join_request = ProjectJoinRequest(project_id=project_id, user_id=user.id)
    db.add(join_request)
    await db.commit()
    await db.refresh(join_request)
    return JoinRequestOut(
        id=join_request.id,
        project_id=project_id,
        user=user,
        status=join_request.status,
        created_at=join_request.created_at,
    )


@router.get("/{project_id}/join-requests", response_model=list[JoinRequestOut])
async def list_join_requests(project_id: int, user: CurrentUser, db: DbSession) -> list[JoinRequestOut]:
    await _get_owned_project(db, project_id, user)
    res = await db.execute(
        select(ProjectJoinRequest)
        .options(joinedload(ProjectJoinRequest.user))
        .where(
            ProjectJoinRequest.project_id == project_id,
            ProjectJoinRequest.status == JoinRequestStatus.PENDING,
        )
        .order_by(ProjectJoinRequest.created_at, ProjectJoinRequest.id)
    )
    return [JoinRequestOut.model_validate(r) for r in res.scalars().all()]


@router.post("/{project_id}/join-requests/{request_id}/approve", response_model=ProjectMemberOut)
async def approve_join_request(
    project_id: int,
    request_id: int,
    payload: JoinRequestDecision,
    user: CurrentUser,
    db: DbSession,
) -> ProjectMemberOut:
    await _get_owned_project(db, project_id, user)
    join_request = await _get_pending_request(db, project_id, request_id)

    # The owner may have added this user directly while the request was pending;
    # approving then just applies the chosen role.
    member_res = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == join_request.user_id
        )
    )
    member = member_res.scalar_one_or_none()
    if member:
        member.role = Role(payload.role)
    else:
        member = ProjectMember(project_id=project_id, user_id=join_request.user_id, role=Role(payload.role))
        db.add(member)

    join_request.status = JoinRequestStatus.APPROVED
    join_request.decided_at = datetime.now(UTC)
    requester = join_request.user
    await db.commit()
    return ProjectMemberOut(user=requester, role=payload.role)


@router.post("/{project_id}/join-requests/{request_id}/reject", response_model=JoinRequestOut)
async def reject_join_request(
    project_id: int, request_id: int, user: CurrentUser, db: DbSession
) -> JoinRequestOut:
    await _get_owned_project(db, project_id, user)
    join_request = await _get_pending_request(db, project_id, request_id)
    join_request.status = JoinRequestStatus.REJECTED
    join_request.decided_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(join_request, ["user"])
    return JoinRequestOut.model_validate(join_request)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, user: CurrentUser, db: DbSession) -> ProjectOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Allow access if user is owner OR a member
    if project.owner_id != user.id:
        try:
            await get_member(db, project_id, user.id)
        except HTTPException as exc:
            # If get_member fails, they aren't permitted to see this project
            raise exc

    return ProjectOut.model_validate(project)


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_project_member(project_id: int, payload: MemberAdd, user: CurrentUser, db: DbSession):
    # 1. Verify project existence and ownership
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the project owner can add members")

    role = Role(payload.role)

    # 2. Verify target user exists: by ID if numeric, else (or failing that) by username
    user_obj = None
    user_id_raw = str(payload.user_id).strip()
    if user_id_raw.isdigit():
        user_obj = await db.get(User, int(user_id_raw))
    if not user_obj and user_id_raw:
        res = await db.execute(select(User).where(User.username == user_id_raw))
        user_obj = res.scalar_one_or_none()

    if not user_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found. Please provide a valid User ID or Username.")
    if user_obj.id == project.owner_id:
        # Same rule as PATCH: the owner's own role row must stay "owner", or they
        # lose edit/run rights on their own project.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change the role of the project owner")

    # 3. Check if already a member
    member_res = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user_obj.id)
    )
    member = member_res.scalar_one_or_none()
    if member:
        member.role = role
    else:
        member = ProjectMember(project_id=project.id, user_id=user_obj.id, role=role)
        db.add(member)

    await db.commit()
    return {"message": f"User {user_obj.username} added as {role.value}"}


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
async def list_project_members(project_id: int, user: CurrentUser, db: DbSession):
    # 1. Verify project existence and membership
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.owner_id != user.id:
        try:
            await get_member(db, project_id, user.id)
        except HTTPException as exc:
            # If not owner and not a member, forbid access
            raise exc


    # 2. Get all members of the project
    member_res = await db.execute(
        select(ProjectMember).options(joinedload(ProjectMember.user)).where(ProjectMember.project_id == project_id)
    )
    members = member_res.scalars().all()

    return [
        ProjectMemberOut(user=m.user, role=m.role if isinstance(m.role, str) else m.role.value)
        for m in members
    ]


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_member(project_id: int, user_id: int, user: CurrentUser, db: DbSession):
    # 1. Verify project existence and ownership
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the project owner can remove members")

    if user_id == project.owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove the project owner")

    # 2. Find and remove the member
    member_res = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    member = member_res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(member)
    await db.commit()
    return None


@router.patch("/{project_id}/members/{user_id}", response_model=ProjectMemberOut)
async def update_project_member_role(
    project_id: int,
    user_id: int,
    payload: RoleUpdate,
    user: CurrentUser,
    db: DbSession
):
    # 1. Verify project existence and ownership
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the project owner can change member roles")

    # 2. Verify the target user is a member of the project
    member_res = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    member = member_res.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this project")

    if user_id == project.owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change the role of the project owner")

    # 3. Update the role
    try:
        member.role = Role(payload.role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of {[r.value for r in Role]}"
        ) from None

    await db.commit()
    await db.refresh(member)

    # Return ProjectMemberOut which requires joined User
    # Since member.user might not be loaded, we'll do a quick lookup or rely on joinedload if we had it.
    # To be safe and consistent with list_project_members:
    member_with_user = await db.execute(
        select(ProjectMember).options(joinedload(ProjectMember.user)).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    m = member_with_user.scalar_one()
    return ProjectMemberOut(user=m.user, role=m.role if isinstance(m.role, str) else m.role.value)

