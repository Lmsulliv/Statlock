// A dumb progress bar: a filled track showing value/max. All statistics-free —
// it renders a count the API provides, never computes one. The width is clamped
// to 0–100% so a momentarily inconsistent count (value > max between polls) can
// never overflow the track. aria attributes make it a real progressbar for
// assistive tech.
export function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div
      className="progress-track"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <div className="progress-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}
