import React, { useEffect, useState, useRef } from 'react';
import { Overlay, Popover } from 'react-bootstrap';
import api from '../api';
import { onEvent } from '../ws';

// Map a backend notification `type` to an icon + accent colour.
function meta(type = '') {
  const t = String(type).toUpperCase();
  if (t.includes('REJECT')) return { icon: 'bi-x-circle-fill', tone: 'red' };
  if (t.includes('APPROVE')) return { icon: 'bi-check-circle-fill', tone: 'green' };
  if (t.includes('REVIEW')) return { icon: 'bi-inbox-fill', tone: 'amber' };
  if (t.includes('OVERDUE')) return { icon: 'bi-exclamation-triangle-fill', tone: 'red' };
  if (t.includes('ASSIGN')) return { icon: 'bi-person-check-fill', tone: 'blue' };
  if (t.includes('PERIOD')) return { icon: 'bi-calendar-event-fill', tone: 'blue' };
  if (t.includes('REPORT')) return { icon: 'bi-file-earmark-bar-graph-fill', tone: 'blue' };
  if (t.includes('CLOSE_PACK') || t.includes('ADVANCED')) return { icon: 'bi-arrow-repeat', tone: 'blue' };
  return { icon: 'bi-bell-fill', tone: 'blue' };
}

function relativeTime(value) {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return '';
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 45) return 'just now';
  if (secs < 90) return '1 min ago';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs === 1 ? '' : 's'} ago`;
  const days = Math.round(hrs / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  return new Date(value).toLocaleDateString();
}

export default function Notifications() {
  const [list, setList] = useState([]);
  const [show, setShow] = useState(false);
  const targetRef = useRef(null);

  async function load() {
    try { const { data } = await api.get('/notifications'); setList(data); } catch {}
  }
  useEffect(() => {
    load();
    return onEvent((event) => { if (event === 'notification') load(); });
  }, []);

  const unread = list.filter((n) => !n.read).length;

  async function markRead(n) {
    if (n.read) return;
    setList((prev) => prev.map((x) => (x.id === n.id ? { ...x, read: true } : x)));
    try { await api.post(`/notifications/${n.id}/read`); } catch {} finally { load(); }
  }

  async function markAllRead() {
    if (unread === 0) return;
    setList((prev) => prev.map((x) => ({ ...x, read: true })));
    try { await api.post('/notifications/mark-all-read'); } catch {} finally { load(); }
  }

  return (
    <>
      <div ref={targetRef} className="notif-bell" onClick={() => setShow((s) => !s)}>
        <i className="bi bi-bell-fill" />
        {unread > 0 && <span className="notif-count">{unread > 9 ? '9+' : unread}</span>}
      </div>
      <Overlay
        target={targetRef.current}
        show={show}
        placement="bottom"
        rootClose
        onHide={() => setShow(false)}
      >
        <Popover className="notif-popover">
          <Popover.Body className="notif-body">
            <div className="notif-head">
              <span className="notif-title">
                Notifications
                {unread > 0 && <span className="notif-head-badge">{unread}</span>}
              </span>
              {unread > 0 && (
                <button type="button" className="notif-markall" onClick={markAllRead}>
                  Mark all read
                </button>
              )}
            </div>

            <div className="notif-list">
              {list.length === 0 && (
                <div className="notif-empty">
                  <i className="bi bi-bell-slash" />
                  <span>You&apos;re all caught up</span>
                </div>
              )}
              {list.map((n) => {
                const m = meta(n.type);
                return (
                  <div
                    key={n.id}
                    className={`notif-item${n.read ? '' : ' unread'}`}
                    onClick={() => markRead(n)}
                  >
                    <span className={`notif-icon tone-${m.tone}`}>
                      <i className={`bi ${m.icon}`} />
                    </span>
                    <span className="notif-item-body">
                      <span className="notif-msg">{n.message}</span>
                      <span className="notif-time">{relativeTime(n.created_at)}</span>
                    </span>
                    {!n.read && <span className="notif-unread-dot" />}
                  </div>
                );
              })}
            </div>
          </Popover.Body>
        </Popover>
      </Overlay>
    </>
  );
}
