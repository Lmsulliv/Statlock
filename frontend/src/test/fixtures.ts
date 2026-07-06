// Shared response fixtures for the route/screen tests. Shapes mirror
// api/service.py exactly (see src/api/types.ts).

export const sync = {
  queue: {},
  queue_depth: 0,
  fetched: 10,
  unavailable: 0,
  last_discovery_at: null,
  last_maintenance_at: null,
  pending_era_candidates: 0,
}

export const anonMe = {
  auth_enabled: false,
  authenticated: false,
  user_id: null,
  account_id: null,
  display_name: null,
  demo_account_id: null,
}

// A minimal public profile response for the /player/:id route tests.
export function playerProfile(accountId: number, overrides = {}) {
  return {
    account_id: accountId,
    display_name: String(accountId),
    current_rank: null,
    has_data: true,
    ...overrides,
  }
}

export function heroRecord(id: number, name: string, games: number) {
  return {
    hero_id: id,
    hero_name: name,
    hero_image_url: null,
    games,
    wins: Math.floor(games / 2),
    winrate: 0.5,
    ci_low: 0.3,
    ci_high: 0.7,
    global_matches: 100,
    global_rate: 0.5,
    adjusted_rate: 0.5,
    delta: 0,
    raw_delta: 0,
    verdict: 'not_enough_data',
  }
}

// Two heroes so the most-played default (Haze, 30 games) is observable.
export const heroRecords = {
  provisional: false,
  heroes: [heroRecord(5, 'Haze', 30), heroRecord(7, 'Abrams', 5)],
}

// Everything App + ScopeBar + header touch on any route; individual tests
// spread their target screen's endpoints on top.
export const baseRoutes = {
  '/api/auth/me': anonMe,
  '/api/accounts': [],
  '/api/eras': { eras: [], pending_candidates: [] },
  '/api/heroes': [],
  '/api/ranks': [],
  '/api/sync-status': sync,
  '/api/hero-records': heroRecords,
}

export const emptyTilt = {
  by_session_index: [],
  by_loss_streak: [],
  overall: { games: 0, wins: 0, winrate: null },
  sessions: 0,
  session_gap_hours: 3,
  provisional: false,
}

export const emptyDeaths = {
  by_enemy_hero: [],
  by_damage_source: [],
  timeline: [],
  total_deaths: 0,
  games: 0,
}

export const emptyRecurring = {
  teammates: [],
  opponents: [],
  overall: { games: 0, wins: 0, winrate: null },
  min_co_occurrence: 3,
  hero_id: null,
}

export const emptyTrends = {
  mode: 'rolling',
  granularity: 'week',
  window_games: 20,
  provisional: false,
  metrics: [],
}

export const emptyPerformance = { provisional: false, rows: [] }
