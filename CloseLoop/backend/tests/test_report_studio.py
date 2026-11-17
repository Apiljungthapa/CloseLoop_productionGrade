"""Report Studio analysis: pure functions over hand-checkable data (no database)."""
import json
import unittest
from datetime import date, datetime, timezone

from app.calendar import set_configured_holidays
from app.services import report_studio as rs
from app.services.report_studio import (
    Names, PeriodData, PeriodInfo, PlanRow, TaskRow,
    build_close_status, build_comparison, build_overdue, build_pending_by_member, build_period_completion,
    compute_metrics, member_rows, readiness,
)

TODAY = date(2026, 10, 26)  # a Monday
P11 = PeriodInfo("p11", "P11", date(2026, 10, 5), date(2026, 11, 1), "OPEN")
P10 = PeriodInfo("p10", "P10", date(2026, 9, 7), date(2026, 10, 4), "CLOSED")
NAMES = Names(entities={"DE": "DE", "FR": "FR"}, users={"u1": "Alice", "u2": "Bob"})


def T(tid, status, due, entity="DE", *, who=(), done=(), required=True, completed_on=None, priority="MEDIUM"):
    return TaskRow(
        id=tid, title=f"Task {tid}", status=status, entity_id=entity, due_date=due, completed_on=completed_on,
        is_required=required, priority=priority, assignee_ids=tuple(who), done_ids=frozenset(done),
    )


def dataset():
    return [
        T("d1", "COMPLETED", date(2026, 10, 10), who=["u1"], completed_on=date(2026, 10, 9)),
        T("d2", "COMPLETED", date(2026, 10, 12), who=["u1"], completed_on=date(2026, 10, 14)),
        T("d3", "IN_PROGRESS", date(2026, 10, 20), who=["u1"]),
        T("d4", "NOT_STARTED", date(2026, 10, 30), who=["u2"]),
        T("d5", "PENDING_REVIEW", date(2026, 10, 22), who=["u2"]),
        T("f1", "COMPLETED", date(2026, 10, 15), "FR", who=["u2"], completed_on=date(2026, 10, 15)),
        T("f2", "ASSIGNED", date(2026, 10, 18), "FR", who=["u2"], required=False),
        T("f3", "NOT_STARTED", date(2026, 10, 24), "FR"),
        T("f4", "CARRIED_FORWARD", date(2026, 10, 5), "FR", who=["u1"]),
        T("f5", "UPCOMING", date(2026, 11, 5), "FR"),
    ]


def period(tasks, info=P11, plans=None):
    plans = plans if plans is not None else [PlanRow("pl-de", "DE", "OPEN"), PlanRow("pl-fr", "FR", "OPEN")]
    return PeriodData(info, plans, tasks)


class StudioTestCase(unittest.TestCase):
    def setUp(self):
        set_configured_holidays({})
        self.addCleanup(set_configured_holidays, {})


