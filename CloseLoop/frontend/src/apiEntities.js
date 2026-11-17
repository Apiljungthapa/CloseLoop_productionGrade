// Centralized API client for CloseLoop.
// Every method returns the raw axios response — call sites use `.data`.
import api from "./api.js";

// ===== AUTH / SELF-SERVICE PROFILE =====
export const authAPI = {
  me: () => api.get("/auth/me"),
  // { name?, email?, current_password?, new_password? } — current_password required to change password
  updateMe: (data) => api.patch("/auth/me", data),
};

// ===== TENANTS (PLATFORM_OWNER only) =====
export const tenantAPI = {
  list: () => api.get("/tenants"),
  // payload: { name, status?, plan_limit?, super_admin?: { name, email, password } }
  create: (data) => api.post("/tenants", data),
  update: (id, data) => api.patch(`/tenants/${id}`, data),
  delete: (id) => api.delete(`/tenants/${id}`),
};

// ===== USERS =====
export const userAPI = {
  list: () => api.get("/users"),
  // { email, name, role, password, title?, primary_entity_id? }
  create: (data) => api.post("/users", data),
  // { name?, role?, title?, primary_entity_id?, password? } — title changes
  // require SUPER_ADMIN, same as role changes.
  update: (id, data) => api.patch(`/users/${id}`, data),
  delete: (id) => api.delete(`/users/${id}`),
  // combined assignments view (primary + secondary entities + groups)
  assignments: (id) => api.get(`/users/${id}/assignments`),
};

// ===== USER-ENTITY ASSIGNMENTS =====
export const assignmentAPI = {
  listByUser: (userId) => api.get(`/users/${userId}/entity-assignments`),
  // { user_id, entity_id }
  create: (data) => api.post("/users/entity-assignments", data),
  // { user_id, entity_ids, mode: 'add'|'replace'|'remove' }
  bulk: (data) => api.post("/users/entity-assignments/bulk", data),
  delete: (assignmentId) =>
    api.delete(`/users/entity-assignments/${assignmentId}`),
};

// ===== USER-GROUP ASSIGNMENTS =====
export const groupAssignmentAPI = {
  listByUser: (userId) => api.get(`/users/${userId}/group-assignments`),
  // { user_id, group_id }
  create: (data) => api.post("/users/group-assignments", data),
  // { user_id, group_ids, mode }
  bulk: (data) => api.post("/users/group-assignments/bulk", data),
  delete: (assignmentId) =>
    api.delete(`/users/group-assignments/${assignmentId}`),
};

// ===== ENTITY GROUPS =====
export const entityGroupAPI = {
  // params: { search?, active?, type?, parent_id? }
  list: (params = {}) => api.get("/entity-groups", { params }),
  // params: { include_inactive? }
  tree: (params = {}) => api.get("/entity-groups/tree", { params }),
  // { name, type?, parent_id?, description?, is_active? }
  create: (data) => api.post("/entity-groups", data),
  update: (id, data) => api.patch(`/entity-groups/${id}`, data),
  activate: (id) => api.post(`/entity-groups/${id}/activate`),
  deactivate: (id) => api.post(`/entity-groups/${id}/deactivate`),
  delete: (id) => api.delete(`/entity-groups/${id}`),
};

// ===== ENTITIES =====
export const entityAPI = {
  // params: { search?, group_id? }
  list: (params = {}) => api.get("/entities", { params }),
  // { name, group_ids, primary_group_id?, description? }
  create: (data) => api.post("/entities", data),
  update: (id, data) => api.patch(`/entities/${id}`, data),
  // { entity_ids, group_ids, mode: 'add'|'replace'|'remove' }
  bulkAssignGroups: (data) => api.post("/entities/bulk-assign-groups", data),
  delete: (id) => api.delete(`/entities/${id}`),
};

