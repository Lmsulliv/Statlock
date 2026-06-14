import type { Verdict } from '../api/types'

const LABELS: Record<Verdict, string> = {
  strength: 'Strength',
  weakness: 'Weakness',
  not_enough_data: 'Not enough data',
}

// A verdict earns color only when the interval excludes the baseline; otherwise
// it reads neutral, by design (presentation rule 2).
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const tone =
    verdict === 'strength' ? 'pos' : verdict === 'weakness' ? 'neg' : 'neutral'
  return <span className={`badge tone-${tone}`}>{LABELS[verdict]}</span>
}