class MetricsTests(StudioTestCase):
    def test_hand_checked_numbers(self):
        m = compute_metrics(dataset(), TODAY)
        # carried-forward, upcoming placeholders and the SKIPPED optional f2 are not counted
        self.assertEqual(m["total"], 7)
        self.assertEqual((m["completed"], m["open"]), (3, 4))
        self.assertEqual(m["completion_pct"], 42.9)  # 3 of 7
        self.assertEqual(m["overdue"], 3)  # d3, d5 and the unassigned f3 - NOT the optional f2
        self.assertEqual(m["pending_review"], 1)
        self.assertEqual(m["due_soon"], 1)  # d4 (Oct 30) only
        self.assertEqual(m["unassigned_open"], 1)
        self.assertEqual(m["skipped"], 1)  # f2: reported, but in no total
        self.assertEqual((m["carried_forward"], m["upcoming"]), (1, 1))
        self.assertEqual(m["expected_pct"], 85.7)  # 6 of 7 were already due
        self.assertEqual(m["on_time_pct"], 66.7)  # 2 of the 3 finished on time

    def test_only_skipped_optional_tasks_are_left_out_of_the_totals(self):
        base = [T("a", "COMPLETED", date(2026, 10, 10), completed_on=date(2026, 10, 10)), T("b", "NOT_STARTED", date(2026, 10, 30))]
        skipped = T("o2", "NOT_STARTED", date(2026, 10, 1), required=False)  # optional, past due, not done
        without, with_skipped = compute_metrics(base, TODAY), compute_metrics(base + [skipped], TODAY)
        for key in ("total", "completed", "open", "not_started", "in_progress", "overdue", "completion_pct", "expected_pct"):
            self.assertEqual(with_skipped[key], without[key], key)
        self.assertEqual((with_skipped["skipped"], without["skipped"]), (1, 0))

    def test_other_optional_tasks_count_like_required_ones(self):
        base = [T("a", "COMPLETED", date(2026, 10, 10), completed_on=date(2026, 10, 10)), T("b", "NOT_STARTED", date(2026, 10, 30))]
        optional = [
            T("o1", "COMPLETED", date(2026, 10, 10), required=False, completed_on=date(2026, 10, 10)),  # done
            T("o3", "IN_PROGRESS", date(2026, 10, 30), required=False),  # not due yet
            T("o4", "PENDING_REVIEW", date(2026, 10, 1), required=False),  # past due, but in review
        ]
        m = compute_metrics(base + optional, TODAY)
        self.assertEqual((m["total"], m["completed"], m["open"]), (5, 2, 3))
        self.assertEqual(m["overdue"], 1)  # o4: a past-due optional task that is not skipped is overdue
        self.assertEqual(m["skipped"], 0)

    def test_a_skipped_task_is_never_overdue(self):
        t = T("o", "NOT_STARTED", date(2026, 10, 1), required=False)
        self.assertFalse(rs.is_overdue(t, TODAY))
        self.assertTrue(rs.is_skipped(t, TODAY))
        self.assertTrue(rs.is_overdue(T("r", "NOT_STARTED", date(2026, 10, 1)), TODAY))
        # the same optional task is a normal, counted task until its due date passes
        self.assertFalse(rs.is_skipped(T("o", "NOT_STARTED", TODAY, required=False), TODAY))

    def test_a_completed_or_carried_task_is_never_overdue(self):
        self.assertFalse(rs.is_overdue(T("c", "COMPLETED", date(2026, 10, 1)), TODAY))
        self.assertFalse(rs.is_overdue(T("c", "CARRIED_FORWARD", date(2026, 10, 1)), TODAY))

    def test_due_today_is_not_overdue_yet(self):
        self.assertFalse(rs.is_overdue(T("t", "NOT_STARTED", TODAY), TODAY))

    def test_age_counts_working_days_only(self):
        fri = T("f", "NOT_STARTED", date(2026, 10, 23))
        self.assertEqual(rs.overdue_age(fri, TODAY), 1)  # Monday is the first working day late
        self.assertEqual(rs.overdue_age(fri, date(2026, 10, 25)), 0)  # still the weekend
        set_configured_holidays({date(2026, 10, 26): "Holiday"})
        self.assertEqual(rs.overdue_age(fri, TODAY), 0)  # and a holiday does not age it either

    def test_no_tasks_gives_zeroes_not_errors(self):
        m = compute_metrics([], TODAY)
        self.assertEqual((m["total"], m["completion_pct"], m["expected_pct"], m["on_time_pct"]), (0, 0.0, None, None))

    def test_a_finished_period_is_judged_the_day_after_it_ended(self):
        data = period([T("a", "NOT_STARTED", date(2026, 10, 4))], P10)
        self.assertEqual(data.eval_date(TODAY), date(2026, 10, 5))
        self.assertEqual(period([], P11).eval_date(TODAY), TODAY)  # a running period is judged today