// ===== PERIODS =====
export const periodAPI = {
  list: () => api.get("/periods"),
  current: () => api.get("/periods/current"),
  active: () => api.get("/periods/active"),
  initialize: () => api.post("/periods/initialize"),
  close: (id) => api.post(`/periods/${id}/close`),
  review: (id) => api.get(`/periods/${id}/review`),
  // { cadence: 'FIXED_WEEKS'|'CALENDAR_MONTH'|'CALENDAR_QUARTER', start_date,
  //   period_count, duration_weeks? } — universal schedule generator, works
  // for any tenant/cadence (not just the fixed-week retail pattern).
  generateSchedule: (data) => api.post("/periods/generate", data),
  getConfiguration: () => api.get("/periods/configuration"),
  // { cadence, start_day_of_week, duration_weeks, auto_generate, carry_forward_enabled }
  updateConfiguration: (data) => api.put("/periods/configuration", data),
  // payload: [ { label, start_date, end_date } ]
  importPeriods: (data) => api.post("/periods/import", data),
  // formData: single 'file' field — .xlsx/.xlsm/.csv with Label, Start Date, End Date columns
  importPeriodsFile: (formData) =>
    api.post("/periods/import-file", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }),
  // Dry-run of generateSchedule — same payload shape, nothing persisted.
  // Returns { cadence, periods: [{ label, start_date, end_date, bs_start_label,
  // bs_end_label, total_days, working_days, closing_date, holidays }] }.
  preview: (data) => api.post("/periods/preview", data),
  // .ics calendar-sync feed of the tenant's period schedule + Nepal holidays.
  exportIcs: () => api.get("/periods/export.ics", { responseType: "blob" }),
};

// ===== HOLIDAYS =====
// Days the platform is closed on top of Saturday/Sunday — global, shared by
// every tenant. Any signed-in user can list them (the date pickers and overdue
// labels need them); only the PLATFORM_OWNER can change them.
export const holidayAPI = {
  // Optional { from, to } ('YYYY-MM-DD') limits the range.
  list: (params) => api.get("/holidays", { params }),
  // { holiday_date, name, category?: 'ALL' | 'GENDER' | 'RELIGION', audience?, remarks?,
  // bs_date? } — several holidays can share a date; 409 only for the same date + name.
  // A GENDER / RELIGION holiday needs `audience` (e.g. 'Female'). Every recorded
  // holiday is skipped for everyone's task dates (`non_working` in the response).
  create: (data) => api.post("/holidays", data),
  // { holidays: [{ holiday_date, name, ...same fields }], source?: 'MANUAL' | 'API' } —
  // add or update by date + name, so re-sending a list changes nothing; an 'API'
  // batch never overwrites or duplicates a MANUAL holiday.
  // -> { created, updated, skipped }
  bulk: (data) => api.post("/holidays/bulk", data),
  remove: (id) => api.delete(`/holidays/${id}`),
};

// ===== NEPALI (BS) CALENDAR =====
export const calendarAPI = {
  // Today in both AD and BS, plus the current Nepali fiscal year.
  today: () => api.get("/calendar/today"),
  // A Sun-first AD month grid annotated with BS date/weekend/holiday/period
  // per day — powers the Period Calendar widget. month is 1-12.
  month: (year, month) => api.get("/calendar/month", { params: { year, month } }),
  // A BS fiscal year's bounds + its 12 months / 4 quarters / 2 halves,
  // each with AD start/end — feeds the BS-cadence generator UI.
  fiscalYear: (startYear) => api.get("/calendar/fiscal-year", { params: { start_year: startYear } }),
  // One AD date rendered on the BS calendar — the Nepali date-picker's
  // closed-field label. adDate is an ISO "YYYY-MM-DD" string.
  convert: (adDate) => api.get("/calendar/convert", { params: { ad_date: adDate } }),
  // Many AD dates rendered on the BS calendar in one call — { labels: {
  // 'YYYY-MM-DD': bsLabel } }. Powers BS-mode display of every date across
  // a pack's task table/detail panel/assignment sections without one
  // request per date. Max 500 dates per call.
  convertBatch: (dates) => api.post("/calendar/convert-batch", { dates }),
  // A whole Bikram Sambat month laid out for a Sun-first picker grid; each
  // day carries its real AD date. Navigation (prev/next) is by BS month.
  bsMonth: (bsYear, bsMonth) =>
    api.get("/calendar/bs-month", { params: { bs_year: bsYear, bs_month: bsMonth } }),
};

