import React, { useState, useEffect } from "react";
import {
  Card,
  Badge,
  Button,
  ProgressBar,
  Spinner,
  Alert,
} from "react-bootstrap";
import { taskAPI } from "../apiEntities.js";
import { StatusBadge, displayStatus } from "./Bits.jsx";

export default function TaskDetailPanel({ taskId, onClose, reload }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (!taskId) {
      setLoading(false);
      return;
    }

    async function loadDetail() {
      try {
        setLoading(true);
        setError(null);
        const { data } = await taskAPI.getDetail(taskId);
        setDetail(data);
      } catch (err) {
        setError(err?.response?.data?.detail || "Failed to load task details");
      } finally {
        setLoading(false);
      }
    }

    loadDetail();
  }, [taskId]);

  if (!taskId) return null;

  if (loading) {
    return (
      <div className="cl-task-detail-panel">
        <div className="p-4 text-center">
          <Spinner animation="border" size="sm" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="cl-task-detail-panel">
        <div className="p-3">
          <Button variant="link" className="p-0 mb-3" onClick={onClose}>
            <i className="bi bi-x-lg me-1"></i>Close
          </Button>
          <Alert variant="danger">{error}</Alert>
        </div>
      </div>
    );
  }

  if (!detail) return null;

  const statusDisplay = displayStatus(detail.status, detail.assigned_to_id);
  const daysLeftStyle =
    detail.days_left !== null && detail.days_left <= 0 ? "text-danger" : "";
  const daysLeftText =
    detail.due_label || (detail.days_left !== null ? `${detail.days_left} Days left` : "");

  return (
    <div className="cl-task-detail-panel">
      {/* Header with close button */}
      <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
        <h5 className="mb-0">{detail.title}</h5>
        <Button
          variant="link"
          className="p-0 text-muted"
          onClick={onClose}
          title="Close"
        >
          <i className="bi bi-x-lg"></i>
        </Button>
      </div>

      {/* Status Badges */}
      <div className="d-flex gap-2 flex-wrap mb-3">
        {detail.status === "PENDING_REVIEW" && (
          <Badge bg="warning">Pending Review</Badge>
        )}
        {detail.priority === "HIGH" && <Badge bg="danger">High Priority</Badge>}
        {detail.priority === "MEDIUM" && (
          <Badge bg="info">Medium Priority</Badge>
        )}
        {detail.priority === "LOW" && (
          <Badge bg="secondary">Low Priority</Badge>
        )}
        {detail.status === "COMPLETED" && <Badge bg="success">Completed</Badge>}
      </div>

      {/* Description */}
      {detail.description && (
        <div className="mb-3">
          <h6 className="mb-2">Description</h6>
          <p className="text-muted small mb-0">{detail.description}</p>
        </div>
      )}

      {/* Assignee Info */}
      <div className="mb-3">
        <h6 className="mb-2">Assignee</h6>
        {detail.assigned_to ? (
          <div className="d-flex align-items-center gap-2">
            <div
              className="rounded-circle bg-primary text-white d-flex align-items-center justify-content-center"
              style={{
                width: 32,
                height: 32,
                fontSize: 12,
                fontWeight: "bold",
              }}
            >
              {detail.assigned_to.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <div className="fw-semibold small">{detail.assigned_to.name}</div>
              <div className="text-muted small">Team Lead</div>
            </div>
          </div>
        ) : detail.member_assigned_to ? (
          <div className="d-flex align-items-center gap-2">
            <div
              className="rounded-circle bg-info text-white d-flex align-items-center justify-content-center"
              style={{
                width: 32,
                height: 32,
                fontSize: 12,
                fontWeight: "bold",
              }}
            >
              {detail.member_assigned_to.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <div className="fw-semibold small">
                {detail.member_assigned_to.name}
              </div>
              <div className="text-muted small">Team Member</div>
            </div>
          </div>
        ) : (
          <span className="text-muted small">— unassigned —</span>
        )}
      </div>

      {/* Period and Dates */}
      <div className="mb-3 d-flex align-items-center justify-content-between">
        <div>
          <h6 className="mb-1">Period and Dates</h6>
          {detail.period_end_date && (
            <div className="text-muted small">
              {new Date(detail.period_end_date).toLocaleDateString()}
            </div>
          )}
        </div>
        {detail.days_left !== null && (
          <div className={`text-end ${daysLeftStyle}`}>
            <div className="fw-semibold small">{daysLeftText}</div>
          </div>
        )}
      </div>

      {/* Progress */}
      <div className="mb-3">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <h6 className="mb-0">Progress</h6>
          <span className="small text-muted">
            {detail.completed_subtasks}/{detail.total_subtasks}
          </span>
        </div>
        <ProgressBar
          now={detail.progress_percentage}
          className="progress-thin"
        />
      </div>

      {/* Subtasks */}
      <div className="mb-3">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <h6 className="mb-0">Subtasks</h6>
          <Button size="sm" variant="link" className="p-0 small">
            + Add Subtask
          </Button>
        </div>

        {detail.subtasks && detail.subtasks.length > 0 ? (
          <div className="space-y-2">
            {detail.subtasks.map((subtask) => (
              <Card key={subtask.id} className="mb-2 border-light">
                <Card.Body className="p-2">
                  <div className="d-flex align-items-start gap-2">
                    <div className="flex-grow-1">
                      <div className="fw-semibold small">{subtask.title}</div>
                      {subtask.description && (
                        <div className="text-muted small mt-1">
                          {subtask.description}
                        </div>
                      )}
                      {subtask.assigned_to && (
                        <div className="mt-1">
                          <Badge bg="light" text="dark" className="small">
                            <i className="bi bi-person me-1"></i>
                            {subtask.assigned_to.name}
                          </Badge>
                        </div>
                      )}
                    </div>
                    {subtask.due_date && (
                      <div className="text-muted small text-nowrap">
                        {new Date(subtask.due_date).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                  {subtask.status === "COMPLETED" && (
                    <div className="mt-2">
                      <Badge bg="success" className="small">
                        <i className="bi bi-check-circle me-1"></i>Completed{" "}
                        {subtask.completed_at && (
                          <>
                            {" "}
                            {new Date(
                              subtask.submitted_at,
                            ).toLocaleDateString()}
                          </>
                        )}
                      </Badge>
                    </div>
                  )}
                </Card.Body>
              </Card>
            ))}
          </div>
        ) : (
          <div className="text-muted small">No subtasks yet.</div>
        )}
      </div>

      {/* Comment Section */}
      <div className="border-top pt-3">
        <div className="mb-2">
          <textarea
            className="form-control form-control-sm"
            placeholder="Write a comment..."
            rows={2}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
        <div className="d-flex justify-content-end">
          <Button size="sm" variant="primary">
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}
