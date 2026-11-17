import unittest
from datetime import date
from unittest import mock

import requests

from app.config import settings
from app.services import hrms_timesheet_gateway as gw
from app.services.hrms_timesheet_gateway import (
    ActivityEntry, ExistingEntry, HrmsHttpGateway, HrmsTimesheetRejected, HrmsTimesheetUncertain,
    HrmsTimesheetUnavailable, NewDay, UnavailableGateway, build_payload, get_gateway, parse_entries,
)


class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self.ok, self._body, self._bad = status, 200 <= status < 300, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._body


def script_payload(email, day, hours, work_type, activity_id, project_id, summary=None, sub_values=None):
    """The request body exactly as the working client script (the reference this
    integration was built from) assembles it."""
    entry = {"activityId": activity_id, "projectId": project_id, "hours": hours, "workType": work_type}
    if sub_values:      # activityType == 1: a value for EACH sub activity
        entry["timesheetSubActivities"] = [{"subActivityId": i, "value": v} for i, v in sub_values]
    payload = {"email": email, "date": day, "timesheetActivities": [entry]}
    if summary:
        payload["summary"] = summary
    return payload


DAY = NewDay(
    email="devraj.khatri@foursymmetrons.com", project_id=4, work_date=date(2026, 10, 5),
    summary="Reviewed the dashboard",
    entries=(ActivityEntry(activity_id=31, hours=1.5, work_type="BILLABLE"),),
)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = (settings.HRMS_TENANT_ID, settings.HRMS_SECRET, settings.HRMS_TIMESHEET_POST_PATH,
                       settings.HRMS_TIMESHEET_LIST_PATH, settings.HRMS_BASE_URL)
        settings.HRMS_TENANT_ID, settings.HRMS_SECRET = "17", "s3cret"
        settings.HRMS_BASE_URL = "https://hrms.test/backend"
        settings.HRMS_TIMESHEET_POST_PATH = "/v1/external/timesheets"
        settings.HRMS_TIMESHEET_LIST_PATH = ""

    def tearDown(self):
        (settings.HRMS_TENANT_ID, settings.HRMS_SECRET, settings.HRMS_TIMESHEET_POST_PATH,
         settings.HRMS_TIMESHEET_LIST_PATH, settings.HRMS_BASE_URL) = self._saved


class WireFormatTests(Base):
    """These pin the request to the working client. Changing the HRMS format
    means changing the WIRE FORMAT block AND these tests - on purpose."""

    def test_a_plain_activity_is_exactly_what_the_reference_script_sends(self):
        self.assertEqual(
            build_payload(DAY),
            script_payload("devraj.khatri@foursymmetrons.com", "2026-10-05", 1.5, 0, 31, 4, "Reviewed the dashboard"),
        )

    def test_an_activity_with_sub_activities_sends_a_value_for_each(self):
        day = NewDay("a@b.com", 4, date(2026, 10, 5), "s", (
            ActivityEntry(33, 2.0, "NON_BILLABLE", {68: "Fourth value", 60: "First value"}),))
        self.assertEqual(
            build_payload(day),
            script_payload("a@b.com", "2026-10-05", 2.0, 1, 33, 4, "s", [(60, "First value"), (68, "Fourth value")]),
        )

    def test_work_types_are_the_numbers_the_hrms_uses(self):
        self.assertEqual(gw.WORK_TYPE_VALUES, {"BILLABLE": 0, "NON_BILLABLE": 1, "PAID_NON_BILLABLE": 2})
        for name, number in gw.WORK_TYPE_VALUES.items():
            day = NewDay("a@b.com", 4, date(2026, 10, 5), "", (ActivityEntry(31, 1.0, name),))
            self.assertEqual(build_payload(day)["timesheetActivities"][0]["workType"], number)

    def test_the_summary_is_optional_and_left_out_when_blank(self):
        body = build_payload(NewDay("a@b.com", 4, date(2026, 10, 5), "", (ActivityEntry(31, 1.0, "BILLABLE"),)))
        self.assertNotIn("summary", body)

    def test_one_request_carries_the_whole_day(self):
        day = NewDay("a@b.com", 4, date(2026, 10, 5), "s", (
            ActivityEntry(31, 1.0, "BILLABLE"), ActivityEntry(32, 0.5, "NON_BILLABLE")))
        acts = build_payload(day)["timesheetActivities"]
        self.assertEqual([(a["activityId"], a["hours"], a["workType"], a["projectId"]) for a in acts],
                         [(31, 1.0, 0, 4), (32, 0.5, 1, 4)])

    def test_hours_are_two_decimals_and_the_date_is_ad_iso(self):
        day = NewDay("a@b.com", 4, date(2026, 11, 1), "", (ActivityEntry(31, 0.8333333, "BILLABLE"),))
        body = build_payload(day)
        self.assertEqual((body["timesheetActivities"][0]["hours"], body["date"]), (0.83, "2026-11-01"))

    def test_the_employee_is_the_e_mail_not_an_id(self):
        body = build_payload(DAY)
        self.assertEqual(body["email"], "devraj.khatri@foursymmetrons.com")
        self.assertNotIn("employeeId", body)

    def test_the_route_is_the_one_the_script_posts_to(self):
        self.assertEqual(type(settings).model_fields["HRMS_TIMESHEET_POST_PATH"].default, "/v1/external/timesheets")


