// Shared lookup tables used across the task tables and the detail panel.

export const statusMeta = {
  notstarted: { cls: 'badge-notstarted', label: 'Not Started' },
  progress: { cls: 'badge-progress', label: 'In Progress' },
  duetoday: { cls: 'badge-duetoday', label: 'Due Today' },
  overdue: { cls: 'badge-overdue', label: 'Overdue' },
  completed: { cls: 'badge-done', label: 'Completed' },
  review: { cls: 'badge-review', label: 'Pending Review' },
  skipped: { cls: 'badge-skipped', label: 'Skipped' },
  upcoming: { cls: 'badge-upcoming', label: 'Upcoming' },
  rejected: { cls: 'badge-rejected', label: 'Rejected' },
};

export const priorityMeta = {
  high: { cls: 'p-high', label: 'High Priority' },
  med: { cls: 'p-med', label: 'Medium Priority' },
  low: { cls: 'p-low', label: 'Low Priority' },
};

export const entityColors = {
  NL: '#2F55D4',
  DE: '#E5484D',
  FR: '#1FA971',
  UK: '#8B5CF6',
  SG: '#DE9B1F',
  US: '#0EA5E9',
};

// Order the status tabs appear in on both task pages.
export const statusTabOrder = [
  { status: '', label: 'All Tasks' },
  { status: 'notstarted', label: 'Not Started' },
  { status: 'progress', label: 'In Progress' },
  { status: 'duetoday', label: 'Due Today' },
  { status: 'overdue', label: 'Overdue' },
  { status: 'completed', label: 'Completed' },
  { status: 'review', label: 'Pending Review' },
];
