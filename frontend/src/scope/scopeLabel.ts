import type { Era, PlayedHero, Rank, TrackedAccount } from '../api/types'
import { FULL_BADGE_MAX, FULL_BADGE_MIN, type Scope } from './useScope'

// The active scope, written out in words. Presentation rule 4: scope is always
// printed next to the numbers it produced, because a stat without its scope is
// how misreadings happen.
export function scopeLabel(
  scope: Scope,
  eras: Era[],
  heroes: PlayedHero[],
  ranks: Rank[],
  accounts: TrackedAccount[],
): string {
  const parts: string[] = []

  // accountId === null means the self account (the server's default). Otherwise
  // name the chosen account, falling back to its id until display names exist.
  const account =
    scope.accountId === null
      ? accounts.find((a) => a.is_self)
      : accounts.find((a) => a.account_id === scope.accountId)
  if (scope.accountId === null) {
    parts.push(account?.display_name ?? 'Self account')
  } else {
    parts.push(account?.display_name ?? `Account ${scope.accountId}`)
  }

  if (scope.heroId === null) {
    parts.push('all my heroes')
  } else {
    const hero = heroes.find((h) => h.hero_id === scope.heroId)
    parts.push(`as ${hero?.name ?? `Hero ${scope.heroId}`}`)
  }

  if (scope.eraIds.length === 0) {
    parts.push('All eras')
  } else {
    // The span is rendered by its endpoints. eras is started_at-ordered, so the
    // window's bounds are the min/max index among the selected era ids.
    const idxs = scope.eraIds
      .map((id) => eras.findIndex((e) => e.era_id === id))
      .filter((i) => i >= 0)
    const startLabel = eras[Math.min(...idxs)]?.label ?? 'era'
    const endLabel = eras[Math.max(...idxs)]?.label ?? 'era'
    parts.push(startLabel === endLabel ? startLabel : `${startLabel} to ${endLabel}`)
  }

  const fullRange = scope.badgeMin <= FULL_BADGE_MIN && scope.badgeMax >= FULL_BADGE_MAX
  if (fullRange) {
    parts.push('All ranks')
  } else {
    const tierName = (badge: number) =>
      ranks.find((r) => r.tier === Math.floor(badge / 10))?.name ?? `badge ${badge}`
    const lo = tierName(scope.badgeMin)
    const hi = tierName(scope.badgeMax)
    parts.push(lo === hi ? lo : `${lo}–${hi}`)
  }

  parts.push(scope.inLane ? 'in-lane (same-lane baseline)' : 'overall')
  parts.push(`min ${scope.minGames} games`)
  parts.push(scope.gameMode === '4' ? 'Street Brawl' : 'Normal')

  return parts.join(' · ')
}
