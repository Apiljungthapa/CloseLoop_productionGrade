"""Timesheet service against a REAL Postgres (the HRMS is a fake gateway).

Skipped unless TIMESHEET_TEST_DATABASE_URL points at a database that has been
migrated to head, e.g.

    docker exec closeloop-db psql -U postgres -c "CREATE DATABASE closeloop_ts_scratch"
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/closeloop_ts_scratch alembic upgrade head
    TIMESHEET_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/closeloop_ts_scratch \
        python -m unittest tests.test_timesheet_service_db

Every test creates its own tenant and deletes it afterwards (all tables cascade).
NEVER point this at the production database.

What a timesheet row IS, in these tests: a task / sub task somebody SUBMITTED (or their own
part of a group task), on the day they submitted it, with the Time Taken they entered. The
time-log table is never read.
"""

import os
import unittest
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.calendar import set_configured_holidays
from app.config import settings
from app.models import (
    Entity, EntityGroup, HrmsActivityMapping, Notification, Period, Plan, Role, SubTask,
    SubTaskEntityAssignee, Task, TaskEntityAssignee, TaskStatus, Tenant, TimeEntry, TimesheetLine,
    TimesheetLineSource, TimesheetReminderLog, TimesheetSubmission, User,
)
from app.schemas import TimesheetLineChoice
from app.services import timesheet_events as ev
from app.services import timesheet_reminder_service as rem
from app.services import timesheet_service as svc
from app.services.hrms_timesheet_gateway import (
    ExistingEntry, HrmsTimesheetRejected, HrmsTimesheetUncertain, HrmsTimesheetUnavailable,
)

DB_URL = os.environ.get("TIMESHEET_TEST_DATABASE_URL")
PROJECT_ID = 7
START, END = date(2026, 10, 1), date(2026, 10, 31)
D1, D2, D3 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)

ACTIVITIES = [
    {"id": 1, "name": "Bank Reconciliation", "description": None, "sub_activities": []},
    {"id": 2, "name": "VAT Return Filing", "description": None, "sub_activities": []},
    # an activity whose HRMS entry needs a value for each extra field
    {"id": 6, "name": "Month End Close", "description": None,
     "sub_activities": [{"id": 10, "name": "Accruals Posting", "key": "k"}]},
    # ... and one whose only field is a description (filled from the summary)
    {"id": 7, "name": "Client Call", "description": None,
     "sub_activities": [{"id": 20, "name": "Description", "key": "description"}]},
]


def noon(day: date) -> datetime:
    """12:00 in Nepal (UTC+5:45) on `day`, as the UTC instant the app stores."""
    return datetime(day.year, day.month, day.day, 6, 15, tzinfo=timezone.utc)


class FakeGateway:
    """Stands in for the HRMS: takes whole DAYS (like the real API), one request per date."""

    def __init__(self):
        self.existing: list[ExistingEntry] = []
        self.days = []                  # every request received (NewDay)
        self.mode = "ok"
        self.list_unavailable = False
        self.connected = True      # False = the HRMS timesheet API isn't configured
        self.readable = True       # False = a POST-only HRMS (entries can't be read back)

    @property
    def created(self):
        """Every activity entry received, flattened: what the HRMS now holds."""
        return [
            SimpleNamespace(work_date=d.work_date, activity_id=e.activity_id, hours=e.hours, work_type=e.work_type,
                            sub_values=dict(e.sub_values), email=d.email, summary=d.summary, project_id=d.project_id)
            for d in self.days for e in d.entries
        ]

    def is_available(self):
        return self.connected

    def can_list(self):
        return self.connected and self.readable

    def list_entries(self, *, email, project_id, start, end):
        if self.list_unavailable:
            raise HrmsTimesheetUnavailable("down")
        made = [ExistingEntry(e.work_date, e.activity_id, e.hours, e.summary) for e in self.created]
        return [e for e in self.existing + made if start <= e.work_date <= end]

    def create_day(self, day):
        if self.mode == "reject":
            raise HrmsTimesheetRejected("month is locked")
        if self.mode == "uncertain":
            raise HrmsTimesheetUncertain("timed out")
        self.days.append(day)
        return f"hrms-{len(self.days)}"


