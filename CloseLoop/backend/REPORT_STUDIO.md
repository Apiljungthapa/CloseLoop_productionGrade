# Report Studio

Plain-language reports with KPIs, charts, trends, overdue items, key findings and recommended actions, as a
dashboard, PDF, Excel or CSV. Team Leads and Super Admins only (Team Members have their own workload scorecard).

## Ways in

| Where | What |
|---|---|
| Reports > "Report Studio" banner, or `/<org>/reports/studio` | Type a request, pick an example, or choose the options by hand |
| Close Bhai chat | Type the same request; you get a report card with *Open dashboard / PDF / Excel / CSV* |
| API | `GET /api/report-studio/catalog`, `POST /interpret`, `POST /ask`, `POST /generate` |

Examples that work: "Generate the monthly task completion report", "Prepare a report of pending tasks by team member",
"Compare this period with the previous period", "Generate a close-status report for all entities", "Overdue report for
DE as PDF", "Compare October with September as Excel", "Pending tasks for Binita Gopali".

## Reports

| Kind | Answers |
|---|---|
| `period_completion` | How much of the period is done, by entity, progress through the period, overdue |
| `pending_by_member` | Who holds the open work, who is overloaded, what is overdue per person |
| `period_comparison` | This period against another: KPI changes, entity movers, trend over the last 6 periods |
| `close_status` | Per entity: Closed / Ready to close / On track / At risk / Behind, and what is holding it up |
| `overdue` | Every overdue item with its age in working days, by entity and owner |

## Definitions (identical to the app, so a report never contradicts a screen)

* Counted tasks = tasks of the period's plans, **minus** carried-forward tasks, upcoming placeholders and
  **skipped tasks**: an optional task (`is_required = false`) past its due date that is still not started,
  assigned or in progress. A skipped task never adds to a total, a completed count or a percentage; the report
  only mentions how many were skipped. Every other optional task counts like a required one.
* Open = not started, assigned, in progress, pending review, rejected.
* **Overdue = open, not skipped, due before the evaluation date.** A skipped task is never overdue. Age is in
  *working days* (weekends and configured holidays do not age a task).
* A task is held by its single assignee and by every entity-wide assignee who has not ticked their part.
  Nobody on it = "unassigned".
* Evaluation date = today, or the day after the period ended for a finished period (judged as it stood at close).
* Close status: Closed = every plan closed; Ready = all tasks done; On track = nothing overdue and completion within
  10 points of the work already due; Behind = 25%+ of open work overdue or 25+ points behind; At risk = between.

## Security

* Same gate as the classic reports (`report_access_service`): Super Admin = whole tenant; Team Lead = their entities
  (assignments, groups, plans they own); Team Member and Platform Owner = 403. Every query filters by tenant first.
* Naming an entity outside your scope is a 403; the plain-language parser only knows the entities you can see.
* Spreadsheet formula injection: text starting with `= + - @` is neutralised in CSV and Excel output.
* File exports are written to the audit log (`REPORT_EXPORTED`: kind, format, period, entity).
* Files are built in memory in a worker thread; nothing is stored on disk.

## Code map

* `app/services/report_studio.py` - types, pure analysis (unit-tested), SQL loader, `build_report`
* `app/services/report_exports.py` - Excel (native charts), PDF (reportlab charts), CSV
* `app/services/report_intent.py` - plain language -> report request (deterministic: no model call)
* `app/services/report_request.py` - shared by the API and chat; `app/services/report_chat.py` - chat frames
* `app/routers/report_studio.py` - HTTP; `app/routers/chat.py` hands report requests to `report_chat`
* Frontend: `pages/ReportStudio.jsx`, `components/reports/studio/*`, `components/chat/ReportCard.jsx`

## Adding a report kind

1. Write a `build_<kind>(data, names, today, ...)` returning a `RichReport` in `report_studio.py`; add it to `CATALOG`
   and to `build_report`.
2. Add a phrase rule to `_KIND_RULES` in `report_intent.py`.
3. Add tests in `tests/test_report_studio.py` (hand-checked numbers) and `tests/test_report_intent.py`.

## Known limits

* One period at a time (plus a comparison period); quarter / year-to-date ranges are not offered yet.
* The parser understands the phrasings it is tested for; an unclear request gets a clarifying question, not a guess.
* PDF text uses built-in fonts: Devanagari names print as `?` (Excel and CSV are unaffected).
