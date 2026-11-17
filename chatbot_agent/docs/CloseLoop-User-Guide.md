# CloseLoop User Guide - How-To Steps (for the Close Bhai knowledge base)

Document type: Step-by-step usage guide. It complements the CloseLoop System Reference (which explains what things are) by explaining how to do things.
Every section is self-contained. Button and menu names are written exactly as they appear in the application. Each section says which role can do it.

## 0. Roles, menus and the order of setup

CloseLoop has four roles. What a person sees in the top menu depends on the role:

- Platform Owner: Tenants, Holidays.
- Super Admin: Dashboard, Organization, Periods, Close Packs, Carry Forward, Users, Reports.
- Team Lead: Dashboard, Carry Forward, HRMS Integration, Users, Reports.
- Team Member: Overview, HRMS Integration.

Normal order of setup and use:
1. Platform Owner creates the tenant (the company workspace) and its first Super Admin.
2. Super Admin creates Entity Groups, then Entities (an entity must belong to at least one group).
3. Super Admin invites Team Leads and Team Members and sets their entities.
4. Super Admin sets up Periods.
5. Super Admin builds a Close Pack (the reusable template of tasks).
6. Super Admin generates packs for one or more entities and assigns each pack to a Team Lead.
7. The Team Lead adds extra tasks if needed and assigns tasks to Team Members.
8. Team Members do the work and submit it for review.
9. The Team Lead approves or rejects.
10. When the period closes, unfinished work can be carried forward into the next period.

Words used in this guide:
- Close Pack (also called a pack template): the reusable template that lists tasks and sub-tasks.
- Pack (also called a plan): a Close Pack generated for one period and one entity. This is what people open from their dashboard. Its title is "<Close Pack name> - <period label>".
- Entity: the unit that owns a pack (a company, branch or unit).
- Entity Group: a parent grouping of entities (for example a region).
- Tenant: one customer organisation with its own isolated workspace.

## 1. How to create a tenant (Platform Owner)

1. Log in as the Platform Owner and open **Tenants**.
2. Use the button at the top right of the page to open the **Create tenant** dialog.
3. Fill in Tenant name and Plan limit.
4. Under "Super Admin (initial user)" enter the Super Admin's Name, Email and Password.
5. Click **Create tenant**.
6. A dialog titled "API credentials" shows the tenant's client_id and client_secret. Copy them and click **Done**.

Other actions on a tenant row: the key button regenerates the tenant's API client_secret, **Suspend** / **Activate** switches the tenant off or on, **Delete** removes it.
Each tenant gets its own web address segment (its slug): after login a tenant's pages live under /<tenant slug>/.
A tenant cannot see any other tenant's data. The Platform Owner manages tenants and holidays but does not see a tenant's business data.

## 2. How to create an Entity Group (Super Admin)

Entity Groups organise entities, for example Region > Country > City. They can be nested to any depth.

1. Open **Organization**. The left panel is **Entity Groups**.
2. Click **New Group**. (If a group is already selected, the button reads **Add Child** and creates a group inside it.)
3. In the **Create Entity Group** dialog fill in:
   - Name (required), for example EMEA or Europe.
   - Type: Region, Division, Department, Sub-Region, Business Unit or Custom.
   - Parent Group: choose a parent, or "Top level (no parent)".
   - Description (optional).
4. Save.

You can search groups, filter by Active only / Inactive only / All, filter by type, and expand or collapse the tree. Editing a group re-checks that no loop is created. Deleting a group asks for confirmation and cannot be undone.

## 3. How to create an Entity (Super Admin)

An entity is the unit that gets its own pack for each period. Every entity must belong to at least one Entity Group, so create a group first.

1. Open **Organization**. The right panel is **Entities** (it shows the entities of the selected group, or all).
2. Click **New Entity**.
3. In the **Create Entity** dialog fill in:
   - Entity Name (required).
   - Groups (required): tick every group the entity belongs to. An entity can be in several groups.
   - Primary Group: used for hierarchy display; it defaults to the first group chosen.
   - Description (optional).
