import React from 'react';
import { Modal, Button } from 'react-bootstrap';

export default function ConfirmationModal({ show, onHide, title = 'Confirm', message = 'Are you sure?', onConfirm, confirmLabel = 'Confirm', cancelLabel = 'Cancel', confirmVariant = 'primary' }) {
  return (
    <Modal show={show} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>{title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <div className="small text-muted">{message}</div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>{cancelLabel}</Button>
        <Button variant={confirmVariant} onClick={() => { if (onConfirm) onConfirm(); onHide(); }}>{confirmLabel}</Button>
      </Modal.Footer>
    </Modal>
  );
}
