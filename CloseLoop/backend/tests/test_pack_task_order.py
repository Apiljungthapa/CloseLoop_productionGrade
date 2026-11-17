"""A pack's tasks (and a task's sub-tasks) come back in reading order:
due today first, then tomorrow and onward, no-date rows, and deadlines that
were already missed at the very bottom (most recently missed first).

`GET /plans/{id}` is what every pack screen (Lead, Member, Admin) and the
plan page build their rows from, so the order is fixed once, in
`_task_order` / `_subtask_order`. `today` is passed in, so none of this
depends on the clock.
"""

import unittest
from datetime import date, datetime, timedelta, timezone

from app.models import Priority, SubTask, Task
from app.routers.plans import _deadline_key, _subtask_order, _task_order

TODAY = date(2026, 10, 27)


def _d(offset):
    """A date `offset` days from TODAY (0 = today, -1 = yesterday)."""
    return None if offset is None else TODAY + timedelta(days=offset)


def _t(title, offset, priority=Priority.MEDIUM, created=0):
    return Task(
        title=title,
        due_date=_d(offset),
        priority=priority,
        created_at=datetime(2026, 10, 1, 9, 0, created, tzinfo=timezone.utc),
    )


def _s(title, offset, created=0):
    return SubTask(
        title=title,
        due_date=_d(offset),
        created_at=datetime(2026, 10, 1, 9, 0, created, tzinfo=timezone.utc),
    )


def _tasks(items):
    return [x.title for x in sorted(items, key=lambda t: _task_order(t, TODAY))]


def _subs(items):
    return [x.title for x in sorted(items, key=lambda s: _subtask_order(s, TODAY))]


class DeadlineKeyTests(unittest.TestCase):
    def test_groups_today_or_later_then_undated_then_missed(self):
        self.assertEqual(_deadline_key(TODAY, TODAY), (0, 0))
        self.assertEqual(_deadline_key(_d(1), TODAY), (0, 1))
        self.assertEqual(_deadline_key(_d(20), TODAY), (0, 20))
        self.assertEqual(_deadline_key(None, TODAY), (1, 0))
        self.assertEqual(_deadline_key(_d(-1), TODAY), (2, 1))
        self.assertEqual(_deadline_key(_d(-9), TODAY), (2, 9))

    def test_group_order_is_total(self):
        keys = [_deadline_key(_d(o), TODAY) for o in (-9, -1, None, 20, 1, 0)]
        self.assertEqual(sorted(keys), [(0, 0), (0, 1), (0, 20), (1, 0), (2, 1), (2, 9)])


class TaskOrderTests(unittest.TestCase):
    def test_today_then_tomorrow_then_further_out(self):
        tasks = [_t("in 20 days", 20), _t("tomorrow", 1), _t("today", 0), _t("in 5 days", 5)]
        self.assertEqual(_tasks(tasks), ["today", "tomorrow", "in 5 days", "in 20 days"])

    def test_missed_deadlines_sink_below_everything_upcoming(self):
        tasks = [_t("missed", -3), _t("far away", 40), _t("today", 0)]
        self.assertEqual(_tasks(tasks), ["today", "far away", "missed"])

    def test_missed_group_lists_the_most_recently_missed_first(self):
        tasks = [_t("a week ago", -7), _t("yesterday", -1), _t("three days ago", -3)]
        self.assertEqual(_tasks(tasks), ["yesterday", "three days ago", "a week ago"])

    def test_undated_tasks_sit_after_upcoming_but_above_missed(self):
        tasks = [_t("missed", -2), _t("no date", None), _t("upcoming", 30)]
        self.assertEqual(_tasks(tasks), ["upcoming", "no date", "missed"])

    def test_full_reading_order(self):
        tasks = [
            _t("missed old", -10), _t("tomorrow", 1), _t("no date", None), _t("missed new", -1),
            _t("today", 0), _t("later", 9),
        ]
        self.assertEqual(
            _tasks(tasks),
            ["today", "tomorrow", "later", "no date", "missed new", "missed old"],
        )

    def test_creation_order_no_longer_decides(self):
        first_made = _t("first made", 9, created=1)
        last_made = _t("last made", 0, created=9)
        self.assertEqual(_tasks([first_made, last_made]), ["last made", "first made"])

    def test_same_day_puts_higher_priority_first(self):
        tasks = [
            _t("low", 0, Priority.LOW, created=1),
            _t("high", 0, Priority.HIGH, created=3),
            _t("medium", 0, Priority.MEDIUM, created=2),
        ]
        self.assertEqual(_tasks(tasks), ["high", "medium", "low"])

    def test_priority_also_breaks_ties_inside_the_missed_group(self):
        tasks = [_t("low", -2, Priority.LOW), _t("high", -2, Priority.HIGH)]
        self.assertEqual(_tasks(tasks), ["high", "low"])

    def test_same_day_same_priority_keeps_creation_order_then_title(self):
        tasks = [_t("b second", 3, created=2), _t("z first", 3, created=1), _t("a tied", 3, created=2)]
        self.assertEqual(_tasks(tasks), ["z first", "a tied", "b second"])

    def test_priority_never_outranks_an_earlier_deadline(self):
        tasks = [_t("urgent but later", 6, Priority.HIGH), _t("calm but sooner", 2, Priority.LOW)]
        self.assertEqual(_tasks(tasks), ["calm but sooner", "urgent but later"])

    def test_priority_never_lifts_a_missed_task_above_an_upcoming_one(self):
        tasks = [_t("missed urgent", -1, Priority.HIGH), _t("upcoming calm", 30, Priority.LOW)]
        self.assertEqual(_tasks(tasks), ["upcoming calm", "missed urgent"])

    def test_sorting_is_deterministic(self):
        tasks = [_t(f"t{i}", i % 5 - 2, created=i % 3) for i in range(15)]
        self.assertEqual(_tasks(tasks), _tasks(list(reversed(tasks))))

    def test_the_boundary_moves_with_today(self):
        # The same task is "upcoming" one day and "missed" the next.
        task = _t("due oct 27", 0)
        other = _t("due oct 28", 1)
        self.assertEqual(
            [x.title for x in sorted([other, task], key=lambda t: _task_order(t, TODAY))],
            ["due oct 27", "due oct 28"],
        )
        tomorrow = TODAY + timedelta(days=1)
        self.assertEqual(
            [x.title for x in sorted([task, other], key=lambda t: _task_order(t, tomorrow))],
            ["due oct 28", "due oct 27"],
        )


class SubtaskOrderTests(unittest.TestCase):
    def test_same_reading_order_as_tasks(self):
        subs = [
            _s("missed", -2, created=1), _s("no date", None, created=2),
            _s("later", 8, created=3), _s("today", 0, created=4),
        ]
        self.assertEqual(_subs(subs), ["today", "later", "no date", "missed"])

    def test_same_day_steps_keep_the_order_they_were_authored_in(self):
        subs = [_s("2 review", 3, created=2), _s("1 prepare", 3, created=1), _s("3 approve", 3, created=3)]
        self.assertEqual(_subs(subs), ["1 prepare", "2 review", "3 approve"])


if __name__ == "__main__":
    unittest.main()