4. Save.

To change many entities at once, tick them and click **Bulk assign (N)**. Choose **Add to groups**, **Replace memberships** or **Remove from groups** and pick the groups. The primary group of an entity cannot be removed.
Deleting an entity also deletes all packs that belong to it, and the app asks you to confirm first.

## 4. How to add users and give them access to entities (Super Admin, Team Lead)

Who can add whom:
- Super Admin can add Team Leads and Team Members.
- Team Lead can add Team Members only.

Steps:
1. Open **Users** and click **+ Invite user**.
2. Fill in Name, Email, Password (you choose it and tell the person), Role, Title (optional) and Primary Entity (required, every user needs one).
3. Click **Invite**. The account exists immediately.

Giving a person access to more than their primary entity (Super Admin only):
1. On the Users page find the person and click **Assignments**.
2. Tick the extra entities and entity groups they should work in, then save.

The **Details** button edits a person's title and primary entity. The trash button (Super Admin only) deletes a user.
A person's entities and groups decide whose work they can see. For example, "Whole Entity" assignment reaches every Team Member who belongs to that pack's entity.

## 5. How to set up periods (Super Admin)

A pack is always generated for one period, so periods must exist first. Open **Periods**.

Option A - generate a schedule:
1. Click **Configure & Generate Schedule**.
2. Choose the Cadence: Fixed weeks, English month, English quarter, English half-year, English year, Nepali month (BS), Nepali quarter (BS fiscal), Nepali half-year (BS fiscal) or Nepali year (BS fiscal).
3. Set the Start date. For Fixed weeks also set the Start day of week and Duration (weeks).
4. Turn on or off "Auto-generate next period on close" and "Enable carry-forward of open items".
5. Set Number of periods to generate. Use **Preview** to check the list.
6. Click **Save & Generate**. Warning: this REPLACES all existing periods of the tenant.

Option B - import periods:
1. Click **Import Periods**.
2. Click **Load standard periods** to load the standard P1 to P13 schedule (about four weeks each), or upload your own file with a label, start date and end date for each period.

Closing a period: on the Current period card click **Close this period**. A "Review & Carry-Forward" dialog lists unfinished work. After you confirm, the next period opens, its packs are generated, and carry-forward groups are created. The scheduler can also do this automatically when a period expires.
Task due dates always stay inside their own period.

## 6. How to build a Close Pack (Super Admin)

A Close Pack is the reusable template. Packs for entities are generated from it later.

Create the pack:
1. Open **Close Packs** and click **+ New Pack**.
2. In the **New Pack** dialog fill in:
   - Pack Name (required), for example "Quarterly Tax Pack".
   - Calendar (required): English (Gregorian / AD) or Nepali (Bikram Sambat / BS). It controls every period, date, task schedule and report of the pack.
   - Status: active or inactive (only active packs can be generated).
   - Description (optional).
3. Save. The pack appears in the list on the left with its calendar badge and version (v1).

Add tasks to the template, one at a time:
1. Select the pack in the list. Its page shows the Template Structure.
2. Click **+ Add task template**.
3. In the **Add Task Template** dialog fill in:
   - Name (for example "Bank Reconciliation") and Description (optional).
   - Frequency: Once per period, Daily, Weekly, Monthly, Quarterly, Semi-annual, Annual or Custom.
   - For "Once per period": the due day, for example day 10, counted "from period start" or "from period end".
   - For a recurring task: the First Due Date, an optional End date, and for Weekly the weekdays. "Due Days" sets how many days after each occurrence it is due.
   - Due Time (optional).
   - Priority: Low, Medium or High (default Medium).
   - Tags (optional).
   - Optional: only available on Daily tasks. An uncompleted occurrence of an optional task is never marked overdue.
