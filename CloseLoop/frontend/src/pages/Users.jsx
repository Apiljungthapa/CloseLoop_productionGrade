import React, { useEffect, useState, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Form, Button, Row, Col, Alert, Badge, Modal, Spinner, InputGroup,
} from 'react-bootstrap';
import {
  userAPI, entityAPI, entityGroupAPI, assignmentAPI, groupAssignmentAPI,
} from '../apiEntities.js';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import { getErrorMessage } from '../api';
import Icon from '../components/ui/Icon';
import { colorFor, initialsOf, fmtDate } from './leadDashUtils';

const ROLE_VARIANT = {
  PLATFORM_OWNER: 'dark',
  SUPER_ADMIN: 'danger',
  TEAM_LEAD: 'primary',
  TEAM_MEMBER: 'success',
};

// leaddash pill classes for the styled list
const ROLE_PILL = {
  PLATFORM_OWNER: 'role-pill-owner',
  SUPER_ADMIN: 'role-pill-admin',
  TEAM_LEAD: 'role-pill-lead',
  TEAM_MEMBER: 'role-pill-member',
};

const flattenGroups = (nodes, depth = 0, acc = []) => {
  for (const n of nodes || []) {
    acc.push({ ...n, depth });
    if (Array.isArray(n.children) && n.children.length) {
      flattenGroups(n.children, depth + 1, acc);
    }
  }
  return acc;
};

