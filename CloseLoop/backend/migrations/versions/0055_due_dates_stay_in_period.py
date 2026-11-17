"""due dates stay inside their own period — retire "N days after period end".

A Kartik task is never due in Mangsir: a date in another period shows up only
in that period's own plan. Before this, a template could say "N days after
period end", and a Saturday/Sunday at the end of a period was rolled forward
into the next period's Monday. Both put a task in one period with a date that
belongs to the next.

What this does (idempotent — a second run changes nothing):

  * Pack task / sub-task templates marked AFTER_PERIOD_END become WITHIN_PERIOD
    with 0 days. A once-per-period one is re-anchored to PERIOD_END, day 1 —
    "the period's last working day" — because AFTER_PERIOD_END used to ignore
    its own day/anchor.
  * Open tasks whose due date is past their period's end move to the period's
    last working day on or before it. COMPLETED rows keep their dates (moving
    one would make finished work retroactively late) and UPCOMING placeholders
    keep theirs (they name a later period on purpose).
  * Open sub-tasks past their period end, or past their parent's due date,
    follow the same rule, capped at the parent.

Rows keep `updated_at` (reports use it as the completion time of legacy rows).
Holidays (table from 0054) count as non-working days alongside Sat/Sun.

Revision ID: 0055_due_dates_stay_in_period
Revises: 0054_holidays
Create Date: 2026-10-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0055_due_dates_stay_in_period"
down_revision: Union[str, None] = "0054_holidays"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _last_working_day(cap: str) -> str:
    """SQL: the last working day on/before the date expression `cap` (Mon-Fri
    and not a configured holiday), looking back at most 14 days."""
    return f"""COALESCE(
        (SELECT max(g.d::date)
           FROM generate_series(({cap})::date - 14, ({cap})::date, interval '1 day') AS g(d)
          WHERE extract(isodow FROM g.d) < 6
            AND NOT EXISTS (SELECT 1 FROM holidays h WHERE h.holiday_date = g.d::date)),
        ({cap})::date)"""


TEMPLATE_SQL = [
    # once-per-period: "N days after close" -> the period's last working day
    """UPDATE task_templates
          SET due_anchor = 'PERIOD_END', due_day_of_period = 1
        WHERE due_date_type = 'AFTER_PERIOD_END' AND frequency IS NULL""",
    """UPDATE sub_task_templates
          SET due_anchor = 'PERIOD_END', due_day_of_period = 1
        WHERE due_date_type = 'AFTER_PERIOD_END' AND frequency IS NULL""",
    """UPDATE task_templates
          SET due_date_type = 'WITHIN_PERIOD', post_period_due_days = 0
        WHERE due_date_type = 'AFTER_PERIOD_END' OR post_period_due_days <> 0""",
    """UPDATE sub_task_templates
          SET due_date_type = 'WITHIN_PERIOD', post_period_due_days = 0
        WHERE due_date_type = 'AFTER_PERIOD_END' OR post_period_due_days <> 0""",
]

TASK_SQL = f"""UPDATE tasks t
          SET due_date = {_last_working_day("LEAST(t.due_date, p.end_date)")}
         FROM plans pl
         JOIN periods p ON p.id = pl.period_id
        WHERE t.plan_id = pl.id
          AND t.due_date > p.end_date
          AND t.status::text NOT IN ('COMPLETED', 'UPCOMING')"""

# the tasks' own flags (kept consistent with the templates) — after the dates
TASK_FLAGS_SQL = [
    """UPDATE tasks SET due_date_type = 'WITHIN_PERIOD', post_period_due_days = 0
        WHERE status::text <> 'COMPLETED'
          AND (due_date_type = 'AFTER_PERIOD_END' OR post_period_due_days <> 0)""",
    """UPDATE sub_tasks SET due_date_type = 'WITHIN_PERIOD', post_period_due_days = 0
        WHERE status::text <> 'COMPLETED'
          AND (due_date_type = 'AFTER_PERIOD_END' OR post_period_due_days <> 0)""",
]

SUBTASK_SQL = f"""UPDATE sub_tasks s
          SET due_date = {_last_working_day("LEAST(s.due_date, t.due_date, p.end_date)")}
         FROM tasks t
         JOIN plans pl ON pl.id = t.plan_id
         JOIN periods p ON p.id = pl.period_id
        WHERE s.task_id = t.id
          AND s.due_date IS NOT NULL
          AND s.status::text NOT IN ('COMPLETED', 'UPCOMING')
          AND t.status::text <> 'UPCOMING'
          AND (s.due_date > p.end_date
               OR (t.due_date IS NOT NULL AND s.due_date > t.due_date))"""

# order matters: templates, then task dates, then sub-task dates (capped at the
# parent's new date), then the flags
ALL_SQL = [*TEMPLATE_SQL, TASK_SQL, SUBTASK_SQL, *TASK_FLAGS_SQL]


def upgrade() -> None:
    for statement in ALL_SQL:
        op.execute(statement)


def downgrade() -> None:
    # The old dates and the "N days after period end" setting are not kept, so
    # there is nothing to restore; the code simply no longer reads them.
    pass