4. Save.
5. To add a sub-task, open the task block and click the add sub-task button, which opens **Add Sub-task Template**. A sub-task has its own schedule but can never be due after its parent task.
6. Use the edit and delete buttons on a task or sub-task to change it.

Add many tasks at once from a spreadsheet:
1. Click **Download template (.xlsx)** (or **.csv**) and fill it in. The sheet is called "Tasks & Subtasks".
2. Each row needs Row Type (Task or Subtask), Title, and a Task Key (a short code such as T1 for task rows) or a Parent Task Key (the Task Key of its task, for sub-task rows). Everything else is optional: Description, Priority, Reviewer Required (Yes/No), Tags, Frequency (ONE_OFF, ONCE_PER_PERIOD, DAILY, WEEKLY, MONTHLY, QUARTERLY, SEMI_ANNUAL, ANNUAL), Interval Count, Days Of Week, Recurrence Start, Recurrence End, Due Day Of Period, Due Anchor (PERIOD_START or PERIOD_END), Due Time and Order.
3. Click **Upload template** and choose the .xlsx, .xlsm or .csv file.

Other pack actions:
- **Edit** changes the name, calendar, status or description.
- **Sync plans** re-applies the template to every not-yet-started task in packs of open periods. This also happens automatically after every template edit.
- Every edit creates a new version. A generated pack remembers the version it came from.
- The trash button deletes the pack and everything generated from it, after confirmation.

## 7. How to send a Close Pack into an entity (generate packs) and assign it to a Team Lead (Super Admin)

Sending a Close Pack into entities is called generating plans. The result is one pack per entity for the chosen period.

1. Make sure the Close Pack is active, the period exists, and the entities exist.
2. Open the **Dashboard** and click **Generate Plans**.
3. In the **Generate Plans** dialog:
   - Close Pack: choose the pack (only active packs are listed).
   - Period: choose the period (the current one is preselected).
   - Assign whole pack to a Team Lead (optional): choose the Team Lead who will manage the pack.
   - Entities: tick one or more entities, or use **Select all**. At least one entity is required.
4. Confirm.

What happens:
- One pack is created for each selected entity, titled "<Close Pack name> - <period label>". The tasks come from the template and start unassigned.
- Generating the same combination again does not create duplicates.
- A pack without a Team Lead can be viewed by the people in its scope but edited only by a Super Admin until it is assigned. Once assigned, only the owning Team Lead or a Super Admin can edit it; other Team Leads see it as view-only.

To assign or change the Team Lead later: open the pack from the Super Admin **Dashboard** (list "All Plans") and use "Assign whole pack to".
Note: the pack title does not include the entity name. If one Close Pack is generated for several entities, those packs have the same title and are told apart by the entity shown on the pack card. If you want the entity in the title, put it in the Close Pack name when you create the template.

## 8. How to create a task inside a pack / entity (Team Lead who owns the pack, or Super Admin)

Tasks that come from the Close Pack template appear automatically. To add an extra task that exists only in one pack (one entity and one period), add a custom task. It does not change the template.

