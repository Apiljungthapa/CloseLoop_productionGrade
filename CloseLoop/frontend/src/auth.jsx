import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import api from './api';
import { connectWS, disconnectWS } from './ws';
import { loadHolidays, setHolidays } from './components/ui/workingDays';

const AuthCtx = createContext(null);

// Matches the backend access-token/refresh-cycle lifetime: if there's been
// no user activity for this long, log out locally rather than silently
// keeping the session alive forever via background refreshes.
const IDLE_LIMIT_MS = 30 * 60 * 1000;
const ACTIVITY_EVENTS = ['mousedown', 'keydown', 'scroll', 'touchstart'];

// Check cookie validity periodically so we log out when the 30-min access
// cookie actually expires, even if the user is still active on the page.
const SESSION_CHECK_INTERVAL_MS = 60 * 1000; // every 60 seconds

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem('cl_user');
    return raw ? JSON.parse(raw) : null;
  });
  const [loading, setLoading] = useState(false);
  const idleTimer = useRef(null);
  const userRef = useRef(user);
  userRef.current = user;

  useEffect(() => {
    let cancelled = false;

    async function bootstrapSession() {
      const cachedUser = localStorage.getItem('cl_user');
      if (!cachedUser) {
        disconnectWS();
        return;
      }

      try {
        const { data } = await api.get('/auth/me');
        if (cancelled) return;

        localStorage.setItem('cl_user', JSON.stringify(data));
        setUser(data);
        connectWS();
        loadHolidays();
      } catch {
        if (cancelled) return;
        localStorage.removeItem('cl_user');
        disconnectWS();
        setUser(null);
      }
    }

    bootstrapSession();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!user) return undefined;

    function resetIdleTimer() {
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(() => {
        if (userRef.current) logout();
      }, IDLE_LIMIT_MS);
    }

    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, resetIdleTimer));
    resetIdleTimer();

    // Periodically verify the access cookie is still valid.
    // The backend access-cookie expires after 30 minutes. If a check fails
    // (401) while the refresh interceptor is disabled, we know the session
    // has truly expired and must redirect to login.
    let cancelledSessionCheck = false;
    async function sessionCheck() {
      if (cancelledSessionCheck) return;
      try {
        // Temporary: bypass the 401-refresh interceptor by using a raw
        // fetch with credentials so we do not trigger cookie rotation.
        const res = await fetch('/api/auth/me', { credentials: 'include' });
        if (!res.ok && cancelledSessionCheck) return;
        if (!res.ok) {
          // Session truly expired — log out without triggering another refresh
          localStorage.removeItem('cl_user');
          disconnectWS();
          setUser(null);
          if (sessionCheck.current) clearTimeout(sessionCheck.current);
          return;
        }
      } catch {
        /* network error — ignore, next check will catch it */
      }
      sessionCheck.current = setTimeout(sessionCheck, SESSION_CHECK_INTERVAL_MS);
    }
    sessionCheck.current = setTimeout(sessionCheck, SESSION_CHECK_INTERVAL_MS);

    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, resetIdleTimer));
      if (idleTimer.current) clearTimeout(idleTimer.current);
      cancelledSessionCheck = true;
      if (sessionCheck.current) clearTimeout(sessionCheck.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  async function login(email, password) {
    setLoading(true);
    try {
      const { data } = await api.post('/auth/login', { email, password });
      const { data: me } = await api.get('/auth/me');
      localStorage.setItem('cl_user', JSON.stringify(me));
      setUser(me);
      connectWS();
      loadHolidays();
      return me;
    } finally { setLoading(false); }
  }

  async function logout() {
    try { await api.post('/auth/logout'); } catch { /* cookie may already be gone */ }
    localStorage.removeItem('cl_user');
    disconnectWS();
    setHolidays([]);
    setUser(null);
  }

  function updateUser(patch) {
    setUser((prev) => {
      const next = { ...prev, ...patch };
      localStorage.setItem('cl_user', JSON.stringify(next));
      return next;
    });
  }

  return <AuthCtx.Provider value={{ user, login, logout, loading, updateUser }}>{children}</AuthCtx.Provider>;
}

export function useAuth() { return useContext(AuthCtx); }
