import { useQuery } from '@tanstack/react-query'
import type { Scope } from '../scope/useScope'
import { fetchJson, scopeParams } from './client'
import type { ErasResponse, MatchupRow, SyncStatus } from './types'

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
