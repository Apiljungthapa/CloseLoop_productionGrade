import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../auth.jsx';
import { useLiveRefresh } from '../useLiveRefresh.js';
import Icon from '../ui/Icon';
import Avatar from '../ui/Avatar';
import Badge from '../ui/Badge';
import PriorityTag from '../ui/PriorityTag';
import Toggle from '../ui/Toggle';
import TagsInput from '../ui/TagsInput';
import { ProgressBar } from '../ui/ProgressBar';
import {
  teamAPI, taskAPI, subtaskAPI,
  ATTACHMENT_MAX_BYTES, ATTACHMENT_ALLOWED_EXT,
} from '../../apiEntities';
import { getErrorMessage } from '../../api';
import {
  memberStatusKey, PRIORITY_TO_KEY, personFromUser, initialsOf, colorFor, fmtDate, dueMeta,
} from '../../pages/leadDashUtils';

function fmtBytes(n) {
  if (n == null) return '';
  const kb = n / 1024;
  return kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`;
}

// props: item { id, kind: 'TASK'|'SUBTASK', title }, canSubmit, canWithdraw,
//        bsLabels ({ 'YYYY-MM-DD': bsLabel }, from the parent pack view —
//        see useBsDateLabels), onClose(), onChanged(), onSubmit(item),
//        onWithdraw(item)
export default function MemberTaskPanel({
  item, canSubmit, canWithdraw = false, refreshSignal = 0, bsLabels,
  onClose, onChanged, onSubmit, onWithdraw,
}) {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [commentDraft, setCommentDraft] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);
  const isSubtask = item.kind === 'SUBTASK';
  // A parent task the member reached via one of its sub-tasks — the work
  // (and completion) happens on the sub-tasks, not the task itself.
  const isParentWithSubs = !isSubtask && (data?.subtasks || []).length > 0;

  const load = useCallback(async () => {
    try {
      const { data: d } = await teamAPI.taskDetail(item.id);
      setData(d);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not load this task.'));
    }
  }, [item.id]);

  useEffect(() => { load(); }, [load]);
  // Re-fetch when the parent signals a submit finished (the submit dialog
  // stacks on top of this panel and closes without touching it), and on
  // any live-update event from another device / the Team Lead.
  useEffect(() => {
    if (refreshSignal) load();
  }, [refreshSignal, load]);
  useLiveRefresh(load);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const mutate = async (fn, msg) => {
    setError('');
    try {
      await fn();
      await load();
      onChanged?.();
      return true;
    } catch (e) {
      setError(getErrorMessage(e, msg));
      return false;
    }
  };

  if (error && !data) {
    return (
      <div className="detail-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
        <div className="detail-panel" role="dialog" aria-modal="true">
          <div className="detail-head">
            <button className="detail-close" onClick={onClose} aria-label="Close">
              <Icon name="close" size={13} strokeWidth={2.3} />
            </button>
            <div className="detail-title">Task</div>
          </div>
          <div className="detail-body"><div className="reject-banner">{error}</div></div>
        </div>
      </div>
    );
  }
  if (!data) return null;

  const statusKey = memberStatusKey(data);
  const assignee = data.assigned_to || data.member_assigned_to;
  // Shared (multi-assignee) sub-task: I mark my own part done; the whole
  // sub-task auto-submits once everyone has.
  const shared = !!data.assigned_entity_wide;
  const myRow = shared
    ? (data.entity_assignees || []).find((a) => a.user_id === user.id)
    : null;
  const myPartWaiting = shared && !!myRow?.done
    && data.status !== 'PENDING_REVIEW' && data.status !== 'COMPLETED';
  const submitted = data.status === 'PENDING_REVIEW' || myPartWaiting;
  const done = data.status === 'COMPLETED';
  // My own deadline on a shared (whole-entity) task, if the Lead gave me
  // one — falls back to the task's own due date otherwise (see
  // TaskEntityAssignee.due_date / SubTaskEntityAssignee.due_date). A plain
  // single-assignee item has just the one due date, so this is a no-op.
  const myDue = (shared && myRow?.due_date) || data.due_date;
  // Tags are editable only on an item directly assigned to me — matches
  // the backend's member-edit permission (entity-wide assignees act
  // through mark-done-entity instead, same as they do for status).
  const mineToEdit = isSubtask
    ? data.assigned_to_id === user.id
    : data.assigned_to_id === user.id || data.member_assigned_to_id === user.id;

  // Per-sub-task state for the single "one overview" list below — mirrors
  // the row view-model MemberPackView builds, but computed straight from
  // the fresh detail payload so submit/withdraw work inline here.
  const subState = (st) => {
    const stMyRow = (st.entity_assignees || []).find((a) => a.user_id === user.id);
    const stShared = !!st.assigned_entity_wide;
    const mine = stShared ? !!stMyRow : st.assigned_to_id === user.id;
    const stDone = st.status === 'COMPLETED';
    const stMyPartWaiting = stShared && !!stMyRow?.done
      && st.status !== 'PENDING_REVIEW' && st.status !== 'COMPLETED';
    const stSubmitted = st.status === 'PENDING_REVIEW' || stMyPartWaiting;
    const canSubmitSt = mine && !stDone
      && (stShared ? !stMyRow?.done : st.status !== 'PENDING_REVIEW');
    const canWithdrawSt = mine && !stShared && st.status === 'PENDING_REVIEW';
    // My own deadline on this sub-task, if the Lead gave me one.
    const stMyDue = (stShared && stMyRow?.due_date) || st.due_date;
    return {
      mine, stShared, stDone, stMyPartWaiting, stSubmitted, canSubmitSt, canWithdrawSt, stMyDue,
    };
  };

  // People a sub-task is assigned to — one entry for a single assignee,
  // one per person for a multi-assignee / whole-entity sub-task (each with
  // their own done state + date).
  const subAssignees = (st) => {
    if (st.assigned_entity_wide) {
      return (st.entity_assignees || []).map((a) => ({
        id: a.user_id,
        name: a.user?.name || 'Member',
        done: !!a.done,
        done_at: a.done_at,
      }));
    }
    if (st.assigned_to) {
      return [{ id: st.assigned_to.id, name: st.assigned_to.name, done: false, done_at: null }];
    }
    return [];
  };

  // Toggle-off on an un-reviewed sub-task submission — pull it back so the
  // member can keep working. Delegates to the page's withdraw handler and
  // then refreshes this panel.
  const withdrawSub = async (subtaskId) => {
    if (!onWithdraw) return;
    setError('');
    try {
      await onWithdraw({ id: subtaskId });
      await load();
      onChanged?.();
    } catch (e) {
      setError(getErrorMessage(e, 'Could not withdraw this submission.'));
    }
  };

  const updateTags = (tags) => mutate(
    () => (isSubtask ? subtaskAPI.update(item.id, { tags }) : taskAPI.update(item.id, { tags })),
    'Could not update tags.',
  );

  const download = async (att) => {
    try {
      const res = isSubtask
        ? await subtaskAPI.downloadAttachment(item.id, att.id)
        : await taskAPI.downloadAttachment(item.id, att.id);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url; a.download = att.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(getErrorMessage(e, 'Could not download the file.'));
    }
  };

  const uploadFiles = async (fileList) => {
    const files = Array.from(fileList);
    for (const f of files) {
      const ext = '.' + (f.name.split('.').pop() || '').toLowerCase();
      if (!ATTACHMENT_ALLOWED_EXT.includes(ext)) {
        setError(`"${f.name}" — file type ${ext || '(none)'} is not allowed.`);
        return;
      }
      if (f.size > ATTACHMENT_MAX_BYTES) {
        setError(`"${f.name}" is too large (max ${ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB).`);
        return;
      }
    }
    setUploading(true);
    await mutate(async () => {
      for (const f of files) await teamAPI.uploadAttachment(item.id, f);
    }, 'Could not upload the file.');
    setUploading(false);
  };

  const removeAttachment = (att) =>
    mutate(
      () => (isSubtask
        ? subtaskAPI.deleteAttachment(item.id, att.id)
        : taskAPI.deleteAttachment(item.id, att.id)),
      'Could not remove the file.',
    );

  const postComment = async () => {
    if (!commentDraft.trim() || isSubtask) return;
    const ok = await mutate(
      () => taskAPI.addComment(item.id, { body: commentDraft.trim() }),
      'Could not post the comment.',
    );
    if (ok) setCommentDraft('');
  };

  return (
    <div className="detail-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="detail-panel" role="dialog" aria-modal="true">
        <div className="detail-head">
          <button className="detail-close" onClick={onClose} aria-label="Close">
            <Icon name="close" size={13} strokeWidth={2.3} />
          </button>
          <div className="detail-title">{data.title}</div>
          <div className="detail-tags">
            <Badge status={statusKey} lateDays={data.late_by_days} />
            <PriorityTag priority={PRIORITY_TO_KEY[data.priority] || 'med'} />
            {data.entity_name && (
              <span className="entity-chip">
                <Icon name="clipboard" size={13} /> {data.entity_name}
              </span>
            )}
          </div>
        </div>

        <div className="detail-body">
          {error && <div className="reject-banner">{error}</div>}
          {data.status === 'REJECTED' && data.rejection_note && (
            <div className="reject-banner">
              <b>Sent back by your Team Lead.</b> {data.rejection_note}
            </div>
          )}

          <div className="detail-section">
            <h3>Description</h3>
            <div className="detail-desc">{data.description || 'No description.'}</div>
          </div>

          <div className="detail-section">
            <h3>Tags</h3>
            <TagsInput
              value={data.tags || []}
              onChange={updateTags}
              readOnly={!mineToEdit}
            />
          </div>

          {isParentWithSubs ? (
            <div className="detail-section">
              <h3>Completion</h3>
              <div className="empty-note">
                This task completes automatically once every sub-task is approved.
                Submit the sub-task{(data.subtasks || []).length > 1 ? 's' : ''} assigned
                to you from the list below.
              </div>
            </div>
          ) : (
          <div className="detail-section">
            <div className="section-head-row">
              <h3 style={{ margin: 0 }}>Completion</h3>
              <Toggle
                variant={done ? 'on' : submitted ? 'review' : ''}
                disabled={done || (submitted ? !canWithdraw : !canSubmit)}
                title={done ? 'Approved' : submitted
                  ? (canWithdraw ? 'Withdraw — back to In Progress' : 'Awaiting review')
                  : 'Submit for review'}
                onClick={async () => {
                  if (canSubmit) { onSubmit(item); return; }
                  if (submitted && canWithdraw && onWithdraw) {
                    setError('');
                    try {
                      await onWithdraw(item);
                      await load();
                      onChanged?.();
                    } catch (e) {
                      setError(getErrorMessage(e, 'Could not withdraw this submission.'));
                    }
                  }
                }}
              />
            </div>
            <div className="empty-note">
              {done
                ? 'Approved by your Team Lead.'
                : myPartWaiting
                  ? 'Your part is done — waiting for the other people on this sub-task.'
                  : submitted
                    ? (canWithdraw
                      ? 'Submitted — waiting for review. Toggle off to pull it back and keep working.'
                      : 'Submitted — waiting for your Team Lead to review.')
                    : canSubmit
                      ? (shared
                        ? 'When your part is done, record your time and notes.'
                        : 'When your work is done, submit it for your Team Lead to review.')
                      : 'This task is not currently assigned to you.'}
            </div>
            {shared && (data.entity_assignees || []).length > 0 && (
              <div className="empty-note" style={{ marginTop: 6 }}>
                {(data.entity_assignees || []).filter((a) => a.done).length}
                {' / '}
                {(data.entity_assignees || []).length} people done
              </div>
            )}
          </div>
          )}

          <div className="detail-section detail-grid">
            <div>
              <div className="detail-label">
                {shared
                  ? `Assignees (${(data.entity_assignees || []).length})`
                  : 'Assignee'}
              </div>
              {isParentWithSubs ? (
                <div className="empty-note" style={{ padding: '2px 0 8px' }}>Assigned per sub-task</div>
              ) : shared ? (
                <div className="assignee-chip-row">
                  {(data.entity_assignees || []).map((a) => (
                    <div key={a.id} className="assignee-chip">
                      <Avatar
                        initials={initialsOf(a.user?.name)}
                        color={colorFor(a.user_id)}
                        style={{ width: 26, height: 26, fontSize: 9.5 }}
                      />
                      <div className="ac-meta">
                        <div className="ac-name">
                          {a.user?.name || 'Member'}
                          {a.user_id === user.id && ' (You)'}
                        </div>
                        <div className={`ac-status${a.done ? ' done' : ''}`}>
                          {a.done
                            ? `Done${a.done_at ? ` · ${fmtDate(a.done_at, bsLabels)}` : ''}`
                            : 'In progress'}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : assignee ? (
                <div className="assignee-chip">
                  <Avatar
                    initials={initialsOf(assignee.name)}
                    color={colorFor(assignee.id)}
                    style={{ width: 26, height: 26, fontSize: 9.5 }}
                  />
                  <div className="ac-meta">
                    <div className="ac-name">{assignee.name}</div>
                    <div className="ac-status">Team Member</div>
                  </div>
                </div>
              ) : (
                <div className="empty-note" style={{ padding: '2px 0 8px' }}>Unassigned</div>
              )}
            </div>
            <div className="detail-dates">
              <div className="detail-label" style={{ textAlign: 'right' }}>Due date</div>
              <div className="dd">{myDue ? fmtDate(myDue, bsLabels) : '—'}</div>
              {!done && myDue && (
                <div className="dleft">
                  {dueMeta(myDue, statusKey, bsLabels)}
                </div>
              )}
              {done && (
                <>
                  <div className="detail-label" style={{ textAlign: 'right', marginTop: 16 }}>Completed</div>
                  <div className="dd">{fmtDate(data.updated_at || data.submitted_at, bsLabels)}</div>
                </>
              )}
            </div>
          </div>

          {data.total_subtasks > 0 && (
            <div className="detail-section">
              <div className="detail-progress-row">
                <h3 style={{ margin: 0 }}>Progress</h3>
                <b>{data.completed_subtasks}/{data.total_subtasks}</b>
              </div>
              <ProgressBar pct={Math.round(data.progress_percentage || 0)} />
            </div>
          )}

          {(data.subtasks || []).length > 0 && (
            <div className="detail-section">
              <h3>Subtasks</h3>
              {data.subtasks.map((st) => {
                const s = subState(st);
                const stKey = s.stMyPartWaiting ? 'review' : memberStatusKey(st);
                return (
                  <div className="subtask-card" key={st.id}>
                    <div className="section-head-row" style={{ marginBottom: 4 }}>
                      <div className="st-title">{st.title}</div>
                      {s.mine && (
                        <Toggle
                          variant={s.stDone ? 'on' : s.stSubmitted ? 'review' : ''}
                          disabled={
                            s.stDone || (s.stSubmitted ? !s.canWithdrawSt : !s.canSubmitSt)
                          }
                          title={s.stDone
                            ? 'Approved'
                            : s.stSubmitted
                              ? (s.canWithdrawSt
                                ? 'Withdraw — back to In Progress'
                                : 'Awaiting review')
                              : 'Submit for review'}
                          onClick={() => {
                            if (s.canSubmitSt) {
                              onSubmit({
                                id: st.id,
                                kind: 'SUBTASK',
                                title: st.title,
                                entityWide: s.stShared,
                              });
                            } else if (s.stSubmitted && s.canWithdrawSt) {
                              withdrawSub(st.id);
                            }
                          }}
                        />
                      )}
                    </div>
                    {st.description && <div className="st-desc">{st.description}</div>}
                    <div className="st-status-row" style={{ marginTop: 6 }}>
                      <Badge status={stKey} lateDays={st.late_by_days} />
                      {s.mine && (
                        <span style={{ fontSize: 11.5, color: 'var(--gray-mute)', fontWeight: 600 }}>
                          Assigned to you
                        </span>
                      )}
                    </div>
                    {st.status === 'REJECTED' && st.rejection_note && (
                      <div className="reject-banner" style={{ marginTop: 6 }}>
                        <b>Sent back by your Team Lead.</b> {st.rejection_note}
                      </div>
                    )}
                    {s.stMyPartWaiting && (
                      <div className="empty-note" style={{ marginTop: 4 }}>
                        Your part is done — waiting for the other people on this sub-task.
                      </div>
                    )}

                    <div className="st-meta">
                      <div className="st-meta-block">
                        <div className="detail-label">
                          {st.assigned_entity_wide
                            ? `Assignees (${subAssignees(st).length})`
                            : 'Assignee'}
                        </div>
                        {subAssignees(st).length === 0 ? (
                          <div className="empty-note" style={{ padding: 0 }}>Unassigned</div>
                        ) : (
                          <div className="assignee-chip-row">
                            {subAssignees(st).map((p) => (
                              <div className="assignee-chip" key={p.id}>
                                <Avatar
                                  initials={initialsOf(p.name)}
                                  color={colorFor(p.id)}
                                  style={{ width: 26, height: 26, fontSize: 9.5 }}
                                />
                                <div className="ac-meta">
                                  <div className="ac-name">
                                    {p.name}{p.id === user.id ? ' (You)' : ''}
                                  </div>
                                  {st.assigned_entity_wide && (
                                    <div className={`ac-status${p.done ? ' done' : ''}`}>
                                      {p.done
                                        ? `Done${p.done_at ? ` · ${fmtDate(p.done_at, bsLabels)}` : ''}`
                                        : 'In progress'}
                                    </div>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      <div className="st-meta-block st-meta-right">
                        <div className="detail-label">Due date</div>
                        <div className="dd">{s.stMyDue ? fmtDate(s.stMyDue, bsLabels) : '—'}</div>
                        {s.stDone && (
                          <>
                            <div className="detail-label" style={{ marginTop: 12 }}>Completed</div>
                            <div className="dd">{fmtDate(st.updated_at || st.submitted_at, bsLabels)}</div>
                            {st.completion_time_minutes != null && (
                              <div className="dleft">
                                {Math.floor(st.completion_time_minutes / 60)}h{' '}
                                {st.completion_time_minutes % 60}m logged
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </div>

                    {st.completion_notes && (
                      <div className="assignee-note" style={{ marginLeft: 0, marginTop: 6 }}>
                        {st.completion_notes}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="detail-section">
            <div className="section-head-row">
              <h3 style={{ margin: 0 }}>Attachments</h3>
              <button className="link-btn" disabled={uploading} onClick={() => fileRef.current?.click()}>
                {uploading ? 'Uploading…' : '+ Upload'}
              </button>
            </div>
            <input
              ref={fileRef} type="file" multiple style={{ display: 'none' }}
              accept={ATTACHMENT_ALLOWED_EXT.join(',')}
              onChange={(e) => { uploadFiles(e.target.files); e.target.value = ''; }}
            />
            <div
              className="dropzone"
              onClick={() => !uploading && fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (!uploading && e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files);
              }}
            >
              <div className="dz-icon"><Icon name="upload" size={22} strokeWidth={1.7} /></div>
              <div>{uploading ? <b>Uploading…</b> : <><b>Click to upload</b> or drag a file here</>}</div>
              <div className="muted-sm" style={{ fontSize: 11 }}>
                Up to {ATTACHMENT_MAX_BYTES / (1024 * 1024)} MB
              </div>
            </div>
            <div className="attach-list">
              {(data.attachments || []).length === 0 && (
                <div className="empty-note">No attachments yet.</div>
              )}
              {(data.attachments || []).map((f) => (
                <div className="attach-item" key={f.id}>
                  <div className="fname" onClick={() => download(f)} style={{ cursor: 'pointer' }}>
                    <div className="ficon">
                      {(f.filename.split('.').pop() || 'FILE').toUpperCase().slice(0, 4)}
                    </div>
                    {f.filename}
                  </div>
                  <div className="fsize">
                    {fmtBytes(f.size_bytes)}
                    <span className="attach-remove" onClick={() => removeAttachment(f)}>
                      <Icon name="close" size={13} strokeWidth={2.3} />
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {!isSubtask && (
            <div className="detail-section">
              <h3>Comments</h3>
              {(data.comments || []).length === 0 && <div className="empty-note">No comments yet.</div>}
              {(data.comments || []).map((c) => {
                const p = personFromUser(c.user) || { name: 'User', initials: '?', color: '#8A90A6' };
                return (
                  <div className="comment-item" key={c.id}>
                    <div className="c-avatar" style={{ background: p.color }}>{p.initials}</div>
                    <div className="c-body">
                      <div className="c-name">{p.name}</div>
                      <div className="c-bubble">{c.body}</div>
                      <div className="c-meta">
                        <span className="c-time">{new Date(c.created_at).toLocaleString()}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {!isSubtask && (
          <div className="detail-footer">
            <input
              type="text" placeholder="Write a comment..."
              value={commentDraft}
              onChange={(e) => setCommentDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && postComment()}
            />
            <button className="btn btn-primary" onClick={postComment}>Send</button>
          </div>
        )}
      </div>
    </div>
  );
}
