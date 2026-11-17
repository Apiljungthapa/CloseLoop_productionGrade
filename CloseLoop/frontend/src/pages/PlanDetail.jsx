// Plan Detail: hierarchical view of Tasks → SubTasks with role-aware
// action buttons.
//
// Flexible completion hierarchy — the "Mark as Done" action only ever
// appears at the LOWEST level actually defined for a given branch, and
// every level uses the SAME assign → execute → approve flow:
//   * A Task has Subtasks               → Lead assigns a Team Member as the
//                                          Subtask owner; member submits via
//                                          "Mark as Done"; Lead approves or
//                                          rejects (POST /subtasks/{id}/review).
//                                          The Task auto-completes once every
//                                          Subtask is approved.
//   * A Task has no Subtasks at all     → Lead assigns a Team Member via
//                                          member_assigned_to_id; member
//                                          submits via "Mark as Done"; Lead
//                                          approves or rejects
//                                          (POST /tasks/{id}/review).
// The backend enforces this: direct COMPLETED writes are rejected — every
// Task/Subtask must go through submit-for-review + review to complete.
//
// Role matrix on this page:
//   * Admin (SUPER_ADMIN)
//       - Read-only structural view of every plan in the tenant
//       - Assigns the WHOLE plan (pack) to a Team Lead here (plan-level
//         "Assign to Team Lead" control) — NOT individual tasks; the
//         backend rejects a per-task lead assignment from an admin
//       - NO add / edit / delete on Tasks / Subtasks (those are done in
//         the Admin's Close-Pack template editor, or by the owning
//         Lead's workbench, never inline here by an admin).
//
//   * Team Lead (TEAM_LEAD)
//       - Can VIEW every plan in the tenant (browse "all packs"), but
//         full structural authority — create / edit / delete Tasks,
//         Subtasks, set localized due dates, reassign Team Members —
//         ONLY on plans they explicitly own (`plan.can_edit`, driven by
//         `Plan.owner_id === me.id`). A plan generated but not assigned
//         to them is read-only, full stop — no entity-scope fallback.
//       - Approves or rejects each completed Subtask (owned plans only).
//       - When every Subtask completes, the Task completes.
//
//   * Team Member (TEAM_MEMBER)
//       - View their assigned work
//       - Mark assigned Subtasks/Tasks done
//       - Cannot modify structure or assignments
import React, { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Card,
  Button,
  Modal,
  Form,
  Alert,
  Badge,
  Accordion,
  ProgressBar,
} from "react-bootstrap";
import { useAuth } from "../auth.jsx";
import { useOrgPath } from "../orgPath.js";
import { StatusBadge, displayStatus } from "../components/Bits.jsx";
import CompletionSubmissionModal from "../components/CompletionSubmissionModal.jsx";
import ConfirmationModal from "../components/ConfirmationModal.jsx";
import TaskDetailPanel from "../components/TaskDetailPanel.jsx";
import DateInput from "../components/ui/DateInput.jsx";
import { useLiveRefresh } from "../components/useLiveRefresh.js";
import { planAPI, userAPI, taskAPI, subtaskAPI } from "../apiEntities.js";
import { getErrorMessage } from "../api";

const apiErr = (e) => getErrorMessage(e, e?.message || "Something went wrong");

function getAssigneeLabel(assignmentId, assignee, fallback = "— unassigned —") {
  if (!assignmentId || !assignee?.name) return fallback;
  return assignee.name;
}