class CreateDayTests(Base):
    def post(self, **kw):
        with mock.patch.object(gw.requests, "post", **kw) as m:
            try:
                return m, HrmsHttpGateway().create_day(DAY)
            except Exception as exc:  # noqa: BLE001
                return m, exc

    def test_success_posts_json_to_the_route_with_the_tenant_header(self):
        m, out = self.post(return_value=_Resp(201, {"code": "OK", "data": {"id": 9001}}))
        self.assertEqual(out, "9001")
        args, kwargs = m.call_args
        self.assertEqual(args[0], "https://hrms.test/backend/v1/external/timesheets")
        self.assertEqual(kwargs["json"], build_payload(DAY))
        self.assertTrue(kwargs["headers"]["x-tenant-id"].startswith("17."))
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["timeout"], settings.HRMS_TIMEOUT_SECONDS)

    def test_created_without_an_id_in_the_body_is_still_success(self):
        self.assertIsNone(self.post(return_value=_Resp(201, {"code": "OK"}))[1])
        self.assertIsNone(self.post(return_value=_Resp(201, bad_json=True))[1])

    def test_a_4xx_is_a_definite_refusal_with_the_hrms_message(self):
        _, out = self.post(return_value=_Resp(422, {"message": "Employee not found"}))
        self.assertIsInstance(out, HrmsTimesheetRejected)
        self.assertEqual(str(out), "Employee not found")
        # the HRMS answers 400 (not 401) when an auth header is missing - still a refusal, and shown as is
        _, out = self.post(return_value=_Resp(400, {"message": "Required header is missing : Authorization"}))
        self.assertIsInstance(out, HrmsTimesheetRejected)

    def test_bad_credentials_are_unavailable_not_a_data_refusal(self):
        for code in (401, 403):
            self.assertIsInstance(self.post(return_value=_Resp(code, {}))[1], HrmsTimesheetUnavailable)

    def test_a_5xx_may_have_been_stored_so_it_is_uncertain(self):
        self.assertIsInstance(self.post(return_value=_Resp(500, {}))[1], HrmsTimesheetUncertain)
        self.assertIsInstance(self.post(return_value=_Resp(503, {}))[1], HrmsTimesheetUncertain)

    def test_no_answer_is_uncertain_but_never_connecting_is_not_sent(self):
        self.assertIsInstance(self.post(side_effect=requests.ReadTimeout())[1], HrmsTimesheetUncertain)
        self.assertIsInstance(self.post(side_effect=requests.ConnectionError())[1], HrmsTimesheetUncertain)
        self.assertIsInstance(self.post(side_effect=requests.ConnectTimeout())[1], HrmsTimesheetUnavailable)

    def test_nothing_is_sent_when_not_configured(self):
        for attr, val in (("HRMS_TIMESHEET_POST_PATH", ""), ("HRMS_SECRET", ""), ("HRMS_TENANT_ID", "")):
            with self.subTest(attr):
                old = getattr(settings, attr)
                setattr(settings, attr, val)
                with mock.patch.object(gw.requests, "post") as m:
                    with self.assertRaises(HrmsTimesheetUnavailable):
                        HrmsHttpGateway().create_day(DAY)
                    m.assert_not_called()
                setattr(settings, attr, old)


