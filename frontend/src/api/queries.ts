import { useQuery } from '@tanstack/react-query'
import type { Scope } from '../scope/useScope'
import { fetchJson, scopeParams } from './client'
import type { ErasResponse, MatchupRow, PlayedHero, Rank, SyncStatus } from './types'

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

// Rank tiers (name + color + derived badge art) for the rank-range selector.
// Reference data, so it's effectively static for the session.
export function useRanks() {
  return useQuery({
    queryKey: ['ranks'],
    queryFn: () => fetchJson<Rank[]>('/api/ranks'),
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
