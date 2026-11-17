import React, { useCallback, useEffect, useState } from 'react';
import { Card, Table, Form, Button, Row, Col, Alert, Badge } from 'react-bootstrap';
import { getErrorMessage } from '../api';
import { holidayAPI } from '../apiEntities';
import { loadHolidays } from '../components/ui/workingDays';

const LINE = /^(\d{4}-\d{2}-\d{2})\s*[,\t]\s*(.+)$/;

// "2026-10-20, Dashain" per line -> { holidays, bad } (bad = the unreadable lines).
function parseLines(text) {
  const holidays = [];
  const bad = [];
  text.split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line) return;
    const m = LINE.exec(line);
    if (m) holidays.push({ holiday_date: m[1], name: m[2].trim() });
    else bad.push(line);
  });
  return { holidays, bad };
}

const CATEGORIES = [
  { value: 'ALL', label: 'Everyone' },
  { value: 'GENDER', label: 'Gender-specific' },
  { value: 'RELIGION', label: 'Religion-specific' },
];

// Who a holiday is for, as short text: "Everyone", "Gender: Female", ...
function appliesTo(h) {
  if (!h.category || h.category === 'ALL') return 'Everyone';
  const kind = h.category === 'GENDER' ? 'Gender' : 'Religion';
  return `${kind}: ${h.audience ? h.audience.charAt(0) + h.audience.slice(1).toLowerCase() : '—'}`;
}

function weekday(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
}

