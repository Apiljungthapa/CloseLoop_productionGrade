"""One place that turns "words from a user" into a built report - shared by the Report Studio API and the chat.

``ask`` loads what the caller may name (their periods, entities and people), interprets the sentence, and
builds the report. Role and entity scope are enforced inside ``build_report`` and ``load_context`` - callers
cannot widen them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..calendar import nepal_today
from ..models import Entity, Period, Role, Tenant, User
from . import report_access_service as access
from . import report_intent
from .report_studio import PeriodInfo, ReportError, RichReport, build_report


async def load_context(db: AsyncSession, me: User) -> report_intent.Context:
    """What this caller may name in a request: the tenant's periods, their own entities, the tenant's people."""
    tenant_id = me.tenant_id or ""
    scope = await access.resolve_report_entity_scope(db, me)
    periods = (await db.execute(
        select(Period).where(Period.tenant_id == tenant_id).order_by(Period.start_date)
    )).scalars().all()
    entity_query = select(Entity.id, Entity.name).where(Entity.tenant_id == tenant_id)
    if scope is not None:
        entity_query = entity_query.where(Entity.id.in_(list(scope)))
    entities = {eid: name for eid, name in (await db.execute(entity_query.order_by(Entity.name))).all()}
    users = {uid: name for uid, name in (await db.execute(
        select(User.id, User.name).where(
            User.tenant_id == tenant_id, User.role.in_([Role.TEAM_MEMBER, Role.TEAM_LEAD, Role.SUPER_ADMIN])
        ).order_by(User.name)
    )).all()}
    infos = [PeriodInfo(p.id, p.label, p.start_date, p.end_date, getattr(p.status, "value", str(p.status))) for p in periods]
    return report_intent.Context(periods=infos, entities=entities, users=users, today=nepal_today())


async def tenant_name(db: AsyncSession, tenant_id: str) -> Optional[str]:
    return (await db.execute(select(Tenant.name).where(Tenant.id == tenant_id))).scalar_one_or_none()


@dataclass
class AskResult:
    interpretation: report_intent.Interpretation
    report: Optional[RichReport] = None


async def ask(db: AsyncSession, me: User, text: str) -> AskResult:
    """Interpret ``text`` and, when it is clear, build the report. A request the engine cannot satisfy comes back
    as an un-understood interpretation carrying the reason, never as an exception the caller must remember."""
    access.ensure_can_run_report(me, access.REPORT_STUDIO)  # 403 for a Team Member / Platform Owner
    ctx = await load_context(db, me)
    result = report_intent.interpret(text, ctx)
    if not result.understood or result.spec is None:
        return AskResult(result)
    try:
        report = await build_report(
            db, me=me, spec=result.spec, today=ctx.today, tenant_name=await tenant_name(db, me.tenant_id or ""))
    except ReportError as exc:
        result.understood = False
        result.clarification = exc.message
        return AskResult(result)
    return AskResult(result, report)
