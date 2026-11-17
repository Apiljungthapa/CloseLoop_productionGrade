"""Team Lead -> team member time log: the lead scope helper and the endpoint's
guards. No database — a fake session answers each query by the table it hits."""
import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from fastapi import HTTPException

from app.models import Role
from app.routers import time_entries as te
from app.schemas import MyTimeSummaryOut, TimeDayOut, TimeMonthOut, TimeWeekOut, UserMini
from app.services import report_access_service as access

TENANT = "t1"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """execute(stmt) -> canned rows, keyed by the table named in the SELECT."""

    def __init__(self, *, tasks=(), fanout=(), subtasks=(), users=(), time_rows=()):
        self.tasks, self.fanout, self.subtasks, self.users = tasks, fanout, subtasks, users
        self.time_rows = time_rows
        self.time_sql = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "task_entity_assignees" in sql:
            return _Result([(u,) for u in self.fanout])
        if "FROM sub_tasks" in sql:
            return _Result(list(self.subtasks))
        if "FROM tasks" in sql:
            return _Result(list(self.tasks))
        if "FROM time_entries" in sql:
            self.time_sql.append(stmt.compile().params)
            return _Result(list(self.time_rows))
        if "FROM users" in sql:
            return _Result(list(self.users))
        raise AssertionError(f"unexpected query: {sql}")


def lead():
    return SimpleNamespace(id="lead1", tenant_id=TENANT, role=Role.TEAM_LEAD)


def member(uid, role=Role.TEAM_MEMBER):
    return SimpleNamespace(id=uid, tenant_id=TENANT, role=role, name=f"User {uid}", title=None)


class LeadUserScopeTests(unittest.TestCase):
    def scope(self, db):
        async def entity_scope(_db, _user):
            return {"ent1"}

        original = access.resolve_report_entity_scope
        access.resolve_report_entity_scope = entity_scope
        try:
            return asyncio.run(access.resolve_report_user_scope(db, lead()))
        finally:
            access.resolve_report_entity_scope = original

    def test_includes_leaf_task_member_fanout_and_subtask_people(self):
        db = FakeDB(
            tasks=[("lead1", "m_leaf"), ("lead2", None)],
            fanout=["m_fan1", "m_fan2"],
            subtasks=[("m_sub", "m_rev"), (None, None)],
        )
        self.assertEqual(
            self.scope(db),
            {"lead1", "lead2", "m_leaf", "m_fan1", "m_fan2", "m_sub", "m_rev"},
        )

    def test_unassigned_members_stay_out(self):
        self.assertEqual(self.scope(FakeDB(tasks=[("lead1", None)])), {"lead1"})


class UserSummaryEndpointTests(unittest.TestCase):
    def call(self, db, me, target_id, scope):
        async def user_scope(_db, _user):
            return scope

        original = access.resolve_report_user_scope
        access.resolve_report_user_scope = user_scope
        try:
            return asyncio.run(te.user_time_summary(target_id, 30, me, db))
        finally:
            access.resolve_report_user_scope = original

    def test_in_scope_member_gets_summary_with_user(self):
        target = member("m1")
        today = te.nepal_today()
        db = FakeDB(users=[target], time_rows=[(today, 90)])
        out = self.call(db, lead(), "m1", {"lead1", "m1"})
        self.assertEqual(out.user.id, "m1")
        self.assertEqual(out.range_days, 30)
        self.assertEqual(len(out.daily), 30)
        self.assertEqual(out.today_minutes, 90)
        self.assertEqual(out.range_total_minutes, 90)
        # the time query is for the MEMBER, not the lead who is asking
        self.assertEqual(db.time_sql[0]["user_id_1"], "m1")
        self.assertEqual(db.time_sql[0]["tenant_id_1"], TENANT)

    def test_member_outside_scope_is_403(self):
        with self.assertRaises(HTTPException) as cm:
            self.call(FakeDB(users=[member("m2")]), lead(), "m2", {"lead1", "m1"})
        self.assertEqual(cm.exception.status_code, 403)

    def test_other_leads_are_not_viewable(self):
        other = member("lead2", role=Role.TEAM_LEAD)
        with self.assertRaises(HTTPException) as cm:
            self.call(FakeDB(users=[other]), lead(), "lead2", {"lead1", "lead2"})
        self.assertEqual(cm.exception.status_code, 403)

    def test_unknown_user_is_404(self):
        with self.assertRaises(HTTPException) as cm:
            self.call(FakeDB(users=[]), lead(), "nope", {"lead1"})
        self.assertEqual(cm.exception.status_code, 404)

    def test_lead_can_open_their_own(self):
        me = lead()
        me.name, me.title = "Lead One", None
        out = self.call(FakeDB(users=[me]), me, "lead1", {"lead1"})
        self.assertEqual(out.user.id, "lead1")


class RoutingTests(unittest.TestCase):
    def test_only_team_leads_pass_the_role_gate(self):
        dep = te.user_time_summary.__defaults__  # (days, me, db) defaults
        gate = next(d.dependency for d in dep if getattr(d, "dependency", None) and d.dependency.__name__ == "_dep")
        for role, allowed in ((Role.TEAM_LEAD, True), (Role.SUPER_ADMIN, False), (Role.TEAM_MEMBER, False)):
            user = SimpleNamespace(role=role)
            if allowed:
                self.assertIs(asyncio.run(gate(user)), user)
            else:
                with self.assertRaises(HTTPException) as cm:
                    asyncio.run(gate(user))
                self.assertEqual(cm.exception.status_code, 403)

    def test_route_registered(self):
        self.assertIn("/api/time-entries/user/{user_id}/summary", [r.path for r in te.router.routes])

    def test_user_field_is_optional_so_my_summary_is_unchanged(self):
        out = MyTimeSummaryOut(
            today=date(2026, 10, 1), week_start=date(2026, 9, 27), month_start=date(2026, 10, 1),
            today_minutes=0, week_minutes=0, month_minutes=0, range_days=7, range_total_minutes=0,
            daily=[TimeDayOut(date=date(2026, 10, 1), minutes=0)],
            weekly=[TimeWeekOut(week_start=date(2026, 9, 27), minutes=0)],
            monthly=[TimeMonthOut(month_start=date(2026, 10, 1), minutes=0)],
        )
        self.assertIsNone(out.user)
        out.user = UserMini(id="m1", name="Maya")
        self.assertEqual(out.model_dump(mode="json")["user"]["name"], "Maya")


if __name__ == "__main__":
    unittest.main()
