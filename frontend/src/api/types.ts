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
  // Raw kill counts vs this enemy hero across the games faced (not a rate, no
  // confidence interval). Totals over the same scope as `games`.
  kills_by_them_on_you: number
  kills_by_you_on_them: number
}

// ── Performance (api/service.performance) ────────────────────────────────────

// One continuous metric (net worth/min, kills, deaths, ...) for one scope row.
// `games` is the non-null sample size the stat rests on (sparse metrics like
// healing can be 0 even when the scope has matches). Means are on the metric's
// own scale, not 0..1; ci bounds are null when the sample is too thin for an
// interval. `verdict` is already direction-aware server-side: for a metric where
// lower is better (deaths), beating the baseline reads as a strength.
export interface MetricField {
  key: string
  label: string
  higher_is_better: boolean
  games: number
  mean: number | null
  ci_low: number | null // 95% t-interval lower bound
  ci_high: number | null
  baseline_mean: number | null // population mean, null when no baseline
  baseline_games: number // population sample size for this metric's hero
  delta: number | null // mean - baseline_mean, in metric units
  verdict: Verdict
}

// One scope row: the "overall" row (hero_id null) or one hero. The metrics list
// is in the canonical server order; `games` is the match count for the scope.
export interface PerformanceRow {
  scope: 'overall' | 'hero'
  hero_id: number | null
  hero_name: string | null
  hero_image_url: string | null
  games: number
  metrics: MetricField[]
}

// Early-game (laning) report rows are structurally identical to performance rows
// (a metrics list of MetricField), just a different metric set: net worth, last
// hits, and denies at the lane-end mark. Same renderer, same honesty machinery.
export type LaningRow = PerformanceRow

// ── Death patterns (api/service.death_patterns) ──────────────────────────────

// One enemy hero in the death ranking: raw deaths you suffered to it plus the
// games you faced it, across the scope. No verdict/interval — there is no stored
// per-matchup death baseline, so (like the match-detail kill trades) these are
// honest raw counts, never a fabricated comparison.
export interface DeathByEnemyHero {
  enemy_hero_id: number
  enemy_hero_name: string
  enemy_hero_image_url: string | null
  deaths: number
  games_faced: number
}

// One game-minute bin of the death timeline. `deaths` is the raw total in the
// bin; the rest are the continuous-metric block (_metric_fields): `mean` is your
// deaths per game in the bin, compared to the live population `baseline_mean`.
// `verdict` is direction-aware server-side — fewer deaths than the field reads as
// a strength — and is not_enough_data when there is no population to compare to.
export interface DeathTimelineBin {
  minute: number
  label: string
  deaths: number
  games: number
  mean: number | null
  ci_low: number | null
  ci_high: number | null
  baseline_mean: number | null
  baseline_games: number
  delta: number | null
  verdict: Verdict
}

// One enemy hero in the damage ranking: how much GROSS damage it dealt you,
// averaged per game you faced it. From damage_matrix (pre-mitigation), so it's a
// raw relative ranking with no verdict — there is no honest baseline because the
// gross total doesn't reconcile with net damage taken (see api-findings).
export interface DamageByEnemyHero {
  enemy_hero_id: number
  enemy_hero_name: string
  enemy_hero_image_url: string | null
  total_damage: number
  games_faced: number
  avg_per_game: number
}

export interface DeathPatternsResponse {
  by_enemy_hero: DeathByEnemyHero[]
  by_damage_source: DamageByEnemyHero[]
  timeline: DeathTimelineBin[]
  total_deaths: number // timed deaths placed on the timeline (untimed excluded)
  games: number
}

// ── Trends (api/service.trends) ──────────────────────────────────────────────

// One point of a metric's time series: a calendar bucket (week/month) or a
// rolling-window position. `value` is a win rate (0..1) for win_rate, else the
// metric's mean on its own scale; `n` is that point's sample size. `enough_data`
// is the honesty floor (n >= VERDICT_FLOOR): a thin window/bucket reads
// not-enough-data, and the frontend greys it and breaks the trend line.
export interface TrendPoint {
  label: string
  n: number
  value: number | null
  ci_low: number | null
  ci_high: number | null
  enough_data: boolean
}

// One metric's whole series. `baseline` is the single reference line drawn under
// the sparkline: the account's overall win rate for win_rate, the live
// population mean for the continuous metrics (null when there is no baseline).
export interface TrendMetric {
  key: string
  label: string
  higher_is_better: boolean
  baseline: number | null
  points: TrendPoint[]
}

export interface TrendsResponse {
  mode: 'rolling' | 'calendar'
  granularity: 'week' | 'month'
  window_games: number
  metrics: TrendMetric[]
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
// OWN win rate over the same match set (overall, or on the filtered hero), the
// "you vs your usual self" baseline. `display_name` is resolved server-side
// (manual label > Steam persona > bare account id); the UI keeps a defensive
// fallback to the id in case it is ever absent.
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

// A tracked account, for the account switcher in the ScopeBar and the Accounts
// screen (where it's added via the importer and named via inline rename).
// display_name is resolved server-side (manual label > Steam persona > id), so
// the switcher shows the same name as the rest of the app.
export interface TrackedAccount {
  account_id: number
  display_name: string | null
  is_self: boolean
}

// The viewer's identity (GET /api/auth/me). `auth_enabled` reflects whether Steam
// login is configured at all (DEADLOCK_BASE_URL): when false the app is in local
// single-user mode and shows no login controls. `authenticated` is true only when
// a real session is present. When logged in, account_id is the user's self account
// and display_name its resolved name.
export interface Me {
  auth_enabled: boolean
  authenticated: boolean
  user_id: number | null
  account_id: number | null
  display_name: string | null
}

// The body returned by the rename writes (PUT/DELETE /api/accounts/{id}/name):
// the account and its now-effective resolved name (the label just set, or the
// Steam persona / id it reverted to after a clear).
export interface AccountName {
  account_id: number
  display_name: string
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

// The account's current (most-recent) rank, resolved to display name + color.
export interface CurrentRank {
  badge: number // tier*10 + subtier, 0..116
  tier: number
  subtier: number
  name: string | null
  color: string | null
  badge_url: string
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
  current_rank: CurrentRank | null
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
  display_name: string // resolved server-side; "0" for anonymized players
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

// One enemy player's kill trade vs the perspective account in a single match.
// Raw counts in both directions, attributed by slot off kill_events, so an
// anonymized opponent (account_id 0, indistinguishable by id) still counts and
// is labeled by its hero. Enemy team only; teammates would always be 0/0.
export interface KillTrade {
  player_slot: number
  account_id: number // 0 for anonymized opponents
  display_name: string // resolved server-side; the UI shows it only when id != 0
  hero_id: number
  hero_name: string
  image_url: string | null
  team: number
  kills_by_them_on_you: number
  kills_by_you_on_them: number
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
  trades: KillTrade[]
}
