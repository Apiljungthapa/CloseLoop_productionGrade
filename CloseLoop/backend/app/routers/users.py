"""Users + entity/group assignments (bulk + combined view)."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    User,
    Role,
    Entity,
    EntityGroup,
    UserEntityAssignment,
    UserEntityGroupAssignment,
)
from ..schemas import (
    UserIn,
    UserUpdate,
    UserOut,
    UserEntityAssignIn,
    UserEntityAssignOut,
    UserEntityAssignBulkIn,
    UserGroupAssignIn,
    UserGroupAssignOut,
    UserGroupAssignBulkIn,
    UserAssignmentsOut,
    EntityMini,
    EntityGroupMini,
)
from ..auth import require_tenant, hash_password_async, require_roles
from ..services.audit_service import log_action

router = APIRouter(prefix="/api/users", tags=["users"])


# ---- listing / CRUD ----
@router.get("", response_model=list[UserOut])
async def list_users(
    me: User = Depends(require_tenant), db: AsyncSession = Depends(get_db)
):
    # Team Member has no legitimate need to browse the tenant roster —
    # no member-facing feature depends on it (dropdowns that use this
    # endpoint are all lead/admin-only UI). Return just their own record
    # rather than leaking every admin/lead/peer's name+email+role.
    if me.role == Role.TEAM_MEMBER:
        return [UserOut.model_validate(me)]

    filters = [User.tenant_id == me.tenant_id]

    # Team Lead must NOT see Super Admins or Platform Owners
    if me.role == Role.TEAM_LEAD:
        filters.append(User.role.notin_([Role.SUPER_ADMIN, Role.PLATFORM_OWNER]))

    rows = await db.execute(
        select(User).where(*filters).order_by(User.created_at)
    )
    users = [UserOut.model_validate(u) for u in rows.scalars().all()]

    # Team Lead only sees peers: other team leads and team members
    if me.role == Role.TEAM_LEAD:
        users = [u for u in users if u.role in (Role.TEAM_LEAD, Role.TEAM_MEMBER)]

    return users


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    if me.role not in (Role.SUPER_ADMIN, Role.TEAM_LEAD):
        raise HTTPException(
            403, "Only Super Admin / Team Lead may create users"
        )

    # Role rules: SUPER_ADMIN may create anyone below; TEAM_LEAD may create
    # TEAM_MEMBER only.
    if me.role == Role.TEAM_LEAD and payload.role != Role.TEAM_MEMBER:
        raise HTTPException(403, "Team Lead may only create TEAM_MEMBER")
    if me.role == Role.SUPER_ADMIN and payload.role == Role.PLATFORM_OWNER:
        raise HTTPException(403, "Cannot create PLATFORM_OWNER from a tenant scope")

    existing = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Email already in use")

    # validate primary entity belongs to tenant
    if payload.primary_entity_id:
        e = (
            await db.execute(
                select(Entity).where(
                    Entity.id == payload.primary_entity_id,
                    Entity.tenant_id == me.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not e:
            raise HTTPException(404, "Primary entity not found")

    u = User(
        tenant_id=me.tenant_id,
        email=payload.email,
        name=payload.name,
        password_hash=await hash_password_async(payload.password),
        role=payload.role,
        title=payload.title,
        primary_entity_id=payload.primary_entity_id,
        invited_by_id=me.id,
    )
    db.add(u)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_CREATED",
        entity_type="User",
        entity_id=u.id,
        meta={"email": u.email, "role": u.role.value},
    )
    await db.commit()
    await db.refresh(u)
    return UserOut.model_validate(u)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    if me.role != Role.SUPER_ADMIN and me.id != user_id:
        raise HTTPException(403, "Not allowed")
    u = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    if payload.name is not None:
        u.name = payload.name
    if payload.role is not None:
        if me.role != Role.SUPER_ADMIN:
            raise HTTPException(403, "Only Super Admin may change role")
        if payload.role == Role.PLATFORM_OWNER:
            raise HTTPException(403, "Cannot assign PLATFORM_OWNER from a tenant scope")
        u.role = payload.role
    if payload.title is not None:
        if me.role != Role.SUPER_ADMIN:
            raise HTTPException(403, "Only Super Admin may change title")
        u.title = payload.title or None
    if payload.primary_entity_id is not None:
        e = (
            await db.execute(
                select(Entity).where(
                    Entity.id == payload.primary_entity_id,
                    Entity.tenant_id == me.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not e:
            raise HTTPException(404, "Primary entity not found")
        u.primary_entity_id = payload.primary_entity_id
    if payload.password:
        u.password_hash = await hash_password_async(payload.password)

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_UPDATED",
        entity_type="User",
        entity_id=u.id,
    )
    await db.commit()
    await db.refresh(u)
    return UserOut.model_validate(u)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    if me.role != Role.SUPER_ADMIN:
        raise HTTPException(403, "Only Super Admin can delete users")
    if user_id == me.id:
        raise HTTPException(400, "Cannot delete yourself")
    u = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "Not found")
    await db.delete(u)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_DELETED",
        entity_type="User",
        entity_id=user_id,
        commit=False,
    )
    await db.commit()


# ---- Combined assignments view ----
@router.get("/{user_id}/assignments", response_model=UserAssignmentsOut)
async def user_assignments(
    user_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Return primary entity + secondary entities + group assignments for a user."""
    assert me.tenant_id
    u = (
        await db.execute(
            select(User).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    primary: Optional[EntityMini] = None
    if u.primary_entity_id:
        pe = (
            await db.execute(
                select(Entity).where(
                    Entity.id == u.primary_entity_id,
                    Entity.tenant_id == me.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if pe:
            primary = EntityMini.model_validate(pe)

    # Secondary entities
    sec_rows = await db.execute(
        select(Entity)
        .join(UserEntityAssignment, UserEntityAssignment.entity_id == Entity.id)
        .where(
            UserEntityAssignment.user_id == user_id,
            UserEntityAssignment.tenant_id == me.tenant_id,
        )
        .order_by(Entity.name)
    )
    secondary = [EntityMini.model_validate(e) for e in sec_rows.scalars().all()]

    # Groups
    grp_rows = await db.execute(
        select(EntityGroup)
        .join(
            UserEntityGroupAssignment,
            UserEntityGroupAssignment.group_id == EntityGroup.id,
        )
        .where(
            UserEntityGroupAssignment.user_id == user_id,
            UserEntityGroupAssignment.tenant_id == me.tenant_id,
        )
        .order_by(EntityGroup.name)
    )
    groups = [EntityGroupMini.model_validate(g) for g in grp_rows.scalars().all()]

    return UserAssignmentsOut(
        user_id=user_id,
        primary_entity=primary,
        secondary_entities=secondary,
        groups=groups,
    )


# ---- Entity assignments ----
@router.get("/{user_id}/entity-assignments", response_model=list[UserEntityAssignOut])
async def list_entity_assignments(
    user_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    target = (
        await db.execute(
            select(User.id).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    rows = await db.execute(
        select(UserEntityAssignment, Entity.name)
        .outerjoin(Entity, Entity.id == UserEntityAssignment.entity_id)
        .where(
            UserEntityAssignment.user_id == user_id,
            UserEntityAssignment.tenant_id == me.tenant_id,
        )
    )
    out: list[UserEntityAssignOut] = []
    for a, ent_name in rows.all():
        item = UserEntityAssignOut.model_validate(a)
        item.entity_name = ent_name
        out.append(item)
    return out


@router.post(
    "/entity-assignments",
    response_model=UserEntityAssignOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def assign_user_to_entity(
    payload: UserEntityAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    e = (
        await db.execute(
            select(Entity).where(
                Entity.id == payload.entity_id, Entity.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Entity not found")
    u = (
        await db.execute(
            select(User).where(
                User.id == payload.user_id, User.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    if u.primary_entity_id == payload.entity_id:
        raise HTTPException(
            400,
            "This entity is already the user's primary entity",
        )

    existing = (
        await db.execute(
            select(UserEntityAssignment).where(
                UserEntityAssignment.user_id == payload.user_id,
                UserEntityAssignment.entity_id == payload.entity_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        item = UserEntityAssignOut.model_validate(existing)
        item.entity_name = e.name
        return item

    a = UserEntityAssignment(
        tenant_id=me.tenant_id, user_id=payload.user_id, entity_id=payload.entity_id
    )
    db.add(a)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_ENTITY_ASSIGNED",
        entity_type="UserEntityAssignment",
        meta={"user_id": payload.user_id, "entity_id": payload.entity_id},
    )
    await db.commit()
    await db.refresh(a)
    item = UserEntityAssignOut.model_validate(a)
    item.entity_name = e.name
    return item


@router.post(
    "/entity-assignments/bulk",
    response_model=list[UserEntityAssignOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def bulk_assign_entities(
    payload: UserEntityAssignBulkIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Bulk attach/replace/detach secondary entities for a user.

    mode='add'     : add the given entities.
    mode='replace' : overwrite the full secondary-entity set.
    mode='remove'  : detach the given entities.
    """
    assert me.tenant_id
    mode = (payload.mode or "add").lower()
    if mode not in ("add", "replace", "remove"):
        raise HTTPException(400, "mode must be one of: add, replace, remove")

    u = (
        await db.execute(
            select(User).where(
                User.id == payload.user_id, User.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    entity_ids = list(dict.fromkeys(payload.entity_ids or []))
    # Reject assigning the primary as secondary
    if u.primary_entity_id and u.primary_entity_id in entity_ids:
        entity_ids = [eid for eid in entity_ids if eid != u.primary_entity_id]

    # Validate entities (only those we'll touch)
    target_ids = entity_ids
    if mode == "replace":
        target_ids = entity_ids  # may be empty -> wipe all
    if target_ids:
        valid = (
            await db.execute(
                select(Entity.id).where(
                    Entity.id.in_(target_ids), Entity.tenant_id == me.tenant_id
                )
            )
        ).all()
        valid_ids = {r[0] for r in valid}
        unknown = [eid for eid in target_ids if eid not in valid_ids]
        if unknown:
            raise HTTPException(404, f"Entities not found: {unknown}")

    if mode == "replace":
        await db.execute(
            delete(UserEntityAssignment).where(
                UserEntityAssignment.user_id == payload.user_id,
                UserEntityAssignment.tenant_id == me.tenant_id,
            )
        )
        for eid in entity_ids:
            db.add(
                UserEntityAssignment(
                    tenant_id=me.tenant_id,
                    user_id=payload.user_id,
                    entity_id=eid,
                )
            )
    elif mode == "remove":
        if entity_ids:
            await db.execute(
                delete(UserEntityAssignment).where(
                    UserEntityAssignment.user_id == payload.user_id,
                    UserEntityAssignment.tenant_id == me.tenant_id,
                    UserEntityAssignment.entity_id.in_(entity_ids),
                )
            )
    else:  # add
        existing_rows = (
            await db.execute(
                select(UserEntityAssignment.entity_id).where(
                    UserEntityAssignment.user_id == payload.user_id,
                    UserEntityAssignment.tenant_id == me.tenant_id,
                )
            )
        ).all()
        existing_set = {r[0] for r in existing_rows}
        for eid in entity_ids:
            if eid in existing_set:
                continue
            db.add(
                UserEntityAssignment(
                    tenant_id=me.tenant_id,
                    user_id=payload.user_id,
                    entity_id=eid,
                )
            )

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_ENTITY_BULK_ASSIGN",
        entity_type="UserEntityAssignment",
        meta={"user_id": payload.user_id, "mode": mode, "entity_ids": entity_ids},
    )
    await db.commit()

    rows = await db.execute(
        select(UserEntityAssignment, Entity.name)
        .outerjoin(Entity, Entity.id == UserEntityAssignment.entity_id)
        .where(
            UserEntityAssignment.user_id == payload.user_id,
            UserEntityAssignment.tenant_id == me.tenant_id,
        )
    )
    out: list[UserEntityAssignOut] = []
    for a, name in rows.all():
        item = UserEntityAssignOut.model_validate(a)
        item.entity_name = name
        out.append(item)
    return out


@router.delete(
    "/entity-assignments/{assignment_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def remove_user_entity_assignment(
    assignment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    a = (
        await db.execute(
            select(UserEntityAssignment).where(
                UserEntityAssignment.id == assignment_id,
                UserEntityAssignment.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Not found")
    await db.delete(a)
    await db.commit()


# ---- Group assignments ----
@router.get("/{user_id}/group-assignments", response_model=list[UserGroupAssignOut])
async def list_group_assignments(
    user_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    target = (
        await db.execute(
            select(User.id).where(User.id == user_id, User.tenant_id == me.tenant_id)
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    rows = await db.execute(
        select(UserEntityGroupAssignment, EntityGroup.name)
        .outerjoin(EntityGroup, EntityGroup.id == UserEntityGroupAssignment.group_id)
        .where(
            UserEntityGroupAssignment.user_id == user_id,
            UserEntityGroupAssignment.tenant_id == me.tenant_id,
        )
    )
    out: list[UserGroupAssignOut] = []
    for a, gname in rows.all():
        item = UserGroupAssignOut.model_validate(a)
        item.group_name = gname
        out.append(item)
    return out


@router.post(
    "/group-assignments",
    response_model=UserGroupAssignOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def assign_user_to_group(
    payload: UserGroupAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    g = (
        await db.execute(
            select(EntityGroup).where(
                EntityGroup.id == payload.group_id,
                EntityGroup.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Group not found")
    u = (
        await db.execute(
            select(User).where(
                User.id == payload.user_id, User.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    existing = (
        await db.execute(
            select(UserEntityGroupAssignment).where(
                UserEntityGroupAssignment.user_id == payload.user_id,
                UserEntityGroupAssignment.group_id == payload.group_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        item = UserGroupAssignOut.model_validate(existing)
        item.group_name = g.name
        return item

    a = UserEntityGroupAssignment(
        tenant_id=me.tenant_id, user_id=payload.user_id, group_id=payload.group_id
    )
    db.add(a)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_GROUP_ASSIGNED",
        entity_type="UserEntityGroupAssignment",
        meta={"user_id": payload.user_id, "group_id": payload.group_id},
    )
    await db.commit()
    await db.refresh(a)
    item = UserGroupAssignOut.model_validate(a)
    item.group_name = g.name
    return item


@router.post(
    "/group-assignments/bulk",
    response_model=list[UserGroupAssignOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def bulk_assign_groups(
    payload: UserGroupAssignBulkIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    mode = (payload.mode or "add").lower()
    if mode not in ("add", "replace", "remove"):
        raise HTTPException(400, "mode must be one of: add, replace, remove")

    u = (
        await db.execute(
            select(User).where(
                User.id == payload.user_id, User.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")

    group_ids = list(dict.fromkeys(payload.group_ids or []))
    if group_ids:
        valid = (
            await db.execute(
                select(EntityGroup.id).where(
                    EntityGroup.id.in_(group_ids),
                    EntityGroup.tenant_id == me.tenant_id,
                )
            )
        ).all()
        valid_ids = {r[0] for r in valid}
        unknown = [gid for gid in group_ids if gid not in valid_ids]
        if unknown:
            raise HTTPException(404, f"Groups not found: {unknown}")

    if mode == "replace":
        await db.execute(
            delete(UserEntityGroupAssignment).where(
                UserEntityGroupAssignment.user_id == payload.user_id,
                UserEntityGroupAssignment.tenant_id == me.tenant_id,
            )
        )
        for gid in group_ids:
            db.add(
                UserEntityGroupAssignment(
                    tenant_id=me.tenant_id,
                    user_id=payload.user_id,
                    group_id=gid,
                )
            )
    elif mode == "remove":
        if group_ids:
            await db.execute(
                delete(UserEntityGroupAssignment).where(
                    UserEntityGroupAssignment.user_id == payload.user_id,
                    UserEntityGroupAssignment.tenant_id == me.tenant_id,
                    UserEntityGroupAssignment.group_id.in_(group_ids),
                )
            )
    else:
        existing_rows = (
            await db.execute(
                select(UserEntityGroupAssignment.group_id).where(
                    UserEntityGroupAssignment.user_id == payload.user_id,
                    UserEntityGroupAssignment.tenant_id == me.tenant_id,
                )
            )
        ).all()
        existing_set = {r[0] for r in existing_rows}
        for gid in group_ids:
            if gid in existing_set:
                continue
            db.add(
                UserEntityGroupAssignment(
                    tenant_id=me.tenant_id,
                    user_id=payload.user_id,
                    group_id=gid,
                )
            )

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="USER_GROUP_BULK_ASSIGN",
        entity_type="UserEntityGroupAssignment",
        meta={"user_id": payload.user_id, "mode": mode, "group_ids": group_ids},
    )
    await db.commit()

    rows = await db.execute(
        select(UserEntityGroupAssignment, EntityGroup.name)
        .outerjoin(EntityGroup, EntityGroup.id == UserEntityGroupAssignment.group_id)
        .where(
            UserEntityGroupAssignment.user_id == payload.user_id,
            UserEntityGroupAssignment.tenant_id == me.tenant_id,
        )
    )
    out: list[UserGroupAssignOut] = []
    for a, gname in rows.all():
        item = UserGroupAssignOut.model_validate(a)
        item.group_name = gname
        out.append(item)
    return out


@router.delete(
    "/group-assignments/{assignment_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def remove_user_group_assignment(
    assignment_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    a = (
        await db.execute(
            select(UserEntityGroupAssignment).where(
                UserEntityGroupAssignment.id == assignment_id,
                UserEntityGroupAssignment.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Not found")
    await db.delete(a)
    await db.commit()
