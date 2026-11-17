"""timesheet: a submitted task is filed once PER PERSON.

Timesheets no longer read the time-log table. A person's row now comes from a task or
sub task THEY SUBMITTED (or their own part of a group task they marked done), and the
database must keep "filed once" true for that person - but two people may each file
their own part of one group task, so 0059's global UNIQUE(task_id) / UNIQUE(subtask_id)
is replaced by one per person:

  * timesheet_line_sources.user_id     new - whose submission this is (back-filled from the line)
  * UNIQUE(task_id)    -> UNIQUE(user_id, task_id)
  * UNIQUE(subtask_id) -> UNIQUE(user_id, subtask_id)

Nothing has been filed yet on production (0 submissions), so this changes no data.

Revision ID: 0060_timesheet_per_person_sources
Revises: 0059_timesheet_typed_hours
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0060_timesheet_per_person_sources"
down_revision: Union[str, None] = "0059_timesheet_typed_hours"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "user_id" not in _cols("timesheet_line_sources"):
        op.add_column("timesheet_line_sources", sa.Column("user_id", sa.String(length=64), nullable=True))
        op.create_foreign_key(
            "fk_ts_source_user", "timesheet_line_sources", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
    op.execute(
        "UPDATE timesheet_line_sources s SET user_id = l.user_id "
        "FROM timesheet_lines l WHERE l.id = s.line_id AND s.user_id IS NULL"
    )
    for name in ("uq_timesheet_source_task", "uq_timesheet_source_subtask"):
        op.drop_constraint(name, "timesheet_line_sources", type_="unique")
    op.create_unique_constraint("uq_timesheet_source_user_task", "timesheet_line_sources", ["user_id", "task_id"])
    op.create_unique_constraint("uq_timesheet_source_user_subtask", "timesheet_line_sources", ["user_id", "subtask_id"])


def downgrade() -> None:
    for name in ("uq_timesheet_source_user_subtask", "uq_timesheet_source_user_task"):
        op.drop_constraint(name, "timesheet_line_sources", type_="unique")
    # Two people's parts of one task can't both satisfy the old global rule: keep the first.
    op.execute(
        "DELETE FROM timesheet_line_sources a USING timesheet_line_sources b "
        "WHERE a.task_id IS NOT NULL AND a.task_id = b.task_id AND a.ctid > b.ctid"
    )
    op.execute(
        "DELETE FROM timesheet_line_sources a USING timesheet_line_sources b "
        "WHERE a.subtask_id IS NOT NULL AND a.subtask_id = b.subtask_id AND a.ctid > b.ctid"
    )
    op.create_unique_constraint("uq_timesheet_source_task", "timesheet_line_sources", ["task_id"])
    op.create_unique_constraint("uq_timesheet_source_subtask", "timesheet_line_sources", ["subtask_id"])
    op.drop_constraint("fk_ts_source_user", "timesheet_line_sources", type_="foreignkey")
    op.drop_column("timesheet_line_sources", "user_id")
