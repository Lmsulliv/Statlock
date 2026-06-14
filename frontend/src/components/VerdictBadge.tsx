import type { Verdict } from '../api/types'
import { VERDICT_TONE, verdictHint, verdictLabel } from './verdict'

// A verdict earns color only by its confidence tier (clear = vivid, leaning =
// muted, none = gray); magnitude never drives color (presentation rule 2). The
// neutral tier's wording depends on the sample (see verdictLabel).
export function VerdictBadge({ verdict, games }: { verdict: Verdict; games: number }) {
  return (
    <span className={`badge tone-${VERDICT_TONE[verdict]}`} title={verdictHint(verdict, games)}>
      {verdictLabel(verdict, games)}
    </span>
  )
}
