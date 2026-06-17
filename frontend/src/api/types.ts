// TypeScript mirror of the API response shapes (api/service.py). These are the
// exact field names the backend emits; the frontend renders them and never
// recomputes a statistic. If a field changes server-side, change it here too.

// Five confidence tiers (see stats/__init__.py). "clear" = the 95% interval
// excludes the baseline; "leaning" = a softer 80%-band signal; otherwise none.
export type Verdict =
  | 'clear_strength'
  | 'leaning_strength'
  | 'not_enough_data'
  | 'leaning_weakness'
  | 'clear_weakness'

/** The shared statistics block on every matchup/item row (_stat_fields). */
export interface StatFields {
  winrate: number | null // wins/games, or null when games == 0
  ci_low: number // Wilson 95% lower bound
  ci_high: number // Wilson 95% upper bound
  global_matches: number // baseline sample size for this scope
  global_rate: number | null // baseline win rate, null when no baseline
  adjusted_rate: number | null // shrinkage estimate toward the baseline
  delta: number | null // adjusted_rate - global_rate (shrinkage-adjusted)
  raw_delta: number | null // winrate - global_rate (plain personal vs global)
  verdict: Verdict
}

export interface MatchupRow extends StatFields {
  enemy_hero_id: number
  enemy_hero_name: string
  enemy_hero_image_url: string | null
  games: number
  wins: number
}

// ── Tilt (api/service.tilt) ──────────────────────────────────────────────────

// One bucket of the tilt tables. Carries the same StatFields as a matchup row,
// but here `global_rate`/`global_matches` are the account's OWN overall in-scope
// rate (the "you vs your usual self" baseline), not a population baseline. Each
// row is exactly one of a session-index bucket (`index`) or a loss-streak bucket
// (`streak`); `label` is the server's display string ("1".."6+", "0 losses"..).
export interface TiltBucket extends StatFields {
  label: string
  games: number
  wins: number
  capped: boolean // true for the folded "6+" / "3+" tail bucket
  index?: number // session-index buckets only
  streak?: number // loss-streak buckets only
}

export interface TiltResponse {
  by_session_index: TiltBucket[]
  by_loss_streak: TiltBucket[]
  overall: { games: number; wins: number; winrate: number | null }
  sessions: number
  session_gap_hours: number
}

// ── Recurring players (api/service.recurring_players) ────────────────────────

// One other real player who keeps sharing your matches. Carries the same
// StatFields as a matchup/tilt row, but `global_rate`/`global_matches` are your
// OWN win rate over the same match set (overall, or on the filtered hero) — the
// "you vs your usual self" baseline. `display_name` is null unless the player is
// a tracked account; otherwise the UI shows the bare account_id.
export interface RecurringPlayer extends StatFields {
  account_id: number
  display_name: string | null
  games: number
  wins: number
}

export interface RecurringPlayersResponse {
  teammates: RecurringPlayer[] // win rate WITH them
  opponents: RecurringPlayer[] // win rate AGAINST them
  overall: { games: number; wins: number; winrate: number | null }
  min_co_occurrence: number // shared games needed to be listed at all
  hero_id: number | null // set when the baseline is hero-filtered
}

export interface PlayedHero {
  hero_id: number
  name: string
  image_url: string | null
}

export interface Rank {
  tier: number // 0 Obscurus .. 11 Eternus
  name: string
  color: string | null
  badge_url: string // derived server-side from the tier
}

// A tracked account, for the (viewing-only) account switcher in the ScopeBar.
// display_name is null until names are added in a later phase.
export interface TrackedAccount {
  account_id: number
  display_name: string | null
  is_self: boolean
}

export interface ItemRow extends StatFields {
  item_id: number
  item_name: string
  item_image_url: string | null
  games: number
  wins: number
  avg_purchase_s: number | null
  global_avg_purchase_s: number | null
  purchase_timing_delta_s: number | null
}

// The improvement digest (api/service.improvement). The server flattens matchup
// and item rows into one entry shape, tagging each with `kind` + a display
// `subject` (the enemy hero name or item name), then groups them into three
// lists. Kind-specific fields are optional here because an entry is one or the
// other. The frontend renders these groups verbatim — it never re-filters them,
// because the server already guarantees no unconfirmed delta lands outside the
// watch list (presentation-spec scenario 5).
export interface ImprovementEntry extends StatFields {
  kind: 'matchup' | 'item'
  subject: string
  games: number
  wins: number
  // matchup-only
  enemy_hero_id?: number
  enemy_hero_name?: string
  enemy_hero_image_url?: string | null
  // item-only
  item_id?: number
  item_name?: string
  item_image_url?: string | null
  hero_id?: number
  avg_purchase_s?: number | null
  global_avg_purchase_s?: number | null
  purchase_timing_delta_s?: number | null
}

export interface Improvement {
  confirmed_weaknesses: ImprovementEntry[]
  confirmed_strengths: ImprovementEntry[]
  watch_list: ImprovementEntry[]
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
  image_url: string | null
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

// ── Match detail (api/service.match_detail) ──────────────────────────────────

// One of the 12 players in a match. `won` is derived against winning_team and
// `lane` is the raw assigned_lane integer (the API names no lanes). `is_you`
// marks the perspective account, set server-side from the account_id param.
export interface MatchDetailPlayer {
  player_slot: number
  account_id: number
  hero_id: number
  hero_name: string
  image_url: string | null
  team: number
  lane: number | null
  kills: number | null
  deaths: number | null
  assists: number | null
  net_worth: number | null
  last_hits: number | null
  denies: number | null
  won: boolean | null
  is_you: boolean
}

export interface MatchPurchase {
  item_id: number
  item_name: string
  item_image_url: string | null
  purchase_time_s: number | null
  sold_time_s: number // 0 = never sold
}

// One death in the whole-match feed. Killer fields are null for a non-player
// (environment) kill. The *_is_you flags are relative to the perspective account.
export interface DeathEvent {
  game_time_s: number | null
  victim_slot: number
  victim_hero_id: number
  victim_hero_name: string
  victim_image_url: string | null
  victim_team: number
  victim_is_you: boolean
  killer_slot: number | null
  killer_hero_id: number | null
  killer_hero_name: string | null
  killer_image_url: string | null
  killer_team: number | null
  killer_is_you: boolean
}

export interface MatchDetail {
  match_id: number
  start_time: string
  duration_s: number
  game_mode: string
  winning_team: number
  average_badge_team0: number | null
  average_badge_team1: number | null
  account_id: number | null // the resolved "you" perspective
  players: MatchDetailPlayer[]
  purchases: MatchPurchase[]
  deaths: DeathEvent[]
}
