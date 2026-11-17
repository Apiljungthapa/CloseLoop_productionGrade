// Native WebSocket client (FastAPI uses standard WS, no socket.io).
// Auto-reconnect with backoff, exposes a connection-state event so dashboards
// can re-fetch after reconnecting (in case they missed broadcasts while offline).

let ws = null;
const listeners = new Set();
let reconnectTimer = null;
let backoff = 1000;       // start at 1s
const MAX_BACKOFF = 15000;
let manuallyClosed = false;

function shouldReconnect(code) {
  return ![1000, 1001, 1005, 1006, 4401, 4403].includes(code);
}

function emit(event, payload) {
  listeners.forEach((cb) => {
    try { cb(event, payload); } catch (e) { console.error('WS listener error', e); }
  });
}

export function connectWS() {
  manuallyClosed = false;
  if (ws) try { ws.close(); } catch {}
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // No token in the URL — the session cookie rides along automatically on
  // this same-origin WS handshake.
  const url = `${proto}://${location.host}/ws`;
  try {
    ws = new WebSocket(url);
  } catch (e) {
    console.warn('WS connect failed', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    backoff = 1000;
    emit('ws:connected', {});
  };
  ws.onmessage = (e) => {
    try {
      const { event, payload } = JSON.parse(e.data);
      emit(event, payload);
    } catch (err) {
      console.warn('Bad WS message', err);
    }
  };
  ws.onerror = () => {
    // onclose will fire next and handle reconnect
  };
  ws.onclose = (event) => {
    emit('ws:disconnected', { code: event?.code, reason: event?.reason });
    if (!manuallyClosed && shouldReconnect(event?.code)) scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (!manuallyClosed) {
      backoff = Math.min(backoff * 1.6, MAX_BACKOFF);
      connectWS();
    }
  }, backoff);
}

export function disconnectWS() {
  manuallyClosed = true;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (ws) try { ws.close(); } catch {}
  ws = null;
}

export function onEvent(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