export default function PlanDetail() {
  const { id } = useParams();
  const { user } = useAuth();
  const orgPath = useOrgPath();
  const [plan, setPlan] = useState(null);
  const [users, setUsers] = useState([]);
  const [err, setErr] = useState("");
  const [flash, setFlash] = useState(null);
  const [showAddSubtask, setShowAddSubtask] = useState(false);
  const [activeTaskForAddSubtask, setActiveTaskForAddSubtask] = useState(null);
  const [showAddTask, setShowAddTask] = useState(false);
  const [editingTask, setEditingTask] = useState(null);
  const [editingSubtask, setEditingSubtask] = useState(null);
  const [selectedTaskId, setSelectedTaskId] = useState(null);

  async function confirmAndDelete(label, fn) {
    if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;
    try {
      await fn();
      setFlash({ variant: "success", message: `${label} deleted.` });
      await load();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    }
  }

  const load = useCallback(async () => {
    try {
      const { data } = await planAPI.get(id);
      setPlan(data);
    } catch (e) {
      setErr(apiErr(e));
    }
  }, [id]);

  const loadUsers = useCallback(async () => {
    try {
      const { data } = await userAPI.list();
      setUsers(data);
    } catch {}
  }, []);

  useEffect(() => {
    load();
    loadUsers();
  }, [load, loadUsers]);
  useLiveRefresh(load);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(t);
  }, [flash]);

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!plan) return <div className="text-muted">Loading…</div>;

  const leads = users.filter((u) => u.role === "TEAM_LEAD");
  const members = users.filter((u) => u.role === "TEAM_MEMBER");

  const tasks = plan.tasks || [];
  const typeLabel = plan.calendar_type === 'BS' ? 'Nepali (BS) Plan' : plan.calendar_type === 'AD' ? 'English (AD) Plan' : 'Plan';
  const periodLabel =
    plan.period_label ||
    (plan.period_start_date
      ? `${new Date(plan.period_start_date).toLocaleDateString()}`
      : "");

  const isAdminHere = user.role === "SUPER_ADMIN";
  const isLeadHere = user.role === "TEAM_LEAD";
  // Structural authority — Admin always, Lead only when the whole pack is
  // assigned to them (`Plan.owner_id === me.id`, computed server-side).
  const canEdit = !!plan.can_edit;

  async function assignPlanOwner(ownerId) {
    try {
      const { data } = await planAPI.assignOwner(plan.id, ownerId || null);
      setPlan(data);
      setFlash({
        variant: "success",
        message: ownerId
          ? `Pack assigned to ${data.owner?.name || "the Team Lead"}.`
          : "Pack unassigned.",
      });
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    }
  }

  return (
    <>
      <Link to={orgPath('/')} className="text-muted small">
        <i className="bi bi-arrow-left me-1"></i>Back to dashboard
      </Link>
      <Card className="mt-2 mb-3">
        <Card.Body>
          <div className="d-flex align-items-center flex-wrap gap-2">
            <div className="flex-grow-1">
              <h3 className="mb-1">{plan.title}</h3>
              <div className="text-muted small">
                <Badge bg="light" text="dark" className="me-2">
                  {typeLabel}
                </Badge>
                {plan.entity_name && (
                  <Badge bg="info" className="me-2">
                    <i className="bi bi-building me-1"></i>
                    {plan.entity_name}
                  </Badge>
                )}
                {periodLabel && (
                  <Badge bg="secondary" className="me-2">
                    <i className="bi bi-calendar3 me-1"></i>
                    {periodLabel}
                  </Badge>
                )}
                {plan.due_date && (
                  <span className="ms-2">
                    Due {new Date(plan.due_date).toLocaleDateString()}
                  </span>
                )}
              </div>
              {plan.description && (
                <div className="text-muted mt-2 small">{plan.description}</div>
              )}
              {isAdminHere ? (
                <div className="d-flex align-items-center gap-2 mt-2">
                  <Form.Label className="mb-0 small text-muted">
                    Assign whole pack to:
                  </Form.Label>
                  <Form.Select
                    size="sm"
                    style={{ width: 220 }}
                    value={plan.owner_id || ""}
                    onChange={(e) => assignPlanOwner(e.target.value)}
                  >
                    <option value="">— unassigned —</option>
                    {leads.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </Form.Select>
                </div>
              ) : (
                <div className="muted-sm mt-2">
                  <i className="bi bi-person me-1"></i>
                  Owner: {plan.owner ? plan.owner.name : "— unassigned —"}
                </div>
              )}
            </div>
            <div className="text-end">
              <StatusBadge
                status={displayStatus(plan.status, plan.assigned_to_id)}
              />
              {typeof plan.progress === "number" && (
                <div className="muted-sm mt-2" style={{ minWidth: 140 }}>
                  <ProgressBar now={plan.progress} className="progress-thin" />
                  <div className="text-end">
                    {plan.completed_count || 0} / {plan.task_count || 0} tasks
                  </div>
                </div>
              )}
            </div>
          </div>
        </Card.Body>
      </Card>

      {flash && (
        <Alert
          variant={flash.variant}
          onClose={() => setFlash(null)}
          dismissible
          className="py-2"
        >
          {flash.message}
        </Alert>
      )}

      {isLeadHere && canEdit && (
        <Card className="mb-3 border-primary">
          <Card.Body className="py-2 d-flex flex-wrap align-items-center gap-2">
            <div className="flex-grow-1 small">
              <i className="bi bi-tools me-2 text-primary"></i>
              <strong>Lead workbench:</strong>{" "}
              <span className="text-muted">
                add tasks · edit structure · approve submissions
              </span>
            </div>
            <Button
              size="sm"
              variant="primary"
              onClick={() => setShowAddTask(true)}
            >
              <i className="bi bi-plus-lg me-1"></i>Add Task
            </Button>
          </Card.Body>
        </Card>
      )}

      {isLeadHere && !canEdit && (
        <Alert variant="secondary" className="py-2 small mb-3">
          <i className="bi bi-eye me-2"></i>
          <strong>View only</strong> — this pack{" "}
          {plan.owner
            ? `is assigned to ${plan.owner.name}`
            : "isn't assigned to a Team Lead yet"}
          . You can browse the full task tree, but only the assigned Lead (or an
          admin) can edit it.
        </Alert>
      )}

      {tasks.length === 0 && (
        <div className="empty-state mb-3">
          <i className="bi bi-clipboard"></i>
          <div>No tasks in this plan.</div>
        </div>
      )}

      <Accordion defaultActiveKey={tasks[0]?.id} alwaysOpen>
        {tasks.map((task) => (
          <TaskBlock
            key={task.id}
            task={task}
            calendarType={plan?.calendar_type}
            members={members}
            canEdit={canEdit}
            reload={load}
            setFlash={setFlash}
            onSelectTask={(taskId) => setSelectedTaskId(taskId)}
            onAddSubtask={(task) => {
              setActiveTaskForAddSubtask(task);
              setShowAddSubtask(true);
            }}
            onEditTask={(t) => setEditingTask(t)}
            onDeleteTask={(t) =>
              confirmAndDelete(`task "${t.title}"`, () => taskAPI.delete(t.id))
            }
            onEditSubtask={(s) => setEditingSubtask(s)}
            onDeleteSubtask={(s) =>
              confirmAndDelete(`sub-task "${s.title}"`, () =>
                subtaskAPI.delete(s.id),
              )
            }
          />
        ))}
      </Accordion>

      {/* Task Detail Panel */}
      {selectedTaskId && (
        <TaskDetailPanel
          taskId={selectedTaskId}
          onClose={() => setSelectedTaskId(null)}
          reload={load}
        />
      )}

      <AddTaskModal
        show={showAddTask}
        onHide={() => setShowAddTask(false)}
        plan={plan}
        members={members}
        onCreated={() => {
          setShowAddTask(false);
          load();
        }}
      />
      <AddSubtaskModal
        show={showAddSubtask}
        onHide={() => {
          setShowAddSubtask(false);
          setActiveTaskForAddSubtask(null);
        }}
        task={activeTaskForAddSubtask}
        calendarType={plan?.calendar_type}
        members={members}
        onCreated={() => {
          setShowAddSubtask(false);
          setActiveTaskForAddSubtask(null);
          load();
        }}
      />
      <EditTaskModal
        task={editingTask}
        calendarType={plan?.calendar_type}
        onHide={() => setEditingTask(null)}
        onSaved={() => {
          setEditingTask(null);
          load();
        }}
        onError={(e) => setFlash({ variant: "danger", message: apiErr(e) })}
      />
      <EditSubtaskModal
        subTask={editingSubtask}
        parentTask={
          editingSubtask
            ? (plan?.tasks || []).find((t) => t.id === editingSubtask.task_id)
            : null
        }
        calendarType={plan?.calendar_type}
        members={members}
        onHide={() => setEditingSubtask(null)}
        onSaved={() => {
          setEditingSubtask(null);
          load();
        }}
        onError={(e) => setFlash({ variant: "danger", message: apiErr(e) })}
      />
    </>
  );
}