1. Open the **Dashboard** and click the pack card (the pack of the entity you want).
2. Check that you are the owner. If the page says "View only", you cannot add tasks.
3. Click **+ Add Task**.
4. In the **Add Task** dialog fill in:
   - Task Name (required).
   - Assignment (optional): **Specific people** (pick one or more Team Members) or **Whole Entity** (every Team Member of this pack's entity).
   - Due Date (must fall inside the pack's running period; for a recurring task it is the First Due Date) and Due Time (optional).
   - Priority: High, Medium or Low.
   - Repeat (optional): a recurring custom task repeats across periods with the same rules as a Close Pack task. Daily tasks can be marked Optional.
   - Details (optional) and Tags (optional).
   - Subtasks (optional): click **+ Add Subtask**. When a task has sub-tasks, assign each sub-task to a member instead of assigning the task itself.
5. Click **Add Task** at the bottom of the dialog to save. The task appears in the pack's list.

Rules:
- Tasks cannot be added to a closed pack.
- Only custom tasks you added can be deleted. Tasks that came from the template cannot be deleted from the pack (change the template instead).
- A sub-task cannot be due after its parent task.
- Team Members cannot create or restructure tasks.

How to put the same task in every entity: add it to the Close Pack template (section 6). Packs of open periods for every entity pick it up automatically. There is no feature literally named "global task".

## 9. How to assign a task (Team Lead who owns the pack, Super Admin)

In a pack's task list, each task row has a three-dot menu with these options.

Assign to specific people:
1. Open the three-dot menu on the task and choose **Assign to specific people**.
2. Tick the Team Members.
3. If the task is part of a recurring series, tick "Apply to all N occurrences of this recurring task" to assign every occurrence at once.
4. Confirm.

Assign to the whole entity:
1. Open the three-dot menu and choose **Assign to whole entity**.
2. Confirm with **Yes, assign to everyone**. Every Team Member of the pack's entity receives the task.

How shared tasks behave: when a task has more than one assignee (specific people or whole entity), each person marks their own part done with their own time and notes, and the task moves to review once everyone has finished. For an optional shared task, the first person to finish completes it for the others.

Other assignment facts:
- The "Assign to ..." options appear for tasks without sub-tasks that are not completed and not Upcoming. For a task with sub-tasks use **Edit subtasks** and assign each sub-task.
- Choosing a person when creating a task (section 8) assigns it at the same time.
- Bulk assignment: tick several task rows. A bar appears with **Assign**, **Unassign**, **Approve** and **Reject**. For recurring tasks choose "Only the N selected tasks" or "Every occurrence".
- A Super Admin can use the same menus from the pack page opened from the Super Admin Dashboard.

Task statuses: Not Started, Assigned, In Progress, Pending Review, Completed, Rejected, Carried Forward, Upcoming. The list has tabs: All Tasks, Due Today, Overdue, Skipped, Not Started, In Progress, Completed, Pending Review, Rejected, Upcoming.
Upcoming tasks belong to a future period and cannot be edited until that period becomes current.

## 10. How a Team Member does and submits work (Team Member)

1. Open **Overview**. Each pack card shows your progress. Click a pack to see your tasks.
2. Click a task to open its side panel. There you see the description, tags, due date, comments and attachments.
3. You can add comments and upload evidence or attachments in the panel.
4. When the work is finished, use the **Completion** control ("Submit for review") in the panel.
5. In the **Submit for Review** dialog enter Time spent (hours and minutes) and Notes, then click **Submit for Review**. The status becomes Pending Review.
6. If you submitted by mistake and the review has not happened yet, switch the control off to pull the task back to In Progress.
7. For a shared task the dialog is called **Mark Your Part Done** and the button is **Mark My Part Done**. For shared tasks, and for a Team Lead's own solo tasks, time and notes are required.

If the task is rejected you see the reason, do the rework and submit it again.

## 11. How a Team Lead reviews work (Team Lead)

1. Open the pack from the **Dashboard**. Tasks waiting for review show under the **Pending Review** tab.
2. Open the review, titled **Review Task** (or Subtask). Read the assignee's time and notes.
3. Click **Approve** to complete it, or write a Review Note and click **Reject**. The note is required to reject and is sent back to the assignee.
4. To handle many at once, tick the rows and use **Approve** or **Reject** in the bulk bar (rejecting needs one shared note).

Only the pack's owning Team Lead reviews the pack's tasks. Packs owned by other Team Leads are read-only.

## 12. Carry-forward (Super Admin, Team Lead)

When a period closes, unfinished eligible work is collected into a carry-forward group with status Pending.
1. Open **Carry Forward**. Filter by Pending, Approved or Rejected.
2. Click **Approve...** to copy the items into the next period, or **Reject...** to drop them. Add an optional note in the dialog.
3. Approved items are copied into the next period and the originals are marked Carried Forward.
Carry-forward can be turned on or off in the period schedule settings.

## 13. Other features

Deadlines: the Deadlines page lists every open task that is due, soonest first. A Team Member's Overview also has a deadlines panel.

Optional and skipped tasks: an optional task whose due date has passed without being started is "Skipped". It is listed under the Skipped tab and is not counted in totals or progress, and it is never overdue. Optional work that is completed or in review counts normally.

Weekends and holidays: Saturday and Sunday are non-working days, and so is every holiday recorded in the system. A due date generated by CloseLoop that lands on a non-working day moves to the next working day (or the previous one if the next would leave the period). Dates you pick by hand cannot be non-working days; the date pickers do not allow them.
Holidays are one list shared by all tenants. Only the Platform Owner changes it: open **Holidays**, add a holiday with its date and name, or paste many lines in the form "YYYY-MM-DD, Name". A new holiday applies to dates created afterwards; it does not move tasks that already exist.

Time Log: on the Overview, the "Time Logged Today" card shows progress toward the daily target of 8 hours. Click **View history** to open the Time Log page with your time by day, week and month. A Team Lead can open a member's history from the Users page with the **Time log** button.

HRMS Integration: open **HRMS Integration**, choose Month, Year and Project. CloseLoop lists every day of the month with the tasks you submitted, matched to HRMS activities. Review the list, optionally set "Only up to" a date, then send it. The "Submissions for <month>" list shows what was sent, and you can mark a submission as "It arrived" or "It didn't arrive". If everything you submitted in the month has already been sent, the page says so.

Reports: Team Leads and Super Admins open **Reports** for the reports of their scope. Live status of the current period is under Reports > Current Period Status.
Report Studio: describe the report you want in plain words (for example "Compare this period with the previous period, as an Excel file") and get KPIs, charts, key findings and recommended actions. Results can be viewed as a dashboard or downloaded as PDF, Excel or CSV.

Team Members page (Team Lead): shows each team member's workload for the period and flags members over 80% load.

Notifications: the bell in the top bar shows your alerts, for example when a pack is assigned to you. Unread alerts can be marked as read.

Profile and password: open **My Profile** to change your name, email or password. Leave the password boxes blank to keep your current password; to set a new one you must enter your current password and confirm the new one. If you cannot sign in, use "Forgot password? Reset it here" on the login page.

Close Bhai (the chat assistant): the floating button at the bottom right opens the assistant. It answers questions about your own organisation's tasks, packs and periods and about how to use CloseLoop. It is read-only: it explains and reports, it does not create or change anything. Use **New conversation** to start fresh and **My documents** / **Add documents** to add reference files for your organisation.

## 14. Common questions

Why is a pack "View only" for me, or why is there no + Add Task button?
Only the Team Lead who owns the pack (or a Super Admin) can edit it. A pack that has not been assigned to a Team Lead is view-only for everyone. The pack must also be open, not closed.

Why do some pack names include an entity and others do not?
A pack title is the Close Pack template name plus the period label. CloseLoop never adds the entity itself. If a template is generated for several entities, all those packs share the same title and are told apart by the entity on the card. When someone wants the entity visible in the title, they put it in the template name.

Why did a pack keep its old name after I renamed the Close Pack?
Renaming a Close Pack changes the template. Packs already generated keep the title they had; only packs generated afterwards use the new name.

Why does my task's due date fall on a weekday and not on the date I expected?
Generated dates never land on Saturday, Sunday or a recorded holiday; they move to a working day.

Why can't I edit an Upcoming task?
Upcoming tasks belong to a future period and unlock when that period becomes current.

Can I delete a task that came from the template?
No. Only custom tasks added with + Add Task can be deleted. To remove a template task for the future, a Super Admin deletes it from the Close Pack.

Can the chatbot create a task for me?
No. Close Bhai only explains the steps and reports data. You create tasks in the application as described in sections 6 to 9.
