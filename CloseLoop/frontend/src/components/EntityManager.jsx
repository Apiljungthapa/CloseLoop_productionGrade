// Entities — multi-group assignment with bulk operations, edit modal, filters.
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Card, Button, Form, Modal, Alert, Spinner, ListGroup, Badge, InputGroup,
} from 'react-bootstrap';
import { entityAPI, entityGroupAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';

const TYPE_LABELS = {
  REGION: 'Region',
  DIVISION: 'Division',
  DEPARTMENT: 'Department',
  SUBSIDIARY: 'Sub-Region',
  BUSINESS_UNIT: 'Business Unit',
  CUSTOM: 'Custom',
};
const typeLabel = (t) => TYPE_LABELS[t] || t;

const flattenGroups = (nodes, depth = 0, acc = []) => {
  for (const node of nodes || []) {
    acc.push({ ...node, depth });
    if (Array.isArray(node.children) && node.children.length > 0) {
      flattenGroups(node.children, depth + 1, acc);
    }
  }
  return acc;
};

export default function EntityManager({
  groupId, onSelect, selectedEntityId, canManage = true, refreshKey = 0,
}) {
  const [entities, setEntities] = useState([]);
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState(''); // independent of `groupId` prop

  const [showEntityModal, setShowEntityModal] = useState(false);
  const [editingEntity, setEditingEntity] = useState(null); // null => create

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [showBulkModal, setShowBulkModal] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      // entities filter: prop groupId OR explicit groupFilter; search either way
      const params = {};
      if (groupId) params.group_id = groupId;
      else if (groupFilter) params.group_id = groupFilter;
      if (search.trim()) params.search = search.trim();

      const [entRes, grpRes] = await Promise.all([
        entityAPI.list(params),
        entityGroupAPI.tree({ include_inactive: true }),
      ]);
      setEntities(entRes.data || []);
      setGroups(flattenGroups(grpRes.data || []));
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load entities'));
    } finally {
      setLoading(false);
    }
  }, [groupId, groupFilter, search]);

  useEffect(() => { loadData(); }, [loadData, refreshKey]);

  // Clear selection when entity list changes substantially
  useEffect(() => {
    setSelectedIds((prev) => {
      const stillThere = new Set(entities.map((e) => e.id));
      const next = new Set();
      prev.forEach((id) => { if (stillThere.has(id)) next.add(id); });
      return next;
    });
  }, [entities]);

  const groupById = useMemo(() => {
    const m = new Map();
    for (const g of groups) m.set(g.id, g);
    return m;
  }, [groups]);

  const groupName = (id) => groupById.get(id)?.name || '—';

  // ----- Create / Edit -----
  const openCreate = () => {
    const defaults = groupId ? [groupId] : (groupFilter ? [groupFilter] : []);
    setEditingEntity({
      isNew: true,
      id: null,
      name: '',
      description: '',
      group_ids: defaults,
      primary_group_id: defaults[0] || '',
    });
    setShowEntityModal(true);
  };

  const openEdit = (entity) => {
    setEditingEntity({
      isNew: false,
      id: entity.id,
      name: entity.name,
      description: entity.description || '',
      group_ids: entity.group_ids || [entity.group_id],
      primary_group_id: entity.group_id,
    });
    setShowEntityModal(true);
  };

  async function handleEntitySubmit(e) {
    e.preventDefault();
    const ent = editingEntity;
    if (!ent.group_ids || ent.group_ids.length === 0) {
      setError('Please select at least one entity group');
      return;
    }
    const primary = ent.primary_group_id || ent.group_ids[0];
    const group_ids = ent.group_ids.includes(primary) ? ent.group_ids : [primary, ...ent.group_ids];
    const payload = {
      name: ent.name.trim(),
      description: ent.description || null,
      group_ids,
      primary_group_id: primary,
    };
    try {
      if (ent.isNew) await entityAPI.create(payload);
      else await entityAPI.update(ent.id, payload);
      setShowEntityModal(false);
      setEditingEntity(null);
      await loadData();
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to save entity'));
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Delete this entity and all associated plans?')) return;
    try {
      await entityAPI.delete(id);
      if (selectedEntityId === id && onSelect) onSelect(null);
      await loadData();
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete entity'));
    }
  }

  // ----- Bulk selection / assign -----
  const toggleSelected = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === entities.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(entities.map((e) => e.id)));
  };

  return (
    <>
      <Card>
        <Card.Header className="bg-white">
          <div className="d-flex justify-content-between align-items-center mb-2">
            <strong>
              <i className="bi bi-building me-2"></i>
              Entities{groupId ? ` in ${groupName(groupId)}` : ''}
            </strong>
            {canManage && (
              <div className="d-flex gap-2">
                {selectedIds.size > 0 && (
                  <Button
                    size="sm"
                    variant="warning"
                    onClick={() => setShowBulkModal(true)}
                  >
                    <i className="bi bi-collection me-1"></i>
                    Bulk assign ({selectedIds.size})
                  </Button>
                )}
                <Button size="sm" variant="success" onClick={openCreate}>
                  <i className="bi bi-plus-lg me-1"></i>New Entity
                </Button>
              </div>
            )}
          </div>
          <div className="d-flex flex-wrap gap-2 align-items-center">
            <InputGroup size="sm" style={{ maxWidth: 260 }}>
              <InputGroup.Text><i className="bi bi-search"></i></InputGroup.Text>
              <Form.Control
                placeholder="Search entities…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && (
                <Button variant="outline-secondary" onClick={() => setSearch('')}>
                  <i className="bi bi-x"></i>
                </Button>
              )}
            </InputGroup>
            {!groupId && (
              <Form.Select
                size="sm"
                value={groupFilter}
                onChange={(e) => setGroupFilter(e.target.value)}
                style={{ maxWidth: 260 }}
              >
                <option value="">All groups</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {Array(g.depth).fill('\u00A0\u00A0').join('')}{g.name}
                  </option>
                ))}
              </Form.Select>
            )}
            <Button
              size="sm"
              variant="outline-secondary"
              onClick={loadData}
              className="ms-auto"
              title="Refresh"
            >
              <i className="bi bi-arrow-clockwise"></i>
            </Button>
          </div>
        </Card.Header>
        <Card.Body className="p-0">
          {error && <Alert variant="danger" className="m-3 mb-0 py-2 small">{error}</Alert>}
          {loading && (
            <div className="text-center py-4">
              <Spinner animation="border" size="sm" />
            </div>
          )}
          {!loading && entities.length === 0 && (
            <div className="text-muted text-center py-4">
              <i className="bi bi-inbox d-block mb-2 fs-3"></i>
              No entities {search || groupFilter || groupId ? 'match your filters' : 'yet'}
            </div>
          )}
          {!loading && entities.length > 0 && (
            <>
              {canManage && (
                <div className="px-3 py-2 border-bottom bg-light d-flex align-items-center small">
                  <Form.Check
                    type="checkbox"
                    checked={selectedIds.size > 0 && selectedIds.size === entities.length}
                    onChange={toggleSelectAll}
                    ref={(el) => {
                      if (el) el.indeterminate =
                        selectedIds.size > 0 && selectedIds.size < entities.length;
                    }}
                    label={
                      <span className="text-muted">
                        Select all ({entities.length})
                      </span>
                    }
                  />
                  {selectedIds.size > 0 && (
                    <Badge bg="primary" className="ms-2">
                      {selectedIds.size} selected
                    </Badge>
                  )}
                </div>
              )}
              <ListGroup variant="flush">
                {entities.map((entity) => {
                  const primary = entity.group_id;
                  const secondaryIds = (entity.group_ids || []).filter((g) => g !== primary);
                  return (
                    <ListGroup.Item
                      key={entity.id}
                      className="d-flex justify-content-between align-items-center"
                    >
                      <div className="d-flex align-items-start gap-2 flex-grow-1">
                        {canManage && (
                          <Form.Check
                            type="checkbox"
                            checked={selectedIds.has(entity.id)}
                            onChange={() => toggleSelected(entity.id)}
                            onClick={(e) => e.stopPropagation()}
                          />
                        )}
                        <div>
                          <div className="fw-600">{entity.name}</div>
                          <div className="small text-muted mt-1">
                            <Badge bg="primary" className="me-1" title="Primary group">
                              <i className="bi bi-star-fill me-1"></i>
                              {entity.primary_group_name || groupName(primary)}
                            </Badge>
                            {secondaryIds.map((gid) => (
                              <Badge key={gid} bg="light" text="dark" className="me-1">
                                <i className="bi bi-folder me-1"></i>
                                {groupName(gid)}
                              </Badge>
                            ))}
                          </div>
                          {entity.description && (
                            <div className="small text-muted mt-1">{entity.description}</div>
                          )}
                        </div>
                      </div>
                      <div className="gap-2 d-flex align-items-center">
                        {onSelect && (
                          <Button
                            size="sm"
                            variant={selectedEntityId === entity.id ? 'primary' : 'outline-primary'}
                            onClick={() => onSelect(entity.id)}
                          >
                            {selectedEntityId === entity.id ? 'Selected' : 'Select'}
                          </Button>
                        )}
                        {canManage && (
                          <>
                            <Button
                              size="sm"
                              variant="outline-secondary"
                              onClick={() => openEdit(entity)}
                              title="Edit"
                            >
                              <i className="bi bi-pencil"></i>
                            </Button>
                            <Button
                              size="sm"
                              variant="outline-danger"
                              onClick={() => handleDelete(entity.id)}
                              title="Delete"
                            >
                              <i className="bi bi-trash"></i>
                            </Button>
                          </>
                        )}
                      </div>
                    </ListGroup.Item>
                  );
                })}
              </ListGroup>
            </>
          )}
        </Card.Body>
      </Card>

      {/* Create / Edit Entity */}
      <Modal show={showEntityModal} onHide={() => setShowEntityModal(false)}>
        <Modal.Header closeButton>
          <Modal.Title>
            {editingEntity?.isNew ? 'Create Entity' : `Edit "${editingEntity?.name}"`}
          </Modal.Title>
        </Modal.Header>
        <Form onSubmit={handleEntitySubmit}>
          <Modal.Body>
            <Form.Group className="mb-3">
              <Form.Label>Entity Name <span className="text-danger">*</span></Form.Label>
              <Form.Control
                placeholder="e.g. New York, San Francisco, UK Subsidiary"
                value={editingEntity?.name || ''}
                onChange={(e) => setEditingEntity({ ...editingEntity, name: e.target.value })}
                required
              />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>
                Groups <span className="text-danger">*</span>
                <span className="text-muted small ms-2">
                  (select all groups this entity belongs to)
                </span>
              </Form.Label>
              <div className="border rounded p-2" style={{ maxHeight: 220, overflowY: 'auto' }}>
                {groups.length === 0 && (
                  <div className="text-muted small">No groups available — create one first.</div>
                )}
                {groups.map((g) => (
                  <Form.Check
                    key={g.id}
                    type="checkbox"
                    id={`ent-grp-${g.id}`}
                    label={
                      <span>
                        {Array(g.depth).fill('\u00A0\u00A0').join('')}
                        {g.name}
                        <Badge bg="light" text="dark" className="ms-2">
                          {typeLabel(g.type)}
                        </Badge>
                        {g.is_active === false && (
                          <Badge bg="secondary" className="ms-1">inactive</Badge>
                        )}
                      </span>
                    }
                    checked={editingEntity?.group_ids?.includes(g.id) || false}
                    onChange={() => {
                      const cur = editingEntity?.group_ids || [];
                      const next = cur.includes(g.id)
                        ? cur.filter((id) => id !== g.id)
                        : [...cur, g.id];
                      // If we just unchecked the primary, fall back to the first remaining
                      let primary = editingEntity?.primary_group_id;
                      if (primary === g.id && !next.includes(primary)) primary = next[0] || '';
                      if (!primary && next.length) primary = next[0];
                      setEditingEntity({ ...editingEntity, group_ids: next, primary_group_id: primary });
                    }}
                  />
                ))}
              </div>
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Primary Group (for hierarchy display)</Form.Label>
              <Form.Select
                value={editingEntity?.primary_group_id || ''}
                onChange={(e) => setEditingEntity({
                  ...editingEntity,
                  primary_group_id: e.target.value,
                })}
              >
                <option value="">— Select primary —</option>
                {(editingEntity?.group_ids || []).map((gid) => (
                  <option key={gid} value={gid}>{groupName(gid)}</option>
                ))}
              </Form.Select>
              <Form.Text className="text-muted">
                Defaults to the first selected group. Used for hierarchy display.
              </Form.Text>
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Description</Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                placeholder="Optional"
                value={editingEntity?.description || ''}
                onChange={(e) => setEditingEntity({ ...editingEntity, description: e.target.value })}
              />
            </Form.Group>
            {error && <Alert variant="danger" className="mt-2 py-2 small">{error}</Alert>}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowEntityModal(false)}>Cancel</Button>
            <Button variant="primary" type="submit">
              {editingEntity?.isNew ? 'Create' : 'Save'}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>

      {/* Bulk assign / detach */}
      <BulkAssignModal
        show={showBulkModal}
        onHide={() => setShowBulkModal(false)}
        groups={groups}
        selectedIds={Array.from(selectedIds)}
        selectedEntities={entities.filter((e) => selectedIds.has(e.id))}
        onSaved={async () => {
          setShowBulkModal(false);
          setSelectedIds(new Set());
          await loadData();
        }}
      />
    </>
  );
}

