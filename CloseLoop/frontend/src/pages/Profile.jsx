// My Profile — self-service page. Every authenticated user, regardless of
// role (PLATFORM_OWNER, SUPER_ADMIN, TEAM_LEAD, TEAM_MEMBER),
// can update their own name / email / password here. Changing the password
// requires re-entering the current one.
import React, { useState } from 'react';
import { Card, Form, Button, Row, Col, Alert, Badge } from 'react-bootstrap';
import { useAuth } from '../auth.jsx';
import { authAPI } from '../apiEntities.js';
import { getErrorMessage } from '../api';

const ROLE_COLORS = {
  PLATFORM_OWNER: 'dark',
  SUPER_ADMIN: 'danger',
  TEAM_LEAD: 'primary',
  TEAM_MEMBER: 'success',
};

export default function Profile() {
  const { user, updateUser } = useAuth();
  const [name, setName] = useState(user.name);
  const [email, setEmail] = useState(user.email);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [saving, setSaving] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setErr('');
    setMsg('');

    if (newPassword && newPassword !== confirmPassword) {
      setErr('New password and confirmation do not match.');
      return;
    }
    if (newPassword && !currentPassword) {
      setErr('Enter your current password to set a new one.');
      return;
    }

    const payload = {};
    if (name !== user.name) payload.name = name;
    if (email !== user.email) payload.email = email;
    if (newPassword) {
      payload.current_password = currentPassword;
      payload.new_password = newPassword;
    }
    if (Object.keys(payload).length === 0) {
      setMsg('Nothing to update.');
      return;
    }

    setSaving(true);
    try {
      const { data } = await authAPI.updateMe(payload);
      updateUser({ name: data.name, email: data.email });
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setMsg('Profile updated.');
    } catch (e2) {
      setErr(getErrorMessage(e2));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Row className="justify-content-center">
      <Col md={7} lg={6}>
        <div className="d-flex align-items-center mb-3">
          <h3 className="mb-0"><i className="bi bi-person-circle me-2"></i>My Profile</h3>
          <div className="ms-auto text-end">
            {user.title && <div className="text-muted small">{user.title}</div>}
            <Badge bg={ROLE_COLORS[user.role] || 'secondary'}>
              {String(user.role).replace(/_/g, ' ')}
            </Badge>
          </div>
        </div>

        <Card>
          <Card.Body>
            <Form onSubmit={submit}>
              <Row className="g-3">
                <Col md={12}>
                  <Form.Label>Name</Form.Label>
                  <Form.Control value={name} onChange={(e) => setName(e.target.value)} required />
                </Col>
                <Col md={12}>
                  <Form.Label>Email</Form.Label>
                  <Form.Control type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
                </Col>

                <Col md={12}><hr /><h6>Change password</h6>
                  <div className="text-muted small mb-2">Leave blank to keep your current password.</div>
                </Col>
                <Col md={12}>
                  <Form.Label>Current password</Form.Label>
                  <Form.Control
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    autoComplete="current-password"
                  />
                </Col>
                <Col md={6}>
                  <Form.Label>New password</Form.Label>
                  <Form.Control
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </Col>
                <Col md={6}>
                  <Form.Label>Confirm new password</Form.Label>
                  <Form.Control
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </Col>
              </Row>

              {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
              {msg && <Alert variant="success" className="mt-3 py-2">{msg}</Alert>}

              <div className="d-flex justify-content-end mt-3">
                <Button type="submit" disabled={saving}>
                  {saving ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </Form>
          </Card.Body>
        </Card>
      </Col>
    </Row>
  );
}
