import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../auth.jsx';
import { getErrorMessage } from '../../api';
import {
  DOC_EXTENSIONS,
  MAX_FILE_MB,
  documentsBatch,
  listDocuments,
  pack,
  removeDocument,
  triage,
  uploadDocuments,
} from './chatApi.js';

// "My documents" inside Close Bhai: add files, watch them being read, remove them. Whatever is added
// here the assistant can answer from, for everyone in the organization. The backend decides the tenant
// from the login; this component only sends files.

const POLL_MS = 2500;
const MAX_POLL_ERRORS = 5;
const REJECTED_REASON = { 413: 'File too large', 415: 'Unsupported file type' };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function uploadError(e) {
  if (e?.response?.status === 413) return 'Those files are too large to send at once. Try fewer or smaller files.';
  return getErrorMessage(e, 'Upload failed. Please try again.');
}

export default function DocumentsPanel({ visible, tabIndex, onBack }) {
  const { user } = useAuth();
  const isAdmin = user?.role === 'SUPER_ADMIN';

  const [docs, setDocs] = useState([]);
  const [limit, setLimit] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [filter, setFilter] = useState('');
  const [dragging, setDragging] = useState(false);
  const [removing, setRemoving] = useState('');
  const [notice, setNotice] = useState('');
  // upload: null, or { phase: 'sending' | 'reading' | 'finished', total, sent, accepted, skipped, batches,
  //                    done, failed, failures, error }
  const [upload, setUpload] = useState(null);

  const uploadRef = useRef(null);
  const abortRef = useRef(null);
  const fileInput = useRef(null);
  const folderInput = useRef(null);

  const patchUpload = useCallback((fn) => {
    uploadRef.current = { ...uploadRef.current, ...fn(uploadRef.current) };
    setUpload(uploadRef.current);
  }, []);

  const loadDocs = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const data = await listDocuments();
      setDocs(data.documents);
      setLimit(data.limit);
    } catch (e) {
      setLoadError(getErrorMessage(e, 'Could not load your documents.'));
    } finally {
      setLoading(false);
    }
  }, []);

  // Load the list whenever the panel is shown.
  useEffect(() => {
    if (visible) loadDocs();
  }, [visible, loadDocs]);

  // `webkitdirectory` is not a React prop; set it on the node.
  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '');
  }, []);

  // Stop sending when the widget goes away (files already handed over keep being read server-side).
  useEffect(() => () => abortRef.current?.abort(), []);

  // While files are being read by the server, follow their progress.
  const reading = upload?.phase === 'reading';
  useEffect(() => {
    if (!reading) return undefined;
    let alive = true;
    (async () => {
      const status = {}; // batch id -> latest { done, failed, finished, failures }
      let errors = 0;
      while (alive) {
        await sleep(POLL_MS);
        if (!alive) return;
        const open = uploadRef.current.batches.filter((id) => !status[id]?.finished);
        for (const id of open) {
          try {
            status[id] = await documentsBatch(id);
            errors = 0;
          } catch {
            errors += 1;
          }
        }
        if (!alive) return;
        const all = Object.values(status);
        const finished = uploadRef.current.batches.every((id) => status[id]?.finished);
        patchUpload(() => ({
          done: all.reduce((n, b) => n + (b.done || 0), 0),
          failed: all.reduce((n, b) => n + (b.failed || 0), 0),
          failures: all.flatMap((b) => b.failures || []),
          ...(finished ? { phase: 'finished' } : {}),
          ...(errors >= MAX_POLL_ERRORS
            ? { phase: 'finished', error: 'Lost contact with the server. Reload the list in a moment to see what was added.' }
            : {}),
        }));
        if (finished || errors >= MAX_POLL_ERRORS) {
          loadDocs();
          return;
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [reading, patchUpload, loadDocs]);

  const start = async (picked) => {
    if (!picked.length || (uploadRef.current && uploadRef.current.phase !== 'finished')) return;
    setNotice('');
    const { ok, skipped } = triage(picked);
    uploadRef.current = {
      phase: ok.length ? 'sending' : 'finished',
      total: ok.length + skipped.length,
      queued: ok.length,
      sent: 0,
      accepted: 0,
      skipped,
      batches: [],
      done: 0,
      failed: 0,
      failures: [],
      error: '',
    };
    setUpload(uploadRef.current);
    if (!ok.length) return;

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    for (const group of pack(ok)) {
      try {
        const res = await uploadDocuments(group, ctrl.signal);
        patchUpload((u) => ({
          sent: u.sent + group.length,
          accepted: u.accepted + (res.accepted || 0),
          batches: res.batch_id ? [...u.batches, res.batch_id] : u.batches,
          skipped: [
            ...u.skipped,
            ...(res.rejected || []).map((r) => ({ name: r.name, reason: REJECTED_REASON[r.status] || 'Not accepted' })),
          ],
        }));
      } catch (e) {
        if (ctrl.signal.aborted) break;
        patchUpload(() => ({ error: uploadError(e) })); // the same cause (limit, session, server) would hit the rest too
        break;
      }
    }
    abortRef.current = null;
    patchUpload((u) => ({ phase: u.batches.length && !ctrl.signal.aborted ? 'reading' : 'finished' }));
    if (!uploadRef.current.batches.length) loadDocs();
  };

  const choose = (e) => {
    const picked = Array.from(e.target.files || []);
    e.target.value = ''; // let the same files be chosen again
    start(picked);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    start(Array.from(e.dataTransfer?.files || []));
  };

  const remove = async (doc) => {
    if (!window.confirm(`Remove "${doc.name}"? The assistant will no longer be able to use it.`)) return;
    setRemoving(doc.id);
    setNotice('');
    try {
      await removeDocument(doc.id);
      setDocs((list) => list.filter((d) => d.id !== doc.id));
    } catch (e) {
      setNotice(getErrorMessage(e, 'Could not remove that document.'));
    } finally {
      setRemoving('');
    }
  };

  const dismiss = () => {
    uploadRef.current = null;
    setUpload(null);
  };

  const busy = upload && upload.phase !== 'finished';
  const shown = filter.trim()
    ? docs.filter((d) => d.name.toLowerCase().includes(filter.trim().toLowerCase()))
    : docs;
  const accepted = upload?.accepted || 0;
  const processed = (upload?.done || 0) + (upload?.failed || 0);
  const pct = upload
    ? upload.phase === 'sending'
      ? Math.round((upload.sent / Math.max(upload.queued, 1)) * 100)
      : Math.round((processed / Math.max(accepted, 1)) * 100)
    : 0;

  return (
    <section
      className="clchat-docs"
      hidden={!visible}
      aria-label="My documents"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className="clchat-docs-bar">
        <button type="button" className="clchat-docs-back" onClick={onBack} tabIndex={tabIndex}>
          <i className="bi bi-arrow-left" aria-hidden="true" /> Back to chat
        </button>
        <span className="clchat-docs-count">
          {docs.length}
          {limit ? ` of ${limit}` : ''} documents
        </span>
      </div>

      <div className={`clchat-drop${dragging ? ' clchat-drop-on' : ''}`}>
        <i className="bi bi-cloud-arrow-up" aria-hidden="true" />
        <p>
          <b>Add documents</b> for Close Bhai to read, then ask about them in the chat.
        </p>
        <div className="clchat-drop-btns">
          <button type="button" className="clchat-docs-btn" onClick={() => fileInput.current?.click()} disabled={busy} tabIndex={tabIndex}>
            Choose files
          </button>
          <button type="button" className="clchat-docs-btn clchat-docs-btn-ghost" onClick={() => folderInput.current?.click()} disabled={busy} tabIndex={tabIndex}>
            Choose a folder
          </button>
        </div>
        <small>
          {DOC_EXTENSIONS.join(' ')} · up to {MAX_FILE_MB} MB each · you can add hundreds at once · or drop them here
        </small>
        <input ref={fileInput} type="file" multiple accept={DOC_EXTENSIONS.join(',')} onChange={choose} hidden />
        <input ref={folderInput} type="file" multiple onChange={choose} hidden />
      </div>

      {upload && (
        <div className="clchat-prog" role="status" aria-live="polite">
          <div className="clchat-prog-line">
            <b>
              {upload.phase === 'sending' && `Sending ${upload.sent} of ${upload.queued}…`}
              {upload.phase === 'reading' && `Reading ${processed} of ${accepted}…`}
              {upload.phase === 'finished' &&
                (accepted ? `Added ${upload.done || 0} of ${accepted} documents` : upload.error ? 'Nothing was added' : 'No documents added')}
            </b>
            {upload.phase === 'finished' && (
              <button type="button" className="clchat-mini" onClick={dismiss} tabIndex={tabIndex}>
                Dismiss
              </button>
            )}
          </div>
          {upload.phase !== 'finished' && (
            <div className="clchat-bar" aria-hidden="true">
              <span style={{ width: `${Math.min(pct, 100)}%` }} />
            </div>
          )}
          {upload.error && <p className="clchat-docs-err">{upload.error}</p>}
          {upload.failed > 0 && (
            <details>
              <summary>{upload.failed} could not be read</summary>
              <ul>
                {upload.failures.slice(0, 30).map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    {f.name} <span>{f.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {upload.skipped.length > 0 && (
            <details>
              <summary>{upload.skipped.length} skipped</summary>
              <ul>
                {upload.skipped.slice(0, 30).map((f, i) => (
                  <li key={`${f.name}-${i}`}>
                    {f.name} <span>{f.reason}</span>
                  </li>
                ))}
                {upload.skipped.length > 30 && <li>…and {upload.skipped.length - 30} more</li>}
              </ul>
            </details>
          )}
        </div>
      )}

      {notice && <p className="clchat-docs-err">{notice}</p>}

      {docs.length > 8 && (
        <input
          className="clchat-docs-filter"
          type="search"
          placeholder="Find a document…"
          aria-label="Find a document"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          tabIndex={tabIndex}
        />
      )}

      <ul className="clchat-doclist">
        {loading && !docs.length && <li className="clchat-docs-empty">Loading…</li>}
        {loadError && (
          <li className="clchat-docs-empty">
            {loadError}{' '}
            <button type="button" className="clchat-mini clchat-retry" onClick={loadDocs} tabIndex={tabIndex}>
              Retry
            </button>
          </li>
        )}
        {!loading && !loadError && !docs.length && <li className="clchat-docs-empty">No documents yet.</li>}
        {shown.map((d) => (
          <li key={d.id} className="clchat-doc">
            <i className="bi bi-file-earmark-text" aria-hidden="true" />
            <span className="clchat-doc-name" title={d.name}>
              {d.name}
            </span>
            {(d.mine || isAdmin) && (
              <button
                type="button"
                className="clchat-mini clchat-doc-del"
                onClick={() => remove(d)}
                disabled={removing === d.id}
                aria-label={`Remove ${d.name}`}
                title="Remove"
                tabIndex={tabIndex}
              >
                <i className="bi bi-trash" aria-hidden="true" />
              </button>
            )}
          </li>
        ))}
        {filter.trim() && docs.length > 0 && !shown.length && <li className="clchat-docs-empty">No match.</li>}
      </ul>
    </section>
  );
}
