"""
Access service — resolves which entities a user can act on.

PLATFORM_OWNER   : no tenant access (manages tenants)
SUPER_ADMIN      : all entities in their tenant
TEAM_LEAD        : primary_entity + extra UserEntityAssignment rows,
                   plus any assigned groups + descendant groups + entities below
TEAM_MEMBER      : primary_entity + extra UserEntityAssignment rows
"""

from __future__ import annotations
from typing import List, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from ..models import (
    User,
    Role,
    Entity,
    EntityGroup,
    EntityGroupMembership,
    UserEntityAssignment,
    UserEntityGroupAssignment,
)


async def _descendant_group_ids(
    db: AsyncSession,
    tenant_id: str,
    root_ids: List[str],
    include_inactive: bool = True,
) -> Set[str]:
    """Recursively collect all descendant group ids in this tenant.

    When include_inactive=False, inactive groups (and their subtrees) are skipped.
    """
    if not root_ids:
        return set()
    # filter roots themselves
    if not include_inactive:
        active_root_rows = await db.execute(
            select(EntityGroup.id).where(
                EntityGroup.tenant_id == tenant_id,
                EntityGroup.id.in_(root_ids),
                EntityGroup.is_active.is_(True),
            )
        )
        root_ids = [r[0] for r in active_root_rows.all()]
        if not root_ids:
            return set()

    all_ids: Set[str] = set(root_ids)
    frontier: List[str] = list(root_ids)
    while frontier:
        q = select(EntityGroup.id).where(
            EntityGroup.tenant_id == tenant_id,
            EntityGroup.parent_id.in_(frontier),
        )
        if not include_inactive:
            q = q.where(EntityGroup.is_active.is_(True))
        result = await db.execute(q)
        children = [row[0] for row in result.all()]
        new = [c for c in children if c not in all_ids]
        all_ids.update(new)
        frontier = new
    return all_ids


async def get_lead_direct_entity_ids(db: AsyncSession, user: User) -> Set[str]:
    """Return only the entity_ids a Team Lead directly belongs to.

    "Directly belongs to" means:
      * Their primary_entity_id, OR
      * An explicit UserEntityAssignment (secondary entity).

    Group-based access is intentionally EXCLUDED — Team Lead plan/dashboard
    visibility must not span a whole group just because the lead manages
    that group structurally. This keeps Plan/Pack visibility tight: a lead
    sees only entities they personally belong to, plus plans where a task
    has been explicitly assigned to them (handled at the call site).

    Tenant isolation is enforced (returns empty set if no tenant_id).
    """
    if not user.tenant_id:
        return set()

    ids: Set[str] = set()

    if user.primary_entity_id:
        ids.add(user.primary_entity_id)

    result = await db.execute(
        select(UserEntityAssignment.entity_id).where(
            UserEntityAssignment.user_id == user.id,
            UserEntityAssignment.tenant_id == user.tenant_id,
        )
    )
    ids.update(r[0] for r in result.all())

    return ids


async def get_accessible_entity_ids(db: AsyncSession, user: User) -> Set[str]:
    """Return the set of entity_ids the user may operate on within their tenant."""
    if user.role == Role.PLATFORM_OWNER or not user.tenant_id:
        return set()

    if user.role == Role.SUPER_ADMIN:
        result = await db.execute(
            select(Entity.id).where(Entity.tenant_id == user.tenant_id)
        )
        return {r[0] for r in result.all()}

    ids: Set[str] = set()

    # Primary entity
    if user.primary_entity_id:
        ids.add(user.primary_entity_id)

    # Direct entity assignments
    result = await db.execute(
        select(UserEntityAssignment.entity_id).where(
            UserEntityAssignment.user_id == user.id,
            UserEntityAssignment.tenant_id == user.tenant_id,
        )
    )
    ids.update(r[0] for r in result.all())

    # Group assignments — expand to descendants, then to entities
    result = await db.execute(
        select(UserEntityGroupAssignment.group_id).where(
            UserEntityGroupAssignment.user_id == user.id,
            UserEntityGroupAssignment.tenant_id == user.tenant_id,
        )
    )
    group_roots = [r[0] for r in result.all()]
    if group_roots:
        all_group_ids = await _descendant_group_ids(
            db, user.tenant_id, group_roots, include_inactive=False
        )
        if all_group_ids:
            result = await db.execute(
                select(Entity.id)
                .outerjoin(
                    EntityGroupMembership,
                    Entity.id == EntityGroupMembership.entity_id,
                )
                .where(
                    Entity.tenant_id == user.tenant_id,
                    or_(
                        Entity.group_id.in_(all_group_ids),
                        EntityGroupMembership.group_id.in_(all_group_ids),
                    ),
                )
                .distinct()
            )
            ids.update(r[0] for r in result.all())

    return ids


async def get_accessible_group_ids(db: AsyncSession, user: User) -> Set[str]:
    if user.role == Role.PLATFORM_OWNER or not user.tenant_id:
        return set()
    if user.role == Role.SUPER_ADMIN:
        result = await db.execute(
            select(EntityGroup.id).where(EntityGroup.tenant_id == user.tenant_id)
        )
        return {r[0] for r in result.all()}

    result = await db.execute(
        select(UserEntityGroupAssignment.group_id).where(
            UserEntityGroupAssignment.user_id == user.id,
            UserEntityGroupAssignment.tenant_id == user.tenant_id,
        )
    )
    roots = [r[0] for r in result.all()]
    return await _descendant_group_ids(
        db, user.tenant_id, roots, include_inactive=False
    )


async def can_access_entity(db: AsyncSession, user: User, entity_id: str) -> bool:
    ids = await get_accessible_entity_ids(db, user)
    return entity_id in ids
