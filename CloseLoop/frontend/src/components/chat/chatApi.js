import api from '../../api';

// Talks to the CloseLoop backend only (/api/chat/*). The backend authenticates the user from
// their session cookie and decides the tenant itself — this code never sends a tenant or a key.

export class ChatError extends Error {
  constructor(message, { status = 0, retryAfter = 0 } = {}) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

const FRIENDLY = {
  401: 'Your session has expired. Please sign in again.',
  403: 'The assistant is not available for your account.',
  429: 'You are asking too quickly. Please wait a moment and try again.',
  502: 'The assistant is unavailable right now. Please try again shortly.',
  503: 'The assistant is not available right now.',
};

export async function fetchChatStatus() {
  try {
    const { data } = await api.get('/chat/status');
    return { enabled: !!data?.enabled, documents: !!data?.documents };
  } catch {
    return { enabled: false, documents: false };
  }
}

export function clearChatSession(sessionId) {
  // Best effort: forgetting server-side memory must never block "new chat".
  return api.delete(`/chat/session/${sessionId}`).catch(() => {});
}

async function errorFrom(res) {
  let detail = '';
  try {
    const j = await res.json();
    detail = typeof j.detail === 'string' ? j.detail : '';
  } catch {
    /* not JSON */
  }
  return new ChatError(detail || FRIENDLY[res.status] || `Request failed (${res.status})`, {
    status: res.status,
    retryAfter: parseInt(res.headers.get('Retry-After') || '0', 10) || 0,
  });
}

function parseBlock(block) {
  let event = 'message';
  let data = '';
  block.split('\n').forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trimStart();
  });
  if (!data) return null;
  try {
    return { event, payload: JSON.parse(data) };
  } catch {
    return null;
  }
}

/**
 * Ask a question and stream the answer. `on` receives SSE events by name:
 * status, route, rewrite, meta, sources, token, done. Resolves when the stream ends; rejects with
 * ChatError on HTTP errors or an `error` event.
 */
export async function streamChat({ question, sessionId, signal, on = {} }) {
  const send = () =>
    fetch('/api/chat/stream', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, session_id: sessionId }),
      signal,
    });

  let res = await send();
  if (res.status === 401) {
    // Access cookie lapsed: rotate once (same as the axios interceptor does for normal calls).
    try {
      await api.post('/auth/refresh');
      res = await send();
    } catch {
      /* fall through to the 401 below */
    }
  }
  if (!res.ok || !res.body) throw await errorFrom(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const dispatch = (block) => {
    const parsed = parseBlock(block);
    if (!parsed) return;
    if (parsed.event === 'error') {
      throw new ChatError(parsed.payload.message || 'The assistant hit an error', {
        status: parsed.payload.status || 0,
        retryAfter: parsed.payload.retry_after || 0,
      });
    }
    on[parsed.event]?.(parsed.payload);
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      dispatch(block);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}

// ---- documents the assistant can read ---------------------------------------------------------
// Same rule as the questions: only /api/chat/* is called, and the backend picks the tenant from the
// login, so a tenant or key is never sent from here.

export const DOC_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md', '.csv', '.json', '.html', '.htm', '.xml', '.log'];
export const MAX_FILE_MB = 20; // a request is capped at 25 MB by the proxies in front of the backend
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;
const FILES_PER_REQUEST = 10;
const BYTES_PER_REQUEST = MAX_FILE_BYTES;

/** Split a pick into files worth sending and files skipped with a reason. */
export function triage(files) {
  const ok = [];
  const skipped = [];
  const seen = new Set();
  files.forEach((f) => {
    if (f.name.startsWith('.')) return; // .DS_Store and other hidden files: not worth mentioning
    const dot = f.name.lastIndexOf('.');
    const ext = dot >= 0 ? f.name.slice(dot).toLowerCase() : '';
    let reason = '';
    if (!DOC_EXTENSIONS.includes(ext)) reason = 'Unsupported file type';
    else if (f.size === 0) reason = 'Empty file';
    else if (f.size > MAX_FILE_BYTES) reason = `Larger than ${MAX_FILE_MB} MB`;
    else if (seen.has(f.name)) reason = 'Same name as another file in this upload';
    if (reason) skipped.push({ name: f.name, reason });
    else {
      seen.add(f.name);
      ok.push(f);
    }
  });
  return { ok, skipped };
}

/** Group files into requests of at most 10 files and 20 MB, so hundreds can be sent one request at a time. */
export function pack(files) {
  const groups = [];
  let cur = [];
  let bytes = 0;
  files.forEach((f) => {
    if (cur.length && (cur.length >= FILES_PER_REQUEST || bytes + f.size > BYTES_PER_REQUEST)) {
      groups.push(cur);
      cur = [];
      bytes = 0;
    }
    cur.push(f);
    bytes += f.size;
  });
  if (cur.length) groups.push(cur);
  return groups;
}

export async function listDocuments() {
  const { data } = await api.get('/chat/documents');
  return { documents: data?.documents || [], limit: data?.limit || 0 };
}

/** Send one group of files. Resolves { batch_id, accepted, rejected: [{ name, status }] }. */
export async function uploadDocuments(files, signal) {
  const form = new FormData();
  files.forEach((f) => form.append('files', f, f.name));
  const { data } = await api.post('/chat/documents', form, { signal, timeout: 120000 });
  return data;
}

/** Progress of one upload: { total, finished, done, failed, failures: [{ name, reason }] }. */
export async function documentsBatch(batchId) {
  const { data } = await api.get(`/chat/documents/batches/${batchId}`);
  return data;
}

export function removeDocument(documentId) {
  return api.delete(`/chat/documents/${encodeURIComponent(documentId)}`);
}
