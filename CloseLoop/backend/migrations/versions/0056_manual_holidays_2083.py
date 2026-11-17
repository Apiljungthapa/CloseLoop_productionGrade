"""manual holidays — categories, BS dates, several per date, and the 2083 BS list.

Until a public-holiday API exists, the official holidays of Nepali year 2083 BS
(2026-27 AD) are maintained by hand. This turns the `holidays` table (0054) into
a place that can hold them:

  * `bs_date`   the same day in Bikram Sambat, "YYYY-MM-DD" (the AD date stays
                the source of truth; existing rows are back-filled).
  * `category`  ALL, GENDER or RELIGION (one group, named in `audience`:
                FEMALE, CHRISTIAN). Existing rows become ALL, so nothing already
                configured changes.
  * `audience`, `remarks`  who a scoped holiday is for, and a free note.
  * unique per (date, name) instead of per date, so Dashain and a gender-only
    holiday can share a day without one replacing the other.

Then it seeds the 2083 list. 14 rows were supplied; Christmas appears twice
(same date, name and category), so 13 are stored. Re-running changes nothing:
the insert skips a (date, name) that is already there and never edits it, so an
owner's later correction survives. Rows are MANUAL, so a future API feed never
overwrites them.

Which categories are skipped for everyone's task dates is decided in code
(calendar.holiday_registry.SKIPPED_FOR_EVERYONE), not here; the category and
audience stay on the row for staff-specific rules once staff attributes exist.

Downgrade removes the scoped rows (the old schema would treat every row as a
day off for everyone) and any second holiday on a date, then restores the
one-per-date rule.

Revision ID: 0056_manual_holidays_2083
Revises: 0055_due_dates_stay_in_period
Create Date: 2026-10-27
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0056_manual_holidays_2083"
down_revision: Union[str, None] = "0055_due_dates_stay_in_period"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (name, BS date, AD date, category, audience, remarks) — exactly as supplied,
# including the repeated Christmas row (S.N. 14).
SUPPLIED_2083 = [
    ("Tihar-Laxmi Puja", "2083-07-22", date(2026, 11, 8), "ALL", None, "Tihar Holiday"),
    ("Tihar-Gobardhan Puja", "2083-07-24", date(2026, 11, 10), "ALL", None, "Tihar Holiday"),
    ("Tihar-Bhaitika", "2083-07-25", date(2026, 11, 11), "ALL", None, "Tihar Holiday"),
    ("Maha Shivaratri", "2083-11-22", date(2027, 3, 6), "ALL", None, "Shivaratri"),
    ("Teej", "2083-05-29", date(2026, 9, 14), "GENDER", "FEMALE", "Only for female staff"),
    ("International Women's Day", "2083-11-24", date(2027, 3, 8), "GENDER", "FEMALE", "Only for female staff"),
    ("Christmas", "2083-09-10", date(2026, 12, 25), "RELIGION", "CHRISTIAN", "Only for Christians"),
    ("Holi", "2083-12-07", date(2027, 3, 21), "ALL", None, "Holi"),
    ("English New Year", "2083-09-17", date(2027, 1, 1), "ALL", None, "Happy New Year"),
    ("Dashain-Fulpati", "2083-06-31", date(2026, 10, 17), "ALL", None, "Dashain Holiday"),
    ("Dashain-Ekadashi", "2083-07-05", date(2026, 10, 22), "ALL", None, "Dashain Holiday"),
    ("Dashain-Dashami", "2083-07-04", date(2026, 10, 21), "ALL", None, "Dashain Holiday"),
    ("Dashain-Astami", "2083-07-01", date(2026, 10, 18), "ALL", None, "Dashain Holiday"),
    ("Christmas", "2083-09-10", date(2026, 12, 25), "RELIGION", "CHRISTIAN", "Only for Christians"),
]


def _unique(rows):
    """The supplied rows without repeats of the same holiday (date + name)."""
    seen, out = set(), []
    for row in rows:
        key = (row[2], row[0].casefold())
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


HOLIDAYS_2083 = _unique(SUPPLIED_2083)


def _bs_iso(ad: date) -> Optional[str]:
    try:
        import nepali_datetime

        b = nepali_datetime.date.from_datetime_date(ad)
        return f"{b.year:04d}-{b.month:02d}-{b.day:02d}"
    except Exception:  # outside the BS tables: leave it blank
        return None


def _constraint_names(insp, getter) -> set:
    return {c["name"] for c in getter("holidays")}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("holidays"):  # 0054 creates it; belt and braces
        return

    have = {c["name"] for c in insp.get_columns("holidays")}
    if "bs_date" not in have:
        op.add_column("holidays", sa.Column("bs_date", sa.String(length=10), nullable=True))
    if "category" not in have:
        op.add_column(
            "holidays",
            sa.Column("category", sa.String(length=20), nullable=False, server_default="ALL"),
        )
    if "audience" not in have:
        op.add_column("holidays", sa.Column("audience", sa.String(length=50), nullable=True))
    if "remarks" not in have:
        op.add_column("holidays", sa.Column("remarks", sa.String(length=500), nullable=True))

    if "ck_holidays_category" not in _constraint_names(insp, insp.get_check_constraints):
        op.create_check_constraint(
            "ck_holidays_category", "holidays", "category IN ('ALL', 'GENDER', 'RELIGION')"
        )

    uniques = _constraint_names(insp, insp.get_unique_constraints)
    if "uq_holidays_date" in uniques:
        op.drop_constraint("uq_holidays_date", "holidays", type_="unique")
    if "uq_holidays_date_name" not in uniques:
        op.create_unique_constraint("uq_holidays_date_name", "holidays", ["holiday_date", "name"])

    # BS date for the rows that predate the column.
    blank = bind.execute(sa.text("SELECT id, holiday_date FROM holidays WHERE bs_date IS NULL")).all()
    for row_id, ad in blank:
        iso = _bs_iso(ad)
        if iso:
            bind.execute(
                sa.text("UPDATE holidays SET bs_date = :bs WHERE id = :id"), {"bs": iso, "id": row_id}
            )

    # The 2083 list. Never edits an existing (date, name); safe to run again.
    insert = sa.text(
        "INSERT INTO holidays (id, holiday_date, bs_date, name, category, audience, remarks, source) "
        "VALUES (:id, :ad, :bs, :name, :category, :audience, :remarks, 'MANUAL') "
        "ON CONFLICT (holiday_date, name) DO NOTHING"
    )
    for name, bs, ad, category, audience, remarks in HOLIDAYS_2083:
        bind.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "ad": ad,
                "bs": bs,
                "name": name,
                "category": category,
                "audience": audience,
                "remarks": remarks,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("holidays"):
        return

    # The old schema has no categories: a GENDER / RELIGION row would turn into
    # a day off for everyone. Drop those, then any second holiday on a date.
    bind.execute(sa.text("DELETE FROM holidays WHERE category <> 'ALL'"))
    bind.execute(
        sa.text(
            "DELETE FROM holidays h USING holidays k "
            "WHERE h.holiday_date = k.holiday_date "
            "AND (h.created_at, h.id) > (k.created_at, k.id)"
        )
    )

    uniques = _constraint_names(insp, insp.get_unique_constraints)
    if "uq_holidays_date_name" in uniques:
        op.drop_constraint("uq_holidays_date_name", "holidays", type_="unique")
    if "ck_holidays_category" in _constraint_names(insp, insp.get_check_constraints):
        op.drop_constraint("ck_holidays_category", "holidays", type_="check")
    for column in ("remarks", "audience", "category", "bs_date"):
        op.drop_column("holidays", column)
    if "uq_holidays_date" not in uniques:
        op.create_unique_constraint("uq_holidays_date", "holidays", ["holiday_date"])
