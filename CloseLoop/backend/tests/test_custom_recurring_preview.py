"""A Team Lead's own recurring tasks ("Add Task" with a repeat) in the upcoming-
period preview.

They have no Close Pack template — only a `recurrence_rule` stored on every
instance — so `GET /plans/preview` has to read them back from that rule, with the
same maths `extend_recurring_custom_tasks` uses when the previous period closes.
"""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from app.calendar import set_configured_holidays, to_ad
from app.models import RecurrenceFrequency as F
from app.services.plan_generation_service import (
    CustomPreviewGroup,
    _custom_preview_groups,
    _custom_rule_defn,
    preview_custom_recurring_for_period,
    preview_custom_rule_for_period,
)

# Period 2: 2026-11-27 .. 2026-12-24 (Fri .. Thu), the one being previewed.
PERIOD = SimpleNamespace(id="p2", start_date=date(2026, 11, 27), end_date=date(2026, 12, 24))
ORIGIN = SimpleNamespace(id="p1", start_date=date(2026, 10, 30), end_date=date(2026, 11, 26))


def _rule(**kw):
    base = dict(
        title="Lead Task", description=None, frequency="MONTHLY", interval_count=1,
        days_of_week=None, is_required=True, anchor="2026-11-12", end=None, calendar="AD",
        due_days=0, priority=None, tags=[], subtasks=[],
    )
    base.update(kw)
    return base


def _rep(rule=None, *, group="g1", plan="plan1", entity="e1", title="Lead Task", **kw):
    return SimpleNamespace(
        recurring_group_id=group, plan_id=plan, entity_id=entity, title=title,
        occurrence_date=kw.get("occurrence_date"), due_date=kw.get("due_date", date(2026, 11, 12)),
        recurrence_rule=_rule() if rule is None else rule,
    )


def _plan(pid="plan1", *, pack="pack1", owner="lead1", period="p1"):
    return SimpleNamespace(id=pid, source_pack_id=pack, owner_id=owner, period_id=period)


