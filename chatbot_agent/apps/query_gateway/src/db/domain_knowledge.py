"""Curated CloseLoop knowledge handed to the SQL-planning model.

The schema alone does not say what the data *means* (e.g. which column holds the assignee,
what "overdue" or "pending" mean). Everything here is plain text for the prompt; none of it is
trusted for security, which is enforced separately on the generated SQL.
"""

TABLE_NOTES: dict[str, str] = {
    "tasks": "A work item inside a plan (one entity, one period). Has status, due_date, priority.",
    "sub_tasks": "A checklist item under a task. Same status enum as tasks.",
    "plans": "A close pack instantiated for one entity in one period (the pack the user opens). tasks.plan_id points "
    "here; source_pack_id -> close_packs.name; title is '<pack name> — <period label>'.",
    "periods": "A reporting period (month/quarter/...). label like 'P11'; status OPEN/IN_PROGRESS/CLOSED/ARCHIVED.",
    "entities": "A business unit / branch / company that owns plans and tasks.",
    "entity_groups": "Hierarchy above entities (region, division, department...). parent_id builds the tree.",
    "entity_group_memberships": "Which entity belongs to which entity group.",
    "users": "People in the tenant. role: SUPER_ADMIN, TEAM_LEAD, TEAM_MEMBER (PLATFORM_OWNER is internal).",
    "user_entity_assignments": "Which users work in which entity.",
    "user_entity_group_assignments": "Which users are responsible for which entity group.",
    "task_entity_assignees": "Per-user assignment rows for entity-wide tasks, with a per-user done flag.",
    "sub_task_entity_assignees": "Same as task_entity_assignees, for sub_tasks.",
    "close_packs": "A reusable checklist template set (monthly close, quarterly close...).",
    "close_pack_versions": "Versioned snapshots of a close pack.",
    "task_templates": "Template tasks inside a close pack; tasks.source_template_id points here.",
    "sub_task_templates": "Template sub-tasks of a task template.",
    "carry_forward_groups": "A batch of unfinished items moved from one period to the next, with review status.",
    "carry_forward_items": "The individual items inside a carry-forward batch.",
    "comments": "Comments written by users on tasks.",
    "attachments": "Files attached to tasks or sub-tasks (metadata only).",
    "time_entries": "Time logged by users against tasks/entities; duration_minutes is the amount.",
    "notifications": "In-app notifications sent to users (type like TASK_ASSIGNED, TASK_REJECTED).",
    "report_snapshots": "Generated reports (report_type, period, entity).",
    "period_configurations": "Settings that control how periods are generated.",
    "tenants": "The customer organisation. Never query across tenants.",
}

# "Counts toward totals / progress": every task EXCEPT a skipped one (optional, past due, not done).
# Written null-safe (a plain NOT (... AND due_date < today) would drop optional tasks with no due date).
# Same rule as the app: backend services/optional_tasks.py, frontend taskStatusKey.
NOT_SKIPPED = (
    "(t.is_required = true OR t.due_date IS NULL OR t.due_date >= CURRENT_DATE "
    "OR t.status NOT IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS'))"
)

