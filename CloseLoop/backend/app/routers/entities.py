"""Entities — supports multi-group assignment with one primary group + bulk ops."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Entity, EntityGroup, EntityGroupMembership, User, Role
from ..schemas import EntityIn, EntityUpdate, EntityOut, EntityBulkAssignIn
from ..auth import require_tenant, require_roles
from ..services.access_service import get_accessible_entity_ids
from ..services.audit_service import log_action

router = APIRouter(prefix="/api/entities", tags=["entities"])


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
async def _enrich(
    db: AsyncSession, tenant_id: str, ents: list[Entity]
) -> list[EntityOut]:
    """Attach primary_group_name + group_names."""
    if not ents:
        return []
    all_gids: set[str] = set()
    for e in ents:
        all_gids.update(e.group_ids)
    if not all_gids:
        return [EntityOut.model_validate(e) for e in ents]
    rows = await db.execute(
        select(EntityGroup.id, EntityGroup.name).where(
            EntityGroup.tenant_id == tenant_id,
            EntityGroup.id.in_(all_gids),
        )
    )
    name_by_id = {gid: name for gid, name in rows.all()}
    out: list[EntityOut] = []
    for e in ents:
        item = EntityOut.model_validate(e)
        item.primary_group_name = name_by_id.get(e.group_id)
        item.group_names = [name_by_id.get(g, g) for g in e.group_ids]
        out.append(item)
    return out


async def _validate_groups(
    db: AsyncSession, tenant_id: str, group_ids: list[str]
) -> list[EntityGroup]:
    if not group_ids:
        raise HTTPException(400, "group_ids is required")
    groups = list(
        (
            await db.execute(
                select(EntityGroup).where(
                    EntityGroup.id.in_(group_ids),
                    EntityGroup.tenant_id == tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(groups) != len(group_ids):
        raise HTTPException(404, "One or more groups not found")
    inactive = [g for g in groups if not g.is_active]
    if inactive:
        names = ", ".join(g.name for g in inactive)
        raise HTTPException(400, f"Cannot assign inactive group(s): {names}")
    return groups


# ------------------------------------------------------------
# List + filter
# ------------------------------------------------------------
@router.get("", response_model=list[EntityOut])
async def list_entities(
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    search: Optional[str] = Query(None),
    group_id: Optional[str] = Query(
        None, description="Filter to entities in this group (primary or secondary)"
    ),
):
    assert me.tenant_id
    filters = [Entity.tenant_id == me.tenant_id]
    if search:
        filters.append(func.lower(Entity.name).like(f"%{search.lower()}%"))

    base_query = select(Entity).options(selectinload(Entity.groups)).where(*filters)
    if group_id:
        base_query = (
            base_query.outerjoin(
                EntityGroupMembership, Entity.id == EntityGroupMembership.entity_id
            )
            .where(
                or_(
                    Entity.group_id == group_id,
                    EntityGroupMembership.group_id == group_id,
                )
            )
            .distinct()
        )

    rows = await db.execute(base_query.order_by(Entity.name))
    ents = list(rows.scalars().unique().all())
    if me.role not in (Role.SUPER_ADMIN, Role.PLATFORM_OWNER):
        accessible = await get_accessible_entity_ids(db, me)
        ents = [e for e in ents if e.id in accessible]
    return await _enrich(db, me.tenant_id, ents)


# ------------------------------------------------------------
# Create
# ------------------------------------------------------------
@router.post(
    "",
    response_model=EntityOut,
    status_code=201,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def create_entity(
    payload: EntityIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    group_ids = list(dict.fromkeys(payload.group_ids or []))
    if not group_ids:
        raise HTTPException(400, "group_ids is required")

    primary_group_id = payload.primary_group_id or group_ids[0]
    if primary_group_id not in group_ids:
        # Ensure primary is included in membership set
        group_ids = [primary_group_id, *group_ids]

    await _validate_groups(db, me.tenant_id, group_ids)

    # Dup-name check (within tenant)
    dup = (
        await db.execute(
            select(Entity).where(
                Entity.tenant_id == me.tenant_id,
                func.lower(Entity.name) == payload.name.strip().lower(),
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "An entity with this name already exists")

    e = Entity(
        tenant_id=me.tenant_id,
        group_id=primary_group_id,
        name=payload.name.strip(),
        description=payload.description,
    )
    db.add(e)
    await db.flush()

    for gid in group_ids:
        db.add(
            EntityGroupMembership(
                tenant_id=me.tenant_id,
                entity_id=e.id,
                group_id=gid,
            )
        )

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_CREATED",
        entity_type="Entity",
        entity_id=e.id,
        meta={
            "name": e.name,
            "primary_group_id": e.group_id,
            "group_ids": group_ids,
        },
    )
    await db.commit()
    await db.refresh(e)
    enriched = await _enrich(db, me.tenant_id, [e])
    return enriched[0]


# ------------------------------------------------------------
# Update
# ------------------------------------------------------------
@router.patch(
    "/{entity_id}",
    response_model=EntityOut,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def update_entity(
    entity_id: str,
    payload: EntityUpdate,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    e = (
        await db.execute(
            select(Entity).where(
                Entity.id == entity_id, Entity.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Not found")
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")
        dup = (
            await db.execute(
                select(Entity).where(
                    Entity.tenant_id == me.tenant_id,
                    Entity.id != e.id,
                    func.lower(Entity.name) == new_name.lower(),
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "Another entity with this name exists")
        e.name = new_name
    if payload.description is not None:
        e.description = payload.description

    if payload.group_ids is not None or payload.primary_group_id is not None:
        # Resolve resulting group set
        new_group_ids = (
            list(dict.fromkeys(payload.group_ids))
            if payload.group_ids is not None
            else list(e.group_ids)
        )
        if not new_group_ids:
            raise HTTPException(400, "group_ids cannot be empty")
        new_primary = payload.primary_group_id or new_group_ids[0]
        if new_primary not in new_group_ids:
            new_group_ids = [new_primary, *new_group_ids]
        await _validate_groups(db, me.tenant_id, new_group_ids)

        e.group_id = new_primary
        await db.execute(
            delete(EntityGroupMembership).where(EntityGroupMembership.entity_id == e.id)
        )
        for gid in new_group_ids:
            db.add(
                EntityGroupMembership(
                    tenant_id=me.tenant_id,
                    entity_id=e.id,
                    group_id=gid,
                )
            )

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_UPDATED",
        entity_type="Entity",
        entity_id=e.id,
    )
    await db.commit()
    await db.refresh(e)
    enriched = await _enrich(db, me.tenant_id, [e])
    return enriched[0]


# ------------------------------------------------------------
# Bulk assign / unassign groups
# ------------------------------------------------------------
@router.post(
    "/bulk-assign-groups",
    response_model=list[EntityOut],
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def bulk_assign_groups(
    payload: EntityBulkAssignIn,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Attach/replace/detach groups for many entities at once.

    - mode='add'     : add memberships, preserving existing.
    - mode='replace' : overwrite full membership set (primary stays unless removed).
    - mode='remove'  : detach memberships (cannot detach primary group).
    """
    assert me.tenant_id
    mode = (payload.mode or "add").lower()
    if mode not in ("add", "replace", "remove"):
        raise HTTPException(400, "mode must be one of: add, replace, remove")
    if not payload.entity_ids:
        raise HTTPException(400, "entity_ids is required")
    if not payload.group_ids and mode != "replace":
        raise HTTPException(400, "group_ids is required")

    # Validate entities
    ents = list(
        (
            await db.execute(
                select(Entity)
                .options(selectinload(Entity.groups))
                .where(
                    Entity.id.in_(payload.entity_ids),
                    Entity.tenant_id == me.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(ents) != len(set(payload.entity_ids)):
        raise HTTPException(404, "One or more entities not found")

    target_gids = list(dict.fromkeys(payload.group_ids or []))
    if target_gids:
        await _validate_groups(db, me.tenant_id, target_gids)

    affected = 0
    for e in ents:
        current = set(e.group_ids)
        if mode == "add":
            for gid in target_gids:
                if gid in current:
                    continue
                db.add(
                    EntityGroupMembership(
                        tenant_id=me.tenant_id,
                        entity_id=e.id,
                        group_id=gid,
                    )
                )
                current.add(gid)
                affected += 1
        elif mode == "replace":
            new_set = set(target_gids)
            if e.group_id not in new_set:
                # Primary must be in new set; if user removed it, keep primary
                new_set.add(e.group_id)
            await db.execute(
                delete(EntityGroupMembership).where(
                    EntityGroupMembership.entity_id == e.id
                )
            )
            for gid in new_set:
                db.add(
                    EntityGroupMembership(
                        tenant_id=me.tenant_id,
                        entity_id=e.id,
                        group_id=gid,
                    )
                )
            affected += 1
        elif mode == "remove":
            to_remove = [gid for gid in target_gids if gid != e.group_id]
            if to_remove:
                await db.execute(
                    delete(EntityGroupMembership).where(
                        EntityGroupMembership.entity_id == e.id,
                        EntityGroupMembership.group_id.in_(to_remove),
                    )
                )
                affected += 1

    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_GROUPS_BULK_ASSIGN",
        entity_type="Entity",
        meta={
            "mode": mode,
            "entity_ids": payload.entity_ids,
            "group_ids": target_gids,
            "affected": affected,
        },
    )
    await db.commit()

    # Refresh + enrich
    refreshed = list(
        (
            await db.execute(
                select(Entity)
                .options(selectinload(Entity.groups))
                .where(
                    Entity.id.in_(payload.entity_ids),
                    Entity.tenant_id == me.tenant_id,
                )
                .order_by(Entity.name)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return await _enrich(db, me.tenant_id, refreshed)


# ------------------------------------------------------------
# Delete
# ------------------------------------------------------------
@router.delete(
    "/{entity_id}",
    status_code=204,
    dependencies=[Depends(require_roles(Role.SUPER_ADMIN))],
)
async def delete_entity(
    entity_id: str,
    me: User = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    assert me.tenant_id
    e = (
        await db.execute(
            select(Entity).where(
                Entity.id == entity_id, Entity.tenant_id == me.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Not found")
    await db.delete(e)
    await log_action(
        db,
        tenant_id=me.tenant_id,
        user_id=me.id,
        action="ENTITY_DELETED",
        entity_type="Entity",
        entity_id=entity_id,
        commit=False,
    )
    await db.commit()