class ListEntriesTests(Base):
    """The HRMS in use has no read route, so this half is optional and unconfirmed."""

    def setUp(self):
        super().setUp()
        settings.HRMS_TIMESHEET_LIST_PATH = "/v1/external/timesheets/list"

    def test_rows_parse(self):
        rows = parse_entries({"data": [
            {"date": "2026-10-05T00:00:00.000Z", "activityId": 33, "hours": "1.5", "summary": "x"},
            {"date": "2026-10-06", "activityId": 31, "hours": 2},
        ]})
        self.assertEqual(rows, [ExistingEntry(date(2026, 10, 5), 33, 1.5, "x"), ExistingEntry(date(2026, 10, 6), 31, 2.0, "")])
        self.assertEqual(parse_entries([]), [])

    def test_an_unreadable_row_fails_closed_instead_of_being_skipped(self):
        with self.assertRaises(HrmsTimesheetUnavailable):
            parse_entries({"data": [{"date": "2026-10-05", "activityId": 33, "hours": 1}, {"oops": 1}]})
        with self.assertRaises(HrmsTimesheetUnavailable):
            parse_entries({"data": "nope"})

    def test_reads_by_e_mail_with_the_configured_params(self):
        with mock.patch.object(gw.requests, "get", return_value=_Resp(200, {"data": [{"date": "2026-10-05", "activityId": 33, "hours": 1.5}]})) as m:
            rows = HrmsHttpGateway().list_entries(email="a@b.com", project_id=7, start=date(2026, 10, 1), end=date(2026, 10, 31))
        self.assertEqual(rows, [ExistingEntry(date(2026, 10, 5), 33, 1.5, "")])
        self.assertEqual(m.call_args.kwargs["params"], {"email": "a@b.com", "projectId": 7,
                                                        "startDate": "2026-10-01", "endDate": "2026-10-31"})

    def test_any_failure_means_unreadable_never_an_empty_list(self):
        for kw in (dict(return_value=_Resp(500, {})), dict(side_effect=requests.ConnectionError()),
                   dict(return_value=_Resp(200, bad_json=True))):
            with mock.patch.object(gw.requests, "get", **kw):
                with self.assertRaises(HrmsTimesheetUnavailable):
                    HrmsHttpGateway().list_entries(email="a@b.com", project_id=1, start=date(2026, 10, 1), end=date(2026, 10, 31))


class CapabilityTests(Base):
    def test_the_hrms_in_use_can_send_but_not_list(self):
        g = HrmsHttpGateway()
        self.assertEqual((g.is_available(), g.can_list()), (True, False))
        with self.assertRaises(HrmsTimesheetUnavailable):
            g.list_entries(email="a@b.com", project_id=1, start=date(2026, 10, 1), end=date(2026, 10, 31))

    def test_gateway_choice_follows_the_post_path(self):
        self.assertIsInstance(get_gateway(), HrmsHttpGateway)
        settings.HRMS_TIMESHEET_POST_PATH = "  "
        g = get_gateway()
        self.assertIsInstance(g, UnavailableGateway)
        self.assertEqual((g.is_available(), g.can_list()), (False, False))


if __name__ == "__main__":
    unittest.main()