GLOSSARY = """\
Business meaning (use these definitions when the user's words are loose):
- "task", "work", "to-do", "item" -> tasks. "subtask", "checklist item" -> sub_tasks.
- Done / finished / complete / closed task -> status = 'COMPLETED'.
- Open / pending / outstanding / not done / remaining -> status IN ('NOT_STARTED','IN_PROGRESS',
  'PENDING_REVIEW','REJECTED','ASSIGNED'). CARRIED_FORWARD tasks were moved to the next period and UPCOMING
  tasks are locked copies for a future period: neither is open work, so exclude both from "open", "pending"
  and "overdue" counts unless the user asks about carry-forward / upcoming.
- Waiting for review / approval -> status = 'PENDING_REVIEW'. Sent back / returned -> status = 'REJECTED'
  (the reason is in rejection_note).
- Required vs optional: tasks.is_required (boolean). Optional tasks have is_required = false (they come from
  Daily recurring tasks marked Optional); they are generated and visible, and one left undone after its due
  date is SKIPPED (see Skipped below).
- COUNTING RULE (same as the app's dashboards): every task counts EXCEPT a skipped one. An optional task that
  is completed, in review, rejected or not yet due counts like any other task (a completed optional task is
  in Completed and in the completion rate). Every count of tasks - "how many tasks", total, completed, open,
  pending, in progress, completion rate / percentage, per person, per entity, per period - must add this
  NOT SKIPPED condition to its WHERE: @@NOT_SKIPPED@@
  (use the alias t of the tasks table). Only questions about skipped / optional tasks use the opposite.
  A sub-task inherits its parent task's flag: it is skipped on its own due date and status.
- DASHBOARD STATUS TABS (All Tasks, Due Today, Overdue, Not Started, In Progress, Pending Review, Rejected,
  Completed, Upcoming). Each task that is not skipped (and never CARRIED_FORWARD) sits in exactly ONE tab.
  When the user names a tab, use its exact condition below so the number equals the screen:
  * Overdue / late / past due -> t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS') AND t.due_date < CURRENT_DATE.
    Past-due work that is PENDING_REVIEW or REJECTED is NOT overdue (it shows under Pending Review / Rejected).
    These three statuses are exactly the skipped statuses, so Overdue is REQUIRED tasks only (add
    t.is_required = true): an optional task that is past due and not done is skipped, never overdue.
  * Due Today -> the same three statuses AND t.due_date = CURRENT_DATE (optional tasks included: not skipped
    yet). Finished or in-review work is not "due".
  * Not Started -> t.status IN ('NOT_STARTED','ASSIGNED') AND (t.due_date IS NULL OR t.due_date > CURRENT_DATE).
  * In Progress -> t.status = 'IN_PROGRESS' AND (t.due_date IS NULL OR t.due_date > CURRENT_DATE).
    Not Started / In Progress tabs only hold work that is not yet due: a task that is late or due today shows
    under Overdue / Due Today even though its status is unchanged. For "how many are not started / in progress"
    return BOTH the tab figure and the count that has that status but is already late or due today (see examples).
  * Pending Review -> PENDING_REVIEW. Rejected -> REJECTED. Completed -> COMPLETED. Upcoming -> UPCOMING.
  CURRENT_DATE is already Nepal's date (the app's calendar), so "today" here is the same day the screen shows.
- A PACK (what the user opens as e.g. "the P11 Goals - FR pack") is ONE row of plans: a close pack for ONE entity in
  ONE period. Its name is close_packs.name via plans.source_pack_id, and plans.title reads '<pack name> — <period
  label>', e.g. 'Goals - FR — P11'. The period is periods.label via plans.period_id, the entity is entities.name via
  plans.entity_id, and plans.owner_id is the Team Lead who owns it. Find a pack by its words, not exactly:
  plans.title ILIKE '%goals%fr%' (the user's words in order, joined by %; leave the period label out of the pattern
  and filter it with periods.label). The same pack name can have a plan per entity, so return one row PER PLAN
  (title, entity, owner) and give the total when more than one matches.
  The number on a status tab INSIDE a pack (Due Today, Overdue, Not Started, In Progress, Completed ...) counts that
  plan's TOP-LEVEL tasks only (tasks.plan_id = plans.id) and never its sub_tasks: a task with sub-tasks is one row
  whose status is already rolled up from them. The pack screen puts EVERY task of the plan in exactly one tab, in
  this order: UPCOMING -> Upcoming; COMPLETED -> Completed; REJECTED -> Rejected; PENDING_REVIEW -> Pending Review;
  otherwise past due -> Skipped when optional and NOT_STARTED/ASSIGNED/IN_PROGRESS, else Overdue; otherwise due today
  -> Due Today; otherwise IN_PROGRESS -> In Progress; otherwise Not Started. Unlike the tenant-wide rules above, a
  CARRIED_FORWARD task is NOT left out inside a pack: the screen files it by its date like open work. So a pack's
  Due Today = t.status NOT IN ('COMPLETED','REJECTED','PENDING_REVIEW','UPCOMING') AND t.due_date = CURRENT_DATE.
  Put the tab's conditions in the ON clause of a LEFT JOIN from plans to tasks, so a pack with nothing in the tab
  returns 0 and a pack that does not exist returns no row at all (say "no such pack", not "0").
- Skipped (the dashboard's "Skipped" tab) -> an OPTIONAL task whose due date has passed while it was still not
  done: is_required = false AND due_date < CURRENT_DATE AND status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS').
  A skipped task is in no total and no percentage. An optional task that is completed, in review, rejected or
  not yet due is NOT skipped and counts normally.
  "Skipped" is NOT a status value and is NOT the same as CARRIED_FORWARD: never filter on a 'SKIPPED' status and
  never use CARRIED_FORWARD to answer a skipped question. Overdue and skipped never overlap (required vs optional).
  "Overdue / late / missed / past due OPTIONAL tasks" is a skipped question: an optional task that is past due
  and not done is skipped, never overdue.
  For "skipped in P11" join tasks -> plans -> periods and filter periods.label, as for any per-period count.
- Completed late -> completed_at::date > due_date.
- Due this week / month / today -> compare due_date to CURRENT_DATE using date_trunc / intervals.
- WHO a task is assigned to:
  * tasks.assigned_to_id is NOT used in practice (always NULL). Use tasks.member_assigned_to_id for a
    single assignee, and task_entity_assignees (user_id, done) for entity-wide tasks
    (tasks.assigned_entity_wide = true). A user "has" a task if either matches.
  * An entity-wide assignee stops holding the task the moment they tick their own part (done = true). So for
    anything a person still has to do - open / pending / overdue / due soon / in progress / per-person counts of
    those - count their task_entity_assignees row ONLY WHILE a.done = false:
    EXISTS (SELECT 1 FROM task_entity_assignees AS a WHERE a.task_id = t.id AND a.user_id = u.id AND a.done = false).
    A row with done = true is work that person already finished, never work they owe (it is also not "overdue" for
    them). Without this a person who finished their part is wrongly counted as late.
  * UNASSIGNED = a task with t.member_assigned_to_id IS NULL and no task_entity_assignees row at all. A task is
    never both single-assigned and entity-wide. When the user asks for a breakdown BY PERSON, include the
    Unassigned bucket as its own row with COALESCE(u.name, 'Unassigned') (see the "by team member" example).
  * "Who has the most / least X": return the top 3 rows (ORDER BY ... DESC LIMIT 3), not just one, so a tie is
    visible, and the answer must name EVERY person tied for first.
  * sub_tasks use assigned_to_id and reviewer_id; sub_task_entity_assignees for entity-wide ones.
  * "reviewer"/"approver" -> sub_tasks.reviewer_id; submitted_by_id is who submitted the work.
- Priority values: LOW, MEDIUM, HIGH. Status/priority/role values are UPPERCASE enum labels; match them exactly.
- Current / this period -> the periods row with status IN ('OPEN','IN_PROGRESS') and CURRENT_DATE between
  start_date and end_date (else the latest by start_date). Last / previous period -> the one before it.
  A period is referred to by label (e.g. 'P11', 'Mangsir 2083') or its dates. Match the label EXACTLY and
  case-insensitively: LOWER(p.label) = LOWER('Mangsir 2083'). Never match a period with ILIKE on only the year or
  only one word ('%2083%' matches every 2083 period and would answer with another period's number). If the user
  gives only a month name, ILIKE 'Mangsir%' is fine, but then return one row PER matching period with its label.
  If the named period does not exist, say so and list the available labels; never answer for a different period.
- A COUNT THAT CAN BE ZERO must still return a row. Put the period (and entity / person) in the WHERE and put the
  status / due-date / required conditions INSIDE the aggregate: COUNT(t.id) FILTER (WHERE <conditions>), with
  no GROUP BY on anything but the label. A period that exists but has nothing matching answers 0 - "not found" is
  only for a period, person or entity that is not in the table. (Putting those conditions in the WHERE of a
  grouped count makes a zero disappear as "no rows", which reads as "not found".) Start such a query FROM periods
  and LEFT JOIN plans and tasks (see the Mangsir example): it returns one row for every period that exists - with 0
  when nothing matches - so NO ROW AT ALL means that period does not exist: answer "there is no period called X"
  and list the available labels. NEVER answer 0 for a period that is not in the table.
- Department / region / division / business unit -> entity_groups.type; branch / company / unit -> entities.
- Hours worked / time spent -> SUM(time_entries.duration_minutes) / 60.0.
- Completion rate / progress -> completed tasks divided by all tasks, counting every task that is NOT SKIPPED
  and excluding CARRIED_FORWARD and UPCOMING, multiplied by 100 and rounded. Completed optional tasks count.
- "Total tasks" / "how many tasks" / "all tasks" -> COUNT of tasks WHERE the NOT SKIPPED condition holds AND
  status NOT IN ('CARRIED_FORWARD','UPCOMING') (skipped optional tasks are not added; carried-forward work lives
  on as a new task in the next period so it is not counted twice; UPCOMING tasks are locked future copies, not
  live work).
- Team lead / admin / member -> users.role = TEAM_LEAD / SUPER_ADMIN / TEAM_MEMBER.
""".replace("@@NOT_SKIPPED@@", NOT_SKIPPED)

