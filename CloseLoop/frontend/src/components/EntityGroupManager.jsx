// Entity Group Hierarchy — recursive tree view with search, filter, activate/edit.
import React, {
  useState, useEffect, useMemo, useRef,
} from 'react';
import {
  Card, Button, Form, Modal, Alert, Spinner, Badge, InputGroup,
} from 'react-bootstrap';
import { entityGroupAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';

const ENTITY_GROUP_TYPES = [
  'REGION', 'DIVISION', 'DEPARTMENT', 'SUBSIDIARY', 'BUSINESS_UNIT', 'CUSTOM',
];

const TYPE_LABELS = {
  REGION: 'Region',
  DIVISION: 'Division',
  DEPARTMENT: 'Department',
  SUBSIDIARY: 'Sub-Region',
  BUSINESS_UNIT: 'Business Unit',
  CUSTOM: 'Custom',
};

const TYPE_VARIANT = {
  REGION: 'primary',
  DIVISION: 'info',
  DEPARTMENT: 'success',
  SUBSIDIARY: 'warning',
  BUSINESS_UNIT: 'secondary',
  CUSTOM: 'dark',
};

const typeLabel = (t) => TYPE_LABELS[t] || t;

// Flatten tree (for parent dropdown / search results)
const flattenGroups = (nodes, depth = 0, acc = []) => {
  for (const node of nodes || []) {
    acc.push({ ...node, depth });
    if (Array.isArray(node.children) && node.children.length > 0) {
      flattenGroups(node.children, depth + 1, acc);
    }
  }
  return acc;
};

// Filter tree by search and active flag (keep ancestors of any match)
function filterTree(nodes, searchLc, activeFilter) {
  const out = [];
  for (const n of nodes) {
    const filteredChildren = filterTree(n.children || [], searchLc, activeFilter);
    const selfMatchSearch = !searchLc || n.name.toLowerCase().includes(searchLc);
    const selfMatchActive =
      activeFilter === 'all' ||
      (activeFilter === 'active' && n.is_active !== false) ||
      (activeFilter === 'inactive' && n.is_active === false);
    if ((selfMatchSearch && selfMatchActive) || filteredChildren.length > 0) {
      out.push({ ...n, children: filteredChildren });
    }
  }
  return out;
}

// Collect IDs of all nodes in a forest
function collectIds(nodes, acc = new Set()) {
  for (const n of nodes) {
    acc.add(n.id);
    if (n.children) collectIds(n.children, acc);
  }
  return acc;
}

// ============================================================
// TreeNode — recursive
// ============================================================
function TreeNode({
  node, depth, expanded, toggleExpand,
  onSelect, selectedGroupId,
  onEdit, onDelete, onToggleActive, canManage,
}) {
  const hasChildren = Array.isArray(node.children) && node.children.length > 0;
  const isOpen = expanded.has(node.id);
  const isSelected = selectedGroupId === node.id;
  const isInactive = node.is_active === false;

  return (
    <div className="tree-node">
      <div
        className={`tree-row${isSelected ? ' selected' : ''}${isInactive ? ' inactive' : ''}`}
        style={{ paddingLeft: `${depth * 1.25}rem` }}
      >
        <button
          type="button"
          className="tree-chevron"
          onClick={() => hasChildren && toggleExpand(node.id)}
          aria-label={hasChildren ? (isOpen ? 'Collapse' : 'Expand') : 'Leaf'}
        >
          {hasChildren ? (
            <i className={`bi ${isOpen ? 'bi-chevron-down' : 'bi-chevron-right'}`}></i>
          ) : (
            <i className="bi bi-dot text-muted"></i>
          )}
        </button>

        <button
          type="button"
          className="tree-label"
          onClick={() => onSelect && onSelect(node.id)}
          title={node.description || ''}
        >
          <i className={`bi ${hasChildren ? 'bi-folder2-open' : 'bi-folder'} me-2 text-primary`}></i>
          <span className="fw-600">{node.name}</span>
          <Badge bg={TYPE_VARIANT[node.type] || 'secondary'} className="ms-2">
            {typeLabel(node.type)}
          </Badge>
          {typeof node.entity_count === 'number' && (
            <Badge bg="light" text="dark" className="ms-1" title="Entities in this group">
              <i className="bi bi-building me-1"></i>{node.entity_count}
            </Badge>
          )}
          {isInactive && <Badge bg="secondary" className="ms-1">Inactive</Badge>}
        </button>

        {canManage && (
          <div className="tree-actions">
            <Button size="sm" variant="outline-secondary" onClick={() => onEdit(node)} title="Edit">
              <i className="bi bi-pencil"></i>
            </Button>
            <Button
              size="sm"
              variant={isInactive ? 'outline-success' : 'outline-warning'}
              onClick={() => onToggleActive(node)}
              title={isInactive ? 'Activate' : 'Deactivate'}
            >
              <i className={`bi ${isInactive ? 'bi-toggle-on' : 'bi-toggle-off'}`}></i>
            </Button>
            <Button size="sm" variant="outline-danger" onClick={() => onDelete(node)} title="Delete">
              <i className="bi bi-trash"></i>
            </Button>
          </div>
        )}
      </div>

      {hasChildren && isOpen && (
        <div className="tree-children">
          {node.children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              toggleExpand={toggleExpand}
              onSelect={onSelect}
              selectedGroupId={selectedGroupId}
              onEdit={onEdit}
              onDelete={onDelete}
              onToggleActive={onToggleActive}
              canManage={canManage}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// Main component
// ============================================================
export default function EntityGroupManager({
  onSelect, selectedGroupId, canManage = true, onGroupsChanged,
}) {
  const [groupTree, setGroupTree] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState('active'); // active | inactive | all
  const [typeFilter, setTypeFilter] = useState('');

  const [expanded, setExpanded] = useState(new Set());

  const [showModal, setShowModal] = useState(false);
  const [editingGroup, setEditingGroup] = useState(null); // null when creating

  const containerRef = useRef(null);

  // Clicking outside the tree/panel deselects the current group (no refresh needed).
  useEffect(() => {
    function handleClickOutside(e) {
      if (!selectedGroupId) return;
      if (containerRef.current && containerRef.current.contains(e.target)) return;
      if (e.target.closest && e.target.closest('.modal')) return; // ignore edit/create modal
      if (onSelect) onSelect(null);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [selectedGroupId, onSelect]);

  async function loadGroups() {
    setLoading(true);
    try {
      const res = await entityGroupAPI.tree({ include_inactive: true });
      setGroupTree(res.data || []);
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load entity groups'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadGroups(); }, []);

  // Filtered tree (search + active flag)
  const filteredTree = useMemo(() => {
    const searchLc = (search || '').trim().toLowerCase();
    let t = filterTree(groupTree, searchLc, activeFilter);
    if (typeFilter) {
      // keep nodes matching type OR with descendants matching
      const matchType = (nodes) => {
        const out = [];
        for (const n of nodes) {
          const fc = matchType(n.children || []);
          if (n.type === typeFilter || fc.length) out.push({ ...n, children: fc });
        }
        return out;
      };
      t = matchType(t);
    }
    return t;
  }, [groupTree, search, activeFilter, typeFilter]);

  const flatForDropdown = useMemo(() => flattenGroups(groupTree), [groupTree]);

  // Auto-expand all when searching
  useEffect(() => {
    if (search.trim()) {
      const ids = collectIds(filteredTree);
      setExpanded(ids);
    }
  }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleExpand = (id) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const expandAll = () => setExpanded(collectIds(groupTree));
  const collapseAll = () => setExpanded(new Set());

  const openCreate = (parentId = null) => {
    setEditingGroup({
      isNew: true,
      name: '', description: '',
      type: parentId ? 'SUBSIDIARY' : 'REGION',
      parent_id: parentId,
      is_active: true,
    });
    setShowModal(true);
  };

  const openEdit = (group) => {
    setEditingGroup({
      isNew: false,
      id: group.id,
      name: group.name,
      description: group.description || '',
      type: group.type,
      parent_id: group.parent_id || null,
      is_active: group.is_active !== false,
    });
    setShowModal(true);
  };

  async function handleSubmit(e) {
    e.preventDefault();
    const g = editingGroup;
    try {
      if (g.isNew) {
        const payload = {
          name: g.name.trim(),
          description: g.description || null,
          type: g.type,
          parent_id: g.parent_id || null,
          is_active: g.is_active,
        };
        await entityGroupAPI.create(payload);
      } else {
        const payload = {
          name: g.name.trim(),
          description: g.description || null,
          type: g.type,
          parent_id: g.parent_id || null,
          is_active: g.is_active,
        };
        await entityGroupAPI.update(g.id, payload);
      }
      setShowModal(false);
      setEditingGroup(null);
      await loadGroups();
      if (onGroupsChanged) onGroupsChanged();
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to save entity group'));
    }
  }

  async function handleDelete(group) {
    if (!window.confirm(`Delete group "${group.name}"? This cannot be undone.`)) return;
    try {
      await entityGroupAPI.delete(group.id);
      if (selectedGroupId === group.id && onSelect) onSelect(null);
      await loadGroups();
      if (onGroupsChanged) onGroupsChanged();
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete entity group'));
    }
  }

  async function handleToggleActive(group) {
    try {
      if (group.is_active === false) await entityGroupAPI.activate(group.id);
      else await entityGroupAPI.deactivate(group.id);
      await loadGroups();
      if (onGroupsChanged) onGroupsChanged();
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to toggle group status'));
    }
  }

  return (
    <>
      <Card className="mb-3" ref={containerRef}>
        <Card.Header className="bg-white">
          <div className="d-flex justify-content-between align-items-center mb-2">
            <strong><i className="bi bi-diagram-3 me-2"></i>Entity Groups</strong>
            {canManage && (
              <Button size="sm" variant="primary" onClick={() => openCreate(selectedGroupId)}>
                <i className="bi bi-plus-lg me-1"></i>
                {selectedGroupId ? 'Add Child' : 'New Group'}
              </Button>
            )}
          </div>
          <div className="d-flex flex-wrap gap-2 align-items-center">
            <InputGroup size="sm" style={{ maxWidth: 240 }}>
              <InputGroup.Text><i className="bi bi-search"></i></InputGroup.Text>
              <Form.Control
                placeholder="Search groups…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {search && (
                <Button variant="outline-secondary" onClick={() => setSearch('')}>
                  <i className="bi bi-x"></i>
                </Button>
              )}
            </InputGroup>
            <Form.Select
              size="sm"
              value={activeFilter}
              onChange={(e) => setActiveFilter(e.target.value)}
              style={{ maxWidth: 130 }}
            >
              <option value="active">Active only</option>
              <option value="inactive">Inactive only</option>
              <option value="all">All</option>
            </Form.Select>
            <Form.Select
              size="sm"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              style={{ maxWidth: 160 }}
            >
              <option value="">All types</option>
              {ENTITY_GROUP_TYPES.map((t) => (
                <option key={t} value={t}>{typeLabel(t)}</option>
              ))}
            </Form.Select>
            <div className="ms-auto d-flex gap-1">
              <Button size="sm" variant="outline-secondary" onClick={expandAll} title="Expand all">
                <i className="bi bi-arrows-expand"></i>
              </Button>
              <Button size="sm" variant="outline-secondary" onClick={collapseAll} title="Collapse all">
                <i className="bi bi-arrows-collapse"></i>
              </Button>
              <Button size="sm" variant="outline-secondary" onClick={loadGroups} title="Refresh">
                <i className="bi bi-arrow-clockwise"></i>
              </Button>
            </div>
          </div>
        </Card.Header>
        <Card.Body className="p-2">
          {error && <Alert variant="danger" className="mb-2 py-2 small">{error}</Alert>}
          {loading && <div className="text-center py-3"><Spinner animation="border" size="sm" /></div>}
          {!loading && filteredTree.length === 0 && (
            <div className="text-muted text-center py-4">
              <i className="bi bi-inbox d-block mb-2 fs-3"></i>
              {search || typeFilter || activeFilter !== 'active'
                ? 'No groups match your filters'
                : 'No entity groups yet. Create your first group to get started.'}
            </div>
          )}
          {!loading && filteredTree.length > 0 && (
            <div className="tree-root">
              {filteredTree.map((node) => (
                <TreeNode
                  key={node.id}
                  node={node}
                  depth={0}
                  expanded={expanded}
                  toggleExpand={toggleExpand}
                  onSelect={onSelect}
                  selectedGroupId={selectedGroupId}
                  onEdit={openEdit}
                  onDelete={handleDelete}
                  onToggleActive={handleToggleActive}
                  canManage={canManage}
                />
              ))}
            </div>
          )}
        </Card.Body>
      </Card>

      <Modal show={showModal} onHide={() => setShowModal(false)}>
        <Modal.Header closeButton>
          <Modal.Title>
            {editingGroup?.isNew ? 'Create Entity Group' : `Edit "${editingGroup?.name}"`}
          </Modal.Title>
        </Modal.Header>
        <Form onSubmit={handleSubmit}>
          <Modal.Body>
            <Form.Group className="mb-3">
              <Form.Label>Name <span className="text-danger">*</span></Form.Label>
              <Form.Control
                placeholder="e.g. EMEA, Europe, UK"
                value={editingGroup?.name || ''}
                onChange={(e) => setEditingGroup({ ...editingGroup, name: e.target.value })}
                required
              />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Type</Form.Label>
              <Form.Select
                value={editingGroup?.type || 'REGION'}
                onChange={(e) => setEditingGroup({ ...editingGroup, type: e.target.value })}
              >
                {ENTITY_GROUP_TYPES.map((t) => (
                  <option key={t} value={t}>{typeLabel(t)}</option>
                ))}
              </Form.Select>
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Parent Group</Form.Label>
              <Form.Select
                value={editingGroup?.parent_id || ''}
                onChange={(e) => setEditingGroup({
                  ...editingGroup,
                  parent_id: e.target.value || null,
                })}
              >
                <option value="">— Top level (no parent) —</option>
                {flatForDropdown
                  .filter((g) => !editingGroup || g.id !== editingGroup.id)
                  .map((g) => (
                    <option key={g.id} value={g.id}>
                      {Array(g.depth).fill('\u00A0\u00A0').join('')}{g.name} ({typeLabel(g.type)})
                    </option>
                  ))}
              </Form.Select>
              <Form.Text className="text-muted">
                Re-parenting validates for cycles automatically.
              </Form.Text>
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Description</Form.Label>
              <Form.Control
                as="textarea"
                rows={2}
                placeholder="Optional"
                value={editingGroup?.description || ''}
                onChange={(e) => setEditingGroup({ ...editingGroup, description: e.target.value })}
              />
            </Form.Group>
            <Form.Check
              type="switch"
              id="group-active-switch"
              label="Active"
              checked={editingGroup?.is_active !== false}
              onChange={(e) => setEditingGroup({ ...editingGroup, is_active: e.target.checked })}
            />
            {error && <Alert variant="danger" className="mt-3 py-2 small">{error}</Alert>}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowModal(false)}>Cancel</Button>
            <Button variant="primary" type="submit">
              {editingGroup?.isNew ? 'Create' : 'Save'}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </>
  );
}
