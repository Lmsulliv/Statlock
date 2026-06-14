// TypeScript mirror of the API response shapes (api/service.py). These are the
// exact field names the backend emits; the frontend renders them and never
// recomputes a statistic. If a field changes server-side, change it here too.

export type Verdict = 'strength' | 'weakness' | 'not_enough_data'

/** The shared statistics block on every matchup/item row (_stat_fields). */
export interface StatFields {
  winrate: number | null // wins/games, or null when games == 0
  ci_low: number // Wilson 95% lower bound
  ci_high: number // Wilson 95% upper bound
  global_matches: number // baseline sample size for this scope
  global_rate: number | null // baseline win rate, null when no baseline
  adjusted_rate: number | null // shrinkage estimate toward the baseline
  delta: number | null // adjusted_rate - global_rate
  verdict: Verdict
}

export interface MatchupRow extends StatFields {
  enemy_hero_id: number
  enemy_hero_name: string
  games: number
  wins: number
}

export interface ItemRow extends StatFields {
  item_id: number
  item_name: string
  games: number
  wins: number
  avg_purchase_s: number | null
  global_avg_purchase_s: number | null
  purchase_timing_delta_s: number | null
}

export interface QueueCounts {
  pending?: number
  failed?: number
  fetched?: number
  unavailable?: number
  [status: string]: number | undefined
}

export interface SyncStatus {
  queue: QueueCounts
  queue_depth: number
  fetched: number
  unavailable: number
  last_discovery_at: string | null
  last_maintenance_at: string | null
  pending_era_candidates: number
  message?: string
}

export interface Era {
  era_id: number
  label: string
  started_at: string
}

export interface EraCandidate {
  candidate_id: number
  post_url: string
  post_title: string | null
  posted_at: string
  change_lines: number | null
  score: number | null
  status: string
}

export interface ErasResponse {
  eras: Era[]
  pending_candidates: EraCandidate[]
  message?: string
}

export interface MmrPoint {
  match_id: number
  badge: number
  start_time: string
}

export interface RecentMatch {
  match_id: number
  hero_id: number
  hero_name: string
  won: boolean
  kills: number
  deaths: number
  assists: number
  net_worth: number
  start_time: string
  game_mode: string
}

export interface Overview {
  account_id: number | null
  mmr_series: MmrPoint[]
  last_matches: RecentMatch[]
  sync: SyncStatus
  message?: string
}
