// DateInput — calendar-aware date field.
//
//   calendarType === 'BS'  -> Bikram Sambat (Nepali) picker (BikramDateInput)
//   otherwise              -> English (AD) picker (GregorianDateInput)
//
// Either way `value` and `onChange` speak AD ISO strings ("YYYY-MM-DD"),
// so callers and the backend are unchanged — only the picking UI differs.
//
// Saturday, Sunday and the configured holidays are non-working days for every
// tenant: both pickers show them grey / red and refuse to select them, because
// this field is used for task / sub-task / assignee due dates. Pass
// `allowNonWorking` only for a date that is NOT task work (e.g. a period's
// start date).
import BikramDateInput from './BikramDateInput';
import GregorianDateInput from './GregorianDateInput';

export default function DateInput({
  calendarType,
  value = '',
  onChange,
  min,
  max,
  disabled = false,
  allowNonWorking = false,
  className = 'field',
  placeholder,
  id,
}) {
  if (calendarType === 'BS') {
    return (
      <BikramDateInput
        id={id}
        value={value || ''}
        onChange={onChange}
        min={min || undefined}
        max={max || undefined}
        disabled={disabled}
        allowNonWorking={allowNonWorking}
        placeholder={placeholder || 'Pick a Nepali date'}
      />
    );
  }
  return (
    <GregorianDateInput
      id={id}
      className={className}
      value={value || ''}
      onChange={onChange}
      min={min || undefined}
      max={max || undefined}
      disabled={disabled}
      allowNonWorking={allowNonWorking}
      placeholder={placeholder || 'Pick a date'}
    />
  );
}