// ===== CLOSE PACKS =====
export const closePackAPI = {
  list: () => api.get("/close-packs"),
  get: (id) => api.get(`/close-packs/${id}`), // returns ClosePackTreeOut
  // { name, description?, calendar_type: 'AD'|'BS', active? }
  create: (data) => api.post("/close-packs", data),
  update: (id, data) => api.patch(`/close-packs/${id}`, data),
  delete: (id) => api.delete(`/close-packs/${id}`),
  // templates — data may include { title, description?, due_day_of_period,
  // active_from?, active_until?, due_time?, reviewer_required?, order? }
  addTaskTemplate: (packId, data) =>
    api.post(`/close-packs/${packId}/task-templates`, data),
  updateTaskTemplate: (templateId, data) =>
    api.patch(`/close-packs/task-templates/${templateId}`, data),
  deleteTaskTemplate: (templateId) =>
    api.delete(`/close-packs/task-templates/${templateId}`),
  addSubTaskTemplate: (taskTemplateId, data) =>
    api.post(
      `/close-packs/task-templates/${taskTemplateId}/sub-task-templates`,
      data,
    ),
  updateSubTaskTemplate: (templateId, data) =>
    api.patch(`/close-packs/sub-task-templates/${templateId}`, data),
  deleteSubTaskTemplate: (templateId) =>
    api.delete(`/close-packs/sub-task-templates/${templateId}`),
  // formData: single 'file' field — .xlsx/.xlsm built from the "Download
  // template" button. Additive (appends to the pack); returns the full
  // updated ClosePackTreeOut on success, or a 400 listing every bad row.
  importTaskTemplatesFile: (packId, formData) =>
    api.post(`/close-packs/${packId}/task-templates/import-file`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    }),

  // Force-propagate this pack's current template tree onto every not-yet-
  // started task/sub-task in a still-open plan (retitles, re-dates, adds
  // missing ones) — normally automatic on every template edit; this is the
  // on-demand re-run, e.g. after a title-format fix that existing rows
  // should also pick up. Returns { plans, tasks_added, tasks_removed, tasks_updated }.
  syncPlans: (packId) => api.post(`/close-packs/${packId}/sync-plans`),

  // Full state of every task/subtask this pack generated for the currently
  // running period — status, due date, assignee, completion notes.
  currentPeriodReport: (packId) => api.get(`/close-packs/${packId}/current-period-report`),
};

// ===== PLANS =====
export const planAPI = {
  list: (params = {}) => api.get("/plans", { params }),
  get: (id) => api.get(`/plans/${id}`),
  // { close_pack_id, period_id, entity_ids: [...], owner_id? }
  generate: (data) => api.post("/plans/generate", data),
  delete: (id) => api.delete(`/plans/${id}`),
  // Admin-only: assign/reassign the whole pack to a Team Lead (or null to clear).
  assignOwner: (id, ownerId) =>
    api.patch(`/plans/${id}/owner`, { owner_id: ownerId }),
  // Read-only: what the pack templates WOULD generate for `period_id` — no
  // rows created. Empty array once that period already has real plans.
  preview: (periodId) => api.get("/plans/preview", { params: { period_id: periodId } }),
};

