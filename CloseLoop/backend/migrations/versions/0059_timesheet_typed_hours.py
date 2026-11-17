"""timesheet: remember tasks filed with hours the user typed.

A task that was DONE (or is due) in the month but has no time logged still gets a
row on its day, with empty hours the user types. Once sent it must not come back,
and two requests must not both send it - so the source row records the task (or
sub task) instead of a time entry, and each is UNIQUE:

  * timesheet_line_sources.task_id     new, FK tasks (SET NULL), UNIQUE
  * timesheet_line_sources.subtask_id  new, FK sub_tasks (SET NULL), UNIQUE

(time_entry_id is already nullable; it is NULL for these rows. NULLs never collide
in a unique constraint, so the many time-entry sources are unaffected.)

Revision ID: 0059_timesheet_typed_hours
Revises: 0058_timesheet_by_email
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_timesheet_typed_hours"
down_revision: Union[str, None] = "0058_timesheet_by_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols("timesheet_line_sources")
    if "task_id" not in have:
        op.add_column("timesheet_line_sources", sa.Column("task_id", sa.String(length=64), nullable=True))
        op.create_foreign_key("fk_ts_source_task", "timesheet_line_sources", "tasks", ["task_id"], ["id"], ondelete="SET NULL")
        op.create_unique_constraint("uq_timesheet_source_task", "timesheet_line_sources", ["task_id"])
    if "subtask_id" not in have:
        op.add_column("timesheet_line_sources", sa.Column("subtask_id", sa.String(length=64), nullable=True))
        op.create_foreign_key("fk_ts_source_subtask", "timesheet_line_sources", "sub_tasks", ["subtask_id"], ["id"], ondelete="SET NULL")
        op.create_unique_constraint("uq_timesheet_source_subtask", "timesheet_line_sources", ["subtask_id"])


def downgrade() -> None:
    # Rows that exist only because of a typed-hours source have nothing to point at once the
    # columns are gone; drop them first so the lines they belonged to aren't left source-less.
    op.execute("DELETE FROM timesheet_line_sources WHERE time_entry_id IS NULL")
    for name in ("uq_timesheet_source_subtask", "uq_timesheet_source_task"):
        op.drop_constraint(name, "timesheet_line_sources", type_="unique")
    for name in ("fk_ts_source_subtask", "fk_ts_source_task"):
        op.drop_constraint(name, "timesheet_line_sources", type_="foreignkey")
    op.drop_column("timesheet_line_sources", "subtask_id")
    op.drop_column("timesheet_line_sources", "task_id")
