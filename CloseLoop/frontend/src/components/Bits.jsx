// Shared small components used in dashboards
import React from 'react';
import { Card, Badge, ProgressBar } from 'react-bootstrap';
import { Link } from 'react-router-dom';
import { useOrgPath } from '../orgPath.js';

const STATUS_VARIANT = {
  OPEN: 'secondary', PENDING: 'secondary', NOT_STARTED: 'secondary',
  ASSIGNED: 'info', IN_PROGRESS: 'primary', PENDING_REVIEW: 'warning',
  COMPLETED: 'success', CLOSED: 'success', REJECTED: 'danger',
  CARRIED_FORWARD: 'warning', APPROVED: 'success', ARCHIVED: 'dark',
  ACTIVE: 'success', PAUSED: 'warning', ENDED: 'dark',
};

export function displayStatus(status, assignedToId) {
  if (status === 'NOT_STARTED' && assignedToId) {
    return 'ASSIGNED';
  }
  return String(status || 'UNKNOWN');
}

export function StatusBadge({ status }) {
  const s = String(status || 'UNKNOWN');
  return <Badge bg={STATUS_VARIANT[s] || 'secondary'}>{s.replace(/_/g, ' ')}</Badge>;
}

export function StatCard({ label, value, icon, variant = 'primary' }) {
  return (
    <Card className="stat-card h-100">
      <Card.Body className="d-flex align-items-center">
        <div className={`text-${variant} me-3`} style={{ fontSize: '2rem' }}>
          <i className={`bi bi-${icon}`}></i>
        </div>
        <div>
          <div className="text-muted small text-uppercase">{label}</div>
          <div className="h3 mb-0">{value}</div>
        </div>
      </Card.Body>
    </Card>
  );
}

export function PlanRow({ plan }) {
  const orgPath = useOrgPath();
  const typeLabel = String(plan.type || 'Plan').replace(/_/g, ' ');
  const taskCount = plan.task_count ?? 0;
  const progress = plan.progress ?? 0;
  return (
    <Link to={orgPath(`/plans/${plan.id}`)} className="text-decoration-none text-dark">
      <Card className="mb-2 stat-card">
        <Card.Body className="py-3">
          <div className="d-flex align-items-center">
            <div className="flex-grow-1">
              <div className="fw-semibold">
                {plan.title}
                {plan.can_edit === false && (
                  <Badge bg="light" text="dark" pill className="ms-2" style={{ fontSize: 10 }}>
                    <i className="bi bi-eye me-1"></i>view only
                  </Badge>
                )}
              </div>
              <div className="muted-sm">
                {typeLabel}
                {plan.entity_name && ` · ${plan.entity_name}`}
                {plan.period_label && ` · ${plan.period_label}`}
                {plan.due_date && ` · Due ${new Date(plan.due_date).toLocaleDateString()}`}
                {` · ${taskCount} task(s)`}
                {plan.owner ? ` · Owner: ${plan.owner.name}` : ' · Unassigned'}
              </div>
            </div>
            <div style={{ width: 180 }} className="me-3">
              <div className="d-flex justify-content-between muted-sm mb-1">
                <span>Progress</span><span>{progress}%</span>
              </div>
              <ProgressBar now={progress} className="progress-thin" />
            </div>
            <StatusBadge status={plan.status} />
          </div>
        </Card.Body>
      </Card>
    </Link>
  );
}