class MemberTests(StudioTestCase):
    def rows(self, tasks=None, member=None):
        return {r["member"]: r for r in member_rows(period(tasks or dataset()), NAMES, TODAY, member)}

    def test_each_person_and_the_unassigned_row(self):
        rows = self.rows()
        alice, bob, nobody = rows["Alice"], rows["Bob"], rows["Unassigned"]
        self.assertEqual((alice["to_do"], alice["in_review"], alice["overdue"], alice["completed"]), (1, 0, 1, 2))
        self.assertEqual(alice["oldest_overdue_days"], 4)
        self.assertEqual(alice["completion_pct"], 66.7)
        # f2 (Bob's optional task) is on his plate but in none of his counts
        self.assertEqual((bob["to_do"], bob["in_review"], bob["overdue"], bob["due_soon"], bob["completed"]), (1, 1, 1, 1, 1))
        self.assertEqual(bob["oldest_overdue_days"], 2)
        self.assertEqual((nobody["to_do"], nobody["overdue"]), (1, 1))

    def test_carried_forward_and_upcoming_are_nobodys_pending_work(self):
        self.assertEqual(sum(r["to_do"] + r["in_review"] for r in self.rows().values()), 4)

    def test_an_entity_wide_task_waits_only_on_those_who_have_not_finished(self):
        shared = T("s", "IN_PROGRESS", date(2026, 10, 28), who=["u1", "u2"], done=["u1"])
        rows = self.rows([shared])
        self.assertEqual((rows["Alice"]["to_do"], rows["Alice"]["completed"]), (0, 1))
        self.assertEqual((rows["Bob"]["to_do"], rows["Bob"]["completed"]), (1, 0))

    def test_filtering_to_one_person(self):
        self.assertEqual(set(self.rows(member="u2")), {"Bob"})

    def test_sorted_by_pending_work_then_overdue(self):
        order = [r["member"] for r in member_rows(period(dataset()), NAMES, TODAY)]
        self.assertEqual(order, ["Bob", "Alice", "Unassigned"])


class ReadinessTests(StudioTestCase):
    def test_states(self):
        plan_open, plan_closed = PlanRow("a", "DE", "OPEN"), PlanRow("b", "DE", "CLOSED")
        done = [T("x", "COMPLETED", date(2026, 10, 10), completed_on=date(2026, 10, 9))]
        self.assertEqual(readiness(done, [plan_closed], TODAY), "closed")
        self.assertEqual(readiness(done, [plan_open], TODAY), "ready")
        self.assertEqual(readiness([], [plan_open], TODAY), "no_tasks")
        calm = done + [T("y", "NOT_STARTED", date(2026, 11, 20))]
        self.assertEqual(readiness(calm, [plan_open], TODAY), "on_track")
        # one of four open tasks overdue = 25% of open work: behind
        mixed = done + [T("y", "NOT_STARTED", date(2026, 10, 20))] + [T(f"z{i}", "NOT_STARTED", date(2026, 11, 20)) for i in range(3)]
        self.assertEqual(readiness(mixed, [plan_open], TODAY), "behind")
        # 1 of 5 open tasks overdue = 20%: at risk
        risky = done + [T("y", "NOT_STARTED", date(2026, 10, 20))] + [T(f"z{i}", "NOT_STARTED", date(2026, 11, 20)) for i in range(4)]
        self.assertEqual(readiness(risky, [plan_open], TODAY), "at_risk")


