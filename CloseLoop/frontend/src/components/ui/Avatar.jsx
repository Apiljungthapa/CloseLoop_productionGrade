// Round initials avatar. `className` picks the sizing rules from global.css
// (m-avatar, sa-avatar, wl-avatar, c-avatar, sm-avatar ...).

export default function Avatar({ initials, color, className = 'm-avatar', style, ...rest }) {
  return (
    <div className={className} style={{ ...(color ? { background: color } : null), ...style }} {...rest}>
      {initials}
    </div>
  );
}
