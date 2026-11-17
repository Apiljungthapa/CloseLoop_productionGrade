import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Form, Spinner } from 'react-bootstrap';
import { Link, useSearchParams } from 'react-router-dom';
import { getErrorMessage } from '../api';
import { reportStudioAPI } from '../apiEntities.js';
import { useAuth } from '../auth.jsx';
import { useOrgPath } from '../orgPath.js';
import StudioReport from '../components/reports/studio/StudioReport.jsx';
import { downloadReport, FORMAT_LABEL } from '../components/reports/studio/download.js';
import '../components/reports/studio/studio.css';

// Report Studio: ask for a report in plain language (or pick the options), see it as a dashboard with KPIs,
// charts, findings and recommended actions, and download it as PDF, Excel or CSV.

const DOWNLOADABLE = new Set(['pdf', 'xlsx', 'csv']);

export default function ReportStudio() {
  const { user } = useAuth();
  const orgPath = useOrgPath();
  const [params] = useSearchParams();
  const [catalog, setCatalog] = useState(null);
  const [text, setText] = useState('');
  const [spec, setSpec] = useState(null); // the report on screen, as the server understood it
  const [form, setForm] = useState({ kind: 'period_completion', period_id: '', compare_period_id: '', entity_id: '', member_id: '' });
  const [showOptions, setShowOptions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState(null); // { message, assumptions, clarification }
  const [report, setReport] = useState(null);
  const outputRef = useRef(null);
  const handledLink = useRef(false);

  useEffect(() => {
    let alive = true;
    reportStudioAPI.catalog()
      .then(({ data }) => {
        if (!alive) return;
        setCatalog(data);
        const current = data.periods.find((p) => p.is_current) || data.periods[0];
        setForm((f) => ({ ...f, period_id: current?.id || '' }));
      })
      .catch((e) => alive && setError(getErrorMessage(e, 'Report Studio is not available for your account.')));
    return () => { alive = false; };
  }, []);

  const show = useCallback((nextReport, nextSpec) => {
    setReport(nextReport);
    setSpec(nextSpec);
    if (nextSpec) {
      setForm({
        kind: nextSpec.kind, period_id: nextSpec.period_id || '', compare_period_id: nextSpec.compare_period_id || '',
        entity_id: nextSpec.entity_id || '', member_id: nextSpec.member_id || '',
      });
    }
    requestAnimationFrame(() => outputRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }, []);

  const download = useCallback(async (format, forSpec) => {
    const target = forSpec || spec;
    if (!target) return;
    setDownloading(format);
    setError('');
    try {
      await downloadReport(target, format);
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloading('');
    }
  }, [spec]);

  const runAsk = useCallback(async (sentence) => {
    const q = sentence.trim();
    if (!q || busy) return;
    setBusy(true);
    setError('');
    setNote(null);
    try {
      const { data } = await reportStudioAPI.ask(q);
      setNote({ message: data.message, assumptions: data.assumptions || [], clarification: data.clarification });
      if (!data.understood || !data.report) {
        setReport(null);
        setSpec(null);
        return;
      }
      show(data.report, data.spec);
      // "...as a PDF": the file is produced as well as the dashboard.
      for (const f of (data.formats || []).filter((x) => DOWNLOADABLE.has(x))) {
        // eslint-disable-next-line no-await-in-loop
        await download(f, data.spec);
      }
    } catch (e) {
      setError(getErrorMessage(e, 'Could not build that report.'));
    } finally {
      setBusy(false);
    }
  }, [busy, show, download]);

  const runSpec = useCallback(async (nextSpec) => {
    setBusy(true);
    setError('');
    setNote(null);
    try {
      const { data } = await reportStudioAPI.generate(nextSpec, 'json');
      show(data, nextSpec);
    } catch (e) {
      setReport(null);
      setError(getErrorMessage(e, 'Could not build that report.'));
    } finally {
      setBusy(false);
    }
  }, [show]);

  // A link from Close Bhai: ?run=<spec json>  (or ?q=<sentence>)
  useEffect(() => {
    if (!catalog || handledLink.current) return;
    handledLink.current = true;
    const run = params.get('run');
    const q = params.get('q');
    if (run) {
      try {
        const linked = JSON.parse(run);
        if (linked && typeof linked.kind === 'string') runSpec(linked);
      } catch {
        setError('That report link is not valid.');
      }
    } else if (q) {
      setText(q);
      runAsk(q);
    }
  }, [catalog, params, runAsk, runSpec]);

  const kinds = catalog?.kinds || [];
  const kindMeta = useMemo(() => kinds.find((k) => k.kind === form.kind), [kinds, form.kind]);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const buildFromOptions = (e) => {
    e.preventDefault();
    const out = { kind: form.kind };
    ['period_id', 'compare_period_id', 'entity_id', 'member_id'].forEach((k) => {
      if (form[k] && (k !== 'compare_period_id' || kindMeta?.uses_comparison) && (k !== 'member_id' || form.kind === 'pending_by_member')) out[k] = form[k];
    });
    runSpec(out);
  };

  return (
    <div className="rs">
      <div className="rs-top">
        <div>
          <h1><i className="bi bi-bar-chart-line" aria-hidden="true" /> Report Studio</h1>
          <p>Describe the report you want. Get KPIs, charts, key findings and recommended actions, as a dashboard, PDF, Excel or CSV.</p>
        </div>
        <Link className="rs-link" to={orgPath(user?.role === 'SUPER_ADMIN' ? '/reports/superadmin' : '/reports/teamlead')}>
          <i className="bi bi-table" aria-hidden="true" /> Classic reports
        </Link>
      </div>

      <section className="rs-ask" aria-label="Ask for a report">
        <form onSubmit={(e) => { e.preventDefault(); runAsk(text); }}>
          <Form.Label htmlFor="rs-text" className="rs-ask-label">What report do you need?</Form.Label>
          <div className="rs-ask-row">
            <Form.Control
              id="rs-text" as="textarea" rows={2} maxLength={1000} value={text} disabled={busy || !catalog}
              placeholder="e.g. Compare this period with the previous period, as an Excel file"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runAsk(text); } }}
            />
            <Button type="submit" disabled={busy || !text.trim() || !catalog}>
              {busy ? <Spinner size="sm" animation="border" /> : <i className="bi bi-stars" aria-hidden="true" />} Generate
            </Button>
          </div>
        </form>
        <div className="rs-examples" aria-label="Examples">
          {(catalog?.examples || []).map((ex) => (
            <button key={ex} type="button" className="rs-example" disabled={busy} onClick={() => { setText(ex); runAsk(ex); }}>{ex}</button>
          ))}
        </div>
        <button type="button" className="rs-link" aria-expanded={showOptions} onClick={() => setShowOptions((v) => !v)}>
          <i className={`bi ${showOptions ? 'bi-chevron-up' : 'bi-sliders'}`} aria-hidden="true" /> {showOptions ? 'Hide options' : 'Or choose the options yourself'}
        </button>
        {showOptions && catalog && (
          <form className="rs-options" onSubmit={buildFromOptions}>
            <Form.Group>
              <Form.Label>Report</Form.Label>
              <Form.Select value={form.kind} onChange={set('kind')}>
                {kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
              </Form.Select>
              {kindMeta && <Form.Text muted>{kindMeta.description}</Form.Text>}
            </Form.Group>
            <Form.Group>
              <Form.Label>Period</Form.Label>
              <Form.Select value={form.period_id} onChange={set('period_id')}>
                {catalog.periods.map((p) => <option key={p.id} value={p.id}>{p.label}{p.is_current ? ' (current)' : ''}</option>)}
              </Form.Select>
            </Form.Group>
            {kindMeta?.uses_comparison && (
              <Form.Group>
                <Form.Label>Compare with</Form.Label>
                <Form.Select value={form.compare_period_id} onChange={set('compare_period_id')}>
                  <option value="">The period before</option>
                  {catalog.periods.filter((p) => p.id !== form.period_id).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </Form.Select>
              </Form.Group>
            )}
            <Form.Group>
              <Form.Label>Entity</Form.Label>
              <Form.Select value={form.entity_id} onChange={set('entity_id')}>
                <option value="">All entities</option>
                {catalog.entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              </Form.Select>
            </Form.Group>
            {form.kind === 'pending_by_member' && (
              <Form.Group>
                <Form.Label>Team member</Form.Label>
                <Form.Select value={form.member_id} onChange={set('member_id')}>
                  <option value="">Everyone</option>
                  {catalog.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </Form.Select>
              </Form.Group>
            )}
            <div className="rs-options-go"><Button type="submit" disabled={busy}>Build report</Button></div>
          </form>
        )}
      </section>

      <div ref={outputRef} className="rs-output" aria-live="polite">
        {error && <Alert variant="danger" dismissible onClose={() => setError('')}>{error}</Alert>}
        {note?.clarification && <Alert variant="warning"><i className="bi bi-question-circle" aria-hidden="true" /> {note.clarification}</Alert>}
        {note?.message && !note.clarification && (
          <div className="rs-understood">
            <i className="bi bi-check2-circle" aria-hidden="true" /> {note.message}
            {note.assumptions.map((a) => <small key={a}>{a}</small>)}
          </div>
        )}
        {busy && !report && <div className="rs-loading"><Spinner animation="border" size="sm" /> Building your report…</div>}
        {report && <StudioReport report={report} downloading={downloading} onDownload={(f) => download(f)} />}
        {!report && !busy && !error && !note && catalog && (
          <div className="rs-hint">
            <i className="bi bi-arrow-up-circle" aria-hidden="true" />
            Pick an example above or type your own request. Say <b>PDF</b>, <b>Excel</b> or <b>CSV</b> to get a file as well
            ({Object.values(FORMAT_LABEL).join(', ')}).
          </div>
        )}
      </div>
    </div>
  );
}
