// Organization Page — manage entity groups + entities hierarchy.
// Replaces the old PeriodPlanner page (period management has moved to /periods).
import React, { useState } from 'react';
import { Row, Col, Card } from 'react-bootstrap';
import EntityGroupManager from '../components/EntityGroupManager.jsx';
import EntityManager from '../components/EntityManager.jsx';

export default function OrganizationPage() {
  const [selectedGroupId, setSelectedGroupId] = useState(null);
  const [selectedEntityId, setSelectedEntityId] = useState(null);
  const [groupRefreshTick, setGroupRefreshTick] = useState(0);

  return (
    <>
      <div className="d-flex align-items-center mb-4 flex-wrap gap-2">
        <div className="flex-grow-1">
          <h2 className="dashboard-title mb-0">
            <i className="bi bi-diagram-3 me-2 text-primary"></i>
            Organization
          </h2>
          <div className="text-muted small">
            Manage your entity groups and entities. Groups can nest (e.g. Region → Country → City).
            Entities are the units that get individual close plans (e.g. NYC, SF).
          </div>
        </div>
      </div>

      <Row className="g-3">
        {/* Left Panel: Entity Group Hierarchy */}
        <Col lg={5}>
          <EntityGroupManager
            onSelect={setSelectedGroupId}
            selectedGroupId={selectedGroupId}
            onGroupsChanged={() => setGroupRefreshTick((value) => value + 1)}
          />
        </Col>

        {/* Right Panel: Entities (filtered by selected group) */}
        <Col lg={7}>
          <EntityManager
            groupId={selectedGroupId}
            onSelect={setSelectedEntityId}
            selectedEntityId={selectedEntityId}
            refreshKey={groupRefreshTick}
          />

          {selectedEntityId && (
            <Card className="mt-3 border-success">
              <Card.Body className="d-flex align-items-center justify-content-between">
                <div>
                  <i className="bi bi-check-circle-fill text-success me-2"></i>
                  <strong>Entity selected.</strong>
                  <span className="text-muted ms-2">
                    Go to <a href="/periods">Periods</a> to initialize/manage periods,
                    or <a href="/close-packs">Close Packs</a> to deploy a template.
                  </span>
                </div>
              </Card.Body>
            </Card>
          )}
        </Col>
      </Row>
    </>
  );
}
