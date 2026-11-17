// The pill toggle used to mark a task/subtask complete.
// `variant` = 'on' (done, blue) | 'review' (amber) | '' (idle).
// `disabled` shows the current state but ignores clicks (e.g. a Team
// Member looking at their own already-approved task).

export default function Toggle({ variant = '', onClick, id, disabled = false, title }) {
  const base = variant === 'on' ? 'toggle on' : variant === 'review' ? 'toggle review' : 'toggle';
  const cls = disabled ? `${base} disabled` : base;
  return (
    <div
      className={cls}
      id={id}
      role="switch"
      aria-checked={variant === 'on'}
      aria-disabled={disabled || undefined}
      title={title}
      onClick={(e) => {
        e.stopPropagation();
        if (disabled) return;
        onClick?.(e);
      }}
    >
      <div className="knob" />
    </div>
  );
}
