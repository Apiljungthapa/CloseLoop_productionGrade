import React, { useEffect, useState } from 'react';
import { Button, Form, Modal } from 'react-bootstrap';

export default function CompletionSubmissionModal({
  show,
  onHide,
  title,
  onSubmit,
  submitting = false,
  initialValues = {},
}) {
  const [notes, setNotes] = useState(initialValues.completion_notes || '');
  const [hours, setHours] = useState(
    initialValues.completion_time_minutes != null
      ? Math.floor(initialValues.completion_time_minutes / 60)
      : ''
  );
  const [minutes, setMinutes] = useState(
    initialValues.completion_time_minutes != null
      ? initialValues.completion_time_minutes % 60
      : ''
  );

  useEffect(() => {
    if (!show) return;
    setNotes(initialValues.completion_notes || '');
    setHours(
      initialValues.completion_time_minutes != null
        ? Math.floor(initialValues.completion_time_minutes / 60)
        : ''
    );
    setMinutes(
      initialValues.completion_time_minutes != null
        ? initialValues.completion_time_minutes % 60
        : ''
    );
  }, [show, initialValues.completion_notes, initialValues.completion_time_minutes]);

  const handleSubmit = () => {
    const totalMinutes = (Number(hours) || 0) * 60 + (Number(minutes) || 0);
    onSubmit({
      completion_notes: notes.trim() || null,
      completion_time_minutes: totalMinutes || null,
    });
  };

  return (
    <Modal show={show} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>{title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Form.Group className="mb-3">
          <Form.Label>Completion Notes</Form.Label>
          <Form.Control
            as="textarea"
            rows={4}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Describe what was completed…"
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>Time Spent</Form.Label>
          <div className="d-flex gap-2">
            <Form.Control
              type="number"
              min="0"
              placeholder="Hours"
              value={hours}
              onChange={(e) => setHours(e.target.value)}
            />
            <Form.Control
              type="number"
              min="0"
              max="59"
              placeholder="Minutes"
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
            />
          </div>
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide} disabled={submitting}>Cancel</Button>
        <Button variant="primary" onClick={handleSubmit} disabled={submitting}>
          {submitting ? 'Submitting…' : 'Submit'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
