import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { usePlayerPin } from '../player/PlayerContext'

// The scope selector, client-side. It mirrors the API's scope params, and the
// URL query string is its single source of truth: every control reads and
// writes the query string, so any view is bookmarkable and renders identically
// elsewhere (presentation-spec acceptance scenario 4).
export interface Scope {
  accountId: number | null // null -> server resolves the is_self account
  eraIds: number[] // empty -> all-time
  badgeMin: number
  badgeMax: number
  minGames: number
  gameMode: string // "1" Normal, "4" Street Brawl
  heroId: number | null // optional "my hero" perspective filter
  inLane: boolean // false = overall game, true = your lane pairing only
}

export const FULL_BADGE_MIN = 0
export const FULL_BADGE_MAX = 116
export const DEFAULT_MIN_GAMES = 3
export const GAME_MODE_NORMAL = '1'

const DEFAULTS: Scope = {
  accountId: null,
  eraIds: [],
  badgeMin: FULL_BADGE_MIN,
  badgeMax: FULL_BADGE_MAX,
  minGames: DEFAULT_MIN_GAMES,
  gameMode: GAME_MODE_NORMAL,
  heroId: null,
  inLane: false,
}

function num(value: string | null): number | null {
  if (value === null || value.trim() === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function scopeFromParams(params: URLSearchParams): Scope {
  return {
    accountId: num(params.get('account_id')),
    eraIds: (params.get('era_ids') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s !== '')
      .map(Number)
      .filter((n) => Number.isFinite(n)),
    badgeMin: num(params.get('badge_min')) ?? DEFAULTS.badgeMin,
    badgeMax: num(params.get('badge_max')) ?? DEFAULTS.badgeMax,
    minGames: num(params.get('min_games')) ?? DEFAULTS.minGames,
    gameMode: params.get('game_mode') ?? DEFAULTS.gameMode,
    heroId: num(params.get('hero_id')),
    inLane: params.get('in_lane') === 'true',
  }
}

export function useScope() {
  const [params, setParams] = useSearchParams()
  const pin = usePlayerPin()
  // On a public /player/:accountId view the account is fixed by the URL path, so
  // it overrides the query param and is never itself written back to the URL.
  const pinnedAccount =
    pin && Number.isFinite(pin.accountId) ? pin.accountId : null
  const scope = useMemo(() => {
    const s = scopeFromParams(params)
    if (pinnedAccount !== null) s.accountId = pinnedAccount
    return s
  }, [params, pinnedAccount])

  const update = useCallback(
    (patch: Partial<Scope>) => {
      const next: Scope = { ...scope, ...patch }
      // Only non-default values land in the URL, keeping it short while still
      // fully determining the view (a missing param falls back to the default).
      // account_id is omitted entirely when pinned (the path already fixes it).
      const sp = new URLSearchParams()
      if (pinnedAccount === null && next.accountId !== null)
        sp.set('account_id', String(next.accountId))
      if (next.eraIds.length > 0) sp.set('era_ids', next.eraIds.join(','))
      if (next.badgeMin !== DEFAULTS.badgeMin) sp.set('badge_min', String(next.badgeMin))
      if (next.badgeMax !== DEFAULTS.badgeMax) sp.set('badge_max', String(next.badgeMax))
      if (next.minGames !== DEFAULTS.minGames) sp.set('min_games', String(next.minGames))
      if (next.gameMode !== DEFAULTS.gameMode) sp.set('game_mode', next.gameMode)
      if (next.heroId !== null) sp.set('hero_id', String(next.heroId))
      if (next.inLane) sp.set('in_lane', 'true')
      setParams(sp, { replace: true })
    },
    [scope, setParams, pinnedAccount],
  )

  return { scope, update }
}