// ===== TASKS =====
export const taskAPI = {
  list: (params = {}) => api.get("/tasks", { params }),
  get: (id) => api.get(`/tasks/${id}`),
  getDetail: (id) => api.get(`/tasks/${id}/detail`),
  // { title?, description?, assigned_to_id?, member_assigned_to_id?, due_date?, status? }
  create: (data) => api.post("/tasks", data),
  update: (id, data) => api.patch(`/tasks/${id}`, data),
  delete: (id) => api.delete(`/tasks/${id}`),
  // Team-Member completion action for a LEAF task (no sub-tasks) — pushes
  // it to PENDING_REVIEW for the Lead to approve/reject.
  submitForReview: (id, data = {}) =>
    api.post(`/tasks/${id}/submit-for-review`, data),
  // { decision: 'APPROVE' | 'REJECT', note? }  <- Team Lead approval
  review: (id, data) => api.post(`/tasks/${id}/review`, data),
  // Whole-entity assignment (Lead/Admin) — fans a LEAF task out to every
  // Team Member scoped to the task's entity.
  assignEntity: (id) => api.post(`/tasks/${id}/assign-entity`),
  // Same multi-assignee mechanics, but to a hand-picked set of user ids
  // rather than everyone on the entity. Calling again with a different
  // list reassigns (allowed any time the task isn't COMPLETED).
  // `dueDates` optionally gives one or more of those people their own
  // deadline ({ userId: 'YYYY-MM-DD' | null }), capped at the task's own
  // due date by the backend.
  assignMulti: (id, userIds, dueDates) => api.post(`/tasks/${id}/assign-multi`, {
    user_ids: userIds, due_dates: dueDates || undefined,
  }),
  // Team Members eligible to be assigned this task — scoped to the task's
  // entity, with a tenant-wide fallback. Drives the "Assign from team"
  // picker in the task detail panel.
  team: (id) => api.get(`/tasks/${id}/team`),
  // Assign EVERY occurrence of a recurring task (same series, same plan)
  // to the same person / people in one call. Occurrences already
  // completed or pending review are left untouched. Returns
  // { updated, skipped, task }.
  assignSeries: (id, userIds) =>
    api.post(`/tasks/${id}/assign-series`, { user_ids: userIds }),
  // Team Lead bulk actions. Both return { updated, skipped: [{ task_id,
  // title, reason }] } — a task that can't be actioned is skipped, never
  // fails the batch. bulkAssign: empty userIds = unassign; scope is
  // 'occurrence' (just these rows) or 'series' (each recurring task's
  // whole series, once per series). bulkReview: decision APPROVE | REJECT
  // (REJECT needs a note).
  bulkAssign: (taskIds, userIds, scope = 'occurrence') =>
    api.post('/tasks/bulk-assign', { task_ids: taskIds, user_ids: userIds, scope }),
  bulkReview: (taskIds, decision, note) =>
    api.post('/tasks/bulk-review', { task_ids: taskIds, decision, note: note || undefined }),
  // Completion action for a multi-assignee task (whole-entity or a
  // hand-picked multi-assign) — marks only the caller's own row; the
  // task auto-submits once everyone is done. Time Taken and Notes are
  // required: { completion_notes, completion_time_minutes }.
  markDoneEntity: (id, data) => api.post(`/tasks/${id}/mark-done-entity`, data),

  // ----- Discussion thread (task detail side panel) -----
  listComments: (id) => api.get(`/tasks/${id}/comments`),
  // { body }
  addComment: (id, data) => api.post(`/tasks/${id}/comments`, data),
  deleteComment: (id, commentId) =>
    api.delete(`/tasks/${id}/comments/${commentId}`),

  // ----- Attachments -----
  listAttachments: (id) => api.get(`/tasks/${id}/attachments`),
  uploadAttachment: (id, file, onUploadProgress) => {
    const form = new FormData();
    form.append("file", file);
    // Do NOT set Content-Type here — the browser must add the multipart
    // boundary itself, or the server rejects it ("Missing boundary").
    return api.post(`/tasks/${id}/attachments`, form, { onUploadProgress });
  },
  downloadAttachment: (id, attachmentId) =>
    api.get(`/tasks/${id}/attachments/${attachmentId}/download`, {
      responseType: "blob",
    }),
  deleteAttachment: (id, attachmentId) =>
    api.delete(`/tasks/${id}/attachments/${attachmentId}`),
};

// Shared attachment limits — mirror backend attachment_storage.py so the
// UI can reject a bad file before the round-trip.
export const ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;
export const ATTACHMENT_ALLOWED_EXT = [
  ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv",
  ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp",
  ".txt", ".zip", ".msg", ".eml",
];

// ===== SUBTASKS =====
export const subtaskAPI = {
  list: (params = {}) => api.get("/subtasks", { params }),
  get: (id) => api.get(`/subtasks/${id}`),
  create: (data) => api.post("/subtasks", data),
  update: (id, data) => api.patch(`/subtasks/${id}`, data),
  delete: (id) => api.delete(`/subtasks/${id}`),
  // Team-Member completion action. A sub-task is always a leaf — this IS
  // the completion action.
  submitForReview: (id, data = {}) =>
    api.post(`/subtasks/${id}/submit-for-review`, data),
  // { decision: 'APPROVE' | 'REJECT', note? }  <- Team Lead approval
  review: (id, data) => api.post(`/subtasks/${id}/review`, data),

  // ----- Multi-assignee (1 or more people) -----
  assignEntity: (id) => api.post(`/subtasks/${id}/assign-entity`),
  // `dueDates` — see taskAPI.assignMulti; capped at this sub-task's own
  // due date by the backend.
  assignMulti: (id, userIds, dueDates) => api.post(`/subtasks/${id}/assign-multi`, {
    user_ids: userIds, due_dates: dueDates || undefined,
  }),
  // Assign EVERY occurrence of a recurring task's matching sub-task (same
  // series, same plan) to the same person / people in one call — the
  // sub-task analogue of taskAPI.assignSeries, needed because a task-level
  // series-assign skips any occurrence that carries sub-tasks. Occurrences
  // already completed or pending review are left untouched. Returns
  // { updated, skipped, subtask }.
  assignSeries: (id, userIds) =>
    api.post(`/subtasks/${id}/assign-series`, { user_ids: userIds }),
  // one assignee's own row on a shared sub-task
  markDoneEntity: (id, data) => api.post(`/subtasks/${id}/mark-done-entity`, data),

  // ----- Attachments -----
  listAttachments: (id) => api.get(`/subtasks/${id}/attachments`),
  downloadAttachment: (id, attachmentId) =>
    api.get(`/subtasks/${id}/attachments/${attachmentId}/download`, {
      responseType: "blob",
    }),
  deleteAttachment: (id, attachmentId) =>
    api.delete(`/subtasks/${id}/attachments/${attachmentId}`),
};

