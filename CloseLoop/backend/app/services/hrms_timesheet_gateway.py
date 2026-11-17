"""The HRMS timesheet calls CloseLoop needs, behind one small interface.

  * create_day    POST one DAY's timesheet (all its activities)      (required)
  * list_entries  what the employee already has in the HRMS          (optional)

Everything else - matching, hours, duplicate protection, the review screen,
reminders - is written against this interface and tested with a fake, so
changing the HRMS (test -> production) never touches that logic.

THE REAL API (taken from a working client script, so confirmed, not guessed)
  POST {HRMS_BASE_URL}/v1/external/timesheets          201 = created
  headers: x-tenant-id: "<tenant>.<HMAC-SHA256(secret, tenant)>"  (same as activities)
  body:
    {
      "email":   "<the employee's work e-mail>",       # NOT an id - the HRMS looks them up
      "date":    "YYYY-MM-DD",
      "summary": "<day summary>",                       # optional, ONE per day
      "timesheetActivities": [                          # one request carries the whole day
        { "activityId": 31, "projectId": 4, "hours": 1.5,
          "workType": 0,                                # 0 billable, 1 non-billable, 2 paid non-billable
          "timesheetSubActivities": [                   # only for an activity with sub activities:
            { "subActivityId": 60, "value": "..." }     #   a value for EACH of them
          ] }
      ]
    }
There is no read route, so existing entries can't be listed (can_list() is False):
the page says so and duplicates are prevented only against what CloseLoop sent.
HRMS_TIMESHEET_LIST_PATH exists for an HRMS that does have one (its parameter and
field names, further down, are unconfirmed).

CONNECTING A DIFFERENT HRMS (e.g. production)
  1. HRMS_BASE_URL and the tenant key; HRMS_TIMESHEET_POST_PATH if the route differs.
  2. Check the WIRE FORMAT block below against that HRMS's API.
  3. TIMESHEET_SUBMIT_ENABLED=true (and TIMESHEET_REMINDERS_ENABLED=true).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Protocol

import requests

from ..config import settings
from . import hrms_service

logger = logging.getLogger(__name__)


class HrmsTimesheetUnavailable(Exception):
    """The HRMS timesheet API isn't connected / reachable. Nothing was sent."""


class HrmsTimesheetRejected(Exception):
    """The HRMS answered and refused the request (validation, unknown employee,
    locked month, ...). Definitely not stored."""


class HrmsTimesheetUncertain(Exception):
    """No usable answer (timeout, dropped connection, server error). The day may
    or may not have been stored."""


@dataclass(frozen=True)
class ExistingEntry:
    work_date: date
    activity_id: int
    hours: float
    summary: str = ""


@dataclass(frozen=True)
class ActivityEntry:
    activity_id: int
    hours: float
    work_type: str  # "BILLABLE" | "NON_BILLABLE" | "PAID_NON_BILLABLE"
    sub_values: dict[int, str] = field(default_factory=dict)  # sub activity id -> value


@dataclass(frozen=True)
class NewDay:
    """One request: everything for one employee on one date."""

    email: str
    project_id: int
    work_date: date
    summary: str
    entries: tuple[ActivityEntry, ...]


class TimesheetGateway(Protocol):
    def is_available(self) -> bool:
        """Can timesheets be sent at all (route configured, credentials set)?"""

    def can_list(self) -> bool:
        """Can the employee's existing HRMS entries be read back?"""

    def list_entries(
        self, *, email: str, project_id: int, start: date, end: date
    ) -> list[ExistingEntry]:
        """Raises HrmsTimesheetUnavailable when it can't be read."""

    def create_day(self, day: NewDay) -> Optional[str]:
        """Store one day; return the HRMS id if it gives one. Raises
        HrmsTimesheetRejected / HrmsTimesheetUncertain / HrmsTimesheetUnavailable."""


# =====================================================================
# WIRE FORMAT - the ONLY place that knows the HRMS field names.
# The POST half is confirmed against a working client; the LIST half is
# unconfirmed (the HRMS in use has no read route). Pinned by
# tests/test_timesheet_gateway.py - change both together.
# =====================================================================

# CloseLoop work type -> the integer the HRMS expects.
WORK_TYPE_VALUES = {"BILLABLE": 0, "NON_BILLABLE": 1, "PAID_NON_BILLABLE": 2}

# Query parameters / row fields of the (optional, unconfirmed) list call.
LIST_PARAMS = {"email": "email", "project_id": "projectId", "start": "startDate", "end": "endDate"}
LIST_FIELDS = {"work_date": "date", "activity_id": "activityId", "hours": "hours", "summary": "summary"}
# =====================================================================


def build_payload(day: NewDay) -> dict[str, Any]:
    activities = []
    for e in day.entries:
        item: dict[str, Any] = {
            "activityId": e.activity_id,
            "projectId": day.project_id,
            "hours": round(e.hours, 2),
            "workType": WORK_TYPE_VALUES[e.work_type],
        }
        if e.sub_values:
            item["timesheetSubActivities"] = [
                {"subActivityId": sid, "value": str(val)} for sid, val in sorted(e.sub_values.items())
            ]
        activities.append(item)
    body: dict[str, Any] = {
        "email": day.email,
        "date": day.work_date.isoformat(),
        "timesheetActivities": activities,
    }
    if day.summary:
        body["summary"] = day.summary
    return body


