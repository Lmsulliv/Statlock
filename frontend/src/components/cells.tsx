import { VERDICT_FLOOR } from './verdict'

// Shared table-cell formatters for Matchups and Items, so the two tables render
// thin samples and missing baselines the same way.

const fmtPct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

const fmtDelta = (x: number | null) =>
  x === null ? '—' : `${x > 0 ? '+' : ''}${Math.round(x * 100)} pts`

// Below the stats floor a delta is just noise (a 3-0 record reads as +50 pts),
// so we show a placeholder. At or above the floor the delta is real context
// even when the verdict is still "Inconclusive" — so we always show the number
// there, and let the verdict column carry the significance call.
export function DeltaCell({ value, games }: { value: number | null; games: number }) {
  if (games < VERDICT_FLOOR) {
    return <span className="muted">not enough info</span>
  }
  return <>{fmtDelta(value)}</>
}

// A raw kill count, rendered plainly — it's an exact observation, not an
// estimated rate, so it never carries a confidence interval or verdict. Below
// the stats floor a count scales with too few games to mean anything (a
// 2-from-1-game number reads as big as 2-from-20), so we mute it, the same
// thin-sample treatment DeltaCell uses.
export function KillCountCell({ value, games }: { value: number; games: number }) {
  if (games < VERDICT_FLOOR) {
    return <span className="muted">{value}</span>
  }
  return <>{value}</>
}

// The global-baseline column. With no baseline rows at this scope there's
// nothing to compare against, so say "no baseline" rather than "— / 0 games".
export function BaselineCell({ rate, matches }: { rate: number | null; matches: number }) {
  if (matches === 0) {
    return <span className="muted">no baseline</span>
  }
  return (
    <>
      <div>{fmtPct(rate)}</div>
      <div className="muted">{matches.toLocaleString()} games</div>
    </>
  )
}
