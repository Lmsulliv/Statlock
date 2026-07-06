import type { Verdict } from '../api/types'
import { InfoTip } from './InfoTip'
import { VERDICT_TONE, verdictLabel, verdictTip } from './verdict'

// A verdict earns color only by its confidence tier (clear = vivid, leaning =
// muted, none = gray); magnitude never drives color (presentation rule 2). The
// neutral tier's wording depends on the sample (see verdictLabel). The badge is
// its own info tooltip trigger (hover/focus/tap), explaining what the tier means
// in one plain sentence.
export function VerdictBadge({ verdict, games }: { verdict: Verdict; games: number }) {
  return (
    <InfoTip tip={verdictTip(verdict, games)}>
      <span className={`badge tone-${VERDICT_TONE[verdict]}`}>
        {verdictLabel(verdict, games)}
      </span>
    </InfoTip>
  )
}
