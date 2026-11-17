// Generate Plans modal. Plans are NEVER created manually in Phase 1 — they're
// always generated from a Close Pack + Period + one-or-more Entities. The backend
// snapshots the active pack version, instantiates Tasks/SubTasks,
// and (when applicable) carries forward open work from the previous period.
import React, { useEffect, useState } from 'react';
import { Button, Modal, Form, Alert, Spinner, Row, Col, Badge } from 'react-bootstrap';
import { closePackAPI, periodAPI, entityAPI, planAPI, userAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';

const apiErr = (e) => getErrorMessage(e, e?.message || 'Failed to generate plans');

export default function NewPlanButton({ onCreated, variant = 'primary', label = 'Generate Plans' }) {
  const [show, setShow] = useState(false);
  const [packs, setPacks] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [entities, setEntities] = useState([]);
  const [leads, setLeads] = useState([]);
  const [packId, setPackId] = useState('');
  const [periodId, setPeriodId] = useState('');
  const [entityIds, setEntityIds] = useState([]);
  const [ownerId, setOwnerId] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!show) return;
    setErr('');
    setLoading(true);
    Promise.all([closePackAPI.list(), periodAPI.list(), entityAPI.list(), userAPI.list()])
      .then(([p, pe, e, u]) => {
        const activePacks = (p.data || []).filter((x) => x.active !== false);
        setPacks(activePacks);
        setPeriods(pe.data || []);
        setEntities(e.data || []);
        setLeads((u.data || []).filter((x) => x.role === 'TEAM_LEAD'));
        // sensible defaults: first active pack, the current/most-recent period
        if (activePacks.length === 1) setPackId(activePacks[0].id);
        const current = (pe.data || []).find((x) => x.status === 'OPEN') || (pe.data || [])[0];
        if (current) setPeriodId(current.id);
      })
      .catch((e) => setErr(apiErr(e)))
      .finally(() => setLoading(false));
  }, [show]);

  function reset() {
    setPackId(''); setPeriodId(''); setEntityIds([]); setOwnerId(''); setErr('');
  }

  function toggleEntity(id) {
    setEntityIds((cur) => cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]);
  }

  function selectAllEntities() {
    setEntityIds(entities.map((e) => e.id));
  }
  function clearEntities() { setEntityIds([]); }

  async function submit(e) {
    e.preventDefault();
    setErr('');
    if (!packId) { setErr('Select a close pack'); return; }
    if (!periodId) { setErr('Select a period'); return; }
    if (entityIds.length === 0) { setErr('Select at least one entity'); return; }
    setBusy(true);
    try {
      const { data } = await planAPI.generate({
        close_pack_id: packId,
        period_id: periodId,
        entity_ids: entityIds,
        owner_id: ownerId || null,
      });
      reset();
      setShow(false);
      onCreated?.(data);
    } catch (e) { setErr(apiErr(e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <Button variant={variant} onClick={() => setShow(true)}>
        <i className="bi bi-magic me-1"></i>{label}
      </Button>
      <Modal show={show} onHide={() => { setShow(false); reset(); }} centered size="lg">
        <Form onSubmit={submit}>
          <Modal.Header closeButton>
            <Modal.Title><i className="bi bi-magic me-2"></i>Generate Plans</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            {loading ? (
              <div className="text-center py-4"><Spinner animation="border" size="sm" /> Loading…</div>
            ) : (
              <>
                <Row className="g-3">
                  <Col md={6}>
                    <Form.Label>Close Pack</Form.Label>
                    <Form.Select value={packId} onChange={(e) => setPackId(e.target.value)} required>
                      <option value="">— select pack —</option>
                      {packs.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.calendar_type === 'BS' ? 'Nepali (BS)' : 'English (AD)'})
                        </option>
                      ))}
                    </Form.Select>
                    <Form.Text className="text-muted">
                      Only active packs are listed. The current version is snapshotted into each plan.
                    </Form.Text>
                  </Col>
                  <Col md={6}>
                    <Form.Label>Period</Form.Label>
                    <Form.Select value={periodId} onChange={(e) => setPeriodId(e.target.value)} required>
                      <option value="">— select period —</option>
                      {periods.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.label || `${p.start_date} → ${p.end_date}`} [{p.status}]
                        </option>
                      ))}
                    </Form.Select>
                    <Form.Text className="text-muted">
                      Pick from existing periods (configure via Periods page).
                    </Form.Text>
                  </Col>
                  <Col md={12}>
                    <Form.Label>Assign whole pack to a Team Lead (optional)</Form.Label>
                    <Form.Select value={ownerId} onChange={(e) => setOwnerId(e.target.value)}>
                      <option value="">— unassigned (view-only until assigned) —</option>
                      {leads.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </Form.Select>
                    <Form.Text className="text-muted">
                      The Lead can then add/edit tasks and assign work within it. Leave unassigned
                      to hand it off later from the plan's page — everyone can view unassigned
                      packs, only the owner (or an admin) can edit one.
                    </Form.Text>
                  </Col>
                </Row>

                <hr />

                <div className="d-flex align-items-center mb-2">
                  <strong className="me-2">Entities</strong>
                  <Badge bg="secondary">{entityIds.length} / {entities.length}</Badge>
                  <div className="ms-auto">
                    <Button size="sm" variant="link" onClick={selectAllEntities}>Select all</Button>
                    <Button size="sm" variant="link" onClick={clearEntities}>Clear</Button>
                  </div>
                </div>
                {entities.length === 0 && (
                  <Alert variant="warning" className="py-2 small mb-0">
                    No entities visible to you. Ask a Super Admin to assign you to entities/groups.
                  </Alert>
                )}
                <div style={{ maxHeight: 240, overflowY: 'auto' }} className="border rounded p-2">
                  {entities.map((e) => (
                    <Form.Check
                      key={e.id}
                      type="checkbox"
                      id={`gen-ent-${e.id}`}
                      label={
                        <span>
                          {e.name}
                          {e.group_name && <span className="text-muted small ms-2">({e.group_name})</span>}
                        </span>
                      }
                      checked={entityIds.includes(e.id)}
                      onChange={() => toggleEntity(e.id)}
                    />
                  ))}
                </div>
                {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
              </>
            )}
          </Modal.Body>
          <Modal.Footer>
            <Button variant="secondary" onClick={() => { setShow(false); reset(); }}>Cancel</Button>
            <Button variant="primary" type="submit" disabled={busy || loading}>
              {busy ? <><Spinner size="sm" animation="border" /> Generating…</> : (
                <><i className="bi bi-magic me-1"></i>Generate {entityIds.length || ''} Plan{entityIds.length === 1 ? '' : 's'}</>
              )}
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </>
  );
}