// ============================================================
// BulkAssignModal
// ============================================================
function BulkAssignModal({ show, onHide, groups, selectedIds, selectedEntities, onSaved }) {
  const [mode, setMode] = useState('add');
  const [chosenGroups, setChosenGroups] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (show) {
      setMode('add');
      setChosenGroups([]);
      setErr('');
    }
  }, [show]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      await entityAPI.bulkAssignGroups({
        entity_ids: selectedIds,
        group_ids: chosenGroups,
        mode,
      });
      await onSaved();
    } catch (e2) {
      setErr(getErrorMessage(e2, 'Bulk assign failed'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal show={show} onHide={onHide} size="lg">
      <Form onSubmit={submit}>
        <Modal.Header closeButton>
          <Modal.Title>
            Bulk Assign Groups
            <Badge bg="primary" className="ms-2">{selectedIds.length} entities</Badge>
          </Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <div className="mb-3 small text-muted">
            Selected: {selectedEntities.map((e) => e.name).join(', ')}
          </div>
          <Form.Group className="mb-3">
            <Form.Label>Action</Form.Label>
            <div className="d-flex gap-2 flex-wrap">
              {[
                { v: 'add', label: 'Add to groups', icon: 'bi-plus-circle' },
                { v: 'replace', label: 'Replace memberships', icon: 'bi-arrow-repeat' },
                { v: 'remove', label: 'Remove from groups', icon: 'bi-dash-circle' },
              ].map((opt) => (
                <Button
                  key={opt.v}
                  type="button"
                  variant={mode === opt.v ? 'primary' : 'outline-primary'}
                  size="sm"
                  onClick={() => setMode(opt.v)}
                >
                  <i className={`bi ${opt.icon} me-1`}></i>{opt.label}
                </Button>
              ))}
            </div>
            <Form.Text className="text-muted">
              {mode === 'add' && 'Append these groups; existing memberships are kept.'}
              {mode === 'replace' && 'Overwrite each entity\u2019s membership set (primary group is preserved).'}
              {mode === 'remove' && 'Detach these groups (cannot detach an entity\u2019s primary group).'}
            </Form.Text>
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>Groups</Form.Label>
            <div className="border rounded p-2" style={{ maxHeight: 260, overflowY: 'auto' }}>
              {groups.map((g) => (
                <Form.Check
                  key={g.id}
                  type="checkbox"
                  id={`bulk-grp-${g.id}`}
                  label={
                    <span>
                      {Array(g.depth).fill('\u00A0\u00A0').join('')}
                      {g.name}
                      <Badge bg="light" text="dark" className="ms-2">
                        {TYPE_LABELS[g.type] || g.type}
                      </Badge>
                      {g.is_active === false && (
                        <Badge bg="secondary" className="ms-1">inactive</Badge>
                      )}
                    </span>
                  }
                  checked={chosenGroups.includes(g.id)}
                  onChange={() => {
                    setChosenGroups((prev) =>
                      prev.includes(g.id)
                        ? prev.filter((id) => id !== g.id)
                        : [...prev, g.id]
                    );
                  }}
                />
              ))}
            </div>
          </Form.Group>
          {err && <Alert variant="danger" className="mt-2 py-2 small">{err}</Alert>}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide} disabled={busy}>Cancel</Button>
          <Button
            variant="primary"
            type="submit"
            disabled={busy || (chosenGroups.length === 0 && mode !== 'replace')}
          >
            {busy ? <Spinner size="sm" animation="border" /> : 'Apply'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}
