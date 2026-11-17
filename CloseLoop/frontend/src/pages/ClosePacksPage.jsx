// ClosePacksPage — Super Admin: manage the Close Pack catalog and its
// template hierarchy (Pack -> TaskTemplates -> SubTaskTemplates). This is a
// template editor, not a view of live in-progress packs — those live on the
// Admin Dashboard's "All Plans" list instead. Ported from the Frontend_0323
// design mockup, wired to the real closePackAPI.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as XLSX from 'xlsx';
import { closePackAPI, periodAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import TaskTemplateBlock, { resolveTemplateDueIso } from '../components/admin/TaskTemplateBlock';
import PackTemplateFormModal, { CALENDAR_TYPES } from '../components/admin/PackTemplateFormModal';
import TemplateItemFormModal from '../components/admin/TemplateItemFormModal';
import { useBsDateLabels } from './leadDashUtils';

const apiErr = (e) => getErrorMessage(e, e?.message || 'Failed');
const CALENDAR_LABEL = Object.fromEntries(CALENDAR_TYPES.map((t) => [t.value, t.label]));

export default function ClosePacksPage() {
  const [packs, setPacks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [tree, setTree] = useState(null); // ClosePackTreeOut
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [err, setErr] = useState('');
  const [flash, setFlash] = useState('');

  const [packFormOpen, setPackFormOpen] = useState(false);
  const [packFormMode, setPackFormMode] = useState('create');
  const [taskFormOpen, setTaskFormOpen] = useState(false);
  const [subtaskFormTaskId, setSubtaskFormTaskId] = useState(null);
  const [editTask, setEditTask] = useState(null);       // TaskTemplateOut being edited
  const [editSubtask, setEditSubtask] = useState(null); // { sub, taskId }
  const [period, setPeriod] = useState(null);   // tenant's running period, for due-date preview

  // The packs list + the selected pack's own detail panel — clicking
  // anywhere outside this area deselects the active pack (see effect
  // below), so "New Pack" only re-enables once the user has visibly
  // stepped away from what they were working on.
  const layoutRef = useRef(null);
  const bulkFileInputRef = useRef(null);
  const anyModalOpen = packFormOpen || taskFormOpen || !!subtaskFormTaskId
    || !!editTask || !!editSubtask;

  const loadPacks = useCallback(async (preferId) => {
    try {
      const { data } = await closePackAPI.list();
      setPacks(data);
      const stillExists = preferId && data.some((p) => p.id === preferId);
      if (stillExists) setSelectedId(preferId);
      else if (!data.some((p) => p.id === selectedId)) setSelectedId(data[0]?.id ?? null);
    } catch (e) { setErr(apiErr(e)); }
  }, [selectedId]);

  const loadTree = useCallback(async (id) => {
    if (!id) { setTree(null); return; }
    try {
      const { data } = await closePackAPI.get(id);
      setTree(data);
    } catch (e) { setErr(apiErr(e)); }
  }, []);

  useEffect(() => { loadPacks(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadTree(selectedId); }, [selectedId, loadTree]);
  // `.current()` (the tenant's OPEN/IN_PROGRESS period), not `.active()`
  // ("whichever period's date range covers today") — see
  // TemplateItemFormModal's own period-fetch comment for why.
  useEffect(() => {
    periodAPI.current().then(({ data }) => setPeriod(data)).catch(() => {});
  }, []);

  // Clicking anywhere outside the packs list / selected pack's detail panel
  // deselects the active pack — "New Pack" re-enables and "Add task
  // template" goes away, so the two never compete for attention at once.
  // Skipped while a modal is open: its content renders outside this ref
  // (see the modals block at the bottom of the JSX below), so without this
  // guard every click inside "Add task template" etc. would look like a
  // click "outside" and deselect the pack out from under the open modal.
  useEffect(() => {
    if (!selectedId || anyModalOpen) return undefined;
    const onMouseDown = (e) => {
      if (layoutRef.current && !layoutRef.current.contains(e.target)) {
        setSelectedId(null);
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [selectedId, anyModalOpen]);

  // For a Nepali (BS) pack, resolve every template's due date and its own
  // recurrence-start date so the tree shows "Due Bhadra 24, 2083" / "Daily
  // from Bhadra 1, 2083" instead of an AD date — same shared batched
  // BS-label lookup every other pack view (dashboards, pack workspace,
  // live task forms) already uses, instead of a one-off per-date fetch.
  const templateBsDates = useMemo(() => {
    if (!period || !tree) return [];
    const out = [];
    for (const t of tree.task_templates || []) {
      const a = resolveTemplateDueIso(t, period); if (a) out.push(a);
      if (t.recurrence_start) out.push(t.recurrence_start);
      for (const s of t.sub_task_templates || []) {
        const b = resolveTemplateDueIso(s, period); if (b) out.push(b);
        if (s.recurrence_start) out.push(s.recurrence_start);
      }
    }
    return out;
  }, [period, tree]);
  const bsMap = useBsDateLabels(templateBsDates, tree?.calendar_type);

  function flashOk(msg) {
    setFlash(msg);
    setTimeout(() => setFlash(''), 2500);
  }

  // Bulk task/subtask import template — one combined table so a task and
  // its subtasks can be entered together, top to bottom. Columns match
  // TaskTemplateIn / SubTaskTemplateIn (backend/app/schemas.py)
  // field-for-field; "Task Key" / "Parent Task Key" are local-to-this-file
  // references only (not saved) that link a Subtask row back to the Task
  // row it belongs to. Shared by both the .xlsx and .csv downloads below,
  // and read back by POST /close-packs/{id}/task-templates/import-file,
  // which accepts either format.
  function buildTaskTemplateData() {
    const TEMPLATE_HEADERS = [
      'Row Type', 'Task Key', 'Parent Task Key', 'Title', 'Description',
      'Priority', 'Reviewer Required', 'Tags',
      'Frequency', 'Interval Count', 'Days Of Week', 'Recurrence Start (YYYY-MM-DD)',
      'Recurrence End (YYYY-MM-DD)', 'Due Day Of Period', 'Due Anchor',
      'Due Date Type', 'Post Period Due Days', 'Due Time (HH:MM)', 'Order',
    ];
    const TEMPLATE_COL_WIDTHS = [
      10, 10, 16, 32, 36, 10, 16, 26, 12, 12, 18, 24, 22, 16, 14, 16, 16, 16, 8,
    ].map((wch) => ({ wch }));
    // Each row exercises a different field so every column has at least
    // one real example to check the backend parser against: a plain
    // once-per-period task+subtask pair, a monthly task with a due time,
    // a subtask that recurs on its own schedule, a weekly task using Days
    // Of Week + an end date, and a task due AFTER its period closes.
    const templateRows = [
      TEMPLATE_HEADERS,
      [
        'Task', 'T1', '', 'Reconcile bank accounts', 'Match statement to ledger for the period',
        'MEDIUM', 'No', 'finance, reconciliation',
        '', '', '', '', '', 1, 'PERIOD_START', '', '', '', 1,
      ],
      [
        'Subtask', '', 'T1', 'Pull latest bank statement', '',
        '', '', '',
        '', '', '', '', '', 1, 'PERIOD_START', '', '', '', 1,
      ],
      [
        'Task', 'T2', '', 'Review payroll register', '',
        'HIGH', 'Yes', '',
        'MONTHLY', 1, '', '2026-01-01', '', 1, 'PERIOD_START', '', '', '17:00', 2,
      ],
      [
        'Subtask', '', 'T2', 'Verify overtime entries', '',
        '', '', '',
        'DAILY', 2, '', '2026-01-01', '2026-01-31', '', 'PERIOD_START', '', '', '', 1,
      ],
      [
        'Task', 'T3', '', 'Send weekly status update', '',
        'LOW', 'No', 'reporting',
        'WEEKLY', 1, 'Mon, Wed, Fri', '2026-01-05', '2026-06-30', '', 'PERIOD_START', '', '', '09:00', 3,
      ],
      [
        'Task', 'T4', '', 'File month-end report (last working day)', '',
        'MEDIUM', 'No', '',
        '', '', '', '', '', 1, 'PERIOD_END', 'WITHIN_PERIOD', 0, '', 4,
      ],
    ];

    const instructionRows = [
      ['Field', 'Applies To', 'Required', 'Notes'],
      ['Row Type', 'Every row', 'Yes', '"Task" or "Subtask" — exactly as written.'],
      ['Task Key', 'Task rows', 'Yes', 'A short code you choose (e.g. T1, T2) — unique per task, used only to link its subtasks in this file. Not saved.'],
      ['Parent Task Key', 'Subtask rows', 'Yes', 'Must match the Task Key of a Task row ABOVE it in this sheet — put each task right before its own subtasks.'],
      ['Title', 'Every row', 'Yes', 'Task or subtask name.'],
      ['Description', 'Every row', 'No', 'Optional detail.'],
      ['Priority', 'Task rows', 'No', 'LOW, MEDIUM, or HIGH. Defaults to MEDIUM. Not used on subtasks.'],
      ['Reviewer Required', 'Task rows', 'No', 'Yes or No. Defaults to No. Not used on subtasks.'],
      ['Tags', 'Task rows', 'No', 'Comma-separated, e.g. "finance, reconciliation". Not used on subtasks.'],
      ['Frequency', 'Every row', 'No', 'Blank = once per period. Otherwise DAILY, WEEKLY, MONTHLY, QUARTERLY, SEMI_ANNUAL, or ANNUAL.'],
      ['Interval Count', 'Every row', 'No', 'Repeat every N days/weeks/months/etc. Defaults to 1.'],
      ['Days Of Week', 'Every row', 'Only for WEEKLY', 'Comma-separated weekdays, e.g. "Mon, Wed, Fri".'],
      ['Recurrence Start', 'Every row', 'Only if Frequency set', 'First occurrence date, YYYY-MM-DD.'],
      ['Recurrence End', 'Every row', 'No', 'Last occurrence date, YYYY-MM-DD. Leave blank for no end date.'],
      ['Due Day Of Period', 'Every row', 'Task rows: no · Subtask rows: yes, unless Frequency is set', 'Whole number, "day N" of the period. Tasks default to day 1; a non-recurring subtask must set this or Frequency. Ignored if recurring. Day 1 with Due Anchor PERIOD_END is the period’s last working day.'],
      ['Due Anchor', 'Every row', 'No', 'PERIOD_START (day N counts from the period start) or PERIOD_END (from the period end). Defaults to PERIOD_START.'],
      ['Due Date Type', 'Every row', 'No', 'Always WITHIN_PERIOD — a due date stays inside its own period. AFTER_PERIOD_END is retired: if a sheet still says it, the row is read as Due Anchor PERIOD_END, Due Day 1 (the period’s last working day).'],
      ['Post Period Due Days', 'Every row', 'No', 'Retired — ignored (kept so older sheets still import).'],
      ['Due Time', 'Every row', 'No', 'Optional time of day, 24-hour HH:MM.'],
      ['Order', 'Every row', 'No', 'Whole number controlling display order. Defaults to 0.'],
    ];
    const INSTRUCTIONS_COL_WIDTHS = [20, 14, 16, 70].map((wch) => ({ wch }));

    return { templateRows, TEMPLATE_COL_WIDTHS, instructionRows, INSTRUCTIONS_COL_WIDTHS };
  }

  function downloadTaskTemplateXlsx() {
    const { templateRows, TEMPLATE_COL_WIDTHS, instructionRows, INSTRUCTIONS_COL_WIDTHS } =
      buildTaskTemplateData();

    const wb = XLSX.utils.book_new();
    const instructionsSheet = XLSX.utils.aoa_to_sheet(instructionRows);
    instructionsSheet['!cols'] = INSTRUCTIONS_COL_WIDTHS;
    XLSX.utils.book_append_sheet(wb, instructionsSheet, 'Instructions');

    const templateSheet = XLSX.utils.aoa_to_sheet(templateRows);
    templateSheet['!cols'] = TEMPLATE_COL_WIDTHS;
    XLSX.utils.book_append_sheet(wb, templateSheet, 'Tasks & Subtasks');

    XLSX.writeFile(wb, 'close-pack-task-template.xlsx');
  }

  // CSV can only hold one table, so it carries just the Tasks & Subtasks
  // rows (no Instructions sheet) — the backend reads a .csv upload as
  // exactly that one table, same columns as the .xlsx sheet of the same name.
  function downloadTaskTemplateCsv() {
    const { templateRows } = buildTaskTemplateData();
    const escapeCell = (v) => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csv = templateRows.map((row) => row.map(escapeCell).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'close-pack-task-template.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function openBulkUploadPicker() {
    bulkFileInputRef.current?.click();
  }

  async function handleBulkFileChosen(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !selectedId) return;
    const countSubtasks = (list) =>
      (list || []).reduce((n, t) => n + (t.sub_task_templates?.length || 0), 0);
    const beforeTasks = tree?.task_templates?.length || 0;
    const beforeSubtasks = countSubtasks(tree?.task_templates);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const { data } = await closePackAPI.importTaskTemplatesFile(selectedId, formData);
      setTree(data);
      const addedTasks = (data.task_templates?.length || 0) - beforeTasks;
      const addedSubtasks = countSubtasks(data.task_templates) - beforeSubtasks;
      flashOk(`Imported ${addedTasks} task(s) and ${addedSubtasks} sub-task(s) from the file.`);
    } catch (err) {
      setErr(apiErr(err));
    }
  }

  const toggleExpanded = (taskId) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      next.has(taskId) ? next.delete(taskId) : next.add(taskId);
      return next;
    });
  };

  async function createPack(draft) {
    const { data } = await closePackAPI.create(draft);
    await loadPacks(data.id);
    flashOk('Pack created.');
  }

  async function updatePackMeta(draft) {
    if (!selectedId) return;
    await closePackAPI.update(selectedId, draft);
    await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
    flashOk('Pack updated.');
  }

  async function deletePack() {
    if (!selectedId || !tree) return;
    if (!window.confirm(`Delete "${tree.name}"? This cannot be undone.`)) return;
    try {
      await closePackAPI.delete(selectedId);
      setSelectedId(null);
      await loadPacks();
      flashOk('Pack deleted.');
    } catch (e) { setErr(apiErr(e)); }
  }

  async function syncPlans() {
    if (!selectedId) return;
    try {
      const { data } = await closePackAPI.syncPlans(selectedId);
      flashOk(
        `Synced ${data.plans} plan(s) — ${data.tasks_added} added, `
        + `${data.tasks_updated} updated, ${data.tasks_removed} removed.`
      );
      await loadTree(selectedId);
    } catch (e) { setErr(apiErr(e)); }
  }

  async function addTaskTemplate(draft) {
    if (!selectedId) return;
    await closePackAPI.addTaskTemplate(selectedId, {
      ...draft,
      due_day_of_period: draft.due_day_of_period ?? 1,
      order: tree?.task_templates?.length ?? 0,
    });
    await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
  }

  async function updateTaskTemplate(templateId, draft) {
    await closePackAPI.updateTaskTemplate(templateId, {
      ...draft,
      due_day_of_period: draft.due_day_of_period ?? 1,
    });
    await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
    flashOk('Task template updated.');
  }

  async function deleteTaskTemplate(taskId) {
    try {
      await closePackAPI.deleteTaskTemplate(taskId);
      await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
    } catch (e) { setErr(apiErr(e)); }
  }

  async function addSubtaskTemplate(taskId, draft) {
    const parent = tree?.task_templates?.find((t) => t.id === taskId);
    await closePackAPI.addSubTaskTemplate(taskId, {
      ...draft,
      order: parent?.sub_task_templates?.length ?? 0,
    });
    await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
  }

  async function updateSubtaskTemplate(templateId, draft) {
    await closePackAPI.updateSubTaskTemplate(templateId, draft);
    await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
    flashOk('Sub-task template updated.');
  }

  async function deleteSubtaskTemplate(_taskId, subtaskId) {
    try {
      await closePackAPI.deleteSubTaskTemplate(subtaskId);
      await Promise.all([loadPacks(selectedId), loadTree(selectedId)]);
    } catch (e) { setErr(apiErr(e)); }
  }

  const selected = tree;

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
      <div className="page-head">
        <div>
          <div className="admin-title-row">
            <Icon name="layers" size={22} style={{ color: 'var(--blue)' }} />
            <h1>Close Packs</h1>
          </div>
          <p>Re-usable templates that drive plan generation.</p>
        </div>
        <div className="head-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={!!selectedId}
            title={selectedId ? 'Click away from the active pack first' : undefined}
            onClick={() => { setPackFormMode('create'); setPackFormOpen(true); }}
          >
            + New Pack
          </button>
        </div>
      </div>

      {flash && <div className="empty-note" style={{ color: 'var(--green)', fontWeight: 700 }}>{flash}</div>}
      {err && <div className="reject-banner">{err}</div>}

      <div className="closepacks-layout" ref={layoutRef}>
        <div className="panel-card">
          <div className="panel-head">
            <h3 className="panel-head-icon-title">
              <Icon name="list" size={15} /> Packs
            </h3>
          </div>

          {packs.length === 0 ? (
            <div className="empty-note">No packs yet — create one to get started.</div>
          ) : (
            <div className="closepack-list">
              {packs.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`closepack-list-item${p.id === selectedId ? ' selected' : ''}`}
                  onClick={() => setSelectedId(p.id)}
                >
                  <div className="cli-name">{p.name}</div>
                  <div className="cli-badges">
                    <span className={`pack-badge cal-${p.calendar_type === 'BS' ? 'bs' : 'ad'}`}>
                      {p.calendar_type === 'BS' ? 'Nepali (BS)' : 'English (AD)'}
                    </span>
                    <span className="pack-badge version">v{p.current_version ?? 1}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {selected ? (
          <div>
            <div className="pack-info-card">
              <div className="pack-info-top">
                <div>
                  <h1>{selected.name}</h1>
                  <div className="admin-pack-badges">
                    <span className={`pack-badge cal-${selected.calendar_type === 'BS' ? 'bs' : 'ad'}`}>
                      {CALENDAR_LABEL[selected.calendar_type] || selected.calendar_type}
                    </span>
                    <span className={`pack-badge status ${selected.active === false ? 'inactive' : 'active'}`}>
                      {selected.active === false ? 'inactive' : 'active'}
                    </span>
                    <span className="pack-version-text">Current version: v{selected.current_version ?? 1}</span>
                  </div>
                </div>

                <div className="pack-detail-actions">
                  <button
                    type="button"
                    className="btn"
                    title="Re-apply this pack's templates to every not-yet-started task in an open plan"
                    onClick={syncPlans}
                  >
                    <Icon name="refresh-cw" size={14} /> Sync plans
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => { setPackFormMode('edit'); setPackFormOpen(true); }}
                  >
                    <Icon name="edit" size={14} /> Edit
                  </button>
                  <button
                    type="button"
                    className="icon-btn icon-btn-red"
                    aria-label={`Delete ${selected.name}`}
                    onClick={deletePack}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </div>
              </div>

              <div className="pack-info-desc">{selected.description || 'No description.'}</div>
              <div className="hint" style={{ marginTop: 10 }}>
                <Icon name="check-circle" size={12} /> Live task status for the current period now lives in{' '}
                <b>Reports → Current Period Status</b>.
              </div>
            </div>

            <div className="panel-card" style={{ marginTop: 18 }}>
              <div className="panel-head">
                <h3 className="panel-head-icon-title">
                  <Icon name="grid" size={14} /> Template Structure
                </h3>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="button" className="btn btn-outline-blue" onClick={downloadTaskTemplateXlsx}>
                    <Icon name="download" size={14} /> Download template (.xlsx)
                  </button>
                  <button type="button" className="btn btn-outline-blue" onClick={downloadTaskTemplateCsv}>
                    <Icon name="download" size={14} /> Download template (.csv)
                  </button>
                  <button type="button" className="btn btn-outline-blue" onClick={openBulkUploadPicker}>
                    <Icon name="upload" size={14} /> Upload template
                  </button>
                  <input
                    ref={bulkFileInputRef}
                    type="file"
                    accept=".xlsx,.xlsm,.csv"
                    style={{ display: 'none' }}
                    onChange={handleBulkFileChosen}
                  />
                  <button type="button" className="btn btn-primary" onClick={() => setTaskFormOpen(true)}>
                    + Add task template
                  </button>
                </div>
              </div>

              {(selected.task_templates || []).length === 0 ? (
                <div className="empty-note">No tasks in this template yet.</div>
              ) : (
                <div className="template-structure-list">
                  {selected.task_templates.map((task) => (
                    <TaskTemplateBlock
                      key={task.id}
                      task={task}
                      period={period}
                      bsMap={bsMap}
                      expanded={expandedIds.has(task.id)}
                      onToggle={() => toggleExpanded(task.id)}
                      onAddSubtask={() => setSubtaskFormTaskId(task.id)}
                      onEditTask={() => setEditTask(task)}
                      onDeleteTask={() => deleteTaskTemplate(task.id)}
                      onEditSubtask={(sub) => setEditSubtask({ sub, taskId: task.id })}
                      onDeleteSubtask={(subtaskId) => deleteSubtaskTemplate(task.id, subtaskId)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="panel-card">
            <div className="empty-note">Select a pack to see its details.</div>
          </div>
        )}
      </div>

      {packFormOpen && (
        <PackTemplateFormModal
          mode={packFormMode}
          initial={packFormMode === 'edit' ? selected : undefined}
          onClose={() => setPackFormOpen(false)}
          onSubmit={(draft) => (packFormMode === 'edit' ? updatePackMeta(draft) : createPack(draft))}
        />
      )}

      {taskFormOpen && (
        <TemplateItemFormModal
          title="Add Task Template"
          subtitle="Add a task to this pack's structure"
          namePlaceholder="e.g. Bank Reconciliation"
          isTask
          calendarType={selected?.calendar_type}
          onClose={() => setTaskFormOpen(false)}
          onSubmit={addTaskTemplate}
        />
      )}

      {subtaskFormTaskId && (
        <TemplateItemFormModal
          title="Add Sub-task Template"
          subtitle="Add a sub-task under this task"
          namePlaceholder="e.g. Account match"
          parentTask={tree?.task_templates?.find((t) => t.id === subtaskFormTaskId)}
          calendarType={selected?.calendar_type}
          onClose={() => setSubtaskFormTaskId(null)}
          onSubmit={(draft) => addSubtaskTemplate(subtaskFormTaskId, draft)}
        />
      )}

      {editTask && (
        <TemplateItemFormModal
          title="Edit Task Template"
          subtitle="Update this task's schedule and details"
          namePlaceholder="e.g. Bank Reconciliation"
          isTask
          calendarType={selected?.calendar_type}
          initial={editTask}
          onClose={() => setEditTask(null)}
          onSubmit={(draft) => updateTaskTemplate(editTask.id, draft)}
        />
      )}

      {editSubtask && (
        <TemplateItemFormModal
          title="Edit Sub-task Template"
          subtitle="Update this sub-task's due date and details"
          namePlaceholder="e.g. Account match"
          parentTask={tree?.task_templates?.find((t) => t.id === editSubtask.taskId)}
          calendarType={selected?.calendar_type}
          initial={editSubtask.sub}
          onClose={() => setEditSubtask(null)}
          onSubmit={(draft) => updateSubtaskTemplate(editSubtask.sub.id, draft)}
        />
      )}
      </div>
    </div>
  );
}
