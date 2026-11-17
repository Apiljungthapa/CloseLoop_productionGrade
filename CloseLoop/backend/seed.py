"""Seed demo data for CloseLoop PHASE 1.

Idempotent: if the demo Platform Owner already exists, this script exits early
unless SEED_RESET=1 is set in the environment, in which case it deletes all
domain rows (preserving schema + alembic_version) and re-seeds.

Schema is owned by Alembic — this script never drops or creates tables.

Demo data:
  - Platform Owner: owner@closeloop.io / owner123
  - Tenant "Acme Finance" with:
      * SUPER_ADMIN: admin@acme.com / admin123
      * TEAM_LEAD: lead@acme.com / lead123        (works on NYC)
      * TEAM_MEMBER: apil@acme.com / member123    (primary NYC)
      * TEAM_MEMBER: miraj@acme.com / member123   (primary SF)
  - EntityGroup tree: Acme (root) -> Americas -> [US -> [NYC, SF], CA -> [Toronto]]
  - PeriodConfiguration (Friday-start, 4-week) + first Period
  - 1 ClosePack "Monthly Close" (MONTHLY type) with:
      Task: Bank Reconciliation
        SubTask: Reconcile Operating Account
        SubTask: Reconcile Payroll Account
      Task: Journal Entries
        SubTask: Post accruals
      Task: Review & Sign-off
        SubTask: Manager review (reviewer required at task level)
  - Plans auto-generated for NYC + SF entities against the first Period.
"""

import asyncio
import os
from sqlalchemy import select, delete

from app.database import AsyncSessionLocal
from app.models import (
    Tenant,
    User,
    Role,
    EntityGroup,
    EntityGroupType,
    Entity,
    UserEntityAssignment,
    UserEntityGroupAssignment,
    PeriodConfiguration,
    Period,
    ClosePack,
    ClosePackVersion,
    CalendarType,
    TaskTemplate,
    SubTaskTemplate,
    Plan,
    Task,
    SubTask,
    CarryForwardGroup,
    CarryForwardItem,
    Notification,
    AuditLog,
)
from app.auth import hash_password
from app.services.period_service import ensure_initial_period, get_or_create_config
from app.services.close_pack_service import snapshot_pack
from app.services.plan_generation_service import generate_plans


DEMO_OWNER_EMAIL = "owner@closeloop.io"


async def already_seeded(db) -> bool:
    res = await db.execute(select(User).where(User.email == DEMO_OWNER_EMAIL))
    return res.scalar_one_or_none() is not None


async def wipe(db) -> None:
    """Delete all domain rows. Order respects FK constraints."""
    for model in (
        SubTask,
        Task,
        Plan,
        CarryForwardItem,
        CarryForwardGroup,
        SubTaskTemplate,
        TaskTemplate,
        ClosePackVersion,
        ClosePack,
        Period,
        PeriodConfiguration,
        UserEntityGroupAssignment,
        UserEntityAssignment,
        Notification,
        AuditLog,
        Entity,
        EntityGroup,
        User,
        Tenant,
    ):
        await db.execute(delete(model))
    await db.commit()


