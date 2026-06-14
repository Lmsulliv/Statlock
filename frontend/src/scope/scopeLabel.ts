import type { Era } from '../api/types'
import { FULL_BADGE_MAX, FULL_BADGE_MIN, type Scope } from './useScope'

// The active scope, written out in words. Presentation rule 4: scope is always
// printed next to the numbers it produced, because a stat without its scope is
// how misreadings happen.
export function scopeLabel(scope: Scope, eras: Era[]): string {
  const parts: string[] = []

  parts.push(scope.accountId === null ? 'Self account' : `Account ${scope.accountId}`)

  if (scope.eraIds.length === 0) {
    parts.push('All eras')
  } else {
    parts.push(
      scope.eraIds
        .map((id) => eras.find((e) => e.era_id === id)?.label ?? `Era ${id}`)
        .join(', '),
    )
  }

  const fullRange = scope.badgeMin <= FULL_BADGE_MIN && scope.badgeMax >= FULL_BADGE_MAX
  parts.push(fullRange ? 'All ranks' : `Badge ${scope.badgeMin}–${scope.badgeMax}`)

  parts.push(`min ${scope.minGames} games`)
  parts.push(scope.gameMode === '4' ? 'Street Brawl' : 'Normal')

  return parts.join(' · ')
}
