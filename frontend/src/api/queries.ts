import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { Scope } from '../scope/useScope'
import { deleteJson, fetchJson, postJson, putJson, scopeParams } from './client'
import type {
  AccountName,
  DeathPatternsResponse,
  ErasResponse,
  Improvement,
  ItemRow,
  LaningRow,
  MatchDetail,
  MatchupRow,
  Me,
  Overview,
  PerformanceRow,
  PlayedHero,
  Rank,
  RecurringPlayersResponse,
  SyncStatus,
  TiltResponse,
  TrackedAccount,
  TrendsResponse,
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

// Continuous-metric performance per hero and overall, each vs a live population
// baseline. Account-wide (no hero_id gate); scope drives the refetch like matchups.
export function usePerformance(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['performance', params],
    queryFn: () => fetchJson<PerformanceRow[]>('/api/performance', params),
  })
}

// Early-game (laning) report: net worth / last hits / denies at the lane-end mark
// per hero and overall, each vs the live population. Same shape as performance.
export function useLaning(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['laning', params],
    queryFn: () => fetchJson<LaningRow[]>('/api/laning', params),
  })
}

// Performance over time: win rate + continuous metrics as a chronological
// series. The view toggles (rolling vs calendar, the window width, week vs
// month) are UI state, not scope, so they're passed in separately and folded
// into both the request params and the query key (so toggling refetches).
export interface TrendsParams {
  mode: 'rolling' | 'calendar'
  granularity: 'week' | 'month'
  windowGames: number
}

export function useTrends(scope: Scope, opts: TrendsParams) {
  const params = {
    ...scopeParams(scope),
    mode: opts.mode,
    granularity: opts.granularity,
    window_games: String(opts.windowGames),
  }
  return useQuery({
    queryKey: ['trends', params],
    queryFn: () => fetchJson<TrendsResponse>('/api/trends', params),
  })
}

// Death patterns: which enemy heroes kill you most (raw counts + games faced)
// and how your deaths distribute over the game timeline vs a live population
// baseline. Account-wide; scope drives the refetch like performance.
export function useDeathPatterns(scope: Scope) {
  const params = scopeParams(scope)
  return useQuery({
    queryKey: ['death-patterns', params],
    queryFn: () => fetchJson<DeathPatternsResponse>('/api/death-patterns', params),
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

// `refetchInterval` lets a screen poll the worker's heartbeat (the Accounts
// screen passes one so a freshly imported account's queue depth updates live).
// Omitted elsewhere, so existing callers keep the default 30s-stale behavior.
export function useSyncStatus(refetchInterval?: number) {
  return useQuery({
    queryKey: ['sync-status'],
    queryFn: () => fetchJson<SyncStatus>('/api/sync-status'),
    refetchInterval,
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

// ── Authentication ───────────────────────────────────────────────────────────

// The viewer's identity (Steam login state). The account switcher and management
// nav key off this. staleTime: Infinity because login/logout are full-page
// navigations (a Steam redirect; a logout reload), so this refetches on load.
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => fetchJson<Me>('/api/auth/me'),
    staleTime: Infinity,
  })
}

// Log out (revoke the session) then hard-reload to the root, so all per-user data
// refetches as anonymous and the cleared cookies take effect. A full reload is the
// simplest correct reset for cookie-based auth.
export function useLogout() {
  return useMutation({
    mutationFn: () => postJson<void>('/api/auth/logout'),
    onSuccess: () => window.location.assign('/'),
  })
}

// Tracked accounts for the ScopeBar's account switcher and the Accounts screen.
// staleTime: Infinity because the list rarely changes within a session; the
// add/rename mutations below invalidate ['accounts'] to force a refetch when it
// does (invalidation overrides staleTime).
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: () => fetchJson<TrackedAccount[]>('/api/accounts'),
    staleTime: Infinity,
  })
}

// Import a tracked account (the owner-gated POST /api/accounts). The server only
// records the account and returns 202; the worker ingests later, so on success
// we refresh the accounts list and the sync-status badge (queue depth bumps as
// the worker discovers matches). Mirrors useCandidateMutation's invalidation.
export function useAddAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { account_id: string; display_name?: string }) =>
      postJson<TrackedAccount>('/api/accounts', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] })
      qc.invalidateQueries({ queryKey: ['sync-status'] })
    },
  })
}

// A rename changes a resolved name, which the API surfaces on the accounts list,
// the recurring-players rows, and any open match detail. Invalidate all three
// (prefix-matched, so every scoped variant refetches).
function invalidateNames(qc: QueryClient) {
  qc.invalidateQueries({ queryKey: ['accounts'] })
  qc.invalidateQueries({ queryKey: ['recurring-players'] })
  qc.invalidateQueries({ queryKey: ['match'] })
}

// Set a manual label for any account (the owner-gated namer, PUT). Works for
// untracked co-players/opponents too, which is the point.
export function useRenameName() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ accountId, displayName }: { accountId: number; displayName: string }) =>
      putJson<AccountName>(`/api/accounts/${accountId}/name`, { display_name: displayName }),
    onSuccess: () => invalidateNames(qc),
  })
}

// Clear a manual label (DELETE), reverting the name to the Steam persona then the
// bare id. Idempotent server-side, so it is safe even when no label was set.
export function useClearName() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (accountId: number) =>
      deleteJson<AccountName>(`/api/accounts/${accountId}/name`),
    onSuccess: () => invalidateNames(qc),
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