async def seed(db) -> None:
    # ------------------------------------------------------------
    # 1. Platform Owner + Tenant
    # ------------------------------------------------------------
    owner = User(
        email=DEMO_OWNER_EMAIL,
        name="Platform Owner",
        password_hash=hash_password("owner123"),
        role=Role.PLATFORM_OWNER,
    )
    db.add(owner)

    tenant = Tenant(name="Acme Finance", slug="acme-finance", plan_limit=200)
    db.add(tenant)
    await db.flush()

    # ------------------------------------------------------------
    # 2. EntityGroup tree
    # ------------------------------------------------------------
    acme = EntityGroup(
        tenant_id=tenant.id,
        name="Acme Corp",
        description="Root organization",
        type=EntityGroupType.CUSTOM,
    )
    db.add(acme)
    await db.flush()

    americas = EntityGroup(
        tenant_id=tenant.id,
        parent_id=acme.id,
        name="Americas",
        type=EntityGroupType.REGION,
    )
    db.add(americas)
    await db.flush()

    us = EntityGroup(
        tenant_id=tenant.id,
        parent_id=americas.id,
        name="United States",
        type=EntityGroupType.SUBSIDIARY,
    )
    ca = EntityGroup(
        tenant_id=tenant.id,
        parent_id=americas.id,
        name="Canada",
        type=EntityGroupType.SUBSIDIARY,
    )
    db.add_all([us, ca])
    await db.flush()

    # ------------------------------------------------------------
    # 3. Entities (leaf nodes)
    # ------------------------------------------------------------
    nyc = Entity(
        tenant_id=tenant.id,
        group_id=us.id,
        name="NYC Office",
        description="New York headquarters",
    )
    sf = Entity(
        tenant_id=tenant.id,
        group_id=us.id,
        name="SF Office",
        description="San Francisco branch",
    )
    toronto = Entity(
        tenant_id=tenant.id,
        group_id=ca.id,
        name="Toronto Office",
        description="Toronto branch",
    )
    db.add_all([nyc, sf, toronto])
    await db.flush()

    # ------------------------------------------------------------
    # 4. Users
    # ------------------------------------------------------------
    admin = User(
        tenant_id=tenant.id,
        email="admin@acme.com",
        name="Acme Admin",
        password_hash=hash_password("admin123"),
        role=Role.SUPER_ADMIN,
    )
    lead = User(
        tenant_id=tenant.id,
        email="lead@acme.com",
        name="Naryan Lead",
        password_hash=hash_password("lead123"),
        role=Role.TEAM_LEAD,
        title="Finance Controller",
        primary_entity_id=nyc.id,
    )
    apil = User(
        tenant_id=tenant.id,
        email="apil@acme.com",
        name="Apil Member",
        password_hash=hash_password("member123"),
        role=Role.TEAM_MEMBER,
        title="Ledger Accountant",
        primary_entity_id=nyc.id,
    )
    miraj = User(
        tenant_id=tenant.id,
        email="miraj@acme.com",
        name="Miraj Member",
        password_hash=hash_password("member123"),
        role=Role.TEAM_MEMBER,
        title="Billing AR Assistant",
        primary_entity_id=sf.id,
    )
    db.add_all([admin, lead, apil, miraj])
    await db.flush()

    # ------------------------------------------------------------
    # 5. Access assignments
    # ------------------------------------------------------------
    # Team Lead also covers SF (in addition to NYC primary).
    db.add(UserEntityAssignment(tenant_id=tenant.id, user_id=lead.id, entity_id=sf.id))
    # Apil also can act on SF for backup coverage.
    db.add(UserEntityAssignment(tenant_id=tenant.id, user_id=apil.id, entity_id=sf.id))
    await db.flush()

    # ------------------------------------------------------------
    # 6. Period configuration + first period (Friday-start, 4-week)
    # ------------------------------------------------------------
    await get_or_create_config(db, tenant.id)
    period = await ensure_initial_period(db, tenant.id)

    # ------------------------------------------------------------
    # 7. Close Pack with templates
    # ------------------------------------------------------------
    pack = ClosePack(
        tenant_id=tenant.id,
        name="Monthly Close",
        description="Standard monthly closing checklist",
        calendar_type=CalendarType.AD,
        active=True,
    )
    db.add(pack)
    await db.flush()

    # Task 1: Bank Reconciliation (day 5)
    t1 = TaskTemplate(
        tenant_id=tenant.id,
        close_pack_id=pack.id,
        title="Bank Reconciliation",
        description="Reconcile all bank accounts to the GL",
        due_day_of_period=5,
        reviewer_required=False,
        order=1,
    )
    db.add(t1)
    await db.flush()

    s1a = SubTaskTemplate(
        tenant_id=tenant.id,
        task_template_id=t1.id,
        title="Reconcile Operating Account",
        description="Operating cash account",
        due_day_of_period=3,
        order=1,
    )
    s1b = SubTaskTemplate(
        tenant_id=tenant.id,
        task_template_id=t1.id,
        title="Reconcile Payroll Account",
        description="Payroll clearing account",
        due_day_of_period=4,
        order=2,
    )
    db.add_all([s1a, s1b])
    await db.flush()

    # Task 2: Journal Entries (day 10)
    t2 = TaskTemplate(
        tenant_id=tenant.id,
        close_pack_id=pack.id,
        title="Journal Entries",
        description="Post all required closing JEs",
        due_day_of_period=10,
        reviewer_required=False,
        order=2,
    )
    db.add(t2)
    await db.flush()

    s2 = SubTaskTemplate(
        tenant_id=tenant.id,
        task_template_id=t2.id,
        title="Post accruals",
        description="All month-end accruals",
        due_day_of_period=10,
        order=1,
    )
    db.add(s2)
    await db.flush()

    # Task 3: Review & Sign-off (day 15, reviewer required)
    t3 = TaskTemplate(
        tenant_id=tenant.id,
        close_pack_id=pack.id,
        title="Review & Sign-off",
        description="Manager review of close package",
        due_day_of_period=15,
        reviewer_required=True,
        order=3,
    )
    db.add(t3)
    await db.flush()

    s3 = SubTaskTemplate(
        tenant_id=tenant.id,
        task_template_id=t3.id,
        title="Manager review",
        description="Lead review and approval",
        due_day_of_period=15,
        order=1,
    )
    db.add(s3)
    await db.flush()

    # ------------------------------------------------------------
    # 8. Snapshot the pack (creates version 1)
    # ------------------------------------------------------------
    await snapshot_pack(db, tenant.id, pack, user_id=admin.id)

    # ------------------------------------------------------------
    # 9. Generate plans for NYC + SF for the first period
    # ------------------------------------------------------------
    await generate_plans(
        db,
        tenant_id=tenant.id,
        pack_id=pack.id,
        period_id=period.id,
        entity_ids=[nyc.id, sf.id],
        user_id=admin.id,
    )

    # Assign generated tasks/sub-tasks for NYC -> apil (with lead as reviewer)
    # and SF -> miraj (with lead as reviewer).
    plan_rows = await db.execute(
        select(Plan).where(Plan.period_id == period.id, Plan.tenant_id == tenant.id)
    )
    for plan in plan_rows.scalars().all():
        member_id = apil.id if plan.entity_id == nyc.id else miraj.id
        task_rows = await db.execute(select(Task).where(Task.plan_id == plan.id))
        for task in task_rows.scalars().all():
            task.assigned_to_id = member_id
            sub_rows = await db.execute(
                select(SubTask).where(SubTask.task_id == task.id)
            )
            for sub in sub_rows.scalars().all():
                sub.assigned_to_id = member_id
                if task.title == "Review & Sign-off":
                    sub.reviewer_id = lead.id

    await db.commit()
    print(
        "Seed complete:\n"
        f"  Platform Owner: {DEMO_OWNER_EMAIL} / owner123\n"
        f"  Tenant '{tenant.name}' ({tenant.id})\n"
        f"  SUPER_ADMIN: admin@acme.com / admin123\n"
        f"  TEAM_LEAD: lead@acme.com / lead123 (NYC + SF)\n"
        f"  TEAM_MEMBER: apil@acme.com / member123 (NYC primary, SF backup)\n"
        f"  TEAM_MEMBER: miraj@acme.com / member123 (SF primary)\n"
        f"  Period: {period.label}\n"
        f"  ClosePack: '{pack.name}' v{pack.current_version}\n"
        f"  Plans generated for NYC + SF in current period.\n"
    )


async def main() -> None:
    async with AsyncSessionLocal() as db:
        seeded = await already_seeded(db)
        if seeded and os.environ.get("SEED_RESET") != "1":
            print(
                f"Demo user {DEMO_OWNER_EMAIL} already exists. "
                "Set SEED_RESET=1 to wipe and re-seed."
            )
            return
        if seeded:
            print("SEED_RESET=1 — wiping existing demo data...")
            await wipe(db)
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