def parse_entries(body: Any) -> list[ExistingEntry]:
    """HRMS list response -> entries. A row that can't be read raises: silently
    skipping it could hide the very duplicate this check exists to catch."""
    rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise HrmsTimesheetUnavailable("Unexpected response format from the HRMS timesheet")
    out: list[ExistingEntry] = []
    for row in rows:
        try:
            out.append(ExistingEntry(
                work_date=date.fromisoformat(str(row[LIST_FIELDS["work_date"]])[:10]),
                activity_id=int(row[LIST_FIELDS["activity_id"]]),
                hours=float(row[LIST_FIELDS["hours"]]),
                summary=str(row.get(LIST_FIELDS["summary"]) or ""),
            ))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise HrmsTimesheetUnavailable("Could not read a row of the HRMS timesheet")
    return out


def _message(resp: requests.Response) -> str:
    try:
        body = resp.json()
        text = body.get("message") if isinstance(body, dict) else None
    except ValueError:
        text = None
    return (str(text) if text else f"The HRMS refused the timesheet (HTTP {resp.status_code})")[:300]


class HrmsHttpGateway:
    """Talks to the HRMS over HTTP with the tenant key. Configured by
    HRMS_TIMESHEET_POST_PATH / HRMS_TIMESHEET_LIST_PATH and the WIRE FORMAT above."""

    def _url(self, path: str) -> str:
        return f"{settings.HRMS_BASE_URL.rstrip('/')}/{path.strip().lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        try:
            return {**hrms_service.signed_headers(), "Content-Type": "application/json"}
        except hrms_service.HrmsNotConfigured:
            raise HrmsTimesheetUnavailable("The HRMS integration is not configured")

    def is_available(self) -> bool:
        return bool(settings.HRMS_TIMESHEET_POST_PATH.strip()
                    and settings.HRMS_TENANT_ID.strip() and settings.HRMS_SECRET.strip())

    def can_list(self) -> bool:
        return self.is_available() and bool(settings.HRMS_TIMESHEET_LIST_PATH.strip())

    def list_entries(self, *, email, project_id, start, end):
        if not self.can_list():
            raise HrmsTimesheetUnavailable("The HRMS timesheet can't be read")
        params = {
            LIST_PARAMS["email"]: email, LIST_PARAMS["project_id"]: project_id,
            LIST_PARAMS["start"]: start.isoformat(), LIST_PARAMS["end"]: end.isoformat(),
        }
        try:
            resp = requests.get(self._url(settings.HRMS_TIMESHEET_LIST_PATH), params=params,
                                headers=self._headers(), timeout=settings.HRMS_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("HRMS timesheet read failed (%s)", type(exc).__name__)
            raise HrmsTimesheetUnavailable("Could not reach the HRMS") from exc
        if not resp.ok:
            logger.warning("HRMS timesheet read returned HTTP %s", resp.status_code)
            raise HrmsTimesheetUnavailable(f"The HRMS returned an error (HTTP {resp.status_code})")
        try:
            return parse_entries(resp.json())
        except ValueError:
            raise HrmsTimesheetUnavailable("Unexpected response from the HRMS timesheet")

    def create_day(self, day):
        if not self.is_available():
            raise HrmsTimesheetUnavailable("The HRMS timesheet API is not connected")
        headers = self._headers()
        try:
            resp = requests.post(self._url(settings.HRMS_TIMESHEET_POST_PATH),
                                 json=build_payload(day), headers=headers,
                                 timeout=settings.HRMS_TIMEOUT_SECONDS)
        except requests.ConnectTimeout as exc:
            raise HrmsTimesheetUnavailable("Could not reach the HRMS") from exc   # never connected: not sent
        except requests.RequestException as exc:
            # Sent (or half sent) and no answer: it may well be stored.
            logger.warning("HRMS timesheet POST gave no answer (%s)", type(exc).__name__)
            raise HrmsTimesheetUncertain("The HRMS did not answer in time") from exc
        if resp.ok:   # the HRMS answers 201 Created
            try:
                body = resp.json()
            except ValueError:
                return None
            data = body.get("data") if isinstance(body, dict) else None
            ident = (data or {}).get("id") if isinstance(data, dict) else (body.get("id") if isinstance(body, dict) else None)
            return str(ident) if ident is not None else None
        if resp.status_code in (401, 403):
            raise HrmsTimesheetUnavailable("The HRMS did not accept CloseLoop's credentials")
        if 400 <= resp.status_code < 500:
            raise HrmsTimesheetRejected(_message(resp))
        raise HrmsTimesheetUncertain(f"The HRMS had a problem (HTTP {resp.status_code})")


class UnavailableGateway:
    """Used when HRMS_TIMESHEET_POST_PATH is blank."""

    def is_available(self) -> bool:
        return False

    def can_list(self) -> bool:
        return False

    def list_entries(self, *, email, project_id, start, end):
        raise HrmsTimesheetUnavailable("The HRMS timesheet API is not connected yet")

    def create_day(self, day):
        raise HrmsTimesheetUnavailable("The HRMS timesheet API is not connected yet")


def get_gateway() -> TimesheetGateway:
    if settings.HRMS_TIMESHEET_POST_PATH.strip():
        return HrmsHttpGateway()
    return UnavailableGateway()
