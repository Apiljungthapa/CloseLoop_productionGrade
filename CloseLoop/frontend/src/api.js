import axios from 'axios';

// Auth is httpOnly cookies (set by the backend), not a token we hold in JS —
// withCredentials makes the browser send/receive them on every request.
const api = axios.create({ baseURL: '/api', withCredentials: true });

let refreshPromise = null;

function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = api.post('/auth/refresh').finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const { config, response } = err;
    const isAuthRoute = config?.url?.startsWith('/auth/');

    if (response?.status === 401 && config && !config._retried && !isAuthRoute) {
      config._retried = true;
      try {
        await refreshSession();
        return api(config);
      } catch {
        // refresh failed — fall through to logout redirect below
      }
    }

    if (response?.status === 401) {
      localStorage.removeItem('cl_user');
      if (!location.pathname.startsWith('/login')) location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export function getErrorMessage(err, fallback = 'Failed') {
  const detail = err?.response?.data?.detail;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === 'string' ? d : `${(d.loc || []).slice(-1)[0] ?? 'field'}: ${d.msg}`))
      .join('; ');
  }
  return detail.msg || fallback;
}

export default api;
