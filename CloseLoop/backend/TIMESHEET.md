# Automated HRMS timesheet

Page: **HRMS Integration** (Team Lead + Team Member). Pick an English (AD) month
and an HRMS project; CloseLoop builds the timesheet from the user's own time
logs, matches the work to HRMS activities, and shows it for review before
anything is sent.

## Status

| Part | State |
|---|---|
| Matching, hours, summaries, duplicate protection, review screen | built, tested |
| Sending (`POST /v1/external/timesheets`, one request per day) | built to a working client script; on when `TIMESHEET_SUBMIT_ENABLED=true` |
| Reading existing HRMS entries | the HRMS has no read route -> not possible; the page says so |
| Reminders (AD + BS month-end) | built, tested; on when `TIMESHEET_REMINDERS_ENABLED=true` |

## The HRMS API (confirmed from a working client script)
`POST {HRMS_BASE_URL}/v1/external/timesheets` - 201 = created - same `x-tenant-id`
tenant-key header as the activities call. One request is one **day**:

```json
{ "email": "<work e-mail>", "date": "2026-10-05", "summary": "<one summary for the day>",
  "timesheetActivities": [
    { "activityId": 31, "projectId": 4, "hours": 1.5, "workType": 0,
      "timesheetSubActivities": [ { "subActivityId": 60, "value": "..." } ] } ] }
```
- The employee is identified by **e-mail** (CloseLoop sends the signed-in user's e-mail;
  the HRMS must have an employee with it). Work type is a number: 0 billable,
  1 non-billable, 2 paid non-billable.
- **Sub activities are fields, not alternatives:** for an activity that has them the
  HRMS gets a `value` for EACH. The page shows one input per sub activity; a field named
  like a description (Description, Summary, Details, Remarks, Notes, Comment) is
  filled from the summary, the rest the user types. A line isn't sendable until every
  field has a value.
- The summary is **per day**, so a day's line summaries are joined (repeats dropped).
- There is no read route (no GET), so CloseLoop can't see what is already in the HRMS:
  the page warns, the user ticks a confirmation, and duplicates are prevented against
  everything CloseLoop itself sent. Because a second POST for a date might replace the
  first (not known), **a date already sent is closed**: later lines on it are held back
  with "this date was already sent"; add extra time for it in the HRMS directly.
- A lost reply (timeout) can't be looked up either: the user checks the HRMS and presses
  **It arrived** / **It didn't arrive**.

