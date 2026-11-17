import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../auth.jsx';
import { onEvent } from '../../ws';
import Notifications from '../Notifications.jsx';
import './appTopBar.css';

// Samrat-style top bar. `nav` is [{ label, to, match(pathname) }].
// Used for the Team Lead and Super Admin areas; the other roles keep the
// standard Bootstrap navbar.
function initialsOf(name = '') {
  const parts = String(name).trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Same stable per-user palette the rest of the app uses for avatars
// (mirrors leadDashUtils.colorFor) so the top-bar badge matches the
// colour a user gets on pack cards, task rows, etc.
const AVATAR_COLORS = [
  '#2F55D4', '#1FA971', '#DE9B1F', '#E5484D', '#8B5CF6',
  '#0EA5E9', '#1A2F6B', '#B9770E',
];
function colorFor(key = '') {
  const s = String(key);
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}

export default function AppTopBar({ nav = [] }) {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const [online, setOnline] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const rightRef = useRef(null);

  useEffect(() => onEvent((event) => {
    if (event === 'ws:connected') setOnline(true);
    if (event === 'ws:disconnected') setOnline(false);
  }), []);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDoc = (e) => { if (!rightRef.current?.contains(e.target)) setMenuOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [menuOpen]);

  useEffect(() => { setMenuOpen(false); }, [loc.pathname]);

  return (
    <div className="app-topbar">
      <div className="logo-block">
        <div className="lt-logo">CloseLoop</div>
        <div className="lt-tag">Close Every Period</div>
      </div>

      <nav className="lt-nav">
        {nav.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className={item.match(loc.pathname) ? 'active' : ''}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="lt-right" ref={rightRef}>
        <Notifications />

        <button className="lt-pill" onClick={() => setMenuOpen((o) => !o)}>
          <span className="lt-avatar" style={{ background: colorFor(user.id || user.name) }}>
            {initialsOf(user.name)}
            <span className={`lt-dot${online ? '' : ' off'}`} />
          </span>
          <span className="lt-idblock">
            <span className="lt-name">{user.name}</span>
            <span className="lt-role">
              {user.title || String(user.role).replace(/_/g, ' ').toLowerCase()}
            </span>
          </span>
          <i className="bi bi-chevron-down lt-caret" />
        </button>

        {menuOpen && (
          <div className="lt-menu">
            <Link to="/profile"><i className="bi bi-person-circle" /> My Profile</Link>
            <button type="button" onClick={logout}>
              <i className="bi bi-box-arrow-right" /> Logout
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
