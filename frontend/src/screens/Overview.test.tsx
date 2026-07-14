import { screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Overview } from './Overview'
import { anonMe, heroRecords, sync } from '../test/fixtures'
import { mockFetch, renderWithProviders } from '../test/renderWithProviders'

const overviewData = (extra: Record<string, unknown> = {}) => ({
  account_id: 1,
  mmr_series: [],
  current_rank: null,
  last_matches: [],
  sync,
  provisional: false,
  ...extra,
})

const noProgress = {
  account_id: 1, known: 0, analyzed: 0,
  prioritized_pending: 0, backfill_pending: 0, deferred: 0, unavailable: 0,
}

const focusEntry = {
  kind: 'matchup', subject: 'Haze', games: 20, wins: 5,
  enemy_hero_id: 5, enemy_hero_name: 'Haze', enemy_hero_image_url: null,
  winrate: 0.25, ci_low: 0.09, ci_high: 0.53, global_matches: 400,
  global_rate: 0.51, adjusted_rate: 0.3, delta: -0.21, raw_delta: -0.26,
  verdict: 'clear_weakness',
}

describe('Overview', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders its cards normally under Street Brawl (no Normal-only note)', async () => {
    mockFetch({
      '/api/overview': overviewData(),
      '/api/ranks': [],
      '/api/accounts/1/progress': noProgress,
    })
    renderWithProviders(<Overview />, { route: '/?game_mode=4' })

    // The real Overview content renders (Brawl summaries flow through), not the
    // deep-analysis Normal-only note.
    expect(await screen.findByText('Rank over time')).toBeInTheDocument()
    expect(screen.getByText(/Last 10 matches/)).toBeInTheDocument()
    expect(
      screen.queryByText(/Deep analysis is available for Normal matches only/),
    ).not.toBeInTheDocument()
  })

  it('shows focus areas and hero cards from the live endpoints', async () => {
    mockFetch({
      '/api/overview': overviewData(),
      '/api/ranks': [],
      '/api/accounts/1/progress': noProgress,
      '/api/improvement': {
        confirmed_weaknesses: [focusEntry],
        confirmed_strengths: [],
        watch_list: [],
        win_conditions: [],
      },
      '/api/hero-records': heroRecords,
    })
    renderWithProviders(<Overview />, { route: '/' })

    expect(await screen.findByText('Focus areas')).toBeInTheDocument()
    expect(screen.getByText('Against Haze')).toBeInTheDocument()
    expect(await screen.findByText('Your heroes')).toBeInTheDocument()
    // Most-played first from hero-records.
    expect(await screen.findByText('Haze')).toBeInTheDocument()
    // The sync card is demoted to the header indicator, off this screen.
    expect(screen.queryByText('Sync status')).not.toBeInTheDocument()
  })

  it('offers the demo profile on the empty landing when one is configured', async () => {
    mockFetch({
      '/api/overview': overviewData({ account_id: null }),
      '/api/ranks': [],
      '/api/auth/me': { ...anonMe, demo_account_id: 555 },
    })
    renderWithProviders(<Overview />, { route: '/' })

    const cta = await screen.findByText('View a live demo profile →')
    expect(cta).toBeInTheDocument()
    expect(cta.closest('a')).toHaveAttribute('href', '/player/555')
  })

  it('hides the demo button when no demo account is configured', async () => {
    mockFetch({
      '/api/overview': overviewData({ account_id: null }),
      '/api/ranks': [],
      '/api/auth/me': anonMe, // demo_account_id: null
    })
    renderWithProviders(<Overview />, { route: '/' })

    expect(await screen.findByText('No tracked account yet.')).toBeInTheDocument()
    expect(screen.queryByText('View a live demo profile →')).not.toBeInTheDocument()
  })

  it('shows the era-candidate banner only to viewers who can manage', async () => {
    const routes = {
      '/api/overview': overviewData({ sync: { ...sync, pending_era_candidates: 2 } }),
      '/api/ranks': [],
      '/api/accounts/1/progress': noProgress,
      '/api/hero-records': { provisional: false, heroes: [] },
      '/api/improvement': {
        confirmed_weaknesses: [], confirmed_strengths: [],
        watch_list: [], win_conditions: [],
      },
    }

    mockFetch({
      ...routes,
      '/api/auth/me': {
        auth_enabled: true, authenticated: true,
        user_id: 1, account_id: 1, display_name: 'me',
      },
    })
    const managed = renderWithProviders(<Overview />, { route: '/' })
    expect(await managed.findByText(/possible new eras detected/)).toBeInTheDocument()
    managed.unmount()
    vi.unstubAllGlobals()

    mockFetch({
      ...routes,
      '/api/auth/me': {
        auth_enabled: true, authenticated: false,
        user_id: null, account_id: null, display_name: null,
      },
    })
    renderWithProviders(<Overview />, { route: '/' })
    expect(await screen.findByText('Rank over time')).toBeInTheDocument()
    // The banner may flash via the local VITE_OWNER fallback until /api/auth/me
    // resolves to "logged out"; then it must go.
    await waitFor(() =>
      expect(screen.queryByText(/possible new eras detected/)).not.toBeInTheDocument(),
    )
  })
})