class PreviewRuleTests(unittest.TestCase):
    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})

    def test_monthly_lands_once_in_a_later_period_and_is_flagged_custom(self):
        rows = preview_custom_rule_for_period(_rep(), _rule(), PERIOD)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["is_custom"])
        self.assertTrue(row["occurs_in_period"])
        # anchor Nov 12 -> Dec 12 is a Saturday: the occurrence stays, the DUE date rolls.
        self.assertEqual(row["occurrence_date"], date(2026, 12, 12))
        self.assertEqual(row["due_date"], date(2026, 12, 14))
        self.assertEqual(row["title"], "Lead Task")
        self.assertIsNone(row["template_id"])

    def test_a_cadence_that_skips_the_period_is_an_upcoming_row(self):
        rows = preview_custom_rule_for_period(_rep(), _rule(frequency="QUARTERLY", anchor="2026-11-12"), PERIOD)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["occurs_in_period"])
        self.assertIsNone(rows[0]["due_date"])
        # Quarterly from Nov 12: Nov 12, Feb 12, May 12 — none inside Nov 27..Dec 24.
        self.assertEqual(rows[0]["next_due_date"], date(2027, 2, 12))
        self.assertTrue(rows[0]["is_custom"])

    def test_every_four_days_moves_weekend_steps_to_monday(self):
        # Mangsir 1 2083 (Tue 2026-11-17) every 4 days — the same series the
        # real extension produces.
        start = to_ad(2083, 8, 1)
        rows = preview_custom_rule_for_period(
            _rep(), _rule(frequency="DAILY", interval_count=4, anchor=start.isoformat()), PERIOD
        )
        days = [r["occurrence_date"] for r in rows]
        self.assertEqual(days[:3], [date(2026, 11, 30), date(2026, 12, 3), date(2026, 12, 7)])
        self.assertTrue(all(d.weekday() < 5 for d in days))
        self.assertTrue(all(PERIOD.start_date <= d <= PERIOD.end_date for d in days))
        self.assertTrue(all(r["title"].startswith("Lead Task — ") for r in rows))

    def test_weekly_gives_a_row_per_week(self):
        rows = preview_custom_rule_for_period(
            _rep(), _rule(frequency="WEEKLY", days_of_week=[0], anchor="2026-11-02"), PERIOD
        )
        self.assertEqual(
            [r["due_date"] for r in rows],
            [date(2026, 11, 30), date(2026, 12, 7), date(2026, 12, 14), date(2026, 12, 21)],
        )

    def test_a_series_that_has_ended_has_no_row(self):
        for freq in ("MONTHLY", "WEEKLY", "DAILY"):
            rows = preview_custom_rule_for_period(
                _rep(), _rule(frequency=freq, days_of_week=[0], end="2026-11-20"), PERIOD
            )
            self.assertEqual(rows, [], freq)

    def test_n_day_due_date_offsets_from_the_occurrence(self):
        rows = preview_custom_rule_for_period(_rep(), _rule(due_days=3, anchor="2026-12-01"), PERIOD)
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 12, 1))
        self.assertEqual(rows[0]["due_date"], date(2026, 12, 4))
        self.assertEqual(rows[0]["due_days"], 3)

    def test_holidays_move_the_due_date_like_real_generation(self):
        set_configured_holidays({date(2026, 12, 1): "Holiday"})
        rows = preview_custom_rule_for_period(_rep(), _rule(anchor="2026-12-01"), PERIOD)
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 12, 1))
        self.assertEqual(rows[0]["due_date"], date(2026, 12, 2))

    def test_sub_tasks_come_from_the_stored_rule(self):
        rule = _rule(
            anchor="2026-12-01",
            subtasks=[{"title": "Sub A", "description": None, "priority": None, "tags": []},
                      {"title": "Sub B", "description": "x", "priority": "HIGH", "tags": ["t"]}],
        )
        rows = preview_custom_rule_for_period(_rep(), rule, PERIOD)
        self.assertEqual([s["title"] for s in rows[0]["subtasks"]], ["Sub A", "Sub B"])
        self.assertTrue(all(s["due_date"] == rows[0]["due_date"] for s in rows[0]["subtasks"]))

    def test_priority_and_tags_are_carried(self):
        rows = preview_custom_rule_for_period(
            _rep(), _rule(anchor="2026-12-01", priority="HIGH", tags=["a", "b"]), PERIOD
        )
        self.assertEqual((rows[0]["priority"], rows[0]["tags"]), ("HIGH", ["a", "b"]))

    def test_bikram_sambat_rules_step_by_bs_months(self):
        # BS Mangsir 2 2083 (Nov 18) -> Poush 2 2083 (Dec 17), not "the same AD
        # day next month" (Dec 18).
        rows = preview_custom_rule_for_period(
            _rep(), _rule(anchor=to_ad(2083, 8, 2).isoformat(), calendar="BS"), PERIOD
        )
        self.assertEqual(rows[0]["occurrence_date"], to_ad(2083, 9, 2))
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 12, 17))
        rows = preview_custom_rule_for_period(
            _rep(), _rule(anchor=to_ad(2083, 8, 2).isoformat(), calendar="AD"), PERIOD
        )
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 12, 18))

    def test_unknown_or_missing_frequency_is_nothing(self):
        self.assertEqual(preview_custom_rule_for_period(_rep(), _rule(frequency=None), PERIOD), [])
        self.assertEqual(preview_custom_rule_for_period(_rep(), _rule(frequency="HOURLY"), PERIOD), [])

    def test_unknown_calendar_falls_back_to_gregorian(self):
        rows = preview_custom_rule_for_period(_rep(), _rule(anchor="2026-12-01", calendar="??"), PERIOD)
        self.assertEqual(rows[0]["occurrence_date"], date(2026, 12, 1))


class RuleDefnTests(unittest.TestCase):
    """The definition the preview and the real extension both work from."""

    def test_title_falls_back_to_the_instance_title(self):
        d = _custom_rule_defn(_rep(title="Instance"), _rule(title=None), F.MONTHLY)
        self.assertEqual(d["title"], "Instance")

    def test_anchor_falls_back_to_the_occurrence_then_the_due_date(self):
        rule = _rule(anchor=None)
        rep = _rep(rule, occurrence_date=date(2026, 11, 5), due_date=date(2026, 11, 9))
        self.assertEqual(_custom_rule_defn(rep, rule, F.MONTHLY)["recurrence_start"], date(2026, 11, 5))
        rep = _rep(rule, occurrence_date=None, due_date=date(2026, 11, 9))
        self.assertEqual(_custom_rule_defn(rep, rule, F.MONTHLY)["recurrence_start"], date(2026, 11, 9))

    def test_end_and_interval_defaults(self):
        d = _custom_rule_defn(_rep(), _rule(end="2027-01-31", interval_count=None, due_days=None), F.WEEKLY)
        self.assertEqual(d["recurrence_end"], date(2027, 1, 31))
        self.assertEqual((d["interval_count"], d["due_days"]), (1, 0))
        self.assertIs(d["frequency"], F.WEEKLY)


