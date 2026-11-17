// Reusable hook: subscribe to plan/task/subtask events and re-run a refresher.
// Also re-runs on websocket reconnect so we never miss broadcasts that fired
// while the socket was offline.
import { useEffect } from 'react';
import { onEvent } from '../ws';

const EVENTS = new Set([
  'plan:created', 'plan:updated', 'plan:deleted',
  'task:created', 'task:updated', 'task:deleted',
  'subtask:created', 'subtask:updated', 'subtask:deleted',
  'time_entry:created', 'time_entry:updated', 'time_entry:deleted',
  'notification',
  'ws:connected',
]);

export function useLiveRefresh(refresh) {
  useEffect(() => {
    return onEvent((event) => { if (EVENTS.has(event)) refresh(); });
  }, [refresh]);
}
