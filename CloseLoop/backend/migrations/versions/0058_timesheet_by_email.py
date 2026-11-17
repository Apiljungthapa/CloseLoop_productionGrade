"""timesheet: the HRMS identifies people by e-mail; sub activities carry values.

The HRMS timesheet API (one POST per day) takes the employee's work e-mail, not an
id, and wants a VALUE for each sub activity of an activity (they are extra fields,
not alternatives). So:

  * timesheet_submissions.hrms_email        new - the e-mail that was sent
  * timesheet_submissions.hrms_employee_id  now optional (no longer needed to send)
  * timesheet_lines.sub_values              new - {sub activity id: value}

The three timesheet tables hold nothing yet that depends on the old shape, and
`hrms_sub_activity_id` stays (always 0) because the live-slot unique index uses it.

Revision ID: 0058_timesheet_by_email
Revises: 0057_timesheets
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0058_timesheet_by_email"
down_revision: Union[str, None] = "0057_timesheets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "hrms_email" not in _cols("timesheet_submissions"):
        op.add_column("timesheet_submissions", sa.Column("hrms_email", sa.String(length=200), nullable=True))
    op.alter_column("timesheet_submissions", "hrms_employee_id", existing_type=sa.Integer(), nullable=True)
    if "sub_values" not in _cols("timesheet_lines"):
        op.add_column("timesheet_lines", sa.Column("sub_values", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("timesheet_lines", "sub_values")
    op.execute("UPDATE timesheet_submissions SET hrms_employee_id = 0 WHERE hrms_employee_id IS NULL")
    op.alter_column("timesheet_submissions", "hrms_employee_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("timesheet_submissions", "hrms_email")
