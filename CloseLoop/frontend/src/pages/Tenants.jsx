import React, { useEffect, useState } from 'react';
import { Card, Table, Form, Button, Row, Col, Alert, Badge, Modal } from 'react-bootstrap';
import api, { getErrorMessage } from '../api';

export default function Tenants() {
  const [tenants, setTenants] = useState([]);
  const [show, setShow] = useState(false);
  const [credentials, setCredentials] = useState(null); // { tenant, client_id, client_secret }

  async function load() { const { data } = await api.get('/tenants'); setTenants(data); }
  useEffect(() => { load(); }, []);

  async function toggle(t) {
    await api.patch(`/tenants/${t.id}`, { status: t.status === 'active' ? 'suspended' : 'active' });
    load();
  }
  async function remove(id) {
    if (!confirm('Delete tenant and ALL its data? This is irreversible.')) return;
    await api.delete(`/tenants/${id}`); load();
  }
  async function rotateCredentials(t) {
    if (!confirm(`Rotate the API secret for "${t.name}"? Any integration using the old secret stops working immediately.`)) return;
    const { data } = await api.post(`/tenants/${t.id}/rotate-credentials`);
    setCredentials({ tenantName: t.name, client_id: data.client_id, client_secret: data.client_secret });
    load();
  }

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <h3 className="mb-0"><i className="bi bi-building me-2"></i>Platform — Tenants</h3>
        <Button className="ms-auto" onClick={() => setShow(true)}>
          <i className="bi bi-plus-lg me-1"></i>New tenant
        </Button>
      </div>
      <Alert variant="warning" className="small">
        <i className="bi bi-shield-lock me-2"></i>
        Platform Owner view — you can manage tenants but cannot read their task content.
      </Alert>

      <Card>
        <Card.Body className="p-0">
          <Table responsive hover className="mb-0">
            <thead className="table-light">
              <tr><th>Name</th><th>Plan limit</th><th>Status</th><th>API client_id</th><th>Created</th><th></th></tr>
            </thead>
            <tbody>
              {tenants.map((t) => (
                <tr key={t.id}>
                  <td><strong>{t.name}</strong></td>
                  <td className="text-muted small">{t.plan_limit ?? '—'}</td>
                  <td><Badge bg={t.status === 'active' ? 'success' : 'secondary'}>{t.status}</Badge></td>
                  <td className="text-muted small"><code>{t.client_id || '—'}</code></td>
                  <td className="text-muted small">{new Date(t.created_at).toLocaleDateString()}</td>
                  <td className="text-end text-nowrap">
                    <Button size="sm" variant="outline-secondary" className="me-1" onClick={() => rotateCredentials(t)} title="Regenerate this tenant's API client_secret">
                      <i className="bi bi-key"></i>
                    </Button>
                    <Button size="sm" variant="outline-secondary" className="me-1" onClick={() => toggle(t)}>
                      {t.status === 'active' ? 'Suspend' : 'Activate'}
                    </Button>
                    <Button size="sm" variant="outline-danger" onClick={() => remove(t.id)}>Delete</Button>
                  </td>
                </tr>
              ))}
              {tenants.length === 0 && (
                <tr><td colSpan={6} className="text-center text-muted py-4">No tenants yet.</td></tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>

      <CreateModal
        show={show}
        onHide={() => setShow(false)}
        onCreated={(created) => {
          setShow(false);
          if (created?.client_id && created?.client_secret) {
            setCredentials({ tenantName: created.name, client_id: created.client_id, client_secret: created.client_secret });
          }
          load();
        }}
      />
      <CredentialsModal credentials={credentials} onHide={() => setCredentials(null)} />
    </>
  );
}

function CredentialsModal({ credentials, onHide }) {
  if (!credentials) return null;
  return (
    <Modal show={!!credentials} onHide={onHide} centered>
      <Modal.Header closeButton><Modal.Title>API credentials — {credentials.tenantName}</Modal.Title></Modal.Header>
      <Modal.Body>
        <Alert variant="warning" className="small mb-3">
          <i className="bi bi-exclamation-triangle me-2"></i>
          The client_secret is shown <strong>only once</strong>, right now. Copy it somewhere safe —
          it can't be retrieved again, only rotated (which invalidates it).
        </Alert>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">client_id</Form.Label>
          <Form.Control readOnly value={credentials.client_id} onFocus={(e) => e.target.select()} />
        </Form.Group>
        <Form.Group className="mb-2">
          <Form.Label className="small text-muted">client_secret</Form.Label>
          <Form.Control readOnly value={credentials.client_secret} onFocus={(e) => e.target.select()} />
        </Form.Group>
        <div className="small text-muted">
          Used with <code>POST /api/oauth/token</code> (grant_type=client_credentials) to get a bearer
          token for the read-only <code>/api/integration/*</code> endpoints — separate from human login.
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="primary" onClick={onHide}>Done</Button>
      </Modal.Footer>
    </Modal>
  );
}

function CreateModal({ show, onHide, onCreated }) {
  const [form, setForm] = useState({ name: '', plan_limit: 100, super_admin: { name: '', email: '', password: '' } });
  const [err, setErr] = useState('');
  async function submit(e) {
    e.preventDefault(); setErr('');
    try {
      const { data } = await api.post('/tenants', form);
      setForm({ name: '', plan_limit: 100, super_admin: { name: '', email: '', password: '' } });
      onCreated(data);
    } catch (e) { setErr(getErrorMessage(e)); }
  }
  return (
    <Modal show={show} onHide={onHide}>
      <Form onSubmit={submit}>
        <Modal.Header closeButton><Modal.Title>Create tenant</Modal.Title></Modal.Header>
        <Modal.Body>
          <Row className="g-3">
            <Col md={8}><Form.Label>Tenant name</Form.Label>
              <Form.Control value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></Col>
            <Col md={4}><Form.Label>Plan limit</Form.Label>
              <Form.Control type="number" value={form.plan_limit} onChange={(e) => setForm({ ...form, plan_limit: +e.target.value })} /></Col>
            <Col md={12}><hr /><h6>Super Admin (initial user)</h6></Col>
            <Col md={6}><Form.Label>Name</Form.Label>
              <Form.Control value={form.super_admin.name} onChange={(e) => setForm({ ...form, super_admin: { ...form.super_admin, name: e.target.value } })} required /></Col>
            <Col md={6}><Form.Label>Email</Form.Label>
              <Form.Control type="email" value={form.super_admin.email} onChange={(e) => setForm({ ...form, super_admin: { ...form.super_admin, email: e.target.value } })} required /></Col>
            <Col md={12}><Form.Label>Password</Form.Label>
              <Form.Control type="text" value={form.super_admin.password} onChange={(e) => setForm({ ...form, super_admin: { ...form.super_admin, password: e.target.value } })} required /></Col>
          </Row>
          {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide}>Cancel</Button>
          <Button variant="primary" type="submit">Create tenant</Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}