class ReportShapeTests(StudioTestCase):
    NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)

    def check_common(self, report):
        json.dumps(report.to_dict())  # must be plain JSON
        self.assertTrue(report.title and report.subtitle)
        self.assertTrue(report.kpis)
        self.assertTrue(report.findings)
        self.assertTrue(report.recommendations)
        self.assertEqual(sum(1 for t in report.tables if t.primary), 1)
        for chart in report.charts:
            for s in chart.series:
                self.assertEqual(len(s.values), len(chart.labels), chart.id)
        for table in report.tables:
            keys = {c.key for c in table.columns}
            for row in table.rows:
                self.assertTrue(keys <= set(row), (table.id, keys - set(row)))

    def test_completion_report(self):
        r = build_period_completion(period(dataset()), NAMES, TODAY, generated_at=self.NOW)
        self.check_common(r)
        k = {x.key: x for x in r.kpis}
        self.assertEqual((k["completion"].value, k["overdue"].value, k["total"].value), (42.9, 3, 7))
        self.assertEqual(k["completion"].tone, "bad")  # 42.9% against 85.7% already due
        self.assertEqual(k["overdue"].tone, "warn")
        ids = {c.id for c in r.charts}
        self.assertTrue({"status_mix", "entity_completion", "burn_up", "overdue_ageing"} <= ids)
        text = " ".join(f.text for f in r.findings)
        self.assertIn("behind", text)
        self.assertIn("overdue", text)
        self.assertTrue(any("review" in x.text.lower() for x in r.recommendations))
        self.assertEqual(r.tables[0].id, "by_entity")

    def test_burn_up_shows_progress_to_date_and_nothing_for_the_future(self):
        def done_series(today):
            chart = next(c for c in build_period_completion(period(dataset()), NAMES, today).charts if c.id == "burn_up")
            return chart.series[0].values, chart.series[1].values

        planned, done = done_series(TODAY)  # Oct 26: weeks end Oct 11 / 18 / 25 / Nov 1
        self.assertEqual(done[2], 3.0)  # everything finished by the end of week 3
        self.assertEqual(done[3], 3.0)  # the running week shows progress to date
        self.assertEqual(planned, sorted(planned))  # cumulative never goes down
        self.assertEqual(planned[-1], 7.0)  # the counted tasks only: no optional, carried-forward or upcoming

        _planned, early = done_series(date(2026, 10, 12))  # early in the period
        self.assertEqual(early, [1.0, 1.0, None, None])  # only d1 was done by Oct 12; later weeks are unknown

    def test_pending_by_member_report(self):
        r = build_pending_by_member(period(dataset()), NAMES, TODAY, generated_at=self.NOW)
        self.check_common(r)
        k = {x.key: x for x in r.kpis}
        self.assertEqual((k["open"].value, k["holders"].value, k["unassigned"].value), (4, 2, 1))
        text = " ".join(f.text for f in r.findings)
        self.assertIn("Bob holds the most pending work: 2 tasks", text)  # d4 and d5; the optional f2 is not counted
        self.assertTrue(any("nobody assigned" in f.text for f in r.findings))
        self.assertTrue(any("Assign" in x.text for x in r.recommendations))
        self.assertEqual(r.tables[0].rows[0]["member"], "Bob")

    def test_pending_report_for_one_member(self):
        r = build_pending_by_member(period(dataset()), NAMES, TODAY, member_id="u1")
        self.assertEqual([row["member"] for row in r.tables[0].rows], ["Alice"])
        self.assertEqual(r.scope["member"], "Alice")

    def test_overdue_report(self):
        r = build_overdue(period(dataset()), NAMES, TODAY, generated_at=self.NOW)
        self.check_common(r)
        rows = r.tables[0].rows
        self.assertEqual([row["title"] for row in rows], ["Task d3", "Task d5", "Task f3"])  # oldest first
        self.assertEqual([row["days_overdue"] for row in rows], [4, 2, 1])
        self.assertEqual(rows[2]["assignees"], "Unassigned")
        ageing = next(c for c in r.charts if c.id == "overdue_ageing")
        self.assertEqual(ageing.series[0].values, [2.0, 1.0, 0.0, 0.0])  # ages 2 and 1 | age 4

    def test_overdue_report_with_nothing_overdue(self):
        r = build_overdue(period([T("a", "COMPLETED", date(2026, 10, 10), completed_on=date(2026, 10, 9))]), NAMES, TODAY)
        self.check_common(r)
        self.assertEqual(r.findings[0].severity, "good")
        self.assertEqual(r.tables[0].rows, [])

    def test_close_status_report(self):
        plans = [PlanRow("a", "DE", "OPEN"), PlanRow("b", "FR", "OPEN")]
        r = build_close_status(period(dataset(), plans=plans), NAMES, TODAY, generated_at=self.NOW)
        self.check_common(r)
        by_entity = {row["entity"]: row for row in r.tables[0].rows}
        self.assertEqual(by_entity["DE"]["total"], 5)
        self.assertEqual(by_entity["FR"]["overdue"], 1)
        self.assertEqual(by_entity["FR"]["blocker"], "1 overdue, 1 unassigned")
        self.assertEqual({"readiness_mix", "entity_completion"} <= {c.id for c in r.charts}, True)
        # worst first
        order = [row["readiness"] for row in r.tables[0].rows]
        self.assertEqual(order, sorted(order, key=["Behind", "At risk", "On track", "Ready to close", "Closed", "No tasks"].index))

    def test_close_status_when_everything_is_closed(self):
        done = [T("x", "COMPLETED", date(2026, 10, 10), completed_on=date(2026, 10, 9))]
        r = build_close_status(period(done, plans=[PlanRow("a", "DE", "CLOSED")]), NAMES, TODAY)
        self.check_common(r)
        self.assertEqual(r.tables[0].rows[0]["readiness"], "Closed")
        self.assertEqual(r.findings[0].severity, "good")

    def test_an_empty_period_is_a_report_not_an_error(self):
        for build in (build_period_completion, build_pending_by_member, build_overdue, build_close_status):
            r = build(period([], plans=[]), NAMES, TODAY)
            json.dumps(r.to_dict())
            self.assertTrue(r.findings, build.__name__)
            self.assertTrue(r.recommendations, build.__name__)