class GroupingTests(unittest.TestCase):
    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})

    def _groups(self, reps, plans, period=PERIOD, origins=None):
        return _custom_preview_groups(
            {r.recurring_group_id: r for r in reps},
            {p.id: p for p in plans},
            {p.id: p for p in (origins or [ORIGIN])},
            period,
        )

    def test_tasks_of_one_pack_and_entity_share_a_group_soonest_first(self):
        a = _rep(_rule(title="Later", anchor="2026-12-18"), group="g1", title="Later")
        b = _rep(_rule(title="Sooner", anchor="2026-12-02"), group="g2", title="Sooner")
        groups = self._groups([a, b], [_plan()])
        self.assertEqual(len(groups), 1)
        self.assertEqual([t["title"] for t in groups[0].tasks], ["Sooner", "Later"])
        self.assertEqual((groups[0].pack_id, groups[0].entity_id, groups[0].owner_id), ("pack1", "e1", "lead1"))

    def test_upcoming_placeholders_sort_after_real_rows(self):
        real = _rep(_rule(title="Real", anchor="2026-12-02"), group="g1", title="Real")
        later = _rep(_rule(title="Later One", frequency="ANNUAL", anchor="2027-06-10"), group="g2", title="Later One")
        groups = self._groups([later, real], [_plan()])
        self.assertEqual([t["title"] for t in groups[0].tasks], ["Real", "Later One"])
        self.assertFalse(groups[0].tasks[1]["occurs_in_period"])

    def test_different_entities_or_packs_are_different_groups(self):
        a = _rep(group="g1", entity="e1", plan="plan1")
        b = _rep(group="g2", entity="e2", plan="plan2")
        c = _rep(group="g3", entity="e1", plan="plan3")
        groups = self._groups([a, b, c], [_plan("plan1"), _plan("plan2"), _plan("plan3", pack="pack2")])
        self.assertEqual(
            sorted((g.pack_id, g.entity_id) for g in groups),
            [("pack1", "e1"), ("pack1", "e2"), ("pack2", "e1")],
        )

    def test_a_plan_with_no_close_pack_is_its_own_ad_hoc_group(self):
        groups = self._groups([_rep(plan="adhoc")], [_plan("adhoc", pack=None)])
        self.assertEqual([(g.pack_id, g.entity_id) for g in groups], [(None, "e1")])

    def test_a_period_before_the_series_began_gets_nothing(self):
        before = SimpleNamespace(id="p0", start_date=date(2026, 10, 2), end_date=date(2026, 10, 29))
        self.assertEqual(self._groups([_rep()], [_plan()], period=before), [])

    def test_the_period_it_was_added_in_and_later_ones_are_eligible(self):
        same = SimpleNamespace(id="p1", start_date=ORIGIN.start_date, end_date=ORIGIN.end_date)
        self.assertEqual(len(self._groups([_rep(_rule(anchor="2026-11-12"))], [_plan()], period=same)), 1)
        later = SimpleNamespace(id="p9", start_date=date(2027, 6, 1), end_date=date(2027, 6, 28))
        self.assertEqual(len(self._groups([_rep()], [_plan()], period=later)), 1)

    def test_a_missing_plan_or_origin_period_is_tolerated(self):
        self.assertEqual(self._groups([_rep(plan="gone")], [_plan()]), [])
        # origin unknown: nothing to compare against, so it is shown rather than hidden.
        self.assertEqual(len(self._groups([_rep()], [_plan()], origins=[SimpleNamespace(
            id="other", start_date=date(2030, 1, 1), end_date=date(2030, 1, 28))])), 1)

    def test_an_ended_series_leaves_no_group(self):
        self.assertEqual(self._groups([_rep(_rule(end="2026-11-20"))], [_plan()]), [])

    def test_the_first_known_owner_is_kept(self):
        a = _rep(group="g1", plan="plan1")
        b = _rep(group="g2", plan="plan2")
        groups = self._groups([a, b], [_plan("plan1", owner=None), _plan("plan2", owner="lead2")])
        self.assertEqual(groups[0].owner_id, "lead2")


class PreviewForPeriodDbTests(unittest.IsolatedAsyncioTestCase):
    """The async wrapper: three reads (representatives, their plans, their
    periods) and nothing written."""

    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})

    def _result(self, rows):
        res = mock.Mock()
        res.scalars.return_value.all.return_value = rows
        return res

    async def test_no_recurring_tasks_stops_after_one_read(self):
        db = SimpleNamespace(execute=mock.AsyncMock(return_value=self._result([])), add=mock.Mock(), flush=mock.AsyncMock())
        out = await preview_custom_recurring_for_period(db, tenant_id="t1", period=PERIOD)
        self.assertEqual(out, [])
        self.assertEqual(db.execute.await_count, 1)
        db.add.assert_not_called()

    async def test_reads_reps_then_plans_then_periods(self):
        rep = _rep(_rule(anchor="2026-12-01"))
        db = SimpleNamespace(
            execute=mock.AsyncMock(side_effect=[
                self._result([rep]), self._result([_plan()]), self._result([ORIGIN]),
            ]),
            add=mock.Mock(), flush=mock.AsyncMock(),
        )
        out = await preview_custom_recurring_for_period(db, tenant_id="t1", period=PERIOD)
        self.assertEqual(db.execute.await_count, 3)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], CustomPreviewGroup)
        self.assertEqual(out[0].tasks[0]["due_date"], date(2026, 12, 1))
        db.add.assert_not_called()
        db.flush.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