// ===== CARRY FORWARD =====
export const carryForwardAPI = {
  list: (params = {}) => api.get("/carry-forwards", { params }),
  get: (id) => api.get(`/carry-forwards/${id}`),
  // { decision: 'APPROVE' | 'REJECT', notes?, item_ids?: [...] }
  decide: (id, data) => api.post(`/carry-forwards/${id}/decision`, data),
};


// ===== HRMS =====
export const hrmsAPI = {
  // Projects from the HRMS, via the backend (which holds the credentials):
  // [{ id, name }] sorted by name. 503 = integration not configured,
  // 502 = the HRMS couldn't be reached / returned an error.
  projects: () => api.get("/hrms/projects"),
  // Activities, in HRMS order: [{ id, name, description, sub_activities:
  // [{ id, name, key }] }]. An activity with sub activities needs one chosen.
  activities: () => api.get("/hrms/activities"),
};

// ===== AUTOMATED TIMESHEET =====
// Everything is the signed-in user's OWN timesheet; dates are English (AD).
// Errors answer { detail: { msg, code } } - getErrorMessage shows msg.
export const timesheetAPI = {
  // { hrms_project_id, month: 'YYYY-MM', through?: 'YYYY-MM-DD' } -> proposed
  // lines, existing HRMS entries, missing time logs, blockers, warnings, totals.
  preview: (params) => api.get("/timesheets/preview", { params }),
  // { hrms_project_id, month, through?, idempotency_key, acknowledge_warnings,
  //   lines: [{ key, hrms_activity_id, hrms_sub_activity_id?, summary, work_type }] }
  submit: (data) => api.post("/timesheets/submit", data),
  // Look in the HRMS for lines whose result was unknown (dropped connection).
  verify: (id) => api.post(`/timesheets/submissions/${id}/verify`),
  // When the HRMS can't be read back: the user says whether unknown entries
  // arrived. outcome: 'SENT' | 'NOT_SENT' (NOT_SENT frees the hours to resend).
  resolve: (id, outcome) => api.post(`/timesheets/submissions/${id}/resolve`, { outcome }),
  submissions: (params) => api.get("/timesheets/submissions", { params }),
  // Months with hours still to send (empty while reminders are switched off).
  obligations: () => api.get("/timesheets/obligations/me"),
};

// ===== TIME ENTRIES =====
export const timeEntryAPI = {
  // The caller's OWN time history: daily series for the last `days` days
  // (7-90), today / week / month totals, last 8 weeks and last 6 months.
  mySummary: (days = 30) => api.get("/time-entries/my/summary", { params: { days } }),
  // Same shape for one team member — Team Lead only (403 otherwise, or when
  // the member is outside the lead's scope). Includes `user` for the header.
  userSummary: (userId, days = 30) =>
    api.get(`/time-entries/user/${userId}/summary`, { params: { days } }),
};

// ===== DASHBOARDS =====
export const dashboardAPI = {
  tenant: () => api.get("/dashboards/tenant"),
  entity: (entityId) => api.get(`/dashboards/entity/${entityId}`),
  teamLead: (userId) => api.get(`/dashboards/team-lead/${userId}`),
  me: () => api.get("/dashboards/me"),
  myPeriodWork: (params = {}) =>
    api.get("/dashboards/me/period-work", { params }),
  // A Team Lead / Admin viewing one team member's work for a period
  // (drives the "member tasks" page from the Team Workload panel). Pass
  // period_id (may be a CLOSED period) for a historical summary.
  memberPeriodWork: (userId, params = {}) =>
    api.get("/dashboards/me/period-work", { params: { ...params, user_id: userId } }),
  // Team-Lead structural workbench for an open period.
  myLeadWorkbench: (params = {}) =>
    api.get("/dashboards/me/lead-workbench", { params }),
  // Per-person open-work load for the Lead/Admin dashboard.
  teamWorkload: (params = {}) =>
    api.get("/dashboards/team-workload", { params }),
  // Entity/period task browser with status-bucket counts.
  taskBoard: (params = {}) => api.get("/dashboards/task-board", { params }),
  // Distinct tags in use across the tenant.
  tags: () => api.get("/dashboards/tags"),
};