## Switching on / moving to another HRMS
`HRMS_TIMESHEET_POST_PATH` (default `/v1/external/timesheets`), `HRMS_BASE_URL` and the
tenant key; then `TIMESHEET_SUBMIT_ENABLED=true`, `TIMESHEET_REMINDERS_ENABLED=true`.
Everything HRMS-specific is in `services/hrms_timesheet_gateway.py` (`WIRE FORMAT`
block, pinned by `tests/test_timesheet_gateway.py`, which also asserts the body equals
the reference script's). An HRMS with a read route: set `HRMS_TIMESHEET_LIST_PATH` and
confirm `LIST_PARAMS` / `LIST_FIELDS` (unconfirmed) - duplicates are then checked
against the HRMS itself.

## How a month becomes a timesheet
1. **Every date is listed.** The page shows each day of the chosen month (up to the
   optional "only up to" date), weekends and recorded holidays marked, days with
   nothing submitted shown as "Nothing submitted".
2. **A person "worked" on a task on the day they SUBMITTED it.** That is the only thing
   that makes a row, and it is read from the tasks themselves
   (`services/timesheet_events.py`): a task or sub task they submitted
   (`submitted_by_id` / `submitted_at`), or their own part of a group task they marked
   done (`done` / `done_at`). **The time-log table is never read**, and neither is a due
   date or an assignment. The day is the Nepal-time day of the submission. A rejected or
   reopened task has its submission cleared by the app, so it is a row again only once it
   is resubmitted. Everyone fills their own timesheet; nobody prepares someone else's, so
   a Team Lead who submitted nothing sees an empty month. Nothing needs linking or
   limiting - the HRMS has no entity.
3. **Hours** are the **Time Taken** entered when the task was submitted (task, sub task,
   or the person's own part of a group task), shown read-only. If it was left blank the
   row has **0 hours**: it is never pre-selected, and cannot be sent until the user types
   hours (1 minute to 24 h). Nothing is invented. Each submission is its own row; rows
   the user puts on the same date and activity are merged into one HRMS entry when sent
   (minutes added, then rounded once). A submission is filed **once per person**:
   `timesheet_line_sources` holds `(user_id, task_id | subtask_id)`, UNIQUE, so it never
   comes back and two requests can't both send it, while two people can each file their
   own part of a group task. A refused send gives the work back.
4. **Matching** (`timesheet_matching.py`, pure) - first hit wins: a confirmed
   choice for the same task template, for the same title, for the parent task
   (sub tasks inherit), then wording. Wording auto-selects only at score >= 0.85,
   a lead of >= 0.15 over the runner-up and a shared meaningful word; a one-word
   activity name never auto-matches. Everything else is "pick an activity" and
   left unselected. Only the ACTIVITY is matched; its sub activities are fields to fill (above).
5. **Summary** - (per row; the HRMS keeps one per day, so a day's are joined) a sub task
   reads `Task: Sub - desc`; a task uses its own description (title if none). Deterministic, editable, flagged if shortened to
   `TIMESHEET_SUMMARY_MAX_CHARS`.
6. **Work type** - Billable by default (Non-billable and Paid non-billable selectable).
7. **Duplicates** - lines on a date already sent from CloseLoop are flagged and
   cannot be sent (and, if the HRMS could be read, lines already on it too). If the
   HRMS has a read route but it fails right now, submitting waits until it works.

## Sending safely (`timesheet_service.submit`)
Re-computes from scratch (a stale screen is refused), applies the user's choices,
re-checks duplicates and the 24h/day limit, records the intent (committed) before
anything leaves, then sends one DAY at a time (one request per date), committing
after each; a day's lines share its fate.
`timesheet_line_sources` is UNIQUE per (person, task / sub task) and `uq_timesheet_lines_live`
claims a (user, project, day, activity) slot while a line is still PENDING, so
two simultaneous submits cannot both go out. An idempotency key makes a repeated
request return the first result. A refusal frees the submitted work to be sent
again; an unknown outcome (`UNCERTAIN`) keeps them locked until it is settled
(**Verify** if the HRMS can be read, otherwise **It arrived / It didn't arrive**). Confirmed activity choices are remembered (`hrms_activity_mappings`) so
next month matches by id.

## Reminders (AD and BS)
Each AD month and each BS month is an obligation with its own deadline (its last
day). A person owes a month while they have SUBMITTED tasks in it that are not yet filed
(sent, being sent, or sent with an unknown result), so one submission settles both
calendars. The time-log table is not read here either. Only people who exist in the
HRMS (work e-mail equals a project member's) are reminded - anyone else has no
timesheet to fill - and if the HRMS member list can't be read nobody is reminded. Reminders go
out on working days only (weekend + recorded holidays): D-3, D-1 and the deadline
(a deadline on a day off is reminded on the working day before), then every
working day after it until nothing is left, for `TIMESHEET_REMINDER_LOOKBACK` closed
months. One bell + e-mail per person per day however many months are behind;
`timesheet_reminder_log` is claimed with `ON CONFLICT DO NOTHING`, so restarts and
extra workers never double-send. The banner (non-dismissible) uses the same data.

## API (`/api/timesheets`)
`GET preview` - `POST submit` - `POST submissions/{id}/verify` (HRMS readable) -
`POST submissions/{id}/resolve` (not readable) - `GET submissions` -
`GET obligations/me`. Errors: `{"detail": {"msg", "code"}}`.

## Facts about the HRMS found while building this
- `activities` is one global list (not per project); `projects[].members[]` carries
  each member's `id` and `workEmail`. A CloseLoop user is the member whose work
  e-mail equals theirs exactly - no fuzzy matching.
- When this was written the activity catalogue held placeholder data ("Activity 2",
  "Testing it", ...), so little will auto-match until real activities exist; the
  remembered-choice path carries month-to-month matching in the meantime.
- The HRMS key opens `GET projects` / `GET activities` only; the timesheet POST (test
  API) is a separate route. The activity list in the test HRMS is dummy data.
- Not known yet: hours precision, summary length limit, accepted work types,
  whether past/locked months are writable. Defaults here: 2 decimals, 1000 chars,
  BILLABLE / NON_BILLABLE.

## Tests
Pure: `tests/test_timesheet_matching|builder|reminders|gateway.py`. Real Postgres (HRMS
faked): `tests/test_timesheet_service_db.py`, skipped unless
`TIMESHEET_TEST_DATABASE_URL` is set - use a throwaway database, **never prod**:

    docker exec closeloop-db psql -U postgres -c "CREATE DATABASE closeloop_ts_scratch"
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/closeloop_ts_scratch alembic upgrade head
    TIMESHEET_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/closeloop_ts_scratch \
        python -m unittest tests.test_timesheet_service_db

Migrations: `0057_timesheets` (six new tables), `0058_timesheet_by_email` (the e-mail
sent, optional employee id, sub-activity values), `0059_timesheet_typed_hours` and
`0060_timesheet_per_person_sources` (a submission is filed once per person). Nothing
existing is altered. (`hrms_project_links`, from 0057, is unused and empty.)