EXAMPLES: list[tuple[str, str]] = [
    (
        "how many tasks are overdue?",
        "SELECT COUNT(t.id) AS overdue_tasks FROM tasks AS t "
        "WHERE t.due_date < CURRENT_DATE AND t.is_required = true "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS')",
    ),
    (
        "how many tasks are due today?",
        "SELECT COUNT(t.id) AS due_today_tasks FROM tasks AS t "
        "WHERE t.due_date = CURRENT_DATE "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS')",
    ),
    (
        "in P11 Goals - FR pack how many tasks are due today?",
        "SELECT pl.title AS pack, e.name AS entity, u.name AS pack_owner, COUNT(t.id) AS due_today_tasks "
        "FROM plans AS pl JOIN periods AS p ON p.id = pl.period_id JOIN entities AS e ON e.id = pl.entity_id "
        "LEFT JOIN users AS u ON u.id = pl.owner_id "
        "LEFT JOIN tasks AS t ON t.plan_id = pl.id AND t.due_date = CURRENT_DATE "
        "AND t.status NOT IN ('COMPLETED','REJECTED','PENDING_REVIEW','UPCOMING') "
        "WHERE p.label = 'P11' AND pl.title ILIKE '%goals%fr%' "
        "GROUP BY pl.id, pl.title, e.name, u.name ORDER BY pl.title, e.name",
    ),
    (
        "how many tasks are in progress?",
        "SELECT COUNT(t.id) FILTER (WHERE t.due_date IS NULL OR t.due_date > CURRENT_DATE) AS in_progress_tab, "
        "COUNT(t.id) FILTER (WHERE t.due_date <= CURRENT_DATE) AS also_in_progress_but_late_or_due_today "
        "FROM tasks AS t WHERE " + NOT_SKIPPED + " AND t.status = 'IN_PROGRESS'",
    ),
    (
        "how many tasks are not started in P11?",
        "SELECT COUNT(t.id) FILTER (WHERE t.due_date IS NULL OR t.due_date > CURRENT_DATE) AS not_started_tab, "
        "COUNT(t.id) FILTER (WHERE t.due_date <= CURRENT_DATE) AS also_not_started_but_late_or_due_today "
        "FROM tasks AS t JOIN plans AS pl ON pl.id = t.plan_id JOIN periods AS p ON p.id = pl.period_id "
        "WHERE p.label = 'P11' AND " + NOT_SKIPPED + " AND t.status IN ('NOT_STARTED','ASSIGNED')",
    ),
    (
        "how many skipped tasks are there?",
        "SELECT COUNT(t.id) AS skipped_tasks FROM tasks AS t "
        "WHERE t.is_required = false AND t.due_date < CURRENT_DATE "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS')",
    ),
    (
        "how many optional tasks are overdue?",
        "SELECT COUNT(t.id) AS skipped_tasks FROM tasks AS t "
        "WHERE t.is_required = false AND t.due_date < CURRENT_DATE "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS')",
    ),
    (
        "skipped tasks in P11 by entity",
        "SELECT e.name AS entity, COUNT(t.id) AS skipped_tasks FROM tasks AS t "
        "JOIN plans AS pl ON pl.id = t.plan_id JOIN periods AS p ON p.id = pl.period_id "
        "JOIN entities AS e ON e.id = t.entity_id "
        "WHERE p.label = 'P11' AND t.is_required = false AND t.due_date < CURRENT_DATE "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS') GROUP BY e.name ORDER BY skipped_tasks DESC",
    ),
    (
        "who has the most pending work",
        "SELECT u.name, COUNT(DISTINCT t.id) AS open_tasks FROM users AS u "
        "JOIN tasks AS t ON (t.member_assigned_to_id = u.id OR EXISTS "
        "(SELECT 1 FROM task_entity_assignees AS a WHERE a.task_id = t.id AND a.user_id = u.id AND a.done = false)) "
        "WHERE " + NOT_SKIPPED + " "
        "AND t.status IN ('NOT_STARTED','IN_PROGRESS','PENDING_REVIEW','REJECTED','ASSIGNED') "
        "GROUP BY u.name ORDER BY open_tasks DESC LIMIT 5",
    ),
    (
        "how many tasks are overdue by team member in P11",
        "SELECT COALESCE(u.name, 'Unassigned') AS team_member, COUNT(DISTINCT t.id) AS overdue_tasks "
        "FROM tasks AS t JOIN plans AS pl ON pl.id = t.plan_id JOIN periods AS p ON p.id = pl.period_id "
        "LEFT JOIN task_entity_assignees AS a ON a.task_id = t.id AND a.done = false "
        "LEFT JOIN users AS u ON u.id = COALESCE(t.member_assigned_to_id, a.user_id) "
        "WHERE p.label = 'P11' AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS') "
        "AND t.due_date < CURRENT_DATE AND t.is_required = true "
        "GROUP BY COALESCE(u.name, 'Unassigned') ORDER BY overdue_tasks DESC",
    ),
    (
        "how many tasks are overdue in Mangsir 2083",
        "SELECT p.label, COUNT(t.id) FILTER (WHERE t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS') "
        "AND t.due_date < CURRENT_DATE AND t.is_required = true) AS overdue_tasks "
        "FROM periods AS p LEFT JOIN plans AS pl ON pl.period_id = p.id LEFT JOIN tasks AS t ON t.plan_id = pl.id "
        "WHERE LOWER(p.label) = LOWER('Mangsir 2083') GROUP BY p.label",
    ),
    (
        "which tasks are late and who is responsible",
        "SELECT t.title, t.due_date, t.status, COALESCE(u.name, STRING_AGG(DISTINCT au.name, ', ')) AS responsible "
        "FROM tasks AS t LEFT JOIN users AS u ON u.id = t.member_assigned_to_id "
        "LEFT JOIN task_entity_assignees AS a ON a.task_id = t.id AND a.done = false "
        "LEFT JOIN users AS au ON au.id = a.user_id "
        "WHERE t.due_date < CURRENT_DATE AND t.is_required = true "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS') "
        "GROUP BY t.id, t.title, t.due_date, t.status, u.name ORDER BY t.due_date LIMIT 50",
    ),
    (
        "task completion percentage for each period",
        "SELECT p.label, COUNT(t.id) AS total_tasks, "
        "SUM(CASE WHEN t.status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_tasks, "
        "ROUND(100.0 * SUM(CASE WHEN t.status = 'COMPLETED' THEN 1 ELSE 0 END) / COUNT(t.id), 1) AS completion_pct "
        "FROM periods AS p JOIN plans AS pl ON pl.period_id = p.id JOIN tasks AS t ON t.plan_id = pl.id "
        "WHERE t.status NOT IN ('CARRIED_FORWARD','UPCOMING') AND " + NOT_SKIPPED + " GROUP BY p.label, p.start_date ORDER BY p.start_date",
    ),
    (
        "tasks finished after the deadline this month",
        "SELECT t.title, t.due_date, t.completed_at FROM tasks AS t "
        "WHERE t.status = 'COMPLETED' AND t.completed_at::date > t.due_date "
        "AND t.completed_at >= date_trunc('month', CURRENT_DATE) ORDER BY t.completed_at DESC",
    ),
    (
        "show rejected tasks of ram with the reason",
        "SELECT t.title, u.name AS assignee, t.rejection_note FROM tasks AS t "
        "JOIN users AS u ON u.id = t.member_assigned_to_id "
        "WHERE t.status = 'REJECTED' AND u.name ILIKE '%ram%' ORDER BY t.updated_at DESC",
    ),
    (
        "hours logged by each person last month",
        "SELECT u.name, ROUND(SUM(te.duration_minutes) / 60.0, 1) AS hours FROM time_entries AS te "
        "JOIN users AS u ON u.id = te.user_id "
        "WHERE te.entry_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' "
        "AND te.entry_date < date_trunc('month', CURRENT_DATE) GROUP BY u.name ORDER BY hours DESC",
    ),
    (
        "what is the current period and how many tasks are in it",
        "SELECT p.label, p.start_date, p.end_date, p.status, COUNT(t.id) AS tasks FROM periods AS p "
        "LEFT JOIN plans AS pl ON pl.period_id = p.id LEFT JOIN tasks AS t ON t.plan_id = pl.id AND " + NOT_SKIPPED + " "
        "WHERE p.status IN ('OPEN','IN_PROGRESS') GROUP BY p.label, p.start_date, p.end_date, p.status "
        "ORDER BY p.start_date DESC LIMIT 1",
    ),
    (
        "tasks by entity and status",
        "SELECT e.name AS entity, t.status, COUNT(t.id) AS tasks FROM tasks AS t "
        "JOIN entities AS e ON e.id = t.entity_id WHERE " + NOT_SKIPPED + " "
        "GROUP BY e.name, t.status ORDER BY e.name, tasks DESC",
    ),
    (
        "how many tasks are there in total and how many are completed?",
        "SELECT COUNT(t.id) AS total_tasks, "
        "SUM(CASE WHEN t.status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_tasks FROM tasks AS t "
        "WHERE " + NOT_SKIPPED + " AND t.status NOT IN ('CARRIED_FORWARD','UPCOMING')",
    ),
    (
        "how many optional tasks are there, how many are done and how many were skipped?",
        "SELECT COUNT(t.id) AS optional_tasks, "
        "COUNT(t.id) FILTER (WHERE t.status = 'COMPLETED') AS completed_optional_tasks, "
        "COUNT(t.id) FILTER (WHERE t.due_date < CURRENT_DATE "
        "AND t.status IN ('NOT_STARTED','ASSIGNED','IN_PROGRESS')) AS skipped_tasks "
        "FROM tasks AS t WHERE t.is_required = false AND t.status NOT IN ('CARRIED_FORWARD','UPCOMING')",
    ),
]