// ===== TEAM (role-aware Lead/Member surface — /api/team/*) =====
export const teamAPI = {
  dashboard: (params = {}) => api.get("/team/dashboard", { params }),
  packs: (params = {}) => api.get("/team/packs", { params }),
  packDetail: (id) => api.get(`/team/packs/${id}`),
  tasks: (params = {}) => api.get("/team/tasks", { params }),
  taskDetail: (id) => api.get(`/team/tasks/${id}`),
  // { assignee_id }
  assign: (id, data) => api.post(`/team/tasks/${id}/assign`, data),
  reassign: (id, data) => api.patch(`/team/tasks/${id}/assignment`, data),
  // { status }
  setStatus: (id, data) => api.patch(`/team/tasks/${id}/status`, data),
  // { comment?, time_minutes? }
  submit: (id, data = {}) => api.post(`/team/tasks/${id}/submit`, data),
  // { status } — "start work" (NOT_STARTED/ASSIGNED/REJECTED -> IN_PROGRESS)
  startWork: (id) => api.patch(`/team/tasks/${id}/status`, { status: "IN_PROGRESS" }),
  // Attachment upload accepts a Task OR SubTask id.
  uploadAttachment: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/team/tasks/${id}/attachments`, form);
  },
  downloadAttachment: (attachmentId) =>
    api.get(`/team/attachments/${attachmentId}`, { responseType: "blob" }),
};

// ===== REPORTS (CSV via ?format=csv) =====
export const reportAPI = {
  // The 5 required report types matching backend endpoints
  periodCloseCompletion: (params = {}) =>
    api.get("/reports/period-close-completion", { params }),
  // Live task-by-task status of the current period's Close-Pack plans
  // (defaults to the period covering today). Was the inline Close Packs
  // page panel; now a first-class navbar report.
  currentPeriodStatus: (params = {}) =>
    api.get("/reports/current-period-status", { params }),
  overdueTasks: (params = {}) => api.get("/reports/overdue-tasks", { params }),
  userWorkload: (params = {}) => api.get("/reports/user-workload", { params }),
  yearEndRollup: (params = {}) =>
    api.get("/reports/year-end-rollup", { params }),
  timeEntries: (params = {}) => api.get("/reports/time-entries", { params }),

  // Legacy report methods (kept for backward compatibility if needed)
  completion: (params = {}) => api.get("/reports/completion", { params }),
  entityPerformance: (params = {}) =>
    api.get("/reports/entity-performance", { params }),
  carryForward: (params = {}) => api.get("/reports/carry-forward", { params }),
  periodSummary: (params = {}) =>
    api.get("/reports/period-summary", { params }),
  groupSummary: (params = {}) => api.get("/reports/group-summary", { params }),

  // CSV/PDF download helpers — return Blob via responseType
  downloadCsv: (kind, params = {}) =>
    api.get(`/reports/${kind}`, {
      params: { ...params, format: "csv" },
      responseType: "blob",
    }),
  downloadPdf: (kind, params = {}) =>
    api.get(`/reports/${kind}`, {
      params: { ...params, format: "pdf" },
      responseType: "blob",
    }),

  // Snapshots
  listSnapshots: (params = {}) => api.get("/reports/snapshots", { params }),
  getSnapshot: (id) => api.get(`/reports/snapshots/${id}`),
};

// ===== NOTIFICATIONS =====
export const notificationAPI = {
  list: () => api.get("/notifications"),
  markRead: (id) => api.post(`/notifications/${id}/read`),
  markAllRead: () => api.post("/notifications/mark-all-read"),
};

// ===== AUDIT =====
export const auditAPI = {
  list: (params = {}) => api.get("/audit-logs", { params }),
};

// ===== REPORT STUDIO (plain-language reports: dashboard / PDF / Excel / CSV) =====
export const reportStudioAPI = {
  catalog: () => api.get("/report-studio/catalog"),
  interpret: (text) => api.post("/report-studio/interpret", { text }),
  ask: (text) => api.post("/report-studio/ask", { text }),
  // format "json" -> the dashboard payload; "pdf" | "xlsx" | "csv" -> a file (Blob)
  generate: (spec, format = "json", table) =>
    api.post(
      "/report-studio/generate",
      { spec, format, ...(table ? { table } : {}) },
      format === "json" ? {} : { responseType: "blob", timeout: 120000 },
    ),
};
