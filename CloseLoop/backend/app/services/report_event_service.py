"""
Report event service.

Handles the *side-effect* arm of the Report Engine — running a report,
persisting it as an immutable ``ReportSnapshot``, and emitting
``REPORT_READY`` notifications to the correct audience.

The query-shaped report functions stay in ``report_service.py``; this
module is the only place that writes ``ReportSnapshot`` rows. Keeping
the two responsibilities separate means a future caller (an on-demand
"Save Snapshot" button, a scheduled rollup, a different event trigger)
can pick its own audience and metadata without forking the report-query
code.

Current event:

  * ``emit_period_close_completion_event`` — fired by
    ``carry_forward_service.close_period`` after a period is closed
    (manually or by the scheduler). Audience = every SUPER_ADMIN in the
    tenant + every TEAM_LEAD who owns at least one plan in the closing
    period.

Failures are isolated: callers are expected to wrap this in a try /
except so a snapshot-or-notify error never blocks the underlying close.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Period, Plan, ReportSnapshot, Role, User
from .notification_service import notify_report_ready
from .report_access_service import REPORT_PERIOD_CLOSE_COMPLETION
from .report_service import period_close_completion_report

logger = logging.getLogger(__name__)


async def emit_period_close_completion_event(
    db: AsyncSession,
    *,
    tenant_id: str,
    period: Period,
    generated_by_id: Optional[str],
) -> Optional[ReportSnapshot]:
    """
    Generate the Period Close Completion Report for ``period`` (tenant-wide
    scope), persist it as a ``ReportSnapshot`` with
    ``trigger='PERIOD_CLOSE_EVENT'``, and notify the relevant audience.

    Returns the persisted snapshot, or ``None`` if the audit emission
    encountered an unrecoverable error (already logged). Callers should
    treat ``None`` as "snapshot failed but period close still
    succeeded" — never raise.
    """
    # 1. Generate the report. entity_scope=None → unrestricted (this is
    #    an admin/system action, not a per-user request).
    result = await period_close_completion_report(
        db,
        tenant_id=tenant_id,
        period_id=period.id,
        entity_scope=None,
    )

    # 2. Persist the snapshot (immutable audit evidence).
    snapshot = ReportSnapshot(
        tenant_id=tenant_id,
        report_type=REPORT_PERIOD_CLOSE_COMPLETION,
        period_id=period.id,
        generated_by_id=generated_by_id,
        trigger="PERIOD_CLOSE_EVENT",
        parameters={
            "period_id": period.id,
            "period_label": period.label,
        },
        payload_json={
            "columns": result.columns,
            "rows": result.rows,
            "summary": result.summary,
        },
    )
    db.add(snapshot)
    await db.flush()

    # 3. Build the audience.
    audience = await _period_close_audience(db, tenant_id, period.id)

    # 4. Push notifications. notify_report_ready dedupes internally.
    message = (
        f"Period Close Completion Report for {period.label} is ready. "
        f"{result.summary.get('completed_plans', 0)} of "
        f"{result.summary.get('plans', 0)} plans closed."
    )
    await notify_report_ready(
        db,
        tenant_id=tenant_id,
        user_ids=audience,
        report_type=REPORT_PERIOD_CLOSE_COMPLETION,
        snapshot_id=snapshot.id,
        message=message,
    )
    logger.info(
        "Emitted PERIOD_CLOSE_EVENT report snapshot %s for tenant %s period %s (%d recipients)",
        snapshot.id,
        tenant_id,
        period.id,
        len(audience),
    )
    return snapshot


async def _period_close_audience(
    db: AsyncSession, tenant_id: str, period_id: str
) -> list[str]:
    """
    Return the user_ids that should receive the period-close report
    notification: every SUPER_ADMIN in the tenant, plus every TEAM_LEAD
    who explicitly owns at least one plan in the closing period.
    """
    admin_rows = await db.execute(
        select(User.id).where(
            User.tenant_id == tenant_id, User.role == Role.SUPER_ADMIN
        )
    )
    audience: list[str] = [uid for (uid,) in admin_rows.all()]

    lead_rows = await db.execute(
        select(Plan.owner_id)
        .where(
            Plan.tenant_id == tenant_id,
            Plan.period_id == period_id,
            Plan.owner_id.is_not(None),
        )
        .distinct()
    )
    lead_ids = {uid for (uid,) in lead_rows.all() if uid}
    if lead_ids:
        lead_role_rows = await db.execute(
            select(User.id).where(
                User.tenant_id == tenant_id,
                User.id.in_(lead_ids),
                User.role == Role.TEAM_LEAD,
            )
        )
        audience.extend(uid for (uid,) in lead_role_rows.all())

    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for uid in audience:
        if uid and uid not in seen:
            seen.add(uid)
            deduped.append(uid)
    return deduped
