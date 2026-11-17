// PeriodsPage — Super Admin: configure the period schedule (start day + duration)
// and generate periods from it, view all periods, close the active period
// (which triggers carry-forward + next-period creation).
import React, { useCallback, useEffect, useState } from 'react';
import {
  Card, Button, Modal, Form, Alert, Row, Col, Badge, Table, Spinner
} from 'react-bootstrap';
import { useAuth } from '../auth.jsx';
import { periodAPI } from '../apiEntities.js';
import { StatusBadge } from '../components/Bits.jsx';
import { getErrorMessage } from '../api';
import PeriodCalendarWidget from '../components/PeriodCalendarWidget.jsx';
import DateInput from '../components/ui/DateInput.jsx';
import { todayIso, fmtDate, useBsDateLabels } from './leadDashUtils';

const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const CADENCE_OPTIONS = [
  { value: 'FIXED_WEEKS', label: 'Fixed weeks', hint: 'e.g. 4-week fiscal periods (P001, P002…)' },
  { value: 'CALENDAR_MONTH', label: 'English month', hint: 'Jan 2026, Feb 2026…' },
  { value: 'CALENDAR_QUARTER', label: 'English quarter', hint: 'Q1 2026, Q2 2026…' },
  { value: 'CALENDAR_HALF_YEAR', label: 'English half-year', hint: 'H1 2026, H2 2026…' },
  { value: 'CALENDAR_YEAR', label: 'English year (annual)', hint: '2026, 2027… one period per calendar year' },
  { value: 'BS_MONTH', label: 'Nepali month (BS)', hint: 'Bhadra 2083, Ashwin 2083…' },
  { value: 'BS_QUARTER', label: 'Nepali quarter (BS fiscal)', hint: 'Q1 FY 2083/84…' },
  { value: 'BS_HALF_YEAR', label: 'Nepali half-year (BS fiscal)', hint: 'H1 FY 2083/84…' },
  { value: 'BS_YEAR', label: 'Nepali year (annual, BS fiscal)', hint: 'FY 2083/84… one period per Nepali fiscal year' },
];
const CADENCE_LABELS = Object.fromEntries(CADENCE_OPTIONS.map((o) => [o.value, o.label]));
const isBsCadence = (cadence) => cadence.startsWith('BS_');

const apiErr = (e) => getErrorMessage(e, e?.message || 'Failed');

// Standard fiscal-period schedule (P1–P13, ~4-week "picnic" periods) — a
// fixed, known-good calendar admins can load in one click instead of
// generating or pasting it by hand. Dates as supplied by the business.
const STANDARD_PERIODS = [
  { label: 'P1', start_date: '2025-12-29', end_date: '2026-01-25' },
  { label: 'P2', start_date: '2026-01-26', end_date: '2026-02-22' },
  { label: 'P3', start_date: '2026-02-23', end_date: '2026-03-22' },
  { label: 'P4', start_date: '2026-03-23', end_date: '2026-04-19' },
  { label: 'P5', start_date: '2026-04-20', end_date: '2026-05-17' },
  { label: 'P6', start_date: '2026-05-18', end_date: '2026-06-14' },
  { label: 'P7', start_date: '2026-06-15', end_date: '2026-07-12' },
  { label: 'P8', start_date: '2026-07-13', end_date: '2026-08-09' },
  { label: 'P9', start_date: '2026-08-10', end_date: '2026-09-06' },
  { label: 'P10', start_date: '2026-09-07', end_date: '2026-10-04' },
  { label: 'P11', start_date: '2026-10-05', end_date: '2026-11-01' },
  { label: 'P12', start_date: '2026-11-02', end_date: '2026-11-29' },
  { label: 'P13', start_date: '2026-11-30', end_date: '2026-12-31' },
];

