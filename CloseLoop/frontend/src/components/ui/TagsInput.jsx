import { useState } from 'react';
import Icon from './Icon';

const MAX_TAGS = 15;
const MAX_TAG_LEN = 50;

function normalize(raw, existing) {
  const t = raw.trim().replace(/\s+/g, ' ').slice(0, MAX_TAG_LEN);
  if (!t) return null;
  if (existing.some((e) => e.toLowerCase() === t.toLowerCase())) return null;
  return t;
}

// Chip-style free-text tag editor. `value`/`onChange` are plain string
// arrays — optional everywhere it's used, so an empty array is the norm.
// Enter or comma commits the current draft as a chip; Backspace on an
// empty draft removes the last one. The backend re-normalizes (trim,
// case-insensitive dedupe, 15-tag cap) on save regardless.
export default function TagsInput({
  value = [], onChange, placeholder = 'Add a tag…', readOnly = false,
}) {
  const [draft, setDraft] = useState('');

  const commit = () => {
    const clean = normalize(draft, value);
    setDraft('');
    if (clean && value.length < MAX_TAGS) onChange([...value, clean]);
  };

  const remove = (tag) => onChange(value.filter((t) => t !== tag));

  if (readOnly) {
    if (!value.length) return <span className="lead-unassigned">No tags</span>;
    return (
      <div className="tag-chip-list">
        {value.map((t) => <span className="tag-chip" key={t}>{t}</span>)}
      </div>
    );
  }

  return (
    <div className="tags-input">
      {value.map((t) => (
        <span className="tag-chip" key={t}>
          {t}
          <span className="tag-remove" onClick={() => remove(t)}>
            <Icon name="close" size={10} strokeWidth={2.6} />
          </span>
        </span>
      ))}
      {value.length < MAX_TAGS && (
        <input
          type="text"
          value={draft}
          placeholder={value.length ? '' : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(); }
            else if (e.key === 'Backspace' && !draft && value.length) remove(value[value.length - 1]);
          }}
          onBlur={() => draft.trim() && commit()}
        />
      )}
    </div>
  );
}
