"""Optional tasks count like any task EXCEPT while skipped (optional, past due, not done)."""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.models import TaskStatus
from app.services.optional_tasks import (
    SKIPPABLE_STATUSES, counted_clause, is_skipped, skipped_clause, task_is_skipped,
)

TODAY = date(2026, 10, 28)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
S = TaskStatus


def skipped(*, required, status, due):
    return is_skipped(is_required=required, status=status, due_date=due, today=TODAY)


class IsSkippedTests(unittest.TestCase):
    def test_optional_past_due_and_not_done_is_skipped(self):
        for status in (S.NOT_STARTED, S.ASSIGNED, S.IN_PROGRESS):
            self.assertTrue(skipped(required=False, status=status, due=YESTERDAY), status)

    def test_required_work_is_never_skipped(self):
        for status in S:
            self.assertFalse(skipped(required=True, status=status, due=YESTERDAY), status)

    def test_optional_work_that_is_done_or_in_review_still_counts(self):
        for status in (S.COMPLETED, S.PENDING_REVIEW, S.REJECTED, S.CARRIED_FORWARD, S.UPCOMING):
            self.assertFalse(skipped(required=False, status=status, due=YESTERDAY), status)

    def test_optional_work_is_not_skipped_until_its_due_date_has_passed(self):
        self.assertFalse(skipped(required=False, status=S.NOT_STARTED, due=TODAY))  # due today
        self.assertFalse(skipped(required=False, status=S.NOT_STARTED, due=TOMORROW))
        self.assertFalse(skipped(required=False, status=S.NOT_STARTED, due=None))
        self.assertTrue(skipped(required=False, status=S.NOT_STARTED, due=YESTERDAY))

    def test_it_depends_on_today_so_an_optional_task_leaves_the_totals_the_next_day(self):
        t = SimpleNamespace(is_required=False, status=S.NOT_STARTED, due_date=TODAY)
        self.assertFalse(task_is_skipped(t, TODAY))
        self.assertTrue(task_is_skipped(t, TOMORROW))

    def test_a_missing_flag_reads_as_required(self):
        self.assertFalse(skipped(required=None, status=S.NOT_STARTED, due=YESTERDAY))

    def test_statuses_match_the_documented_rule(self):
        self.assertEqual(set(SKIPPABLE_STATUSES), {S.NOT_STARTED, S.ASSIGNED, S.IN_PROGRESS})


class SqlClauseTests(unittest.TestCase):
    def sql(self, clause):
        return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def test_counted_is_the_negation_of_skipped(self):
        skipped_sql = self.sql(skipped_clause(TODAY))
        for fragment in ("tasks.is_required IS false", "tasks.due_date IS NOT NULL",
                         "tasks.due_date < '2026-10-28'", "NOT_STARTED", "ASSIGNED", "IN_PROGRESS"):
            self.assertIn(fragment, skipped_sql)
        self.assertNotIn("COMPLETED", skipped_sql)
        self.assertTrue(self.sql(counted_clause(TODAY)).startswith("NOT ("))


if __name__ == "__main__":
    unittest.main()
