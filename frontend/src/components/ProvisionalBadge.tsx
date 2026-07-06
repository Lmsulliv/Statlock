// A small inline badge that marks a result as still-deepening: shown when the
// response carries provisional: true, hidden otherwise. It ALWAYS renders the
// span and only toggles visibility, so the element keeps its box — the badge
// appearing or disappearing never nudges the surrounding layout. Follows the
// VerdictBadge `.badge tone-*` idiom.
export function ProvisionalBadge({ show }: { show: boolean }) {
  return (
    <span
      className={`badge tone-neutral provisional-badge${show ? '' : ' is-hidden'}`}
      // aria-hidden when not shown so screen readers don't announce a badge that
      // is visually gone; the reserved space is purely presentational.
      aria-hidden={!show}
      title="Some matches are still being analyzed; these numbers will improve."
    >
      early results, deepening as matches are analyzed
    </span>
  )
}