export default function HolidaysPage() {
  const [rows, setRows] = useState([]);
  const [date, setDate] = useState('');
  const [name, setName] = useState('');
  const [category, setCategory] = useState('ALL');
  const [audience, setAudience] = useState('');
  const [remarks, setRemarks] = useState('');
  const [bulkText, setBulkText] = useState('');
  const [msg, setMsg] = useState(null); // { variant, text }
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await holidayAPI.list();
      setRows(data);
    } catch (e) {
      setMsg({ variant: 'danger', text: getErrorMessage(e, 'Could not load the holidays') });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  // After any change: refresh this table, and this browser's own copy of the
  // list (what its date pickers use). Other users pick the change up on their
  // next sign-in / page load.
  async function refresh() {
    await Promise.all([load(), loadHolidays()]);
  }

  async function add(e) {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      await holidayAPI.create({
        holiday_date: date,
        name,
        category,
        audience: category === 'ALL' ? null : audience,
        remarks: remarks.trim() || null,
      });
      setDate('');
      setName('');
      setCategory('ALL');
      setAudience('');
      setRemarks('');
      await refresh();
    } catch (err) {
      setMsg({ variant: 'danger', text: getErrorMessage(err, 'Could not add the holiday') });
    } finally {
      setBusy(false);
    }
  }

  async function addMany(e) {
    e.preventDefault();
    setMsg(null);
    const { holidays, bad } = parseLines(bulkText);
    if (bad.length) {
      setMsg({
        variant: 'danger',
        text: `Can't read ${bad.length} line(s) — use "YYYY-MM-DD, Name" on each line: ${bad.slice(0, 3).join(' | ')}${bad.length > 3 ? ' …' : ''}`,
      });
      return;
    }
    if (!holidays.length) {
      setMsg({ variant: 'danger', text: 'Nothing to add — paste one "YYYY-MM-DD, Name" per line.' });
      return;
    }
    setBusy(true);
    try {
      const { data } = await holidayAPI.bulk({ holidays });
      setBulkText('');
      setMsg({ variant: 'success', text: `${data.created} added, ${data.updated} updated.` });
      await refresh();
    } catch (err) {
      setMsg({ variant: 'danger', text: getErrorMessage(err, 'Could not add the holidays') });
    } finally {
      setBusy(false);
    }
  }

  async function remove(h) {
    if (!confirm(`Remove ${h.holiday_date} — ${h.name} (${appliesTo(h)}) from the holidays?`)) return;
    setMsg(null);
    try {
      await holidayAPI.remove(h.id);
      await refresh();
    } catch (err) {
      setMsg({ variant: 'danger', text: getErrorMessage(err, 'Could not remove the holiday') });
    }
  }

  const today = new Date().toISOString().slice(0, 10);

  return (
    <>
      <div className="d-flex align-items-center mb-3">
        <h3 className="mb-0"><i className="bi bi-calendar-x me-2"></i>Platform — Holidays</h3>
      </div>
      <Alert variant="info" className="small">
        <i className="bi bi-info-circle me-2"></i>
        One list for every tenant, on top of Saturday and Sunday. A task that would be due on a holiday
        is due the next working day instead, Daily tasks skip it, and the date pickers won't offer it.
        It applies to dates generated or picked from now on — tasks that already exist keep their dates
        (not-started Close Pack tasks are re-dated the next time their pack is saved).
        Every holiday recorded here is skipped for everyone, whatever its category, and it is the only kind of
        day marked red on the calendars — a festival that isn't in this list is not marked and not skipped.
        Several holidays can share a date; they count as one day off.
      </Alert>
      {msg && <Alert variant={msg.variant} dismissible onClose={() => setMsg(null)} className="py-2">{msg.text}</Alert>}

      <Row className="g-3 mb-3">
        <Col lg={5}>
          <Card className="h-100">
            <Card.Body>
              <h6 className="mb-3">Add a holiday</h6>
              <Form onSubmit={add}>
                <Form.Group className="mb-2">
                  <Form.Label className="small text-muted">Date</Form.Label>
                  <Form.Control type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
                </Form.Group>
                <Form.Group className="mb-2">
                  <Form.Label className="small text-muted">Name</Form.Label>
                  <Form.Control
                    value={name} maxLength={200} placeholder="e.g. Dashain — Vijaya Dashami"
                    onChange={(e) => setName(e.target.value)} required
                  />
                </Form.Group>
                <Form.Group className="mb-2">
                  <Form.Label className="small text-muted">Applicable for</Form.Label>
                  <Form.Select value={category} onChange={(e) => setCategory(e.target.value)}>
                    {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </Form.Select>
                </Form.Group>
                {category !== 'ALL' && (
                  <Form.Group className="mb-2">
                    <Form.Label className="small text-muted">Applies to</Form.Label>
                    <Form.Control
                      value={audience} maxLength={50} required
                      placeholder={category === 'GENDER' ? 'e.g. Female' : 'e.g. Christian'}
                      onChange={(e) => setAudience(e.target.value)}
                    />
                  </Form.Group>
                )}
                <Form.Group className="mb-3">
                  <Form.Label className="small text-muted">Remarks (optional)</Form.Label>
                  <Form.Control
                    value={remarks} maxLength={500} placeholder="e.g. Only for female staff"
                    onChange={(e) => setRemarks(e.target.value)}
                  />
                </Form.Group>
                <Button type="submit" disabled={busy}><i className="bi bi-plus-lg me-1"></i>Add holiday</Button>
              </Form>
            </Card.Body>
          </Card>
        </Col>
        <Col lg={7}>
          <Card className="h-100">
            <Card.Body>
              <h6 className="mb-1">Add many at once</h6>
              <div className="small text-muted mb-2">
                One per line: <code>YYYY-MM-DD, Name</code> — a holiday for everyone. Sending the same date
                and name again changes nothing. Gender- or religion-specific holidays are added one at a time above.
              </div>
              <Form onSubmit={addMany}>
                <Form.Control
                  as="textarea" rows={5} className="mb-3" value={bulkText}
                  placeholder={'2026-10-20, Vijaya Dashami\n2026-11-08, Laxmi Puja'}
                  onChange={(e) => setBulkText(e.target.value)}
                />
                <Button type="submit" variant="outline-primary" disabled={busy || !bulkText.trim()}>Add all</Button>
              </Form>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <Card>
        <Card.Body className="p-0">
          <Table responsive hover className="mb-0">
            <thead className="table-light">
              <tr><th>Date (AD)</th><th>Date (BS)</th><th>Day</th><th>Name</th><th>Applies to</th><th>Remarks</th><th>Source</th><th></th></tr>
            </thead>
            <tbody>
              {rows.map((h) => (
                <tr key={h.id} className={h.holiday_date < today ? 'text-muted' : ''}>
                  <td><strong>{h.holiday_date}</strong></td>
                  <td className="small">{h.bs_date || '—'}</td>
                  <td className="small">{weekday(h.holiday_date)}</td>
                  <td>{h.name}</td>
                  <td>
                    <Badge bg={!h.category || h.category === 'ALL' ? 'success' : 'warning'} text={!h.category || h.category === 'ALL' ? undefined : 'dark'}>
                      {appliesTo(h)}
                    </Badge>
                  </td>
                  <td className="small">{h.remarks || '—'}</td>
                  <td>
                    <Badge bg={h.source === 'API' ? 'info' : 'secondary'}>{h.source}</Badge>
                  </td>
                  <td className="text-end">
                    <Button size="sm" variant="outline-danger" onClick={() => remove(h)}>Remove</Button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={8} className="text-center text-muted py-4">
                  No holidays yet — only Saturday and Sunday are non-working days.
                </td></tr>
              )}
            </tbody>
          </Table>
        </Card.Body>
      </Card>
    </>
  );
}