export default function Users() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const orgPath = useOrgPath();
  const [users, setUsers] = useState([]);
  const [entities, setEntities] = useState([]);
  const [groups, setGroups] = useState([]);
  const [show, setShow] = useState(false);
  const [edit, setEdit] = useState(null);
  const [assign, setAssign] = useState(null); // user being managed

  const load = useCallback(async () => {
    const [{ data: us }, entsRes, grpsRes] = await Promise.all([
      userAPI.list(),
      entityAPI.list().catch(() => ({ data: [] })),
      entityGroupAPI.tree({ include_inactive: true }).catch(() => ({ data: [] })),
    ]);
    setUsers(us);
    setEntities(entsRes.data || []);
    setGroups(flattenGroups(grpsRes.data || []));
  }, []);
  useEffect(() => { load(); }, [load]);

  const entityNameById = (id) => entities.find((e) => e.id === id)?.name || '—';

  // Team Lead should only see TEAM_LEAD and TEAM_MEMBER in the users list
  const displayedUsers = user.role === 'TEAM_LEAD'
    ? users.filter((u) => u.role === 'TEAM_LEAD' || u.role === 'TEAM_MEMBER')
    : users;

  async function remove(id) {
    if (!confirm('Delete this user?')) return;
    await userAPI.delete(id);
    load();
  }

  const canEdit = user.role === 'SUPER_ADMIN';
  const canCreateUsers = ['SUPER_ADMIN', 'TEAM_LEAD'].includes(user.role);
  const isTL = user.role === 'TEAM_LEAD';

  const counts = useMemo(() => ({
    total: displayedUsers.length,
    leads: displayedUsers.filter((u) => u.role === 'TEAM_LEAD').length,
    members: displayedUsers.filter((u) => u.role === 'TEAM_MEMBER').length,
  }), [displayedUsers]);

  return (
    <div className="leaddash leaddash-page">
      <div className="page-wrap">
        <div className="page-head">
          <div>
            <div className="admin-title-row">
              <Icon name="users" size={22} style={{ color: 'var(--blue)' }} />
              <h1>Users</h1>
            </div>
            <p>
              {counts.total} {isTL ? 'people on your team' : 'people in this workspace'}
              {' · '}
              {counts.leads} lead{counts.leads === 1 ? '' : 's'}, {counts.members} member{counts.members === 1 ? '' : 's'}
            </p>
          </div>
          {canCreateUsers && (
            <div className="head-actions">
              <button className="btn btn-primary" onClick={() => setShow(true)}>
                + Invite user
              </button>
            </div>
          )}
        </div>

        <div className="users-table-card">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Role</th>
                <th>Title</th>
                <th>Primary Entity</th>
                <th>Last login</th>
                <th>Joined</th>
                <th style={{ textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {displayedUsers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="users-empty">No users yet.</td>
                </tr>
              ) : displayedUsers.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="user-cell">
                      <div className="u-avatar" style={{ background: colorFor(u.id || u.name) }}>
                        {initialsOf(u.name)}
                      </div>
                      <div className="u-id">
                        <div className="u-name">
                          {u.name}
                          {u.id === user.id && <span className="u-you">You</span>}
                        </div>
                        <div className="u-email">{u.email}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className={`role-pill ${ROLE_PILL[u.role] || ''}`}>
                      {String(u.role).replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="u-muted">{u.title || '—'}</td>
                  <td className="u-muted">
                    {u.primary_entity_id ? entityNameById(u.primary_entity_id) : '—'}
                  </td>
                  <td className="u-muted">
                    {u.last_login ? new Date(u.last_login).toLocaleString() : 'Never'}
                  </td>
                  <td className="u-muted">
                    {u.created_at ? fmtDate(u.created_at) : '—'}
                  </td>
                  <td>
                    <div className="u-actions">
                      {isTL && u.role === 'TEAM_MEMBER' && (
                        <button
                          type="button"
                          className="u-action-btn"
                          onClick={() => navigate(orgPath(`/users/${u.id}/time-log`))}
                          title={`View ${u.name}'s time log`}
                        >
                          <Icon name="bars" size={13} /> Time log
                        </button>
                      )}
                      {(canEdit || user.id === u.id) && (
                        <button
                          type="button"
                          className="u-action-btn"
                          onClick={() => setEdit({
                            id: u.id, name: u.name, title: u.title, primary: u.primary_entity_id,
                          })}
                          title="Edit title & primary entity"
                        >
                          <Icon name="edit" size={13} /> Details
                        </button>
                      )}
                      {canEdit && (
                        <button
                          type="button"
                          className="u-action-btn"
                          onClick={() => setAssign(u)}
                          title="Manage entity & group assignments"
                        >
                          <Icon name="layers" size={13} /> Assignments
                        </button>
                      )}
                      {user.role === 'SUPER_ADMIN' && u.id !== user.id && (
                        <button
                          type="button"
                          className="u-action-btn u-action-danger"
                          onClick={() => remove(u.id)}
                          title={`Delete ${u.name}`}
                        >
                          <Icon name="trash" size={13} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <InviteModal
        show={show}
        onHide={() => setShow(false)}
        onCreated={() => { setShow(false); load(); }}
        role={user.role}
        entities={entities}
      />
      <EditPrimaryModal
        edit={edit}
        entities={entities}
        canEditTitle={canEdit}
        onHide={() => setEdit(null)}
        onSaved={() => { setEdit(null); load(); }}
      />
      <ManageAssignmentsModal
        user={assign}
        entities={entities}
        groups={groups}
        currentRole={user.role}
        onHide={() => setAssign(null)}
        onSaved={() => { setAssign(null); load(); }}
      />
    </div>
  );
}

// ============================================================
// Invite
// ============================================================
function InviteModal({ show, onHide, onCreated, role, entities }) {
  const [form, setForm] = useState({
    name: '', email: '', password: '', role: 'TEAM_MEMBER', title: '', primary_entity_id: '',
  });
  const [err, setErr] = useState('');

  const allowedRoles =
    role === 'SUPER_ADMIN'
      ? ['TEAM_LEAD', 'TEAM_MEMBER']
      : ['TEAM_MEMBER'];

  useEffect(() => {
    if (show) {
      setForm({ name: '', email: '', password: '', role: 'TEAM_MEMBER', title: '', primary_entity_id: '' });
      setErr('');
    }
  }, [show]);

  async function submit(e) {
    e.preventDefault();
    setErr('');
    try {
      const payload = { ...form };
      if (!payload.title) delete payload.title;
      if (!payload.primary_entity_id) delete payload.primary_entity_id;
      await userAPI.create(payload);
      onCreated();
    } catch (e2) { setErr(getErrorMessage(e2)); }
  }

  return (
    <Modal show={show} onHide={onHide}>
      <Form onSubmit={submit}>
        <Modal.Header closeButton><Modal.Title>Invite a user</Modal.Title></Modal.Header>
        <Modal.Body>
          <Row className="g-3">
            <Col md={6}>
              <Form.Label>Name</Form.Label>
              <Form.Control value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </Col>
            <Col md={6}>
              <Form.Label>Email</Form.Label>
              <Form.Control type="email" value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            </Col>
            <Col md={6}>
              <Form.Label>Password</Form.Label>
              <Form.Control type="text" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })} required />
            </Col>
            <Col md={6}>
              <Form.Label>Role</Form.Label>
              <Form.Select value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {allowedRoles.map((r) => (
                  <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>
                ))}
              </Form.Select>
            </Col>
            <Col md={12}>
              <Form.Label>Title <span className="text-muted">(optional)</span></Form.Label>
              <Form.Control
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="e.g. Finance Controller, Ledger Accountant"
              />
            </Col>
            <Col md={12}>
              <Form.Label>Primary Entity <span className="text-danger">*</span></Form.Label>
              <Form.Select value={form.primary_entity_id}
                onChange={(e) => setForm({ ...form, primary_entity_id: e.target.value })}
                required>
                <option value="">(select an entity)</option>
                {entities.map((ent) => (
                  <option key={ent.id} value={ent.id}>{ent.name}</option>
                ))}
              </Form.Select>
              <Form.Text className="text-muted">
                Every user must have a primary entity.
              </Form.Text>
            </Col>
          </Row>
          {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide}>Cancel</Button>
          <Button variant="primary" type="submit">Invite</Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}

// ============================================================
// Edit Primary Entity
// ============================================================
function EditPrimaryModal({ edit, entities, canEditTitle, onHide, onSaved }) {
  const [selected, setSelected] = useState(edit?.primary || '');
  const [title, setTitle] = useState(edit?.title || '');
  const [err, setErr] = useState('');

  useEffect(() => {
    setSelected(edit?.primary || '');
    setTitle(edit?.title || '');
    setErr('');
  }, [edit]);

  async function save(e) {
    e.preventDefault();
    setErr('');
    try {
      const payload = { primary_entity_id: selected || null };
      if (canEditTitle) payload.title = title || null;
      await userAPI.update(edit.id, payload);
      onSaved();
    } catch (e2) {
      setErr(getErrorMessage(e2));
    }
  }

  if (!edit) return null;
  return (
    <Modal show={!!edit} onHide={onHide}>
      <Form onSubmit={save}>
        <Modal.Header closeButton>
          <Modal.Title>Edit Details — {edit.name}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {canEditTitle && (
            <div className="mb-3">
              <Form.Label>Title</Form.Label>
              <Form.Control
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Finance Controller, Ledger Accountant"
              />
              <Form.Text className="text-muted">
                Job title/designation shown next to their name. Doesn't affect permissions.
              </Form.Text>
            </div>
          )}
          <Form.Label>Primary Entity</Form.Label>
          <Form.Select value={selected || ''} onChange={(e) => setSelected(e.target.value)}>
            <option value="">(none)</option>
            {entities.map((ent) => (
              <option key={ent.id} value={ent.id}>{ent.name}</option>
            ))}
          </Form.Select>
          {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide}>Cancel</Button>
          <Button variant="primary" type="submit">Save</Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}

// ============================================================
// Manage Assignments (secondary entities + group access)
// ============================================================
function ManageAssignmentsModal({ user, entities, groups, currentRole, onHide, onSaved }) {
  const [loading, setLoading] = useState(false);
  const [assignments, setAssignments] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const [secondarySelected, setSecondarySelected] = useState(new Set());
  const [groupSelected, setGroupSelected] = useState(new Set());

  const [entSearch, setEntSearch] = useState('');
  const [grpSearch, setGrpSearch] = useState('');

  const visibleEntities = useMemo(() => {
    const q = entSearch.trim().toLowerCase();
    return entities
      .filter((e) => e.id !== user?.primary_entity_id)
      .filter((e) => !q || e.name.toLowerCase().includes(q));
  }, [entities, entSearch, user]);

  const visibleGroups = useMemo(() => {
    const q = grpSearch.trim().toLowerCase();
    return groups.filter((g) => !q || g.name.toLowerCase().includes(q));
  }, [groups, grpSearch]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    (async () => {
      setLoading(true); setErr('');
      try {
        const { data } = await userAPI.assignments(user.id);
        if (cancelled) return;
        setAssignments(data);
        setSecondarySelected(new Set((data.secondary_entities || []).map((e) => e.id)));
        setGroupSelected(new Set((data.groups || []).map((g) => g.id)));
      } catch (e2) {
        if (!cancelled) setErr(getErrorMessage(e2, 'Failed to load assignments'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user]);

  if (!user) return null;

  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      await assignmentAPI.bulk({
        user_id: user.id,
        entity_ids: Array.from(secondarySelected),
        mode: 'replace',
      });
      if (currentRole === 'SUPER_ADMIN') {
        await groupAssignmentAPI.bulk({
          user_id: user.id,
          group_ids: Array.from(groupSelected),
          mode: 'replace',
        });
      }
      onSaved();
    } catch (e2) {
      setErr(getErrorMessage(e2, 'Failed to save assignments'));
    } finally {
      setBusy(false);
    }
  }

  const toggleSet = (set, id, setter) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id); else next.add(id);
    setter(next);
  };

  return (
    <Modal show={!!user} onHide={onHide} size="lg">
      <Form onSubmit={save}>
        <Modal.Header closeButton>
          <Modal.Title>
            Manage Assignments — {user.name}
            {user.title && <span className="text-muted fs-6 ms-2">({user.title})</span>}
            <Badge bg={ROLE_VARIANT[user.role] || 'secondary'} className="ms-2">
              {String(user.role).replace(/_/g, ' ')}
            </Badge>
          </Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {loading ? (
            <div className="text-center py-4"><Spinner animation="border" /></div>
          ) : (
            <>
              {/* Primary entity readout */}
              <div className="mb-3 p-2 bg-light rounded border d-flex align-items-center">
                <i className="bi bi-star-fill text-warning me-2"></i>
                <div>
                  <div className="small text-muted">Primary Entity (use "Primary" button to change)</div>
                  <div className="fw-600">
                    {assignments?.primary_entity?.name || (
                      <span className="text-danger">Not set</span>
                    )}
                  </div>
                </div>
              </div>

              {/* Secondary entities */}
              <h6 className="fw-bold mb-2">
                <i className="bi bi-building me-2"></i>
                Secondary Entities
                <span className="text-muted small ms-2">
                  ({secondarySelected.size} selected)
                </span>
              </h6>
              <div className="text-muted small mb-2">
                Additional entities this user can access (besides the primary).
              </div>
              <InputGroup size="sm" className="mb-2">
                <InputGroup.Text><i className="bi bi-search"></i></InputGroup.Text>
                <Form.Control
                  placeholder="Search entities…"
                  value={entSearch}
                  onChange={(e) => setEntSearch(e.target.value)}
                />
              </InputGroup>
              <div className="border rounded p-2 mb-3"
                   style={{ maxHeight: 180, overflowY: 'auto' }}>
                {visibleEntities.length === 0 && (
                  <div className="text-muted small">No matching entities.</div>
                )}
                {visibleEntities.map((ent) => (
                  <Form.Check
                    key={ent.id}
                    type="checkbox"
                    id={`ua-ent-${ent.id}`}
                    label={ent.name}
                    checked={secondarySelected.has(ent.id)}
                    onChange={() => toggleSet(secondarySelected, ent.id, setSecondarySelected)}
                  />
                ))}
              </div>

              {/* Group access (SUPER_ADMIN only) */}
              {currentRole === 'SUPER_ADMIN' && (
                <>
                  <h6 className="fw-bold mb-2">
                    <i className="bi bi-diagram-3 me-2"></i>
                    Group Access
                    <span className="text-muted small ms-2">
                      ({groupSelected.size} selected)
                    </span>
                  </h6>
                  <div className="text-muted small mb-2">
                    Groups this user can manage — includes all descendant
                    groups and their entities.
                  </div>
                  <InputGroup size="sm" className="mb-2">
                    <InputGroup.Text><i className="bi bi-search"></i></InputGroup.Text>
                    <Form.Control
                      placeholder="Search groups…"
                      value={grpSearch}
                      onChange={(e) => setGrpSearch(e.target.value)}
                    />
                  </InputGroup>
                  <div className="border rounded p-2"
                       style={{ maxHeight: 180, overflowY: 'auto' }}>
                    {visibleGroups.length === 0 && (
                      <div className="text-muted small">No matching groups.</div>
                    )}
                    {visibleGroups.map((g) => (
                      <Form.Check
                        key={g.id}
                        type="checkbox"
                        id={`ua-grp-${g.id}`}
                        label={
                          <span>
                            {Array(g.depth).fill('\u00A0\u00A0').join('')}
                            {g.name}
                            {g.is_active === false && (
                              <Badge bg="secondary" className="ms-2">inactive</Badge>
                            )}
                          </span>
                        }
                        checked={groupSelected.has(g.id)}
                        onChange={() => toggleSet(groupSelected, g.id, setGroupSelected)}
                      />
                    ))}
                  </div>
                </>
              )}

              {err && <Alert variant="danger" className="mt-3 py-2 small">{err}</Alert>}
            </>
          )}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={busy}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={busy || loading}>
            {busy ? <Spinner size="sm" animation="border" /> : 'Save assignments'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}
