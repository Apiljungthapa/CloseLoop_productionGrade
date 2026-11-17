import { useCallback, useEffect, useRef, useState } from 'react';
import { getErrorMessage } from '../../api';

// Dropdown whose options come from the HRMS (through the backend). The list
// is fetched the first time the dropdown is opened — not on page load — and
// again after a failure. Used for Project and Activity (see the thin
// wrappers HrmsProjectSelect / HrmsActivitySelect).
//
//   id, value    — control id; selected option id ('' = none)
//   onChange     — (id, option) => void   (option = the full row, so callers
//                  can read extra fields such as an activity's sub activities)
//   invalid      — outlines the control (validation error)
//   disabledIds  — option ids that can't be picked here (e.g. a project
//                  already used in another block)
//   cacheKey     — which list this is; the fetched list is shared per key in
//                  a short-lived module cache, so several dropdowns on one
//                  page don't each re-request it
//   fetchOptions — () => Promise<axios response with data: [{ id, name }]>
//   placeholder, noun — "Select Project" / "projects" (loading + empty text)
const CACHE_MS = 60 * 1000;
const caches = new Map(); // cacheKey -> { at: ms, data: [...] }
const cacheFresh = (key) => {
  const c = caches.get(key);
  return !!c && Date.now() - c.at < CACHE_MS;
};

export default function HrmsSelect({
  id, value, onChange, invalid = false, disabledIds = [],
  cacheKey, fetchOptions, placeholder, noun,
}) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(() => (cacheFresh(cacheKey) ? 'ready' : 'idle')); // idle | loading | ready | error
  const [options, setOptions] = useState(() => (cacheFresh(cacheKey) ? caches.get(cacheKey).data : []));
  const [chosen, setChosen] = useState(null); // the option picked here, so its name survives a list refresh
  const [error, setError] = useState('');
  const [active, setActive] = useState(-1);
  const wrapRef = useRef(null);
  const listRef = useRef(null);

  const load = useCallback(async () => {
    setStatus('loading');
    setError('');
    try {
      const r = await fetchOptions();
      const list = Array.isArray(r.data) ? r.data : [];
      caches.set(cacheKey, { at: Date.now(), data: list });
      setOptions(list);
      setStatus('ready');
    } catch (e) {
      setError(getErrorMessage(e, `Could not load ${noun} from the HRMS.`));
      setStatus('error');
    }
  }, [fetchOptions, cacheKey, noun]);

  const selected = options.find((o) => String(o.id) === String(value))
    || (chosen && String(chosen.id) === String(value) ? chosen : null);
  const isDisabled = (o) => String(o.id) !== String(value) && disabledIds.map(String).includes(String(o.id));

  const openMenu = () => {
    setOpen(true);
    if (status === 'idle' || status === 'error') load();
    else if (!cacheFresh(cacheKey)) load(); // stale list: refresh quietly while it stays visible
  };
  const closeMenu = () => setOpen(false);

  // Close on outside click.
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // Start keyboard focus on the selected row (or the first) once loaded.
  useEffect(() => {
    if (!open || status !== 'ready') return;
    const i = options.findIndex((o) => String(o.id) === String(value));
    setActive(i >= 0 ? i : (options.length ? 0 : -1));
  }, [open, status, options, value]);

  // Keep the active row in view.
  useEffect(() => {
    if (!open || active < 0) return;
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  const choose = (o) => {
    if (isDisabled(o)) return;
    setChosen(o);
    onChange(String(o.id), o);
    setOpen(false);
  };

  const onKeyDown = (e) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === 'Escape') { e.preventDefault(); closeMenu(); return; }
    if (status !== 'ready' || options.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
    else if (e.key === 'End') { e.preventDefault(); setActive(options.length - 1); }
    else if ((e.key === 'Enter' || e.key === ' ') && active >= 0) { e.preventDefault(); choose(options[active]); }
    else if (e.key === 'Tab') closeMenu();
  };

  // The list may not be loaded yet when a value is already chosen (it is
  // only fetched on open) — `chosen` keeps the label in that case.
  const label = selected ? selected.name : '';

  return (
    <div className={`hrms-dd${open ? ' open' : ''}`} ref={wrapRef}>
      <button
        type="button" id={id}
        className={`hrms-dd-btn${label ? '' : ' is-empty'}`}
        aria-haspopup="listbox" aria-expanded={open} aria-invalid={invalid}
        aria-controls={`${id}-list`}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onKeyDown}
      >
        <span className="hrms-dd-text">{label || placeholder}</span>
        <svg className="hrms-dd-chev" width="16" height="16" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="hrms-dd-menu">
          {status === 'loading' && options.length === 0 && (
            <div className="hrms-dd-note" role="status">
              <span className="hrms-dd-spin" aria-hidden="true" /> Loading {noun}…
            </div>
          )}
          {status === 'error' && (
            <div className="hrms-dd-note hrms-dd-note-err" role="alert">
              <span>{error}</span>
              <button type="button" className="hrms-dd-retry" onClick={load}>Retry</button>
            </div>
          )}
          {status === 'ready' && options.length === 0 && (
            <div className="hrms-dd-note">No {noun} found in the HRMS.</div>
          )}
          {options.length > 0 && status !== 'error' && (
            <ul id={`${id}-list`} className="hrms-dd-list" role="listbox" ref={listRef}>
              {options.map((o, i) => {
                const isSel = String(o.id) === String(value);
                const dis = isDisabled(o);
                return (
                  <li
                    key={o.id} data-idx={i} role="option" aria-selected={isSel} aria-disabled={dis}
                    className={`hrms-dd-item${isSel ? ' selected' : ''}${i === active ? ' active' : ''}${dis ? ' disabled' : ''}`}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(o)}
                  >
                    {o.name}
                    {dis && <span className="hrms-dd-tag">Already added</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
