"""Entity groups — recursive hierarchy with search, filter, and activation."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    EntityGroup,
    EntityGroupType,
    Entity,
    EntityGroupMembership,
    User,
    Role,
)
from ..schemas import (
    EntityGroupIn,
    EntityGroupUpdate,
    EntityGroupOut,
    EntityGroupTreeOut,
    EntityOut,
)
from ..auth import require_tenant, require_roles
from ..services.access_service import get_accessible_group_ids
from ..services.audit_service import log_action

router = APIRouter(prefix="/api/entity-groups", tags=["entity-groups"])


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
async def _entity_counts(db: AsyncSession, tenant_id: str) -> dict[str, int]:
    """Count entities per group (primary + secondary)."""
    primary_rows = await db.execute(
        select(Entity.group_id, func.count(Entity.id))
        .where(Entity.tenant_id == tenant_id)
        .group_by(Entity.group_id)
    )
    counts: dict[str, set[str]] = {}
    primary = {gid: cnt for gid, cnt in primary_rows.all()}

    # Build accurate distinct-entity counts via membership table
    mem_rows = await db.execute(
        select(EntityGroupMembership.group_id, EntityGroupMembership.entity_id).where(
            EntityGroupMembership.tenant_id == tenant_id
        )
    )
    for gid, eid in mem_rows.all():
        counts.setdefault(gid, set()).add(eid)

    # Also include primary group_id entities (in case some entities aren't in membership table yet)
    e_rows = await db.execute(
        select(Entity.group_id, Entity.id).where(Entity.tenant_id == tenant_id)
    )
    for gid, eid in e_rows.all():
        counts.setdefault(gid, set()).add(eid)

    result = {gid: len(ids) for gid, ids in counts.items()}
    # ensure primary-only groups (without membership rows) still appear
    for gid, n in primary.items():
        result.setdefault(gid, n)
    return result


async def _is_descendant(
    db: AsyncSession, tenant_id: str, candidate_parent_id: str, group_id: str
) -> bool:
    """Return True if `candidate_parent_id` is `group_id` itself or any descendant of it.

    Used to prevent cycles when moving a group under a new parent.
    """
    if candidate_parent_id == group_id:
        return True
    frontier = [group_id]
    seen: set[str] = set()
    while frontier:
        rows = await db.execute(
            select(EntityGroup.id).where(
                EntityGroup.tenant_id == tenant_id,
                EntityGroup.parent_id.in_(frontier),
            )
        )
        children = [r[0] for r in rows.all()]
        if candidate_parent_id in children:
            return True
        frontier = [c for c in children if c not in seen]
        seen.update(frontier)
    return False


def _attach_counts(g: EntityGroup, counts: dict[str, int]) -> EntityGroupOut:
    base = EntityGroupOut.model_validate(g)
    base.entity_count = counts.get(g.id, 0)
    return base


# ------------------------------------------------------------
# List + search
# ------------------------------------------------------------
@router.get("", response_model=list[EntityGroupOut])
async def list_groups(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    search: Optional[str] = Query(None, description="Case-insensitive name match"),
    active: Optional[bool] = Query(None),
    type: Optional[EntityGroupType] = Query(None),
    parent_id: Optional[str] = Query(
        None, description="Filter by parent (use 'root' for top-level)"
    ),
):
    filters = [EntityGroup.tenant_id == me.tenant_id]
    if search:
        filters.append(func.lower(EntityGroup.name).like(f"%{search.lower()}%"))
    if active is not None:
        filters.append(EntityGroup.is_active == active)
    if type is not None:
        filters.append(EntityGroup.type == type)
    if parent_id is not None:
        if parent_id in ("root", "null", ""):
            filters.append(EntityGroup.parent_id.is_(None))
        else:
            filters.append(EntityGroup.parent_id == parent_id)

    rows = await db.execute(
        select(EntityGroup).where(*filters).order_by(EntityGroup.name)
    )
    groups = list(rows.scalars().all())
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        accessible = await get_accessible_group_ids(db, me)
        groups = [g for g in groups if g.id in accessible]

    counts = await _entity_counts(db, me.tenant_id)  # type: ignore[arg-type]
    return [_attach_counts(g, counts) for g in groups]


# ------------------------------------------------------------
# Tree view (with entity counts on every node)
# ------------------------------------------------------------
@router.get("/tree", response_model=list[EntityGroupTreeOut])
async def group_tree(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(True),
):
    g_rows = await db.execute(
        select(EntityGroup)
        .where(EntityGroup.tenant_id == me.tenant_id)
        .order_by(EntityGroup.name)
    )
    all_groups = list(g_rows.scalars().all())
    if not include_inactive:
        all_groups = [g for g in all_groups if g.is_active]

    e_rows = await db.execute(
        select(Entity)
        .options(selectinload(Entity.groups))
        .where(Entity.tenant_id == me.tenant_id)
    )
    all_entities = list(e_rows.scalars().all())
    m_rows = await db.execute(
        select(EntityGroupMembership).where(
            EntityGroupMembership.tenant_id == me.tenant_id
        )
    )
    all_memberships = list(m_rows.scalars().all())

    by_parent: dict[Optional[str], list[EntityGroup]] = {}
    for g in all_groups:
        by_parent.setdefault(g.parent_id, []).append(g)

    ents_by_group: dict[str, dict[str, Entity]] = {}
    for e in all_entities:
        ents_by_group.setdefault(e.group_id, {})[e.id] = e
    for m in all_memberships:
        group_entities = ents_by_group.setdefault(m.group_id, {})
        if m.entity_id not in group_entities and m.entity is not None:
            group_entities[m.entity_id] = m.entity

    accessible_groups: set[str] | None = None
    accessible_entities: set[str] | None = None
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        accessible_groups = await get_accessible_group_ids(db, me)
        from ..services.access_service import get_accessible_entity_ids

        accessible_entities = await get_accessible_entity_ids(db, me)

    counts = await _entity_counts(db, me.tenant_id)  # type: ignore[arg-type]

    def build(node: EntityGroup) -> EntityGroupTreeOut:
        children_models = by_parent.get(node.id, [])
        if accessible_groups is not None:
            children_models = [c for c in children_models if c.id in accessible_groups]
        ents_dict = ents_by_group.get(node.id, {})
        ents_models = list(ents_dict.values())
        if accessible_entities is not None:
            ents_models = [e for e in ents_models if e.id in accessible_entities]
        ents_models.sort(key=lambda x: x.name.lower())
        children_models.sort(key=lambda x: x.name.lower())

        return EntityGroupTreeOut(
            id=node.id,
            tenant_id=node.tenant_id,
            parent_id=node.parent_id,
            name=node.name,
            type=node.type,
            description=node.description,
            is_active=node.is_active,
            created_at=node.created_at,
            entity_count=counts.get(node.id, 0),
            children=[build(c) for c in children_models],
            entities=[EntityOut.model_validate(e) for e in ents_models],
        )

    roots = by_parent.get(None, [])
    if accessible_groups is not None:
        roots = [r for r in roots if r.id in accessible_groups]
    roots.sort(key=lambda x: x.name.lower())
    return [build(r) for r in roots]


# ------------------------------------------------------------
# Create
# ------------------------------------------------------------
@router.post(
    "",
    response_model=EntityGroupOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def create_group(
    payload: EntityGroupIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    if payload.parent_id:
        parent = (
            await db.execute(
                select(EntityGroup).where(
                    EntityGroup.id == payload.parent_id,
                    EntityGroup.tenant_id == me.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent group not found")

    # Duplicate-name check (case-insensitive) within same parent scope
    dup = (
        await db.execute(
            select(EntityGroup).where(
                EntityGroup.tenant_id == me.tenant_id,
                EntityGroup.parent_id == payload.parent_id,
                func.lower(EntityGroup.name) == payload.name.strip().lower(),
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A group with this name already exists at this level")

    g = EntityGroup(
        tenant_id=me.tenant_id,
        parent_id=payload.parent_id,
        name=payload.name.strip(),
        type=payload.type,
        description=payload.description,
        is_active=payload.is_active,
    )
    db.add(g)
    await db.flush()
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_GROUP_CREATED",
        entity_type="EntityGroup",
        entity_id=g.id,
        meta={"name": g.name, "parent_id": g.parent_id, "type": g.type.value},
    )
    await db.commit()
    await db.refresh(g)
    return EntityGroupOut.model_validate(g)


# ------------------------------------------------------------
# Update
# ------------------------------------------------------------
@router.patch(
    "/{group_id}",
    response_model=EntityGroupOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_group(
    group_id: str,
    payload: EntityGroupUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    g = (
        await db.execute(
            select(EntityGroup).where(
                EntityGroup.id == group_id, EntityGroup.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Not found")

    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")
        # uniqueness within sibling scope
        target_parent = (
            payload.parent_id if payload.parent_id is not None else g.parent_id
        )
        dup = (
            await db.execute(
                select(EntityGroup).where(
                    EntityGroup.tenant_id == me.tenant_id,
                    EntityGroup.parent_id == target_parent,
                    EntityGroup.id != g.id,
                    func.lower(EntityGroup.name) == new_name.lower(),
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(
                409, "Another group with this name exists at this level"
            )
        g.name = new_name
    if payload.type is not None:
        g.type = payload.type
    if payload.description is not None:
        g.description = payload.description
    if payload.is_active is not None:
        g.is_active = payload.is_active
    if payload.parent_id is not None:
        if payload.parent_id == "":
            new_parent: Optional[str] = None
        else:
            new_parent = payload.parent_id
        if new_parent is not None:
            if new_parent == group_id:
                raise HTTPException(400, "Group cannot be its own parent")
            parent_exists = (
                await db.execute(
                    select(EntityGroup).where(
                        EntityGroup.id == new_parent,
                        EntityGroup.tenant_id == me.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if not parent_exists:
                raise HTTPException(404, "Parent group not found")
            if await _is_descendant(db, me.tenant_id, new_parent, group_id):
                raise HTTPException(
                    400, "Cannot move group under itself or its descendants"
                )
        g.parent_id = new_parent

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_GROUP_UPDATED",
        entity_type="EntityGroup",
        entity_id=g.id,
    )
    await db.commit()
    await db.refresh(g)
    return EntityGroupOut.model_validate(g)


# ------------------------------------------------------------
# Activate / Deactivate
# ------------------------------------------------------------
async def _toggle_active(
    db: AsyncSession, me: User, group_id: str, value: bool
) -> EntityGroup:
    assert me.tenant_id
    g = (
        await db.execute(
            select(EntityGroup).where(
                EntityGroup.id == group_id, EntityGroup.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Not found")
    g.is_active = value
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_GROUP_ACTIVATED" if value else "ENTITY_GROUP_DEACTIVATED",
        entity_type="EntityGroup",
        entity_id=g.id,
    )
    await db.commit()
    await db.refresh(g)
    return g


@router.post(
    "/{group_id}/activate",
    response_model=EntityGroupOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def activate_group(
    group_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    g = await _toggle_active(db, me, group_id, True)
    return EntityGroupOut.model_validate(g)


@router.post(
    "/{group_id}/deactivate",
    response_model=EntityGroupOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def deactivate_group(
    group_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    g = await _toggle_active(db, me, group_id, False)
    return EntityGroupOut.model_validate(g)


# ------------------------------------------------------------
# Delete
# ------------------------------------------------------------
@router.delete(
    "/{group_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_group(
    group_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    g = (
        await db.execute(
            select(EntityGroup).where(
                EntityGroup.id == group_id, EntityGroup.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not g:
        raise HTTPException(404, "Not found")

    # Guard: refuse delete if there are primary-entity children OR child groups
    child_count = (
        await db.execute(
            select(func.count(EntityGroup.id)).where(
                EntityGroup.parent_id == group_id,
                EntityGroup.tenant_id == me.tenant_id,
            )
        )
    ).scalar_one()
    if child_count:
        raise HTTPException(400, "Group has child groups; move or delete them first")
    ent_count = (
        await db.execute(
            select(func.count(Entity.id)).where(
                Entity.group_id == group_id, Entity.tenant_id == me.tenant_id
            )
        )
    ).scalar_one()
    if ent_count:
        raise HTTPException(
            400, "Group has primary entities; reassign them before deleting"
        )

    await db.delete(g)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_GROUP_DELETED",
        entity_type="EntityGroup",
        entity_id=group_id,
        commit=False,
    )
    await db.commit()
