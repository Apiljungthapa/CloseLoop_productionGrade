// CarryForwardPage — Supervisor (SUPER_ADMIN / TEAM_LEAD) reviews
// items queued when a previous period was closed. Per group, they can approve
// (all items, or a subset by item_ids) or reject.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Card, Button, Modal, Form, Alert, Row, Col, Badge, Table, Spinner, Accordion
} from 'react-bootstrap';
import { useAuth } from '../auth.jsx';
import { carryForwardAPI, periodAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';

const apiErr = (e) => getErrorMessage(e, e?.message || 'Failed');

export default function CarryForwardPage() {
  const { user } = useAuth();
  const [groups, setGroups] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [statusFilter, setStatusFilter] = useState('');
  const [fromPeriod, setFromPeriod] = useState('');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [flash, setFlash] = useState(null);
  const [decideGroup, setDecideGroup] = useState(null);
  const [decideKind, setDecideKind] = useState('APPROVE');

  const canDecide = ['SUPER_ADMIN', 'TEAM_LEAD'].includes(user.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (statusFilter) params.status = statusFilter;
      if (fromPeriod) params.from_period_id = fromPeriod;
      const [gs, ps] = await Promise.all([
        carryForwardAPI.list(params),
        periodAPI.list().catch(() => ({ data: [] })),
      ]);
      setGroups(gs.data);
      setPeriods(ps.data);
    } catch (e) { setErr(apiErr(e)); }
    finally { setLoading(false); }
  }, [statusFilter, fromPeriod]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(t);
  }, [flash]);

  function periodLabel(id) {
    const p = periods.find((x) => x.id === id);
    return p ? p.label : (id ? id.slice(0, 8) : '—');
  }

  if (loading) return <div className="text-center py-5"><Spinner animation="border" /></div>;
  if (err) return <Alert variant="danger">{err}</Alert>;

  return (
    <>
      <div className="mb-4">
        <h2 className="dashboard-title mb-0">
          <i className="bi bi-arrow-right-circle me-2 text-warning"></i>Carry-Forward Review
        </h2>
        <div className="text-muted small">
          Items from closed periods awaiting decision. Approve to copy into the next period, reject to drop.
        </div>
      </div>

      {flash && (
        <Alert variant={flash.variant} onClose={() => setFlash(null)} dismissible className="py-2">
          {flash.message}
        </Alert>
      )}

      <Card className="mb-3">
        <Card.Body>
          <Row className="g-2 align-items-end">
            <Col md={4}>
              <Form.Label className="small text-muted mb-1">Status</Form.Label>
              <Form.Select size="sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">All</option>
                <option value="PENDING">Pending</option>
                <option value="APPROVED">Approved</option>
                <option value="REJECTED">Rejected</option>
              </Form.Select>
            </Col>
            <Col md={4}>
              <Form.Label className="small text-muted mb-1">From period</Form.Label>
              <Form.Select size="sm" value={fromPeriod} onChange={(e) => setFromPeriod(e.target.value)}>
                <option value="">All</option>
                {periods.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </Form.Select>
            </Col>
            <Col md={4}>
              <Button size="sm" variant="outline-secondary" onClick={load}>
                <i className="bi bi-arrow-clockwise me-1"></i>Refresh
              </Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>

      {groups.length === 0 ? (
        <div className="empty-state">
          <i className="bi bi-check2-all"></i>
          <div>No carry-forward groups match your filters.</div>
        </div>
      ) : (
        <Accordion defaultActiveKey={groups[0]?.id} alwaysOpen>
          {groups.map((g) => (
            <Accordion.Item key={g.id} eventKey={g.id} className="mb-2">
              <Accordion.Header>
                <div className="d-flex align-items-center w-100 me-3 flex-wrap gap-2">
                  <div className="flex-grow-1">
                    <strong>From {periodLabel(g.from_period_id)}</strong>
                    {g.to_period_id && (
                      <span className="text-muted small ms-2">→ {periodLabel(g.to_period_id)}</span>
                    )}
                  </div>
                  <Badge bg={
                    g.status === 'APPROVED' ? 'success' :
                    g.status === 'REJECTED' ? 'danger' :
                    'warning'
                  }>{g.status}</Badge>
                  <Badge bg="secondary">{(g.items || []).length} item{(g.items || []).length === 1 ? '' : 's'}</Badge>
                </div>
              </Accordion.Header>
              <Accordion.Body>
                {g.notes && (
                  <Alert variant="info" className="py-2 small">
                    <i className="bi bi-info-circle me-1"></i>{g.notes}
                  </Alert>
                )}

                {(g.items || []).length === 0 ? (
                  <div className="text-muted small">No items.</div>
                ) : (
                  <Table size="sm" responsive className="mb-3">
                    <thead>
                      <tr>
                        <th>Title</th>
                        <th>Type</th>
                        <th>Approved</th>
                        <th>Copied to</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(g.items || []).map((it) => (
                        <tr key={it.id}>
                          <td>{it.title}</td>
                          <td><Badge bg="light" text="dark">{it.item_type}</Badge></td>
                          <td>
                            {it.approved === true && <Badge bg="success">yes</Badge>}
                            {it.approved === false && <Badge bg="danger">no</Badge>}
                            {it.approved == null && <Badge bg="warning">pending</Badge>}
                          </td>
                          <td className="text-muted small">
                            {it.copied_to_task_id || it.copied_to_subtask_id || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}

                {canDecide && g.status === 'PENDING' && (
                  <div className="d-flex gap-2">
                    <Button variant="success" size="sm" onClick={() => { setDecideGroup(g); setDecideKind('APPROVE'); }}>
                      <i className="bi bi-check2 me-1"></i>Approve…
                    </Button>
                    <Button variant="danger" size="sm" onClick={() => { setDecideGroup(g); setDecideKind('REJECT'); }}>
                      <i className="bi bi-x-lg me-1"></i>Reject…
                    </Button>
                  </div>
                )}
              </Accordion.Body>
            </Accordion.Item>
          ))}
        </Accordion>
      )}

      <DecisionModal
        group={decideGroup}
        kind={decideKind}
        onHide={() => setDecideGroup(null)}
        onDone={() => { setDecideGroup(null); load(); }}
        setFlash={setFlash}
      />
    </>
  );
}

function DecisionModal({ group, kind, onHide, onDone, setFlash }) {
  const [notes, setNotes] = useState('');
  const [selectedIds, setSelectedIds] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (group) {
      setNotes('');
      // Default to all items selected when approving (subset-pick mode);
      // when rejecting, leave empty (backend rejects whole group).
      const all = (group.items || []).map((it) => it.id);
      setSelectedIds(kind === 'APPROVE' ? all : []);
    }
  }, [group, kind]);

  if (!group) return null;
  const allItems = group.items || [];

  function toggle(id) {
    setSelectedIds((cur) => cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  }
  function allOn() { setSelectedIds(allItems.map((i) => i.id)); }
  function allOff() { setSelectedIds([]); }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const payload = { decision: kind, notes: notes || null };
      if (kind === 'APPROVE' && selectedIds.length > 0 && selectedIds.length < allItems.length) {
        payload.item_ids = selectedIds;
      }
      await carryForwardAPI.decide(group.id, payload);
      setFlash?.({
        variant: kind === 'APPROVE' ? 'success' : 'warning',
        message: kind === 'APPROVE' ? 'Approved and copied to next period.' : 'Rejected.',
      });
      onDone();
    } catch (e) {
      setFlash?.({ variant: 'danger', message: apiErr(e) });
    } finally { setBusy(false); }
  }

  return (
    <Modal show={!!group} onHide={onHide} centered>
      <Form onSubmit={submit}>
        <Modal.Header closeButton>
          <Modal.Title>
            {kind === 'APPROVE' ? 'Approve carry-forward' : 'Reject carry-forward'}
          </Modal.Title>
        </Modal.Header>
        <Modal.Body>
          {kind === 'APPROVE' && allItems.length > 0 && (
            <>
              <div className="d-flex align-items-center mb-2">
                <strong>Items to copy</strong>
                <Badge bg="secondary" className="ms-2">{selectedIds.length} / {allItems.length}</Badge>
                <div className="ms-auto">
                  <Button size="sm" variant="link" onClick={allOn}>All</Button>
                  <Button size="sm" variant="link" onClick={allOff}>None</Button>
                </div>
              </div>
              <div style={{ maxHeight: 200, overflowY: 'auto' }} className="border rounded p-2 mb-3">
                {allItems.map((it) => (
                  <Form.Check
                    key={it.id}
                    type="checkbox"
                    id={`cf-${it.id}`}
                    label={<span>{it.title} <Badge bg="light" text="dark" className="ms-1">{it.item_type}</Badge></span>}
                    checked={selectedIds.includes(it.id)}
                    onChange={() => toggle(it.id)}
                  />
                ))}
              </div>
            </>
          )}
          <Form.Group>
            <Form.Label>Notes <span className="text-muted small">(optional)</span></Form.Label>
            <Form.Control
              as="textarea" rows={3} value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={kind === 'APPROVE' ? 'Reason for carrying these items forward…' : 'Reason for rejection…'}
            />
          </Form.Group>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide}>Cancel</Button>
          <Button variant={kind === 'APPROVE' ? 'success' : 'danger'} type="submit" disabled={busy}>
            {busy ? 'Saving…' : (kind === 'APPROVE' ? 'Approve' : 'Reject')}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}