class ComparisonTests(StudioTestCase):
    def prev_tasks(self):
        return [
            T("p1", "COMPLETED", date(2026, 9, 10), completed_on=date(2026, 9, 9)),
            T("p2", "COMPLETED", date(2026, 9, 12), completed_on=date(2026, 9, 12)),
            T("p3", "COMPLETED", date(2026, 9, 20), "FR", completed_on=date(2026, 9, 19)),
            T("p4", "NOT_STARTED", date(2026, 9, 25), "FR"),
        ]

    def build(self, history=None):
        cur, prev = period(dataset()), period(self.prev_tasks(), P10)
        return build_comparison(cur, prev, history or [prev, cur], NAMES, TODAY)

    def test_deltas_and_directions(self):
        r = self.build()
        k = {x.key: x for x in r.kpis}
        self.assertEqual(k["completion"].delta, -32.1)  # 42.9% now against 75% then
        self.assertEqual(k["completion"].delta_tone, "bad")
        self.assertEqual(k["overdue"].delta, 2.0)  # 3 now against 1 then (P10 judged the day after it ended)
        self.assertEqual(k["overdue"].delta_tone, "bad")
        self.assertEqual(k["completion"].delta_display, "-32.1 pts")
        self.assertIn("P10", k["completion"].hint)

    def test_findings_name_the_movers(self):
        text = " ".join(f.text for f in self.build().findings)
        self.assertIn("Completion is down 32.1 points", text)
        self.assertIn("slipped most", text)
        self.assertIn("still running", text)  # P11 is open, P10 is final

    def test_charts_and_tables(self):
        r = self.build()
        self.assertTrue({"volume_compare", "entity_compare", "trend", "trend_overdue"} <= {c.id for c in r.charts})
        trend = next(c for c in r.charts if c.id == "trend")
        self.assertEqual(trend.labels, ["P10", "P11"])
        kpi_rows = {row["metric"]: row for row in r.tables[0].rows}
        self.assertEqual(kpi_rows["Completion %"]["previous"], 75.0)
        self.assertEqual(kpi_rows["Completion %"]["current"], 42.9)
        self.assertEqual(kpi_rows["Completion %"]["change"], -32.1)
        json.dumps(r.to_dict())

    def test_an_empty_previous_period_says_so(self):
        cur, prev = period(dataset()), period([], P10)
        r = build_comparison(cur, prev, [cur], NAMES, TODAY)
        self.assertIn("nothing to compare", r.findings[0].text)
        json.dumps(r.to_dict())

    def test_a_three_period_decline_is_called_out(self):
        def mk(info, done, total):
            tasks = [T(f"{info.id}{i}", "COMPLETED" if i < done else "NOT_STARTED", date(2026, 1, 1), completed_on=date(2026, 1, 1))
                     for i in range(total)]
            return period(tasks, info)
        a = mk(PeriodInfo("a", "Pa", date(2026, 7, 1), date(2026, 7, 28), "CLOSED"), 9, 10)
        b = mk(PeriodInfo("b", "Pb", date(2026, 8, 1), date(2026, 8, 28), "CLOSED"), 7, 10)
        c = mk(PeriodInfo("c", "Pc", date(2026, 9, 1), date(2026, 9, 28), "CLOSED"), 5, 10)
        r = build_comparison(c, b, [a, b, c], NAMES, TODAY)
        self.assertTrue(any("fallen three periods in a row" in f.text for f in r.findings))
        self.assertTrue(any("three-period decline" in x.text for x in r.recommendations))


class RecommendationTests(StudioTestCase):
    def test_recommendations_are_prioritised_deduplicated_and_never_empty(self):
        recs = rs._prioritise([rs.Recommendation("low", "a"), rs.Recommendation("high", "b"),
                               rs.Recommendation("high", "b"), rs.Recommendation("medium", "c")])
        self.assertEqual([(r.priority, r.text) for r in recs], [("high", "b"), ("medium", "c"), ("low", "a")])
        self.assertEqual(rs._prioritise([])[0].priority, "low")


if __name__ == "__main__":
    unittest.main()