export default function PeriodsPage() {
  const { user } = useAuth();
  const isAdmin = user.role === 'SUPER_ADMIN';

  const [config, setConfig] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [flash, setFlash] = useState(null);
  const [showClose, setShowClose] = useState(null); // period to close
  const [showConfigGenerate, setShowConfigGenerate] = useState(false);
  const [showImport, setShowImport] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, ps, cur] = await Promise.all([
        periodAPI.getConfiguration(),
        periodAPI.list(),
        periodAPI.current().catch(() => ({ data: null })),
      ]);
      setConfig(cfg.data);
      setPeriods(ps.data);
      setCurrent(cur.data);
    } catch (e) { setErr(apiErr(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 4000);
    return () => clearTimeout(t);
  }, [flash]);

  // The tenant's period schedule cadence (BS_* vs everything else) is the
  // calendar this page's own dates (today, the current period) follow —
  // periods aren't scoped to one Pack, so the schedule's own cadence is
  // the closest thing to "this view's calendar type". The per-row AD/BS
  // columns in the table below still show both explicitly, since a period
  // can be referenced by packs of either calendar.
  const isBs = !!config && isBsCadence(config.cadence);
  const todayBsLabels = useBsDateLabels(isBs ? [todayIso()] : [], isBs ? 'BS' : 'AD');

  async function doClose(periodId) {
    try {
      const { data } = await periodAPI.close(periodId);
      setShowClose(null);
      setFlash({
        variant: 'success',
        message: `Period closed. Next period activated. ${data.plans_generated} plan(s) generated, ${data.carry_forward_items} item(s) carried forward.`,
      });
      load();
    } catch (e) {
      setShowClose(null);
      setFlash({ variant: 'danger', message: apiErr(e) });
    }
  }


  async function saveConfigAndGenerate(configPayload, generatePayload) {
    await periodAPI.updateConfiguration(configPayload);
    const { data } = await periodAPI.generateSchedule(generatePayload);
    setFlash({
      variant: 'success',
      message: `Configuration saved. Generated ${data.length} period(s) (${CADENCE_LABELS[generatePayload.cadence]}).`,
    });
    setShowConfigGenerate(false);
    load();
  }

  function onImported(data, source) {
    setFlash({
      variant: 'success',
      message: `Imported ${data.length} period(s)${source ? ` from ${source}` : ''}.`,
    });
    setShowImport(false);
    load();
  }

  if (loading) return <div className="text-center py-5"><Spinner animation="border" /></div>;
  if (err) return <Alert variant="danger">{err}</Alert>;

  return (
    <>
      <div className="d-flex align-items-center mb-4 flex-wrap gap-2">
        <div className="flex-grow-1">
          <h2 className="dashboard-title mb-0">
            <i className="bi bi-calendar3 me-2 text-primary"></i>Periods
          </h2>
          <div className="text-muted small">
            Periods define business work windows for planning, approvals, and close-pack generation.
            Closing a period advances to the next period and carries open work forward if enabled.
            Choose a cadence that fits this tenant — fixed weeks, calendar months, or calendar quarters.
          </div>
          <div className="text-muted small mt-2">
            Current date: <strong>{fmtDate(todayIso(), isBs ? todayBsLabels : undefined)}</strong>
          </div>
        </div>
        {isAdmin && (
          <div className="d-flex gap-2 flex-wrap">
            <Button variant="outline-primary" onClick={() => setShowImport(true)}>
              <i className="bi bi-upload me-1"></i>Import Periods
            </Button>
            <Button variant="primary" onClick={() => setShowConfigGenerate(true)}>
              <i className="bi bi-stars me-1"></i>Configure &amp; Generate Schedule
            </Button>
          </div>
        )}
      </div>

      {flash && (
        <Alert variant={flash.variant} onClose={() => setFlash(null)} dismissible className="py-2">
          {flash.message}
        </Alert>
      )}

      <Row className="g-3">
        <Col lg={4}>
          <Card className="mb-3">
            <Card.Body>
              <PeriodCalendarWidget currentPeriod={current} />
            </Card.Body>
          </Card>

          <Card>
            <Card.Header className="bg-white">
              <strong><i className="bi bi-gear me-2"></i>Schedule</strong>
            </Card.Header>
            <Card.Body>
              {config && (
                <>
                  <div className="d-flex justify-content-between mb-2">
                    <span className="text-muted">Cadence</span>
                    <strong>{CADENCE_LABELS[config.cadence] || config.cadence}</strong>
                  </div>
                  {config.cadence === 'FIXED_WEEKS' && (
                    <>
                      <div className="d-flex justify-content-between mb-2">
                        <span className="text-muted">Start day of week</span>
                        <strong>{DOW_LABELS[config.start_day_of_week] || config.start_day_of_week}</strong>
                      </div>
                      <div className="d-flex justify-content-between mb-2">
                        <span className="text-muted">Duration</span>
                        <strong>{config.duration_weeks} week{config.duration_weeks === 1 ? '' : 's'}</strong>
                      </div>
                    </>
                  )}
                  <div className="d-flex justify-content-between mb-2">
                    <span className="text-muted">Auto-generate</span>
                    <Badge bg={config.auto_generate ? 'success' : 'secondary'}>
                      {config.auto_generate ? 'enabled' : 'disabled'}
                    </Badge>
                  </div>
                  <div className="d-flex justify-content-between">
                    <span className="text-muted">Carry-forward</span>
                    <Badge bg={config.carry_forward_enabled ? 'success' : 'secondary'}>
                      {config.carry_forward_enabled ? 'enabled' : 'disabled'}
                    </Badge>
                  </div>
                </>
              )}
            </Card.Body>
          </Card>

          {current && (
            <Card className="mt-3">
              <Card.Header className="bg-white">
                <strong><i className="bi bi-calendar-event me-2"></i>Current period</strong>
              </Card.Header>
              <Card.Body>
                <h5 className="mb-1">{current.label}</h5>
                <div className="text-muted small mb-2">
                  {isBs && current.bs_start_label
                    ? `${current.bs_start_label} → ${current.bs_end_label}`
                    : `${fmtDate(current.start_date)} → ${fmtDate(current.end_date)}`}
                </div>
                <StatusBadge status={current.status} />
                {isAdmin && current.status !== 'CLOSED' && current.status !== 'ARCHIVED' && (
                  <Button
                    variant="warning"
                    size="sm"
                    className="w-100 mt-3"
                    onClick={() => setShowClose(current)}
                  >
                    <i className="bi bi-lock me-1"></i>Close this period
                  </Button>
                )}
              </Card.Body>
            </Card>
          )}
        </Col>

        <Col lg={8}>
          <Card>
            <Card.Header className="bg-white">
              <strong><i className="bi bi-list me-2"></i>All periods</strong>
            </Card.Header>
            <Card.Body className="p-0">
              {periods.length === 0 ? (
                <div className="empty-state m-3">
                  <i className="bi bi-calendar-x"></i>
                  <div>
                    No periods yet.
                    {isAdmin && ' Click "Configure & Generate Schedule" to start.'}
                  </div>
                </div>
              ) : (
                <Table responsive className="mb-0">
                  <thead>
                    <tr>
                      <th>Label</th>
                      <th>AD Dates</th>
                      <th>BS Dates</th>
                      <th>Status</th>
                      <th>Closed at</th>
                      {isAdmin && <th></th>}
                    </tr>
                  </thead>
                  <tbody>
                    {periods.map((p) => (
                      <tr key={p.id}>
                        <td className="fw-semibold">{p.label}</td>
                        <td className="text-nowrap">
                          {fmtDate(p.start_date)} – {fmtDate(p.end_date)}
                        </td>
                        <td className="text-nowrap text-muted small">
                          {p.bs_start_label || '—'} – {p.bs_end_label || ''}
                        </td>
                        <td><StatusBadge status={p.status} /></td>
                        <td className="text-muted small">
                          {p.closed_at ? new Date(p.closed_at).toLocaleString() : '—'}
                        </td>
                        {isAdmin && (
                          <td>
                            {p.status !== 'CLOSED' && p.status !== 'ARCHIVED' && (
                              <Button
                                variant="outline-warning"
                                size="sm"
                                onClick={() => setShowClose(p)}
                              >
                                Close
                              </Button>
                            )}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <ConfigureAndGenerateModal
        show={showConfigGenerate}
        onHide={() => setShowConfigGenerate(false)}
        config={config}
        hasExisting={periods.length > 0}
        onSubmit={saveConfigAndGenerate}
      />

      <ImportPeriodsModal
        show={showImport}
        onHide={() => setShowImport(false)}
        hasExisting={periods.length > 0}
        onImported={onImported}
      />

      <ConfirmCloseModal
        period={showClose}
        onHide={() => setShowClose(null)}
        onConfirm={doClose}
      />
    </>
  );
}

function ConfirmCloseModal({ period, onHide, onConfirm }) {
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!period) {
      setReview(null);
      setErr('');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr('');
    periodAPI
      .review(period.id)
      .then((r) => {
        if (!cancelled) setReview(r.data);
      })
      .catch((e) => {
        if (!cancelled) setErr(apiErr(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [period]);

  if (!period) return null;

  const pct = review ? Math.round((review.completion_pct || 0) * 100) : 0;
  const totals = review?.totals || {
    plans: 0,
    tasks: 0,
    tasks_completed: 0,
    subtasks: 0,
    subtasks_completed: 0,
  };
  const incompleteTotal =
    (review?.incomplete_tasks?.length || 0) +
    (review?.incomplete_subtasks?.length || 0);

  return (
    <Modal show={!!period} onHide={onHide} centered size="lg">
      <Modal.Header closeButton>
        <Modal.Title>
          <i className="bi bi-clipboard-check me-2"></i>
          Review &amp; Carry-Forward — {period.label}
        </Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {loading && (
          <div className="text-center py-4">
            <Spinner animation="border" size="sm" /> Loading period review…
          </div>
        )}
        {err && <Alert variant="danger">{err}</Alert>}
        {review && (
          <>
            <Row className="g-3 mb-3">
              <Col md={4}>
                <Card className="text-center">
                  <Card.Body>
                    <div className="text-muted small">Completion</div>
                    <h3 className="mb-0">{pct}%</h3>
                  </Card.Body>
                </Card>
              </Col>
              <Col md={4}>
                <Card className="text-center">
                  <Card.Body>
                    <div className="text-muted small">Tasks</div>
                    <h3 className="mb-0">
                      {totals.tasks_completed} / {totals.tasks}
                    </h3>
                  </Card.Body>
                </Card>
              </Col>
              <Col md={4}>
                <Card className="text-center">
                  <Card.Body>
                    <div className="text-muted small">Sub-tasks</div>
                    <h3 className="mb-0">
                      {totals.subtasks_completed} / {totals.subtasks}
                    </h3>
                  </Card.Body>
                </Card>
              </Col>
            </Row>

            {incompleteTotal === 0 ? (
              <Alert variant="success" className="mb-2">
                <i className="bi bi-check-circle me-2"></i>
                Everything in this period is complete — nothing will be carried forward.
              </Alert>
            ) : (
              <>
                <Alert variant="warning" className="mb-2">
                  <i className="bi bi-arrow-right-circle me-2"></i>
                  <strong>{incompleteTotal}</strong> incomplete item(s) will be carried
                  forward into the next period under a labeled
                  &quot;Prior Period Carry-Forward&quot; group.
                </Alert>
                {review.incomplete_tasks?.length > 0 && (
                  <div className="mb-3">
                    <div className="text-muted small mb-1">
                      Incomplete tasks ({review.incomplete_tasks.length})
                    </div>
                    <div
                      className="border rounded p-2"
                      style={{ maxHeight: 160, overflowY: 'auto' }}
                    >
                      {review.incomplete_tasks.map((t) => (
                        <div
                          key={t.id}
                          className="d-flex justify-content-between border-bottom py-1 small"
                        >
                          <span>
                            <Badge bg="secondary" className="me-2">
                              {t.status}
                            </Badge>
                            {t.title}
                          </span>
                          <span className="text-muted">
                            {t.assigned_to_id ? (t.assigned_to?.name || 'Unassigned') : 'Unassigned'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {review.incomplete_subtasks?.length > 0 && (
                  <div className="mb-3">
                    <div className="text-muted small mb-1">
                      Incomplete sub-tasks ({review.incomplete_subtasks.length})
                    </div>
                    <div
                      className="border rounded p-2"
                      style={{ maxHeight: 160, overflowY: 'auto' }}
                    >
                      {review.incomplete_subtasks.map((s) => (
                        <div
                          key={s.id}
                          className="d-flex justify-content-between border-bottom py-1 small"
                        >
                          <span>
                            <Badge bg="secondary" className="me-2">
                              {s.status}
                            </Badge>
                            {s.title}
                          </span>
                          <span className="text-muted">
                            {s.assigned_to_id ? (s.assigned_to?.name || 'Unassigned') : 'Unassigned'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            <ul className="text-muted small mb-0">
              <li>The current period will be marked CLOSED.</li>
              <li>A new period will be created automatically.</li>
              <li>
                Open items will be queued for carry-forward review and copied
                into the next period upon approval.
              </li>
            </ul>
          </>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>
          Cancel
        </Button>
        <Button
          variant="warning"
          onClick={() => onConfirm(period.id)}
          disabled={loading}
        >
          <i className="bi bi-lock me-1"></i>Carry Forward &amp; Close
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

function ConfigureAndGenerateModal({ show, onHide, config, hasExisting, onSubmit }) {
  const [cadence, setCadence] = useState('FIXED_WEEKS');
  const [dow, setDow] = useState(4);
  const [weeks, setWeeks] = useState(4);
  const [autoGen, setAutoGen] = useState(true);
  const [cf, setCf] = useState(true);
  const [count, setCount] = useState(12);
  const [startDate, setStartDate] = useState(() => todayIso());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [preview, setPreview] = useState(null); // { cadence, periods: [...] } | null
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState('');

  useEffect(() => {
    if (show) {
      setCadence(config?.cadence ?? 'FIXED_WEEKS');
      setDow(config?.start_day_of_week ?? 4);
      setWeeks(config?.duration_weeks ?? 4);
      setAutoGen(config?.auto_generate ?? true);
      setCf(config?.carry_forward_enabled ?? true);
      setCount(12);
      setStartDate(todayIso());
      setErr('');
      setPreview(null);
      setPreviewErr('');
    }
  }, [show, config]);

  // Any change to the shape of what would be generated invalidates a stale preview.
  useEffect(() => { setPreview(null); }, [cadence, count, weeks, dow, startDate]);

  function configPayload() {
    return {
      cadence,
      start_day_of_week: Number(dow),
      duration_weeks: Number(weeks),
      auto_generate: autoGen,
      carry_forward_enabled: cf,
    };
  }

  function generatePayload() {
    return {
      cadence,
      // The first period is whichever week/month/quarter/half/BS-unit
      // contains this date — picked below, defaulted to today. For Fixed
      // weeks this date is only a lower bound: the backend snaps it
      // forward to the next occurrence of `start_day_of_week` below, so
      // period 1 always starts on the same weekday as every period after
      // it instead of on whatever raw date happened to be picked here.
      start_date: startDate,
      period_count: Number(count),
      duration_weeks: cadence === 'FIXED_WEEKS' ? Number(weeks) : undefined,
      start_day_of_week: cadence === 'FIXED_WEEKS' ? Number(dow) : undefined,
    };
  }

  async function doPreview() {
    setPreviewErr(''); setPreviewBusy(true);
    try {
      const { data } = await periodAPI.preview(generatePayload());
      setPreview(data);
    } catch (e) { setPreviewErr(apiErr(e)); }
    finally { setPreviewBusy(false); }
  }

  async function submit(e) {
    e.preventDefault();
    if (hasExisting && !window.confirm(
      'This will REPLACE all existing periods for this tenant with the newly generated schedule. Continue?'
    )) return;
    setErr(''); setBusy(true);
    try {
      await onSubmit(configPayload(), generatePayload());
    } catch (e2) { setErr(apiErr(e2)); }
    finally { setBusy(false); }
  }

  return (
    <Modal show={show} onHide={onHide} centered size="lg">
      <Form onSubmit={submit}>
        <Modal.Header closeButton><Modal.Title>Configure &amp; Generate Period Schedule</Modal.Title></Modal.Header>
        <Modal.Body>
          <p className="small text-muted">
            Sets this tenant's cadence (used whenever a period auto-advances on
            close too) and, in one step, builds a full correctly-labeled
            schedule for it — fixed weeks, English calendar month/quarter/
            half-year/year, or Nepali (Bikram Sambat) month/fiscal-quarter/
            fiscal-half-year/fiscal-year.
          </p>
          <Row className="g-3">
            <Col md={12}>
              <Form.Label>Cadence</Form.Label>
              <Form.Select value={cadence} onChange={(e) => setCadence(e.target.value)}>
                {CADENCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label} — {o.hint}</option>
                ))}
              </Form.Select>
            </Col>
            <Col md={cadence === 'FIXED_WEEKS' ? 6 : 12}>
              <Form.Label>Start date</Form.Label>
              <div>
                {/* A period boundary, not task work — non-working days stay selectable. */}
                <DateInput
                  calendarType={isBsCadence(cadence) ? 'BS' : 'AD'}
                  value={startDate}
                  onChange={setStartDate}
                  allowNonWorking
                />
              </div>
              <Form.Text className="text-muted">
                The first period is whichever {isBsCadence(cadence) ? 'Nepali (BS)' : 'English (AD)'} unit contains this date.
              </Form.Text>
            </Col>
            {cadence === 'FIXED_WEEKS' && (
              <Col md={6}>
                <Form.Label>Start day of week</Form.Label>
                <Form.Select value={dow} onChange={(e) => setDow(e.target.value)}>
                  {DOW_LABELS.map((label, i) => (
                    <option key={i} value={i}>{label}</option>
                  ))}
                </Form.Select>
              </Col>
            )}
            <Col md={cadence === 'FIXED_WEEKS' ? 6 : 12}>
              <Form.Label>Duration (weeks) {cadence !== 'FIXED_WEEKS' && <span className="text-muted small">— only used for Fixed weeks</span>}</Form.Label>
              <Form.Control
                type="number" min={1} max={52} value={weeks}
                onChange={(e) => setWeeks(e.target.value)}
                disabled={cadence !== 'FIXED_WEEKS'}
              />
            </Col>
            <Col md={12}>
              <Form.Check
                type="switch"
                id="cfg-auto-gen"
                label="Auto-generate next period on close"
                checked={autoGen}
                onChange={(e) => setAutoGen(e.target.checked)}
              />
              <Form.Check
                type="switch"
                id="cfg-cf"
                label="Enable carry-forward of open items"
                checked={cf}
                onChange={(e) => setCf(e.target.checked)}
              />
            </Col>
            <Col md={12}>
              <Form.Label>Number of periods to generate</Form.Label>
              <Form.Control type="number" min={1} max={104} value={count} onChange={(e) => setCount(e.target.value)} />
            </Col>
          </Row>

          <div className="d-flex justify-content-between align-items-center mt-3">
            <Button variant="outline-primary" size="sm" onClick={doPreview} disabled={previewBusy} type="button">
              <i className="bi bi-eye me-1"></i>{previewBusy ? 'Loading preview…' : 'Preview schedule'}
            </Button>
            {preview && <span className="text-muted small">{preview.periods.length} period(s)</span>}
          </div>
          {previewErr && <Alert variant="danger" className="mt-2 py-2">{previewErr}</Alert>}

          {preview && (
            <div className="border rounded mt-2" style={{ maxHeight: 260, overflowY: 'auto' }}>
              <Table size="sm" className="mb-0 small">
                <thead className="table-light" style={{ position: 'sticky', top: 0 }}>
                  <tr>
                    <th>Label</th>
                    <th>AD range</th>
                    <th>BS range</th>
                    <th>Working days</th>
                    <th>Closing date</th>
                    <th>Holidays</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.periods.map((p) => (
                    <tr key={p.label}>
                      <td className="fw-semibold">{p.label}</td>
                      <td className="text-nowrap">{p.start_date} – {p.end_date}</td>
                      <td className="text-nowrap text-muted">{p.bs_start_label} – {p.bs_end_label}</td>
                      <td>{p.working_days} / {p.total_days}</td>
                      <td>{p.closing_date}</td>
                      <td>{p.holidays.length}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          )}

          {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
          <Alert variant="info" className="small mb-0 mt-3">
            This replaces the tenant's existing periods. The period covering today will be set OPEN.
          </Alert>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={onHide}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save & Generate'}
          </Button>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}

// Two ways to load an explicit period schedule instead of generating one:
// (1) one click to load the standard P1–P13 fiscal-period calendar, or
// (2) upload a spreadsheet with the same Label/Start Date/End Date shape.
// Both replace the tenant's existing periods, same as Configure & Generate.
function ImportPeriodsModal({ show, onHide, hasExisting, onImported }) {
  const [busyStandard, setBusyStandard] = useState(false);
  const [busyFile, setBusyFile] = useState(false);
  const [err, setErr] = useState('');
  const [file, setFile] = useState(null);

  useEffect(() => {
    if (show) {
      setErr('');
      setFile(null);
    }
  }, [show]);

  function confirmReplace() {
    return !hasExisting || window.confirm(
      'This will REPLACE all existing periods for this tenant. Continue?'
    );
  }

  async function loadStandard() {
    if (!confirmReplace()) return;
    setErr(''); setBusyStandard(true);
    try {
      const { data } = await periodAPI.importPeriods(STANDARD_PERIODS);
      onImported(data, 'the standard P1–P13 schedule');
    } catch (e) { setErr(apiErr(e)); }
    finally { setBusyStandard(false); }
  }

  async function uploadFile(e) {
    e.preventDefault();
    if (!file) return;
    if (!confirmReplace()) return;
    setErr(''); setBusyFile(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await periodAPI.importPeriodsFile(formData);
      onImported(data, file.name);
    } catch (e2) { setErr(apiErr(e2)); }
    finally { setBusyFile(false); }
  }

  return (
    <Modal show={show} onHide={onHide} centered size="lg">
      <Modal.Header closeButton><Modal.Title>Import Periods</Modal.Title></Modal.Header>
      <Modal.Body>
        <Card className="mb-3">
          <Card.Header className="bg-white"><strong>Standard periods (P1–P13)</strong></Card.Header>
          <Card.Body>
            <p className="small text-muted">
              Load this tenant's standard ~4-week fiscal-period calendar in one click.
            </p>
            <div className="border rounded mb-3" style={{ maxHeight: 220, overflowY: 'auto' }}>
              <Table size="sm" className="mb-0 small">
                <thead className="table-light" style={{ position: 'sticky', top: 0 }}>
                  <tr><th>Period</th><th>Start Date</th><th>End Date</th></tr>
                </thead>
                <tbody>
                  {STANDARD_PERIODS.map((p) => (
                    <tr key={p.label}>
                      <td className="fw-semibold">{p.label}</td>
                      <td>{fmtDate(p.start_date)}</td>
                      <td>{fmtDate(p.end_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
            <Button variant="primary" size="sm" onClick={loadStandard} disabled={busyStandard}>
              <i className="bi bi-calendar-check me-1"></i>
              {busyStandard ? 'Loading…' : 'Load standard periods'}
            </Button>
          </Card.Body>
        </Card>

        <Card>
          <Card.Header className="bg-white"><strong>Upload a schedule file</strong></Card.Header>
          <Card.Body>
            <p className="small text-muted mb-2">
              Upload a .xlsx, .xlsm, or .csv file with <strong>Label</strong>, <strong>Start Date</strong>,
              and <strong>End Date</strong> columns (any order — a header row is auto-detected, or the
              first three columns are used positionally).
            </p>
            <Form onSubmit={uploadFile}>
              <Form.Group className="mb-3">
                <Form.Control
                  type="file"
                  accept=".xlsx,.xlsm,.csv"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
              </Form.Group>
              <Button variant="outline-primary" size="sm" type="submit" disabled={!file || busyFile}>
                <i className="bi bi-upload me-1"></i>
                {busyFile ? 'Uploading…' : 'Upload & Import'}
              </Button>
            </Form>
          </Card.Body>
        </Card>

        {err && <Alert variant="danger" className="mt-3 py-2">{err}</Alert>}
        <Alert variant="info" className="small mb-0 mt-3">
          Either option replaces the tenant's existing periods. The period covering today will be set OPEN.
        </Alert>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>Close</Button>
      </Modal.Footer>
    </Modal>
  );
}