function TaskBlock({
  task,
  calendarType,
  members,
  canEdit,
  reload,
  setFlash,
  onSelectTask,
  onAddSubtask,
  onEditTask,
  onDeleteTask,
  onEditSubtask,
  onDeleteSubtask,
}) {
  const { user } = useAuth();
  // Admin tier — sees read-only structure; assigns the WHOLE pack to a
  // Team Lead from the plan header, never a per-task assignment here.
  const isAdmin = user.role === "SUPER_ADMIN";
  // Team-Lead tier — structural customization ONLY when this plan is
  // assigned to them (`canEdit`, driven by Plan.owner_id === me.id).
  // Viewing an unassigned/other-lead's plan, `isLead` is false here so
  // every edit affordance below stays hidden — read-only browsing.
  const isLead = user.role === "TEAM_LEAD" && canEdit;
  const subs = task.subtasks || [];
  const totalSubs = subs.length;
  const doneSubs = subs.filter((s) => s.status === "COMPLETED").length;
  const pct = totalSubs ? Math.round((doneSubs * 100) / totalSubs) : 0;

  // Flexible completion hierarchy: this task is directly completable ONLY
  // when it has no sub-tasks at all — otherwise completion is derived from
  // rolling up the sub-tasks. A leaf task follows the assign → submit →
  // approve flow: the Lead assigns a Team Member, the member submits for
  // review, and the Lead approves or rejects it.
  const isLeafTask = totalSubs === 0;
  const taskCompleted = task.status === "COMPLETED";
  const taskPendingReview = task.status === "PENDING_REVIEW";
  const taskRejected = task.status === "REJECTED";
  const entityAssignees = task.entity_assignees || [];
  const myEntityRow = task.assigned_entity_wide
    ? entityAssignees.find((a) => a.user_id === user.id)
    : null;
  const isTaskExecutor =
    user.id === task.assigned_to_id ||
    user.id === task.member_assigned_to_id ||
    !!myEntityRow;
  const canSubmitTaskDone =
    isLeafTask &&
    !task.assigned_entity_wide &&
    !taskCompleted &&
    !taskPendingReview &&
    (isTaskExecutor || isAdmin || isLead);
  const canMarkEntityDone =
    isLeafTask &&
    task.assigned_entity_wide &&
    !taskCompleted &&
    !taskPendingReview &&
    !!myEntityRow;
  const canReviewTask = isLeafTask && taskPendingReview && (isAdmin || isLead);
  const [markingTaskDone, setMarkingTaskDone] = useState(false);
  const [reviewingTask, setReviewingTask] = useState(false);
  const [showTaskReject, setShowTaskReject] = useState(false);
  const [taskRejectNote, setTaskRejectNote] = useState("");
  const [showTaskSubmitModal, setShowTaskSubmitModal] = useState(false);
  const [showMemberAssign, setShowMemberAssign] = useState(false);
  const [confirm, setConfirm] = useState({
    show: false,
    message: "",
    onConfirm: null,
  });

  async function handleSubmitTaskDone(payload) {
    setMarkingTaskDone(true);
    try {
      await taskAPI.submitForReview(task.id, payload);
      setFlash({
        variant: "info",
        message: `Task "${task.title}" submitted to your Team Lead for approval.`,
      });
      setShowTaskSubmitModal(false);
      reload();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    } finally {
      setMarkingTaskDone(false);
    }
  }

  async function handleReviewTask(decision, note) {
    setReviewingTask(true);
    try {
      await taskAPI.review(task.id, { decision, note });
      setFlash({
        variant: decision === "APPROVE" ? "success" : "warning",
        message:
          decision === "APPROVE"
            ? `Task "${task.title}" approved.`
            : `Task "${task.title}" sent back to the assignee.`,
      });
      setShowTaskReject(false);
      setTaskRejectNote("");
      reload();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    } finally {
      setReviewingTask(false);
    }
  }

  async function handleAssignTaskMember(memberId) {
    setConfirm({
      show: true,
      message: "Are you sure you want to assign this?",
      onConfirm: async () => {
        try {
          await taskAPI.update(task.id, {
            member_assigned_to_id: memberId || null,
          });
          reload();
        } catch (e) {
          setFlash({ variant: "danger", message: apiErr(e) });
        }
      },
    });
  }

  async function handleAssignTaskEntity() {
    setConfirm({
      show: true,
      message: "Are you sure you want to assign this task to the whole entity?",
      onConfirm: async () => {
        try {
          await taskAPI.assignEntity(task.id);
          setFlash({
            variant: "success",
            message: `"${task.title}" assigned to the whole entity.`,
          });
          reload();
        } catch (e) {
          setFlash({ variant: "danger", message: apiErr(e) });
        }
      },
    });
  }

  async function handleMarkTaskDoneEntity() {
    setMarkingTaskDone(true);
    try {
      await taskAPI.markDoneEntity(task.id);
      setFlash({
        variant: "info",
        message: `Marked "${task.title}" done — waiting on the rest of the entity.`,
      });
      reload();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    } finally {
      setMarkingTaskDone(false);
    }
  }

  return (
    <Accordion.Item eventKey={task.id} className="mb-2">
      <Accordion.Header onClick={() => onSelectTask(task.id)}>
        <div className="d-flex align-items-center w-100 me-3 flex-wrap gap-2">
          <div className="flex-grow-1">
            <strong>{task.title}</strong>
            {task.is_custom && (
              <Badge bg="info" pill className="ms-2" style={{ fontSize: 10 }}>
                custom
              </Badge>
            )}
            <span className="text-muted ms-2 small">
              Lead: {getAssigneeLabel(task.assigned_to_id, task.assigned_to)}
              {task.due_date && (
                <> · Due {new Date(task.due_date).toLocaleDateString()}</>
              )}
            </span>
          </div>
          <StatusBadge
            status={displayStatus(task.status, task.assigned_to_id)}
          />
          <div style={{ width: 120 }}>
            <ProgressBar now={pct} className="progress-thin" />
            <div className="muted-sm text-end">
              {doneSubs}/{totalSubs}
            </div>
          </div>
        </div>
      </Accordion.Header>
      <Accordion.Body>
        {task.description && (
          <p className="text-muted small">{task.description}</p>
        )}

        <ConfirmationModal
          show={confirm.show}
          onHide={() =>
            setConfirm({ show: false, message: "", onConfirm: null })
          }
          title="Confirm assignment"
          message={confirm.message || "Are you sure you want to assign this?"}
          onConfirm={confirm.onConfirm}
        />

        {/* Team-Lead workbench actions — structural editing */}
        {isLead && (
          <div className="mb-3 d-flex align-items-center flex-wrap gap-2">
            <Form.Group className="mb-0">
              <Form.Label className="me-2 mb-0 small">Task due date</Form.Label>
              <div style={{ width: 170, display: "inline-block" }}>
                <DateInput
                  calendarType={calendarType}
                  value={task.due_date || ""}
                  onChange={async (val) => {
                    try {
                      await taskAPI.update(task.id, { due_date: val || null });
                      reload();
                    } catch (err) {
                      setFlash({ variant: "danger", message: apiErr(err) });
                    }
                  }}
                />
              </div>
            </Form.Group>
            <Button
              size="sm"
              variant="outline-primary"
              onClick={() => onAddSubtask(task)}
            >
              <i className="bi bi-plus-lg me-1"></i>Add Sub-task
            </Button>
            <Button
              size="sm"
              variant="outline-secondary"
              onClick={() => onEditTask(task)}
            >
              <i className="bi bi-pencil me-1"></i>Edit Task
            </Button>
            {task.is_custom && (
              <Button
                size="sm"
                variant="outline-danger"
                onClick={() => onDeleteTask(task)}
              >
                <i className="bi bi-trash me-1"></i>Delete Task
              </Button>
            )}
          </div>
        )}

        {isLeafTask && (
          <div className="mb-3">
            <div className="empty-state mb-0 py-2">
              <i className="bi bi-list-task"></i>
              <div>No sub-tasks for this task — completed directly.</div>
            </div>

            {/* Lead workbench: assign the Team Member who executes this leaf task */}
            {isLead && !taskCompleted && !task.assigned_entity_wide && (
              <div className="mt-2">
                {!showMemberAssign ? (
                  <Button
                    size="sm"
                    variant="link"
                    className="p-0 small text-muted"
                    onClick={() => setShowMemberAssign(true)}
                  >
                    <i className="bi bi-chevron-down me-1"></i>Show
                  </Button>
                ) : (
                  <div className="d-flex align-items-center flex-wrap gap-2 mt-2">
                    <Form.Label className="me-2 mb-0 small text-muted">
                      Assign Team Member:
                    </Form.Label>
                    <Form.Select
                      size="sm"
                      style={{ width: 240 }}
                      value={task.member_assigned_to_id || ""}
                      onChange={(e) => handleAssignTaskMember(e.target.value)}
                    >
                      <option value="">— unassigned —</option>
                      {members.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </Form.Select>
                    <Button
                      size="sm"
                      variant="outline-primary"
                      onClick={handleAssignTaskEntity}
                      title="Assign to every Team Member in this task's entity — all of them must mark it done"
                    >
                      <i className="bi bi-people me-1"></i>Assign whole entity
                    </Button>
                    <Button
                      size="sm"
                      variant="link"
                      className="p-0 small text-muted"
                      onClick={() => setShowMemberAssign(false)}
                    >
                      <i className="bi bi-chevron-up me-1"></i>Hide
                    </Button>
                  </div>
                )}
              </div>
            )}

            {task.assigned_entity_wide && !taskCompleted && (
              <div className="mt-2">
                <div className="muted-sm mb-1">
                  <i className="bi bi-people me-1 text-info"></i>
                  Assigned to the whole entity —{" "}
                  {entityAssignees.filter((a) => a.done).length}/
                  {entityAssignees.length} members done
                  {isLead && (
                    <Button
                      size="sm"
                      variant="link"
                      className="p-0 ms-2"
                      onClick={handleAssignTaskEntity}
                      title="Refresh entity assignment (adds new members, keeps existing progress)"
                    >
                      resync members
                    </Button>
                  )}
                </div>
                <div className="d-flex flex-wrap gap-1">
                  {entityAssignees.map((a) => (
                    <Badge
                      key={a.id}
                      bg={a.done ? "success" : "light"}
                      text={a.done ? undefined : "dark"}
                      className="fw-normal"
                    >
                      <i
                        className={`bi ${a.done ? "bi-check-circle" : "bi-hourglass-split"} me-1`}
                      ></i>
                      {a.user?.name || "Member"}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {taskRejected && task.rejection_note && (
              <div
                className="cp-rejection mt-2 p-2 rounded"
                style={{ background: "#fef2f2", border: "1px solid #fecaca" }}
              >
                <div className="small fw-semibold text-danger">
                  <i className="bi bi-x-octagon me-1"></i>Rejected by Team Lead
                </div>
                <div className="small">{task.rejection_note}</div>
              </div>
            )}

            <div className="d-flex align-items-center gap-2 mt-2 flex-wrap">
              {canSubmitTaskDone && (
                <Button
                  size="sm"
                  variant="success"
                  disabled={markingTaskDone}
                  onClick={() => setShowTaskSubmitModal(true)}
                  title="Submit this task as complete for Team-Lead approval"
                >
                  <i className="bi bi-check2 me-1"></i>
                  {taskRejected ? "Resubmit" : "Mark as Done"}
                </Button>
              )}
              {canMarkEntityDone && (
                <Button
                  size="sm"
                  variant="success"
                  disabled={markingTaskDone || !!myEntityRow?.done}
                  onClick={handleMarkTaskDoneEntity}
                  title="Mark your own part of this whole-entity task done"
                >
                  <i className="bi bi-check2 me-1"></i>
                  {myEntityRow?.done
                    ? "Done — waiting on others"
                    : "Mark as Done"}
                </Button>
              )}
              {taskPendingReview && (
                <span className="muted-sm">
                  <i className="bi bi-hourglass-split me-1 text-warning"></i>
                  Awaiting Team-Lead approval
                  {task.assigned_entity_wide
                    ? " · every entity member marked done"
                    : task.member_assigned_to?.name && (
                        <> · submitted by {task.member_assigned_to.name}</>
                      )}
                </span>
              )}
              {canReviewTask && (
                <>
                  <Button
                    size="sm"
                    variant="success"
                    disabled={reviewingTask}
                    onClick={() => handleReviewTask("APPROVE")}
                  >
                    <i className="bi bi-check2 me-1"></i>Approve
                  </Button>
                  <Button
                    size="sm"
                    variant="outline-danger"
                    disabled={reviewingTask}
                    onClick={() => setShowTaskReject(true)}
                  >
                    <i className="bi bi-x-lg me-1"></i>Reject
                  </Button>
                </>
              )}
            </div>

            {task.submitted_at && (
              <div className="muted-sm mt-2">
                <i className="bi bi-paperclip me-1"></i>
                Submitted by {task.submitted_by?.name || "—"} ·{" "}
                {new Date(task.submitted_at).toLocaleString()}
                {task.completion_notes && (
                  <div className="mt-1">Notes: {task.completion_notes}</div>
                )}
                {task.completion_time_minutes != null && (
                  <div>
                    Time spent: {Math.floor(task.completion_time_minutes / 60)}h{" "}
                    {task.completion_time_minutes % 60}m
                  </div>
                )}
              </div>
            )}

            {canReviewTask && showTaskReject && (
              <div
                className="mt-2 p-2 rounded"
                style={{ background: "#fef2f2", border: "1px solid #fecaca" }}
              >
                <div className="small fw-semibold text-danger mb-1">
                  <i className="bi bi-x-octagon me-1"></i>Why are you rejecting?
                </div>
                <Form.Control
                  as="textarea"
                  rows={2}
                  size="sm"
                  placeholder="e.g. missing supporting doc — please redo and resubmit."
                  value={taskRejectNote}
                  onChange={(e) => setTaskRejectNote(e.target.value)}
                />
                <div className="d-flex gap-2 mt-1">
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={reviewingTask || !taskRejectNote.trim()}
                    onClick={() =>
                      handleReviewTask("REJECT", taskRejectNote.trim())
                    }
                  >
                    <i className="bi bi-x-lg me-1"></i>Send back
                  </Button>
                  <Button
                    size="sm"
                    variant="link"
                    className="text-muted"
                    onClick={() => {
                      setShowTaskReject(false);
                      setTaskRejectNote("");
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {subs.map((sub) => (
          <SubTaskBlock
            key={sub.id}
            subTask={sub}
            parentTask={task}
            calendarType={calendarType}
            members={members}
            reload={reload}
            canManage={isLead}
            setFlash={setFlash}
            onEditSubtask={onEditSubtask}
            onDeleteSubtask={onDeleteSubtask}
          />
        ))}
      </Accordion.Body>
    </Accordion.Item>
  );
}

function SubTaskBlock({
  subTask,
  parentTask,
  calendarType,
  members,
  reload,
  canManage,
  setFlash,
  onEditSubtask,
  onDeleteSubtask,
}) {
  const { user } = useAuth();
  const subCompleted = subTask.status === "COMPLETED";

  // A sub-task is always a leaf now — directly completable. It follows the
  // same assign → submit → approve flow: the Lead assigns a Team Member
  // (the "Reassign owner" control below), the member submits for review,
  // and the Lead approves or rejects it.
  const subPendingReview = subTask.status === "PENDING_REVIEW";
  const subRejected = subTask.status === "REJECTED";
  const isSubtaskAssignee = user.id === subTask.assigned_to_id;
  const canSubmitSubtaskDone =
    !subCompleted && !subPendingReview && (isSubtaskAssignee || canManage);
  const canReviewSubtask = subPendingReview && canManage;
  const [markingSubDone, setMarkingSubDone] = useState(false);
  const [reviewingSub, setReviewingSub] = useState(false);
  const [showSubReject, setShowSubReject] = useState(false);
  const [subRejectNote, setSubRejectNote] = useState("");
  const [showSubSubmitModal, setShowSubSubmitModal] = useState(false);
  const [confirm, setConfirm] = useState({
    show: false,
    message: "",
    onConfirm: null,
  });

  async function handleSubmitSubtaskDone(payload) {
    setMarkingSubDone(true);
    try {
      await subtaskAPI.submitForReview(subTask.id, payload);
      setFlash({
        variant: "info",
        message: `Sub-task "${subTask.title}" submitted to your Team Lead for approval.`,
      });
      setShowSubSubmitModal(false);
      reload();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    } finally {
      setMarkingSubDone(false);
    }
  }

  async function handleReviewSubtask(decision, note) {
    setReviewingSub(true);
    try {
      await subtaskAPI.review(subTask.id, { decision, note });
      setFlash({
        variant: decision === "APPROVE" ? "success" : "warning",
        message:
          decision === "APPROVE"
            ? `Sub-task "${subTask.title}" approved.`
            : `Sub-task "${subTask.title}" sent back to the assignee.`,
      });
      setShowSubReject(false);
      setSubRejectNote("");
      reload();
    } catch (e) {
      setFlash({ variant: "danger", message: apiErr(e) });
    } finally {
      setReviewingSub(false);
    }
  }

  async function reassign(newMemberId) {
    setConfirm({
      show: true,
      message: "Are you sure you want to assign this?",
      onConfirm: async () => {
        try {
          await subtaskAPI.update(subTask.id, {
            assigned_to_id: newMemberId || null,
          });
          reload();
        } catch (e) {
          setFlash({ variant: "danger", message: apiErr(e) });
        }
      },
    });
  }

  return (
    <div className="subtask-card">
      <div className="d-flex align-items-center mb-2 flex-wrap gap-2">
        <div className="flex-grow-1">
          <div className="fw-semibold">
            {subTask.title}
            {subTask.is_custom && (
              <Badge bg="info" pill className="ms-2" style={{ fontSize: 10 }}>
                custom
              </Badge>
            )}
          </div>
          <div className="muted-sm">
            <i className="bi bi-person me-1"></i>Owner:{" "}
            {getAssigneeLabel(subTask.assigned_to_id, subTask.assigned_to)}
            {subTask.due_date && (
              <> · Due {new Date(subTask.due_date).toLocaleDateString()}</>
            )}
          </div>
        </div>
        <StatusBadge
          status={displayStatus(subTask.status, subTask.assigned_to_id)}
        />
      </div>

      {/* Lead workbench actions for sub-tasks */}
      {!subCompleted && canManage && (
        <div className="d-flex align-items-center mb-2 flex-wrap gap-2">
          <Form.Label className="me-2 mb-0 small text-muted">
            Reassign owner to:
          </Form.Label>
          <Form.Select
            size="sm"
            style={{ width: 240 }}
            value={subTask.assigned_to_id || ""}
            onChange={(e) => reassign(e.target.value)}
          >
            <option value="">— unassigned —</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </Form.Select>
          <Form.Group className="mb-0">
            <Form.Label className="me-2 mb-0 small">Due date</Form.Label>
            <div style={{ width: 170, display: "inline-block" }}>
              <DateInput
                calendarType={calendarType}
                value={subTask.due_date || ""}
                max={parentTask?.due_date || undefined}
                onChange={async (picked) => {
                  const val = picked || null;
                  if (val && parentTask?.due_date && val > parentTask.due_date) {
                    setFlash({
                      variant: "danger",
                      message: `Sub-task due date cannot exceed parent task due date (${parentTask.due_date}).`,
                    });
                    return;
                  }
                  try {
                    await subtaskAPI.update(subTask.id, { due_date: val });
                    reload();
                  } catch (err) {
                    setFlash({ variant: "danger", message: apiErr(err) });
                  }
                }}
              />
            </div>
          </Form.Group>
          <Button
            size="sm"
            variant="outline-secondary"
            onClick={() => onEditSubtask(subTask)}
          >
            <i className="bi bi-pencil me-1"></i>Edit
          </Button>
          {subTask.is_custom && (
            <Button
              size="sm"
              variant="outline-danger"
              onClick={() => onDeleteSubtask(subTask)}
            >
              <i className="bi bi-trash me-1"></i>Delete
            </Button>
          )}
        </div>
      )}

      <ConfirmationModal
        show={confirm.show}
        onHide={() => setConfirm({ show: false, message: "", onConfirm: null })}
        title="Confirm assignment"
        message={confirm.message || "Are you sure you want to assign this?"}
        onConfirm={confirm.onConfirm}
      />

      {subRejected && subTask.rejection_note && (
        <div
          className="cp-rejection mt-2 p-2 rounded"
          style={{ background: "#fef2f2", border: "1px solid #fecaca" }}
        >
          <div className="small fw-semibold text-danger">
            <i className="bi bi-x-octagon me-1"></i>Rejected by Team Lead
          </div>
          <div className="small">{subTask.rejection_note}</div>
        </div>
      )}

      <div className="d-flex align-items-center gap-2 mt-2 flex-wrap">
        {canSubmitSubtaskDone && (
          <Button
            size="sm"
            variant="success"
            disabled={markingSubDone}
            onClick={() => setShowSubSubmitModal(true)}
            title="Submit this sub-task as complete for Team-Lead approval"
          >
            <i className="bi bi-check2 me-1"></i>
            {subRejected ? "Resubmit" : "Mark as Done"}
          </Button>
        )}
        {subPendingReview && (
          <span className="muted-sm">
            <i className="bi bi-hourglass-split me-1 text-warning"></i>
            Awaiting Team-Lead approval
          </span>
        )}
        {canReviewSubtask && (
          <>
            <Button
              size="sm"
              variant="success"
              disabled={reviewingSub}
              onClick={() => handleReviewSubtask("APPROVE")}
            >
              <i className="bi bi-check2 me-1"></i>Approve
            </Button>
            <Button
              size="sm"
              variant="outline-danger"
              disabled={reviewingSub}
              onClick={() => setShowSubReject(true)}
            >
              <i className="bi bi-x-lg me-1"></i>Reject
            </Button>
          </>
        )}
      </div>

      {subTask.submitted_at && (
        <div className="muted-sm mt-2">
          <i className="bi bi-paperclip me-1"></i>
          Submitted by {subTask.submitted_by?.name || "—"} ·{" "}
          {new Date(subTask.submitted_at).toLocaleString()}
          {subTask.completion_notes && (
            <div className="mt-1">Notes: {subTask.completion_notes}</div>
          )}
          {subTask.completion_time_minutes != null && (
            <div>
              Time spent: {Math.floor(subTask.completion_time_minutes / 60)}h{" "}
              {subTask.completion_time_minutes % 60}m
            </div>
          )}
        </div>
      )}

      {canReviewSubtask && showSubReject && (
        <div
          className="mt-2 p-2 rounded"
          style={{ background: "#fef2f2", border: "1px solid #fecaca" }}
        >
          <div className="small fw-semibold text-danger mb-1">
            <i className="bi bi-x-octagon me-1"></i>Why are you rejecting?
          </div>
          <Form.Control
            as="textarea"
            rows={2}
            size="sm"
            placeholder="e.g. missing supporting doc — please redo and resubmit."
            value={subRejectNote}
            onChange={(e) => setSubRejectNote(e.target.value)}
          />
          <div className="d-flex gap-2 mt-1">
            <Button
              size="sm"
              variant="danger"
              disabled={reviewingSub || !subRejectNote.trim()}
              onClick={() =>
                handleReviewSubtask("REJECT", subRejectNote.trim())
              }
            >
              <i className="bi bi-x-lg me-1"></i>Send back
            </Button>
            <Button
              size="sm"
              variant="link"
              className="text-muted"
              onClick={() => {
                setShowSubReject(false);
                setSubRejectNote("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <CompletionSubmissionModal
        show={showSubSubmitModal}
        onHide={() => setShowSubSubmitModal(false)}
        title={`Submit "${subTask.title}"`}
        onSubmit={handleSubmitSubtaskDone}
        submitting={markingSubDone}
      />
    </div>
  );
}

function AddTaskModal({ show, onHide, plan, members, onCreated }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    if (!show) return;
    setTitle("");
    setDescription("");
    setAssignedTo("");
    setDueDate("");
  }, [show]);

  const handleCreate = async () => {
    if (!plan) return;
    if (!title.trim()) {
      alert("Title is required");
      return;
    }
    try {
      await taskAPI.create({
        plan_id: plan.id,
        title,
        description,
        assigned_to_id: assignedTo || null,
        due_date: dueDate || null,
      });
      onCreated();
    } catch (e) {
      alert(apiErr(e));
    }
  };

  return (
    <Modal show={show} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>Add Task to {plan?.title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Form.Group className="mb-3">
          <Form.Label>Title</Form.Label>
          <Form.Control
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            autoFocus
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Description</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Assign to Team Member (optional)</Form.Label>
          <Form.Select
            value={assignedTo}
            onChange={(e) => setAssignedTo(e.target.value)}
          >
            <option value="">— unassigned —</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Due date</Form.Label>
          <DateInput
            calendarType={plan?.calendar_type}
            value={dueDate}
            onChange={setDueDate}
          />
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleCreate}>
          Create
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

function AddSubtaskModal({ show, onHide, task, calendarType = 'AD', members, onCreated }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [dueDate, setDueDate] = useState("");

  useEffect(() => {
    if (!show) return;
    setTitle("");
    setDescription("");
    setAssignedTo("");
    setDueDate("");
  }, [show]);

  const handleCreate = async () => {
    if (!task) return;
    if (!title.trim()) {
      alert("Title is required");
      return;
    }
    if (dueDate && task.due_date && dueDate > task.due_date) {
      alert(
        `Sub-task due date cannot exceed parent task due date (${task.due_date}).`,
      );
      return;
    }
    try {
      await subtaskAPI.create({
        task_id: task.id,
        title,
        description,
        assigned_to_id: assignedTo || null,
        due_date: dueDate || null,
      });
      onCreated();
    } catch (e) {
      alert(apiErr(e));
    }
  };

  return (
    <Modal show={show} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>Add Sub-task</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {task?.due_date && (
          <div className="muted-sm mb-2">
            <i className="bi bi-info-circle me-1"></i>
            Parent task due date: <strong>{task.due_date}</strong> (sub-task
            can't exceed this)
          </div>
        )}
        <Form.Group className="mb-3">
          <Form.Label>Title</Form.Label>
          <Form.Control
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            autoFocus
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Description</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Assign to Team Member</Form.Label>
          <Form.Select
            value={assignedTo}
            onChange={(e) => setAssignedTo(e.target.value)}
          >
            <option value="">— unassigned —</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Due date</Form.Label>
          <DateInput
            calendarType={calendarType}
            value={dueDate}
            max={task?.due_date || undefined}
            onChange={setDueDate}
          />
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleCreate}>
          Create
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

function EditTaskModal({ task, calendarType, onHide, onSaved, onError }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!task) {
      setTitle("");
      setDescription("");
      setDueDate("");
      return;
    }
    setTitle(task.title || "");
    setDescription(task.description || "");
    setDueDate(task.due_date || "");
  }, [task]);

  const handleSave = async () => {
    if (!task) return;
    if (!title.trim()) {
      alert("Title is required");
      return;
    }
    setBusy(true);
    try {
      await taskAPI.update(task.id, {
        title,
        description,
        due_date: dueDate || null,
      });
      onSaved();
    } catch (e) {
      onError?.(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal show={!!task} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>Edit Task</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Form.Group className="mb-3">
          <Form.Label>Title</Form.Label>
          <Form.Control
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Description</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Due date</Form.Label>
          <DateInput
            calendarType={calendarType}
            value={dueDate}
            onChange={setDueDate}
          />
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleSave} disabled={busy}>
          Save
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

function EditSubtaskModal({
  subTask,
  parentTask,
  calendarType,
  members,
  onHide,
  onSaved,
  onError,
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!subTask) {
      // Clear when modal closes so the next open starts clean.
      setTitle("");
      setDescription("");
      setDueDate("");
      setAssignedTo("");
      return;
    }
    setTitle(subTask.title || "");
    setDescription(subTask.description || "");
    setDueDate(subTask.due_date || "");
    setAssignedTo(subTask.assigned_to_id || "");
  }, [subTask]);

  const handleSave = async () => {
    if (!subTask) return;
    if (!title.trim()) {
      alert("Title is required");
      return;
    }
    if (dueDate && parentTask?.due_date && dueDate > parentTask.due_date) {
      alert(
        `Sub-task due date cannot exceed parent task due date (${parentTask.due_date}).`,
      );
      return;
    }
    setBusy(true);
    try {
      await subtaskAPI.update(subTask.id, {
        title,
        description,
        due_date: dueDate || null,
        assigned_to_id: assignedTo || null,
      });
      onSaved();
    } catch (e) {
      onError?.(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal show={!!subTask} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>Edit Sub-task</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {parentTask?.due_date && (
          <div className="muted-sm mb-2">
            <i className="bi bi-info-circle me-1"></i>
            Parent task due date: <strong>{parentTask.due_date}</strong>
          </div>
        )}
        <Form.Group className="mb-3">
          <Form.Label>Title</Form.Label>
          <Form.Control
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Description</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Assign to Team Member</Form.Label>
          <Form.Select
            value={assignedTo}
            onChange={(e) => setAssignedTo(e.target.value)}
          >
            <option value="">— unassigned —</option>
            {(members || []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </Form.Select>
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label>Due date</Form.Label>
          <DateInput
            calendarType={calendarType}
            value={dueDate}
            max={parentTask?.due_date || undefined}
            onChange={setDueDate}
          />
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide} disabled={busy}>
          Cancel
        </Button>
        <Button variant="primary" onClick={handleSave} disabled={busy}>
          Save
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