@unittest.skipUnless(DB_URL, "set TIMESHEET_TEST_DATABASE_URL to run the DB-backed timesheet tests")
class TimesheetDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        set_configured_holidays({})
        self._saved = (settings.TIMESHEET_SUBMIT_ENABLED, settings.TIMESHEET_REMINDERS_ENABLED)
        settings.TIMESHEET_SUBMIT_ENABLED = True
        self.engine = create_async_engine(DB_URL)
        self.db: AsyncSession = async_sessionmaker(self.engine, expire_on_commit=False)()
        tag = uuid.uuid4().hex[:8]
        self.tenant = Tenant(name=f"T {tag}", slug=f"t-{tag}")
        self.db.add(self.tenant)
        await self.db.flush()
        tid = self.tenant_id = self.tenant.id
        grp = EntityGroup(tenant_id=tid, name="G")
        self.db.add(grp)
        await self.db.flush()
        self.ent = Entity(tenant_id=tid, group_id=grp.id, name="Himalayan Co")
        self.other_ent = Entity(tenant_id=tid, group_id=grp.id, name="Everest Co")
        self.user = User(tenant_id=tid, email=f"Ann.{tag}@Example.com", password_hash="x",
                         name="Ann", role=Role.TEAM_MEMBER)
        self.other_user = User(tenant_id=tid, email=f"bob.{tag}@example.com", password_hash="x",
                               name="Bob", role=Role.TEAM_MEMBER)
        self.db.add_all([self.ent, self.other_ent, self.user, self.other_user])
        await self.db.flush()
        period = Period(tenant_id=tid, label="Oct", start_date=START, end_date=END)
        self.db.add(period)
        await self.db.flush()
        self.plan = Plan(tenant_id=tid, period_id=period.id, entity_id=self.ent.id, title="P")
        self.other_plan = Plan(tenant_id=tid, period_id=period.id, entity_id=self.other_ent.id, title="P2")
        self.db.add_all([self.plan, self.other_plan])
        await self.db.commit()
        self.gw = FakeGateway()
        self.eligible = {self.user.email.lower()}
        self.project = {"id": PROJECT_ID, "name": "Proj", "members": [
            {"id": 101, "name": "Ann", "work_email": self.user.email.lower()}]}

    async def asyncTearDown(self):
        settings.TIMESHEET_SUBMIT_ENABLED, settings.TIMESHEET_REMINDERS_ENABLED = self._saved
        await self.db.rollback()
        await self.db.execute(Tenant.__table__.delete().where(Tenant.id == self.tenant_id))
        await self.db.commit()
        await self.db.close()
        await self.engine.dispose()

    # ---- helpers: a "submission" is what makes a row
    async def task(self, title, *, desc=None, entity=None, member=None, status=None):
        ent = entity or self.ent
        plan = self.plan if ent.id == self.ent.id else self.other_plan
        t = Task(tenant_id=self.tenant_id, plan_id=plan.id, entity_id=ent.id, title=title, description=desc,
                 member_assigned_to_id=member, status=status or TaskStatus.IN_PROGRESS)
        self.db.add(t)
        await self.db.flush()
        return t

    async def did(self, title, day, minutes=60, *, desc=None, by=None, entity=None):
        """A task `by` (default: me) SUBMITTED on `day` with Time Taken `minutes` (None = left blank)."""
        t = await self.task(title, desc=desc, entity=entity, status=TaskStatus.PENDING_REVIEW)
        t.submitted_by_id = (by or self.user).id
        t.submitted_at = noon(day)
        t.completion_time_minutes = minutes
        await self.db.flush()
        return t

    async def sub_did(self, parent, title, day, minutes=60, *, desc=None, by=None):
        s_ = SubTask(tenant_id=self.tenant_id, task_id=parent.id, entity_id=parent.entity_id, title=title,
                     description=desc, status=TaskStatus.PENDING_REVIEW, submitted_by_id=(by or self.user).id,
                     submitted_at=noon(day), completion_time_minutes=minutes)
        self.db.add(s_)
        await self.db.flush()
        return s_

    async def preview(self, project=..., start=START, end=END, month="2026-10"):
        return await svc.compute(
            self.db, self.user, project_id=PROJECT_ID, month=month, start=start, end=end,
            hrms_activities=ACTIVITIES, hrms_project=self.project if project is ... else project,
            gateway=self.gw)

    async def submit(self, c, *, key=None, edits=None, ack=False, work_type="BILLABLE", minutes=None):
        """edits: line key -> (activity_id,) or (activity_id, {sub activity id: value}).
        minutes: line key -> hours typed (in minutes) for a row whose Time Taken was blank."""
        edits = edits or {}
        minutes = minutes or {}
        choices = []
        for l in c.lines:
            if not (l.selected or l.key in edits):
                continue
            e = edits.get(l.key)
            choices.append(TimesheetLineChoice(
                key=l.key, hrms_activity_id=e[0] if e else l.activity_id,
                sub_values=(e[1] if e and len(e) > 1 else dict(l.sub_values)),
                summary=l.summary, work_type=work_type, minutes=minutes.get(l.key)))
        return await svc.submit(
            self.db, self.user, project_id=PROJECT_ID, month="2026-10", start=START, end=END,
            choices=choices, idempotency_key=key or uuid.uuid4().hex, acknowledge_warnings=ack,
            hrms_activities=ACTIVITIES, hrms_project=self.project, gateway=self.gw)

    def codes(self, items):
        return [i["code"] for i in items]

    # ---- which rows exist
    async def test_every_date_of_the_month_is_listed_with_weekends_and_holidays_marked(self):
        set_configured_holidays({date(2026, 10, 8): "Test holiday"})
        c = await self.preview()
        d = svc._days(START, END)
        self.assertEqual((len(d), d[0]["date"], d[-1]["date"]), (31, START, END))
        by = {x["date"]: x for x in d}
        self.assertEqual((by[date(2026, 10, 3)]["non_working"], by[date(2026, 10, 3)]["label"]), (True, "Weekend"))     # Sat
        self.assertEqual((by[date(2026, 10, 5)]["non_working"], by[date(2026, 10, 5)]["label"]), (False, None))         # Mon
        self.assertEqual((by[date(2026, 10, 8)]["non_working"], by[date(2026, 10, 8)]["label"]), (True, "Test holiday"))
        self.assertEqual(len(svc.preview_dict(c)["days"]), 31)
        self.assertEqual(len(svc.preview_dict(await self.preview(end=date(2026, 10, 12)))["days"]), 12)   # "only up to"

    async def test_only_my_own_submissions_inside_the_month_are_rows(self):
        await self.did("Bank Reconciliation", D1, 60)                                   # mine, in the month
        await self.did("Bank Reconciliation", D1, 45, by=self.other_user)               # somebody else's
        await self.did("Bank Reconciliation", date(2026, 9, 30), 99)                    # last month
        await self.did("Bank Reconciliation", date(2026, 11, 1), 99)                    # next month
        await self.task("Bank Reconciliation", member=self.user.id)                     # assigned to me, never submitted
        await self.db.commit()
        c = await self.preview()
        self.assertEqual([(l.work_date, l.minutes, l.activity_id) for l in c.lines], [(D1, 60, 1)])

    async def test_the_time_log_table_is_never_read(self):
        """Time logged without a submission makes no row; a submission with no time log still does."""
        t = await self.task("Bank Reconciliation")
        self.db.add(TimeEntry(tenant_id=self.tenant_id, user_id=self.user.id, entity_id=self.ent.id, task_id=t.id,
                              entry_date=D1, duration_minutes=480))            # a time-log row, no submission
        await self.did("VAT return filing", D2, 30)                              # a submission, no time-log row
        await self.db.commit()
        c = await self.preview()
        self.assertEqual([(l.work_date, l.activity_id, l.minutes) for l in c.lines], [(D2, 2, 30)])

    async def test_all_entities_count_because_the_hrms_has_none(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.did("VAT return filing", D1, 30, entity=self.other_ent)
        await self.db.commit()
        c = await self.preview()
        self.assertEqual(sorted((l.activity_id, l.minutes) for l in c.lines), [(1, 60), (2, 30)])
        self.assertEqual(self.codes(c.blockers), [])

    async def test_the_day_is_the_nepal_day_of_the_submission(self):
        t = await self.did("Bank Reconciliation", D1, 60)
        t.submitted_at = datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc)      # = 13 Oct 02:15 in Nepal
        await self.db.commit()
        (l,) = (await self.preview()).lines
        self.assertEqual(l.work_date, date(2026, 10, 13))

    async def test_a_submission_without_time_taken_leaves_hours_to_type(self):
        await self.did("Bank Reconciliation", D1, None)
        await self.db.commit()
        c = await self.preview()
        (l,) = c.lines
        self.assertEqual((l.needs_hours, l.minutes, l.selected, l.status), (True, 0, False, "MATCHED"))
        self.assertIn("HOURS_MISSING", self.codes(c.warnings))

    async def test_a_rejected_or_reopened_task_is_not_a_row_any_more(self):
        t = await self.did("Bank Reconciliation", D1, 60)
        t.submitted_by_id, t.submitted_at, t.completion_time_minutes = None, None, None   # what the app does on rejection
        await self.db.commit()
        self.assertEqual((await self.preview()).lines, [])

    async def test_a_task_that_is_only_due_or_assigned_makes_no_row(self):
        t = await self.task("Bank Reconciliation", member=self.user.id)
        t.due_date = D1
        await self.db.commit()
        c = await self.preview()
        self.assertEqual(c.lines, [])
        self.assertIn("NOTHING_SUBMITTED", self.codes(c.warnings))

    async def test_a_sub_task_i_submitted_is_a_row_named_under_its_parent(self):
        parent = await self.task("October close", desc="parent text")                       # the lead's task
        await self.sub_did(parent, "Bank Reconciliation", D2, 30, desc="Clear the open items")
        await self.sub_did(parent, "VAT return filing", D2, 30, by=self.other_user)         # a colleague's
        await self.db.commit()
        (l,) = (await self.preview()).lines
        self.assertEqual((l.work_date, l.minutes, l.activity_id), (D2, 30, 1))
        self.assertEqual(l.summary, "October close: Bank Reconciliation – Clear the open items")
        self.assertEqual((l.sources[0].unit_kind, l.sources[0].subtask_title), ("subtask", "Bank Reconciliation"))

    async def test_my_own_part_of_a_group_task_uses_my_done_time_and_my_minutes(self):
        t = await self.did("Bank Reconciliation", D3, 999, by=self.other_user)             # somebody else's click closed it
        self.db.add(TaskEntityAssignee(tenant_id=self.tenant_id, task_id=t.id, user_id=self.user.id, done=True,
                                       done_at=noon(D1), completion_time_minutes=45))
        self.db.add(TaskEntityAssignee(tenant_id=self.tenant_id, task_id=t.id, user_id=self.other_user.id, done=True,
                                       done_at=noon(D3), completion_time_minutes=999))
        await self.db.commit()
        (l,) = (await self.preview()).lines
        self.assertEqual((l.work_date, l.minutes), (D1, 45))                              # MY part, not the group's
        todo, _ = await ev.unfiled_work(self.db, START, END, tenant_id=self.tenant_id, user_id=self.other_user.id)
        self.assertEqual([(e.day, e.minutes) for e in todo], [(D3, 999)])                  # and theirs is theirs

    async def test_my_own_part_of_a_group_sub_task_counts_too(self):
        parent = await self.task("October close")
        st = SubTask(tenant_id=self.tenant_id, task_id=parent.id, entity_id=parent.entity_id, title="VAT return filing")
        self.db.add(st)
        await self.db.flush()
        self.db.add(SubTaskEntityAssignee(tenant_id=self.tenant_id, subtask_id=st.id, user_id=self.user.id, done=True,
                                          done_at=noon(D2), completion_time_minutes=20))
        self.db.add(SubTaskEntityAssignee(tenant_id=self.tenant_id, subtask_id=st.id, user_id=self.other_user.id, done=False))
        await self.db.commit()
        (l,) = (await self.preview()).lines
        self.assertEqual((l.work_date, l.minutes, l.activity_id), (D2, 20, 2))

    async def test_a_lead_who_submitted_nothing_gets_an_empty_month_with_all_dates(self):
        await self.task("Bank Reconciliation")                        # owned in the lead's plan, nobody submitted
        await self.db.commit()
        c = await self.preview()
        self.assertEqual(c.lines, [])
        self.assertIn("NOTHING_SUBMITTED", self.codes(c.warnings))
        self.assertEqual(len(svc.preview_dict(c)["days"]), 31)

    # ---- project / HRMS preconditions
    async def test_unknown_project_blocks(self):
        c = await self.preview(project=None)
        self.assertIn("PROJECT_NOT_FOUND", self.codes(c.blockers))

    async def test_not_listed_on_the_project_warns_and_the_hrms_decides(self):
        self.project["members"] = [{"id": 5, "name": "Z", "work_email": "z@example.com"}]
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        self.assertNotIn("NOT_A_PROJECT_MEMBER", self.codes(c.blockers))
        self.assertIn("NOT_A_PROJECT_MEMBER", self.codes(c.warnings))
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c)
        self.assertEqual(cm.exception.code, "ACK_REQUIRED")
        sub = await self.submit(c, ack=True)
        self.assertEqual(sub.status, "SUBMITTED")

    async def test_hrms_timesheet_unreadable_blocks_submit(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.list_unavailable = True
        c = await self.preview()
        self.assertIn("HRMS_ENTRIES_UNAVAILABLE", self.codes(c.blockers))
        with self.assertRaises(svc.TimesheetError) as cm:
            await svc.submit(self.db, self.user, project_id=PROJECT_ID, month="2026-10", start=START, end=END,
                             choices=[TimesheetLineChoice(key=c.lines[0].key, hrms_activity_id=1, summary="x")],
                             idempotency_key="k" * 10, acknowledge_warnings=True,
                             hrms_activities=ACTIVITIES, hrms_project=self.project, gateway=self.gw)
        self.assertEqual(cm.exception.code, "HRMS_ENTRIES_UNAVAILABLE")
        self.assertEqual(self.gw.days, [])

    async def test_not_connected_is_its_own_clear_reason(self):
        self.gw.connected = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        self.assertIn("HRMS_NOT_CONNECTED", self.codes(c.blockers))
        self.assertNotIn("HRMS_ENTRIES_UNAVAILABLE", self.codes(c.blockers))
        self.assertEqual(len(c.lines), 1)                         # the review still works
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c)
        self.assertEqual(cm.exception.code, "HRMS_NOT_CONNECTED")

    # ---- sending
    async def test_submit_disabled_flag_refuses(self):
        settings.TIMESHEET_SUBMIT_ENABLED = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        self.assertIn("SUBMIT_DISABLED", self.codes(c.blockers))
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c)
        self.assertEqual(cm.exception.code, "SUBMIT_DISABLED")

    async def test_happy_path_sends_each_day_once_and_files_each_task_once(self):
        a, b = await self.did("Bank Reconciliation - HQ", D1, 90), await self.did("Bank Reconciliation - Branch", D1, 20)
        await self.did("VAT return filing", D2, 45)
        await self.db.commit()
        sub = await self.submit(await self.preview())
        self.assertEqual(sub.status, "SUBMITTED")
        self.assertEqual(sorted((e.work_date, e.activity_id, e.hours) for e in self.gw.created),
                         [(D1, 1, 1.83), (D2, 2, 0.75)])             # the day's two tasks became ONE entry
        self.assertEqual(len(self.gw.days), 2)                       # one request per DAY
        self.assertTrue(all(e.email == self.user.email and e.work_type == "BILLABLE" for e in self.gw.created))
        self.assertEqual(sub.hrms_email, self.user.email)
        srcs = (await self.db.execute(select(TimesheetLineSource).join(TimesheetLine, TimesheetLine.id == TimesheetLineSource.line_id)
                                      .where(TimesheetLine.tenant_id == self.tenant_id))).scalars().all()
        self.assertEqual(sorted((s.user_id == self.user.id, s.task_id is not None, s.time_entry_id) for s in srcs),
                         [(True, True, None)] * 3)                   # all three tasks recorded as filed, none via a time entry
        again = await self.preview()
        self.assertEqual(again.lines, [])                            # nothing comes back
        self.assertEqual((again.totals["already_filed_tasks"], again.totals["already_filed_minutes"]), (3, 155))
        self.assertNotIn(a.id, [l.sources[0].unit_id for l in again.lines])

    async def test_same_idempotency_key_returns_first_result_and_sends_nothing_more(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        first = await self.submit(c, key="same-key-123")
        second = await self.submit(c, key="same-key-123")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.gw.days), 1)

    async def test_second_submit_with_a_new_key_cannot_resend_the_same_work(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        await self.submit(c, key="first-key-1")
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, key="second-key-2")                 # stale screen, new click
        self.assertIn(cm.exception.code, ("STALE_PREVIEW", "LINE_HELD", "ALREADY_SUBMITTING"))
        self.assertEqual(len(self.gw.days), 1)

    async def test_entry_already_on_hrms_is_flagged_and_cannot_be_sent(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.existing.append(ExistingEntry(D1, 1, 3.0))
        c = await self.preview()
        (line,) = c.lines
        self.assertIn("ALREADY_IN_HRMS", line.flags)
        self.assertFalse(line.selected)
        self.assertEqual(line.existing_hours, 3.0)
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (1,)})
        self.assertEqual(cm.exception.code, "LINE_HELD")
        self.assertEqual(self.gw.days, [])

    async def test_hand_picked_activity_that_collides_is_refused_after_the_override(self):
        await self.did("Prepare board presentation", D1, 60)         # unmatched
        await self.db.commit()
        self.gw.existing.append(ExistingEntry(D1, 2, 1.0))
        c = await self.preview()
        (line,) = c.lines
        self.assertEqual(line.status, "UNMATCHED")
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (2,)}, ack=True)
        self.assertEqual(cm.exception.code, "DUPLICATE_ENTRY")

    async def test_hand_picked_lines_need_acknowledgement_and_are_remembered(self):
        await self.did("Prepare board presentation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        (line,) = c.lines
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (2,)})
        self.assertEqual(cm.exception.code, "ACK_REQUIRED")
        sub = await self.submit(c, edits={line.key: (2,)}, ack=True)
        self.assertEqual(sub.status, "SUBMITTED")
        await self.did("prepare Board presentation", date(2026, 11, 3), 30)       # next month, same title
        await self.db.commit()
        nov = await svc.compute(self.db, self.user, project_id=PROJECT_ID, month="2026-11",
                                start=date(2026, 11, 1), end=date(2026, 11, 30),
                                hrms_activities=ACTIVITIES, hrms_project=self.project, gateway=self.gw)
        (nl,) = nov.lines
        self.assertEqual((nl.status, nl.activity_id, nl.tier), ("MATCHED", 2, "MAPPING_TITLE"))

    async def test_every_extra_field_of_an_activity_needs_a_value(self):
        await self.did("Month end close October", D1, 60)
        await self.db.commit()
        c = await self.preview()
        (line,) = c.lines
        self.assertEqual((line.status, line.activity_id, line.sub_values), ("NEEDS_SUB", 6, {10: ""}))
        self.assertFalse(line.selected)
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (6, {})}, ack=True)
        self.assertEqual(cm.exception.code, "SUB_VALUES_REQUIRED")
        self.assertIn("Accruals Posting", cm.exception.message)
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (6, {10: "   "})}, ack=True)
        self.assertEqual(cm.exception.code, "SUB_VALUES_REQUIRED")
        with self.assertRaises(svc.TimesheetError) as cm:                     # a field the activity doesn't have
            await self.submit(c, edits={line.key: (6, {10: "ok", 99: "x"})}, ack=True)
        self.assertEqual(cm.exception.code, "UNKNOWN_SUB_ACTIVITY")
        self.assertEqual(self.gw.days, [])
        sub = await self.submit(c, edits={line.key: (6, {10: "Posted in full"})}, ack=True)
        self.assertEqual(sub.status, "SUBMITTED")
        self.assertEqual(self.gw.created[0].sub_values, {10: "Posted in full"})
        self.assertEqual(sub.lines[0].sub_values, {"10": "Posted in full"})   # stored as JSON (string keys)

    async def test_a_description_field_is_filled_from_the_summary_and_sends_without_a_hand_edit(self):
        await self.did("Client Call", D1, 30, desc="Quarterly review call with the client")
        await self.db.commit()
        c = await self.preview()
        (line,) = c.lines
        self.assertEqual((line.status, line.selected), ("MATCHED", True))
        self.assertEqual(line.sub_values, {20: "Quarterly review call with the client"})
        sub = await self.submit(c)
        self.assertEqual(sub.status, "SUBMITTED")
        self.assertEqual(self.gw.created[0].sub_values, {20: "Quarterly review call with the client"})

    async def test_a_value_for_an_activity_without_fields_is_refused(self):
        await self.did("Bank Reconciliation", D1, 30)
        await self.db.commit()
        c = await self.preview()
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={c.lines[0].key: (1, {10: "x"})}, ack=True)
        self.assertEqual(cm.exception.code, "UNKNOWN_SUB_ACTIVITY")

    async def test_unknown_activity_is_refused(self):
        await self.did("Prepare board presentation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={c.lines[0].key: (999,)}, ack=True)
        self.assertEqual(cm.exception.code, "UNKNOWN_ACTIVITY")

    async def test_rows_moved_to_one_activity_are_sent_as_one_entry(self):
        await self.did("Prepare board presentation", D1, 10)
        await self.did("Quarterly offsite planning", D1, 10)
        await self.did("Another unrelated thing", D1, 10)
        await self.db.commit()
        c = await self.preview()
        await self.submit(c, edits={l.key: (2,) for l in c.lines}, ack=True)
        self.assertEqual([(e.activity_id, e.hours) for e in self.gw.created], [(2, 0.5)])

    async def test_a_day_with_several_activities_goes_as_one_request_with_one_summary(self):
        await self.did("Bank Reconciliation", D1, 60, desc="Matched the HQ account")
        await self.did("VAT return filing", D1, 30, desc="Filed October VAT")
        await self.did("Bank Reconciliation", D2, 45)
        await self.db.commit()
        await self.submit(await self.preview())
        self.assertEqual(len(self.gw.days), 2)
        day1 = next(d for d in self.gw.days if d.work_date == D1)
        self.assertEqual(sorted((e.activity_id, e.hours) for e in day1.entries), [(1, 1.0), (2, 0.5)])
        self.assertEqual(day1.summary, "Matched the HQ account | Filed October VAT")   # ONE summary for the day

    async def test_paid_non_billable_is_a_work_type(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        await self.submit(await self.preview(), work_type="PAID_NON_BILLABLE")
        self.assertEqual(self.gw.created[0].work_type, "PAID_NON_BILLABLE")

    # ---- Time Taken left blank: the user types the hours
    async def test_typed_hours_are_required_and_bounded(self):
        await self.did("Bank Reconciliation", D1, None)
        await self.db.commit()
        c = await self.preview()
        (l,) = c.lines
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={l.key: (1,)}, ack=True)
        self.assertEqual(cm.exception.code, "HOURS_REQUIRED")
        for bad in (0, -5, 1441):
            with self.assertRaises(Exception):                       # the request model refuses these first
                TimesheetLineChoice(key=l.key, hrms_activity_id=1, minutes=bad)
        self.assertEqual(self.gw.days, [])

    async def test_typed_hours_go_to_the_hrms_and_the_task_is_then_filed_once(self):
        t = await self.did("Bank Reconciliation", D1, None, desc="Matched the HQ account")
        await self.db.commit()
        c = await self.preview()
        (l,) = c.lines
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={l.key: (1,)}, minutes={l.key: 90})            # typed hours need acknowledging
        self.assertEqual(cm.exception.code, "ACK_REQUIRED")
        sub = await self.submit(c, edits={l.key: (1,)}, minutes={l.key: 90}, ack=True)
        self.assertEqual(sub.status, "SUBMITTED")
        self.assertEqual([(e.work_date, e.activity_id, e.hours) for e in self.gw.created], [(D1, 1, 1.5)])
        src = (await self.db.execute(select(TimesheetLineSource).where(TimesheetLineSource.line_id == sub.lines[0].id))).scalar_one()
        self.assertEqual((src.user_id, src.task_id, src.minutes), (self.user.id, t.id, 90))
        self.assertEqual((await self.preview()).lines, [])                              # never comes back

    async def test_time_taken_cannot_be_overridden_from_the_page(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, minutes={c.lines[0].key: 999})
        self.assertEqual(cm.exception.code, "HOURS_FIXED")
        await self.submit(c, minutes={c.lines[0].key: 60})                  # the same number is harmless
        self.assertEqual(self.gw.created[0].hours, 1.0)

    async def test_typed_hours_join_time_taken_on_the_same_day_and_activity(self):
        await self.did("Bank Reconciliation - HQ", D1, 60)
        await self.did("Bank Reconciliation - Branch", D1, None)
        await self.db.commit()
        c = await self.preview()
        blank = next(l for l in c.lines if l.needs_hours)
        await self.submit(c, minutes={blank.key: 30}, edits={blank.key: (1,)}, ack=True)
        self.assertEqual(len(self.gw.days), 1)                              # one request for the day...
        self.assertEqual([(e.activity_id, e.hours) for e in self.gw.created], [(1, 1.5)])   # ...one entry, hours added

    async def test_a_day_over_24_hours_is_refused_including_typed_hours(self):
        await self.did("Bank Reconciliation - HQ", D1, 1200)               # 20 h
        await self.did("VAT return filing", D1, None)
        await self.db.commit()
        c = await self.preview()
        blank = next(l for l in c.lines if l.needs_hours)
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={blank.key: (2,)}, minutes={blank.key: 300}, ack=True)     # + 5 h
        self.assertEqual(cm.exception.code, "DAY_OVER_24H")

    # ---- a day already sent is closed
    async def test_a_sent_day_is_closed_so_a_later_submission_cannot_overwrite_it(self):
        """One POST holds a whole day and it is not known whether a second POST for the same
        date adds to it or replaces it - so CloseLoop never sends a date twice."""
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        await self.submit(await self.preview())
        await self.did("VAT return filing", D1, 30)               # submitted later, but it falls on the SAME date
        await self.db.commit()
        c = await self.preview()
        (line,) = c.lines
        self.assertIn("DAY_ALREADY_SENT_FROM_CLOSELOOP", line.flags)
        self.assertFalse(line.selected)
        with self.assertRaises(svc.TimesheetError) as cm:
            await self.submit(c, edits={line.key: (2,)}, ack=True)
        self.assertIn(cm.exception.code, ("LINE_HELD", "DAY_ALREADY_SENT"))
        self.assertEqual(len(self.gw.days), 1)

    # ---- failure handling
    async def test_a_refused_day_fails_all_of_its_rows_and_gives_the_work_back(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.did("VAT return filing", D1, 30)
        await self.db.commit()
        self.gw.mode = "reject"
        sub = await self.submit(await self.preview())
        self.assertEqual([l.status for l in sub.lines], ["FAILED", "FAILED"])
        self.assertIn("locked", sub.lines[0].error)
        n = (await self.db.execute(
            select(func.count()).select_from(TimesheetLineSource)
            .join(TimesheetLine, TimesheetLine.id == TimesheetLineSource.line_id)
            .where(TimesheetLine.tenant_id == self.tenant_id))).scalar_one()
        self.assertEqual(n, 0)                                 # nothing is holding the tasks
        self.gw.mode = "ok"
        again = await self.preview()
        self.assertEqual(len(again.lines), 2)
        self.assertEqual((await self.submit(again)).status, "SUBMITTED")
        self.assertEqual(len(self.gw.created), 2)

    async def test_uncertain_outcome_locks_the_work_until_verified(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview())
        self.assertEqual(sub.lines[0].status, "UNCERTAIN")
        c = await self.preview()
        self.assertEqual(c.lines, [])                           # not offered again
        self.assertIn("NEEDS_VERIFY", self.codes(c.warnings))
        self.gw.mode = "ok"                                     # the HRMS did NOT get it -> verify frees it
        v = await svc.verify_uncertain(self.db, self.user, sub.id, self.gw)
        self.assertEqual(v.lines[0].status, "FAILED")
        self.assertEqual(len((await self.preview()).lines), 1)

    async def test_verify_adopts_an_entry_that_did_arrive(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview())
        self.gw.existing.append(ExistingEntry(D1, 1, 1.0))      # it did arrive
        v = await svc.verify_uncertain(self.db, self.user, sub.id, self.gw)
        self.assertEqual((v.status, v.lines[0].status), ("SUBMITTED", "SENT"))
        self.assertEqual((await self.preview()).lines, [])

    async def test_verify_changes_nothing_when_hrms_cannot_be_read(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview())
        self.gw.list_unavailable = True
        with self.assertRaises(svc.TimesheetError):
            await svc.verify_uncertain(self.db, self.user, sub.id, self.gw)
        await self.db.refresh(sub, ["lines"])
        self.assertEqual(sub.lines[0].status, "UNCERTAIN")

    # ---- POST-only HRMS (entries can be sent but not read back)
    async def test_post_only_hrms_needs_acknowledgement_and_leans_on_the_closeloop_ledger(self):
        self.gw.readable = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        c = await self.preview()
        self.assertEqual(c.blockers, [])
        self.assertFalse(c.hrms_readable)
        self.assertIn("HRMS_ENTRIES_NOT_CHECKED", self.codes(c.warnings))
        self.assertTrue(c.lines[0].selected)                    # still sendable...
        with self.assertRaises(svc.TimesheetError) as cm:       # ...but only once the user confirms
            await self.submit(c)
        self.assertEqual(cm.exception.code, "ACK_REQUIRED")
        self.assertEqual(self.gw.days, [])
        self.assertEqual((await self.submit(c, ack=True)).status, "SUBMITTED")
        await self.did("VAT return filing", D1, 30)              # another submission that lands on the SAME sent date
        await self.db.commit()
        (line,) = (await self.preview()).lines
        self.assertIn("DAY_ALREADY_SENT_FROM_CLOSELOOP", line.flags)   # CloseLoop's own record blocks a second request
        self.assertEqual(len(self.gw.days), 1)

    async def test_unreadable_hrms_cannot_verify_so_the_user_settles_it_by_hand(self):
        self.gw.readable = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview(), ack=True)
        self.assertEqual(sub.lines[0].status, "UNCERTAIN")
        with self.assertRaises(svc.TimesheetError) as cm:
            await svc.verify_uncertain(self.db, self.user, sub.id, self.gw)
        self.assertEqual(cm.exception.code, "CANNOT_VERIFY")
        self.assertEqual((await self.preview()).lines, [])      # locked meanwhile
        done = await svc.resolve_uncertain(self.db, self.user, sub.id, "NOT_SENT")
        self.assertEqual((done.status, done.lines[0].status), ("FAILED", "FAILED"))
        self.gw.mode = "ok"
        self.assertEqual((await self.submit(await self.preview(), ack=True)).status, "SUBMITTED")

    async def test_resolving_as_sent_keeps_the_work_off_the_list(self):
        self.gw.readable = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview(), ack=True)
        done = await svc.resolve_uncertain(self.db, self.user, sub.id, "SENT")
        self.assertEqual((done.status, done.lines[0].status), ("SUBMITTED", "SENT"))
        self.assertEqual((await self.preview()).lines, [])

    async def test_resolve_checks_the_outcome_and_the_owner(self):
        self.gw.readable = False
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        self.gw.mode = "uncertain"
        sub = await self.submit(await self.preview(), ack=True)
        with self.assertRaises(svc.TimesheetError) as cm:
            await svc.resolve_uncertain(self.db, self.user, sub.id, "MAYBE")
        self.assertEqual(cm.exception.code, "BAD_OUTCOME")
        with self.assertRaises(svc.TimesheetError) as cm:
            await svc.resolve_uncertain(self.db, self.other_user, sub.id, "SENT")
        self.assertEqual(cm.exception.code, "NOT_FOUND")

    # ---- the database itself
    async def test_database_refuses_two_live_lines_for_one_slot(self):
        await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        await self.submit(await self.preview())
        dup = TimesheetSubmission(tenant_id=self.tenant_id, user_id=self.user.id, hrms_project_id=PROJECT_ID,
                                  hrms_project_name="Proj", hrms_email="x", range_start=START,
                                  range_end=END, idempotency_key="other-key-99")
        dup.lines.append(TimesheetLine(tenant_id=self.tenant_id, user_id=self.user.id, hrms_project_id=PROJECT_ID,
                                       work_date=D1, hrms_activity_id=1, hrms_sub_activity_id=0, minutes=5,
                                       hours=0.08, summary="x", status="PENDING"))
        self.db.add(dup)
        with self.assertRaises(IntegrityError):
            await self.db.flush()
        await self.db.rollback()

    async def test_a_task_is_filed_once_per_person_but_each_person_can_file_their_own_part(self):
        t = await self.did("Bank Reconciliation", D1, 60)
        await self.db.commit()
        task_id, me, other_id = t.id, self.user.id, self.other_user.id      # plain values: a rollback expires the objects
        status = (await self.submit(await self.preview())).status
        # the same person cannot file the same task again, even from a brand-new submission ...
        again = TimesheetSubmission(tenant_id=self.tenant_id, user_id=me, hrms_project_id=PROJECT_ID,
                                    hrms_project_name="Proj", hrms_email="x", range_start=START, range_end=END,
                                    idempotency_key="again-key-1")
        line = TimesheetLine(tenant_id=self.tenant_id, user_id=me, hrms_project_id=PROJECT_ID, work_date=D3,
                             hrms_activity_id=2, hrms_sub_activity_id=0, minutes=5, hours=0.08, summary="x", status="PENDING")
        line.sources = [TimesheetLineSource(user_id=me, task_id=task_id, minutes=5)]
        again.lines.append(line)
        self.db.add(again)
        with self.assertRaises(IntegrityError):
            await self.db.flush()
        await self.db.rollback()
        # ... but a colleague's part of the same task is a different (user, task) pair.
        other = TimesheetSubmission(tenant_id=self.tenant_id, user_id=other_id, hrms_project_id=PROJECT_ID,
                                    hrms_project_name="Proj", hrms_email="y", range_start=START, range_end=END,
                                    idempotency_key="other-person-1")
        oline = TimesheetLine(tenant_id=self.tenant_id, user_id=other_id, hrms_project_id=PROJECT_ID, work_date=D1,
                              hrms_activity_id=1, hrms_sub_activity_id=0, minutes=5, hours=0.08, summary="x", status="PENDING")
        oline.sources = [TimesheetLineSource(user_id=other_id, task_id=task_id, minutes=5)]
        other.lines.append(oline)
        self.db.add(other)
        await self.db.flush()                                    # allowed
        await self.db.rollback()
        self.assertEqual(status, "SUBMITTED")

    # ---- reminders: a month is owed while submitted work in it is unfiled
    async def notes(self):
        """Reminder bells this test's user has received (the job itself is global)."""
        return (await self.db.execute(
            select(func.count()).select_from(Notification)
            .where(Notification.user_id == self.user.id, Notification.type == "TIMESHEET_REMINDER"))).scalar_one()

    async def _overdue_setup(self):
        await self.did("Bank Reconciliation", D1, 150)       # Oct 5: inside AD October AND BS Ashwin
        await self.db.commit()

    async def test_reminders_go_out_once_a_day_combined_and_stop_after_submitting(self):
        await self._overdue_setup()
        mon = date(2026, 11, 2)                       # AD Oct and BS Ashwin are both past due
        await rem.run_reminders(self.db, mon, self.eligible)
        self.assertEqual(await self.notes(), 1)       # ONE message covering both months
        await rem.run_reminders(self.db, mon, self.eligible)   # same day again: nothing new
        self.assertEqual(await self.notes(), 1)
        logged = (await self.db.execute(select(func.count()).select_from(TimesheetReminderLog)
                                        .where(TimesheetReminderLog.user_id == self.user.id))).scalar_one()
        self.assertEqual(logged, 2)                   # AD 2026-10 and BS 2083-06
        text = (await self.db.execute(select(Notification.message).where(Notification.user_id == self.user.id))).scalar_one()
        self.assertIn("1 task", text)
        await rem.run_reminders(self.db, date(2026, 11, 3), self.eligible)   # next working day
        self.assertEqual(await self.notes(), 2)
        await self.submit(await self.preview())      # settle it
        await rem.run_reminders(self.db, date(2026, 11, 4), self.eligible)
        self.assertEqual(await self.notes(), 2)       # nothing owed -> no more reminders

    async def test_a_submission_with_no_time_taken_still_counts_as_owed(self):
        await self.did("Bank Reconciliation", D1, None)
        await self.db.commit()
        await rem.run_reminders(self.db, date(2026, 11, 2), self.eligible)
        self.assertEqual(await self.notes(), 1)

    async def test_the_time_log_table_does_not_create_an_obligation(self):
        t = await self.task("Bank Reconciliation")
        self.db.add(TimeEntry(tenant_id=self.tenant_id, user_id=self.user.id, entity_id=self.ent.id, task_id=t.id,
                              entry_date=D1, duration_minutes=480))
        await self.db.commit()
        await rem.run_reminders(self.db, date(2026, 11, 2), self.eligible)
        self.assertEqual(await self.notes(), 0)

    async def test_no_reminders_on_days_off(self):
        await self._overdue_setup()
        await rem.run_reminders(self.db, date(2026, 11, 7), self.eligible)   # Saturday
        self.assertEqual(await self.notes(), 0)

    async def test_all_unsent_work_counts_on_every_entity(self):
        await self.did("Bank Reconciliation", D1, 150, entity=self.other_ent)
        await self.db.commit()
        await rem.run_reminders(self.db, date(2026, 11, 2), self.eligible)
        self.assertEqual(await self.notes(), 1)

    async def test_only_people_who_exist_in_the_hrms_are_reminded(self):
        await self._overdue_setup()
        await rem.run_reminders(self.db, date(2026, 11, 2), {"someone.else@example.com"})
        self.assertEqual(await self.notes(), 0)
        await rem.run_reminders(self.db, date(2026, 11, 2), set())            # HRMS list empty/unavailable
        self.assertEqual(await self.notes(), 0)
        await rem.run_reminders(self.db, date(2026, 11, 2), self.eligible)    # the user's mixed-case e-mail still matches
        self.assertEqual(await self.notes(), 1)

    async def test_banner_lists_open_months_when_enabled(self):
        await self._overdue_setup()
        settings.TIMESHEET_REMINDERS_ENABLED = False
        self.assertEqual(await rem.obligations_for_user(self.db, self.user, date(2026, 11, 2), self.eligible), [])
        settings.TIMESHEET_REMINDERS_ENABLED = True
        self.assertEqual(await rem.obligations_for_user(self.db, self.user, date(2026, 11, 2), {"x@example.com"}), [])
        rows = await rem.obligations_for_user(self.db, self.user, date(2026, 11, 2), self.eligible)
        self.assertEqual([(r["calendar"], r["period_key"], r["status"], r["pending_tasks"], r["pending_minutes"]) for r in rows],
                         [("BS", "2083-06", "OVERDUE", 1, 150), ("AD", "2026-10", "OVERDUE", 1, 150)])


if __name__ == "__main__":
    unittest.main()
