import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../../auth.jsx';
import Markdown from './markdown.jsx';
import { ChatError, clearChatSession, fetchChatStatus, streamChat } from './chatApi.js';
import DocumentsPanel from './DocumentsPanel.jsx';
import ReportCard from './ReportCard.jsx';
import './chat.css';

// "Close Bhai": a floating assistant for the signed-in user's own organization. The backend
// decides the tenant from the login, so this component sends only the question and a chat id.

const TITLE = 'Close Bhai';
const MAX_KEPT = 50;
const SUGGESTIONS = [
  'How many tasks are overdue?',
  'Which close packs are in progress?',
  'Who has the most open tasks?',
];
const REPORT_SUGGESTION = 'Generate the monthly task completion report.';
const ROUTE_LABEL = { db: 'Your data', doc: 'Docs', both: 'Data + docs', report: 'Report' };

let counter = 0;
const uid = () => `m${Date.now().toString(36)}${(counter += 1)}`;
const newSessionId = () =>
  (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`)
    .replace(/[^A-Za-z0-9_-]/g, '')
    .slice(0, 64)
    .padEnd(8, 'x');

// Kept in sessionStorage (cleared when the tab closes), per user, so a shared computer doesn't
// show the previous person's answers.
const storeKey = (userId) => `clchat.v1.${userId}`;
function loadState(userId) {
  try {
    const raw = sessionStorage.getItem(storeKey(userId));
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && Array.isArray(parsed.messages) && typeof parsed.sessionId === 'string') return parsed;
  } catch {
    /* storage blocked or corrupt */
  }
  return { sessionId: newSessionId(), messages: [] };
}
function saveState(userId, state) {
  try {
    sessionStorage.setItem(storeKey(userId), JSON.stringify(state));
  } catch {
    /* storage blocked */
  }
}

export default function ChatWidget() {
  const { user } = useAuth();
  const userId = user?.id;
  // Team Leads and Super Admins can ask for reports; for everyone else the same words get a polite explanation.
  const canReport = user?.role === 'SUPER_ADMIN' || user?.role === 'TEAM_LEAD';
  const suggestions = canReport ? [REPORT_SUGGESTION, ...SUGGESTIONS.slice(0, 2)] : SUGGESTIONS;

  const [enabled, setEnabled] = useState(false);
  const [docsEnabled, setDocsEnabled] = useState(false); // can the user add documents for the assistant to read?
  const [view, setView] = useState('chat'); // 'chat' | 'docs'
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [statusLine, setStatusLine] = useState('');
  const [draft, setDraft] = useState('');
  const [unread, setUnread] = useState(false);

  const abortRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const fabRef = useRef(null);
  const openRef = useRef(false);
  openRef.current = open;

  // Is the assistant on for this account? (Off for the Platform Owner and when the server has it disabled.)
  useEffect(() => {
    let alive = true;
    setEnabled(false);
    setDocsEnabled(false);
    setView('chat');
    if (userId) {
      fetchChatStatus().then((s) => {
        if (!alive) return;
        setEnabled(s.enabled);
        setDocsEnabled(s.documents);
      });
    }
    return () => {
      alive = false;
    };
  }, [userId]);

  // Restore this user's conversation.
  useEffect(() => {
    if (!userId) return;
    const s = loadState(userId);
    setMessages(s.messages);
    setSessionId(s.sessionId);
  }, [userId]);

  // Persist finished messages (not while a reply is streaming).
  useEffect(() => {
    if (!userId || !sessionId || busy) return;
    saveState(userId, {
      sessionId,
      messages: messages.filter((m) => !m.streaming).slice(-MAX_KEPT),
    });
  }, [userId, sessionId, messages, busy]);

  // Stop any in-flight answer when the widget goes away.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Keep the newest text in view.
  useEffect(() => {
    const el = listRef.current;
    if (el && open) el.scrollTop = el.scrollHeight;
  }, [messages, statusLine, open]);

  useEffect(() => {
    if (open) {
      setUnread(false);
      const t = setTimeout(() => inputRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [open]);

  const autosize = (el) => {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 110)}px`;
  };

  const ask = useCallback(
    async (text) => {
      const question = text.trim();
      if (!question || busy || !sessionId) return;
      const botId = uid();
      setMessages((ms) => [
        ...ms,
        { id: uid(), role: 'user', text: question },
        { id: botId, role: 'bot', text: '', streaming: true, sources: [] },
      ]);
      setDraft('');
      requestAnimationFrame(() => autosize(inputRef.current));
      setBusy(true);
      setStatusLine('Thinking…');

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const patch = (fn) =>
        setMessages((ms) => ms.map((m) => (m.id === botId ? { ...m, ...fn(m) } : m)));

      // Tokens arrive in bursts; batch them to one update per frame so long answers stay smooth.
      let pending = '';
      let raf = 0;
      const flush = () => {
        raf = 0;
        if (!pending) return;
        const chunk = pending;
        pending = '';
        patch((m) => ({ text: m.text + chunk }));
      };

      try {
        await streamChat({
          question,
          sessionId,
          signal: ctrl.signal,
          on: {
            status: (d) => setStatusLine(d.message || ''),
            route: (d) => patch(() => ({ route: d.route })),
            sources: (d) => patch(() => ({ sources: d.items || [] })),
            report: (d) => patch(() => ({ report: d, route: 'report' })),
            token: (d) => {
              pending += d.text || '';
              if (!raf) raf = requestAnimationFrame(flush);
            },
          },
        });
        cancelAnimationFrame(raf);
        flush();
        patch((m) => ({
          streaming: false,
          text: m.text || 'I did not get an answer back. Please try again.',
        }));
        if (!openRef.current) setUnread(true);
      } catch (e) {
        cancelAnimationFrame(raf);
        flush();
        if (e?.name === 'AbortError') {
          patch((m) => ({ streaming: false, text: m.text || 'Stopped.' }));
        } else {
          patch(() => ({
            streaming: false,
            error: true,
            retry: question,
            text:
              e instanceof ChatError
                ? e.message
                : 'Cannot reach the assistant. Check your connection and try again.',
          }));
          if (!openRef.current) setUnread(true);
        }
      } finally {
        abortRef.current = null;
        setBusy(false);
        setStatusLine('');
      }
    },
    [busy, sessionId]
  );

  const retry = (msg) => {
    setMessages((ms) => {
      const i = ms.findIndex((m) => m.id === msg.id);
      return i > 0 ? [...ms.slice(0, i - 1), ...ms.slice(i + 1)] : ms.filter((m) => m.id !== msg.id);
    });
    ask(msg.retry);
  };

  const newChat = () => {
    if (messages.length && !window.confirm('Start a new conversation? This clears the current one.')) return;
    abortRef.current?.abort();
    if (sessionId) clearChatSession(sessionId);
    setMessages([]);
    setSessionId(newSessionId());
    setStatusLine('');
  };

  const copy = (text, e) => {
    const btn = e.currentTarget;
    navigator.clipboard?.writeText(text).then(() => {
      btn.textContent = 'Copied';
      setTimeout(() => {
        btn.textContent = 'Copy';
      }, 1500);
    });
  };

  const close = () => {
    setOpen(false);
    fabRef.current?.focus();
  };

  if (!enabled) return null;

  return (
    <div
      className={`clchat ${open ? 'clchat-open' : ''}`}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) close();
      }}
    >
      <section className="clchat-panel" role="dialog" aria-label={`${TITLE} assistant`} aria-hidden={!open}>
        <header className="clchat-head">
          <div className="clchat-avatar">
            <i className="bi bi-stars" aria-hidden="true" />
          </div>
          <div className="clchat-who">
            <b>{TITLE}</b>
            <small>Ask about your organization's close work</small>
          </div>
          {docsEnabled && (
            <button
              type="button"
              className={`clchat-head-btn${view === 'docs' ? ' clchat-head-btn-on' : ''}`}
              onClick={() => setView((v) => (v === 'docs' ? 'chat' : 'docs'))}
              title="My documents"
              aria-label="My documents"
              aria-pressed={view === 'docs'}
              tabIndex={open ? 0 : -1}
            >
              <i className="bi bi-upload" aria-hidden="true" />
            </button>
          )}
          <button type="button" className="clchat-head-btn" onClick={newChat} title="New conversation" aria-label="New conversation" tabIndex={open ? 0 : -1}>
            <i className="bi bi-arrow-counterclockwise" aria-hidden="true" />
          </button>
          <button type="button" className="clchat-head-btn" onClick={close} title="Close" aria-label="Close" tabIndex={open ? 0 : -1}>
            <i className="bi bi-x-lg" aria-hidden="true" />
          </button>
        </header>

        <DocumentsPanel visible={docsEnabled && open && view === 'docs'} tabIndex={open ? 0 : -1} onBack={() => setView('chat')} />

        <div className="clchat-messages" ref={listRef} role="log" aria-live="polite" aria-busy={busy} hidden={view === 'docs'}>
          {messages.length === 0 ? (
            <div className="clchat-welcome">
              <div className="clchat-big">
                <i className="bi bi-stars" aria-hidden="true" />
              </div>
              <h4>Hello, I am {TITLE}</h4>
              <p>
                Ask me about the tasks, close packs and periods in your organization
                {docsEnabled ? ', or about documents you add.' : '.'}
                {canReport ? ' You can also ask me to prepare a report, as a dashboard, PDF, Excel or CSV.' : ''}
              </p>
              {docsEnabled && (
                <button type="button" className="clchat-chip clchat-chip-docs" onClick={() => setView('docs')} tabIndex={open ? 0 : -1}>
                  <i className="bi bi-cloud-arrow-up" aria-hidden="true" /> Add documents to ask about
                </button>
              )}
              <div className="clchat-chips">
                {suggestions.map((s) => (
                  <button key={s} type="button" className="clchat-chip" onClick={() => ask(s)} disabled={busy} tabIndex={open ? 0 : -1}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`clchat-msg ${m.role}`}>
                <div className={`clchat-bubble${m.error ? ' clchat-error' : ''}${m.streaming ? ' clchat-streaming' : ''}`}>
                  {m.role === 'bot' && !m.error ? (
                    m.text ? (
                      <Markdown text={m.text} />
                    ) : (
                      <span className="clchat-typing" aria-label="Thinking">
                        <span />
                        <span />
                        <span />
                      </span>
                    )
                  ) : (
                    m.text
                  )}
                </div>
                {m.role === 'bot' && m.report && <ReportCard report={m.report} tabIndex={open ? 0 : -1} onNavigate={close} />}
                {m.role === 'bot' && m.streaming && statusLine && <small className="clchat-status">{statusLine}</small>}
                {m.role === 'bot' && !m.streaming && (
                  <div className="clchat-meta">
                    {!m.error && ROUTE_LABEL[m.route] && <span className="clchat-tag">{ROUTE_LABEL[m.route]}</span>}
                    {!m.error &&
                      [...new Map((m.sources || []).map((s) => [`${s.title || s.document_id}|${s.section || ''}`, s])).values()].map((s) => (
                        <span key={`${s.title}|${s.section}`} className="clchat-src" title={`Relevance ${Math.round((s.score || 0) * 100)}%`}>
                          {s.title || s.document_id}
                          {s.section ? ` · ${s.section}` : ''}
                        </span>
                      ))}
                    {!m.error && m.text && (
                      <button type="button" className="clchat-mini" onClick={(e) => copy(m.text, e)} tabIndex={open ? 0 : -1}>
                        Copy
                      </button>
                    )}
                    {m.error && m.retry && (
                      <button type="button" className="clchat-mini clchat-retry" onClick={() => retry(m)} disabled={busy} tabIndex={open ? 0 : -1}>
                        Retry
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <form
          className="clchat-composer"
          hidden={view === 'docs'}
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          <div className="clchat-row">
            {docsEnabled && (
              <button
                type="button"
                className="clchat-attach"
                onClick={() => setView('docs')}
                title="Add documents"
                aria-label="Add documents"
                tabIndex={open ? 0 : -1}
              >
                <i className="bi bi-paperclip" aria-hidden="true" />
              </button>
            )}
            <textarea
              ref={inputRef}
              className="clchat-input"
              rows={1}
              maxLength={2000}
              value={draft}
              placeholder="Ask about your tasks, packs, periods…"
              aria-label="Your question"
              tabIndex={open ? 0 : -1}
              onChange={(e) => {
                setDraft(e.target.value);
                autosize(e.target);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  ask(draft);
                }
              }}
            />
            {busy ? (
              <button type="button" className="clchat-send" onClick={() => abortRef.current?.abort()} aria-label="Stop" title="Stop" tabIndex={open ? 0 : -1}>
                <i className="bi bi-stop-fill" aria-hidden="true" />
              </button>
            ) : (
              <button type="submit" className="clchat-send" disabled={!draft.trim()} aria-label="Send" tabIndex={open ? 0 : -1}>
                <i className="bi bi-send-fill" aria-hidden="true" />
              </button>
            )}
          </div>
          <small className="clchat-foot">Answers come from your organization's CloseLoop data. Double-check important figures.</small>
        </form>
      </section>

      <button
        ref={fabRef}
        type="button"
        className="clchat-fab"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? 'Close assistant' : `Open ${TITLE} assistant`}
        aria-expanded={open}
      >
        <i className={`bi ${open ? 'bi-x-lg' : 'bi-chat-dots-fill'}`} aria-hidden="true" />
        {unread && !open && <span className="clchat-unread" aria-label="New reply" />}
      </button>
    </div>
  );
}
