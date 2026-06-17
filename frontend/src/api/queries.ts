import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Scope } from '../scope/useScope'
import { fetchJson, postJson, scopeParams } from './client'
import type {
  ErasResponse,
  Improvement,
  ItemRow,
  MatchDetail,
  MatchupRow,
  Overview,
  PlayedHero,
  Rank,
  RecurringPlayersResponse,
  SyncStatus,
  TiltResponse,
  TrackedAccount,
} from './types'

// Each hook's query key embeds the scope params, so changing the scope produces
// a new key and TanStack Query refetches automatically. (More screens get their
// hooks here once their screens are built.)

export function useMatchups(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['matchups', params],
    queryFn: () => fetchJson<MatchupRow[]>('/api/matchups', params),
  })
}

// Items for one chosen hero. hero_id is required by the endpoint, so the query
// is gated with `enabled`: TanStack Query won't run the fetch until a hero is
// picked, which keeps us from sending an invalid request (and lets the screen
// show a "pick a hero" prompt instead of an error).
export function useItems(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['items', params],
    queryFn: () => fetchJson<ItemRow[]>('/api/items', params),
    enabled: scope.heroId !== null,
  })
}

export function useOverview(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['overview', params],
    queryFn: () => fetchJson<Overview>('/api/overview', params),
  })
}

// One match's detail, reached by clicking a recent-match row. accountId is the
// "you" perspective (carried from the current scope) so the highlighted player
// and the shown purchases match whichever account's Overview the click came from.
export function useMatchDetail(matchId: number, accountId: number | null) {
  return useQuery({
    queryKey: ['match', matchId, accountId],
    queryFn: () =>
      fetchJson<MatchDetail>(
        `/api/matches/${matchId}`,
        accountId !== null ? { account_id: String(accountId) } : undefined,
      ),
    enabled: Number.isFinite(matchId),
  })
}

export function useImprovement(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['improvement', params],
    queryFn: () => fetchJson<Improvement>('/api/improvement', params),
  })
}

// Session-index and loss-streak performance for the scoped account.
export function useTilt(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['tilt', params],
    queryFn: () => fetchJson<TiltResponse>('/api/tilt', params),
  })
}

// Recurring teammates (win rate with) and opponents (win rate against) for the
// scoped account. scopeParams already carries hero_id, so the per-hero baseline
// follows the global "my hero" filter automatically.
export function useRecurringPlayers(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['recurring-players', params],
    queryFn: () => fetchJson<RecurringPlayersResponse>('/api/recurring-players', params),
  })
}

export function useSyncStatus() {
  return useQuery({
    queryKey: ['sync-status'],
    queryFn: () => fetchJson<SyncStatus>('/api/sync-status'),
  })
}

export function useEras() {
  return useQuery({
    queryKey: ['eras'],
    queryFn: () => fetchJson<ErasResponse>('/api/eras'),
  })
}

// Confirm or dismiss a pending era candidate — the app's only write path. A
// "mutation" is TanStack Query's wrapper for a write: it tracks pending/error
// state and runs onSuccess. We invalidate every query that reflects era state so
// each view refetches: the eras list/candidates here, the sync-status badge, and
// the overview (whose banner counts pending candidates).
function useCandidateMutation(action: 'confirm' | 'dismiss') {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (candidateId: number) =>
      postJson(`/api/eras/candidates/${candidateId}/${action}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eras'] })
      qc.invalidateQueries({ queryKey: ['sync-status'] })
      qc.invalidateQueries({ queryKey: ['overview'] })
    },
  })
}

export const useConfirmCandidate = () => useCandidateMutation('confirm')
export const useDismissCandidate = () => useCandidateMutation('dismiss')

// Rank tiers (name + color + derived badge art) for the rank-range selector.
// Reference data, so it's effectively static for the session.
export function useRanks() {
  return useQuery({
    queryKey: ['ranks'],
    queryFn: () => fetchJson<Rank[]>('/api/ranks'),
    staleTime: Infinity,
  })
}

// Tracked accounts for the ScopeBar's account switcher. Effectively static for a
// viewing session (new accounts come from the ingest CLI), so cache like ranks.
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => fetchJson<TrackedAccount[]>('/api/accounts'),
    staleTime: Infinity,
  })
}

// The heroes the account plays, for the "my hero" perspective picker. Depends
// only on account + game mode, so the key uses just those (not the full scope).
export function usePlayedHeroes(scope: Scope) {
  const params = scopeParams(scope)
  const key = { account_id: params.account_id, game_mode: params.game_mode }
  return useQuery({
    queryKey: ['heroes', key],
    queryFn: () => fetchJson<PlayedHero[]>('/api/heroes', params),
  })
}
