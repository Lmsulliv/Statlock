import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useLocation } from 'react-router-dom'
import { Overview } from './Overview'
import { anonMe, heroRecords, sync } from '../test/fixtures'
import { mockFetch, renderWithProviders } from '../test/renderWithProviders'

const overviewData = (extra: Record<string, unknown> = {}) => ({
  account_id: 1,
  mmr_series: [],
  current_rank: null,
  seasons: [],
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

// ── Rank over time: season-aware views ──────────────────────────────────────

// A rank point as api/service.rank_timeline returns it: resolved in its own
// season's rank system, so badge 55 is Ritualist (legacy) or Mystic (seasonal).
const rankPoint = (
  match_id: number, badge: number, start_time: string,
  season: string, name: string,
) => {
  const rank_system = season === 'legacy' ? 'legacy' : 'seasonal'
  const tier = Math.floor(badge / 10)
  return {
    match_id, badge, start_time, season, rank_system, tier,
    subtier: badge % 10, name, color: '#DDA326',
    badge_url: `https://cdn.test/${rank_system}/${tier}.png`,
  }
}

const legacy55 = rankPoint(1, 55, '2026-06-01T12:00:00+00:00', 'legacy', 'Ritualist')
const legacy62 = rankPoint(2, 62, '2026-07-01T12:00:00+00:00', 'legacy', 'Emissary')
const s1Placement = rankPoint(3, 34, '2026-08-05T12:00:00+00:00', 'beta-season-1', 'Acolyte')
const s1Mystic55 = rankPoint(4, 55, '2026-09-10T12:00:00+00:00', 'beta-season-1', 'Mystic')
const s1Last = rankPoint(5, 36, '2026-10-31T12:00:00+00:00', 'beta-season-1', 'Acolyte')

const rankedSeasons = [
  { slug: 'beta-season-2', label: 'Beta Season 2', rank_system: 'seasonal',
    started_at: '2026-11-05T20:00:00+00:00', is_current: true, peak: null },
  { slug: 'beta-season-1', label: 'Beta Season 1', rank_system: 'seasonal',
    started_at: '2026-07-30T17:00:00+00:00', is_current: false, peak: s1Mystic55 },
  { slug: 'legacy', label: 'Legacy ranks', rank_system: 'legacy',
    started_at: '2024-10-10T00:00:00+00:00', is_current: false, peak: null },
]

const rankedOverview = () =>
  overviewData({
    mmr_series: [legacy55, legacy62, s1Placement, s1Mystic55, s1Last],
    current_rank: s1Last,
    seasons: rankedSeasons,
  })

// Prints the router's query string so a test can watch the URL change.
function SearchProbe() {
  return <output data-testid="search">{useLocation().search}</output>
}

async function renderRankCard(route: string) {
  mockFetch({
    '/api/overview': rankedOverview(),
    '/api/accounts/1/progress': noProgress,
  })
  const result = renderWithProviders(
    <>
      <Overview />
      <SearchProbe />
    </>,
    { route },
  )
  const select = await screen.findByRole('combobox', { name: 'Rank view' })
  return { ...result, select: select as HTMLSelectElement }
}

// jsdom has no SVG geometry, so give the chart an identity screen transform:
// the cursor's clientX then IS the x coordinate in the SVG's viewBox.
function stubSvgGeometry() {
  const proto = SVGSVGElement.prototype as unknown as Record<string, unknown>
  proto.getScreenCTM = () => ({ inverse: () => ({}) })
  proto.createSVGPoint = () => {
    const pt = { x: 0, y: 0, matrixTransform: () => ({ x: pt.x, y: pt.y }) }
    return pt
  }
  return () => {
    delete proto.getScreenCTM
    delete proto.createSVGPoint
  }
}

describe('Rank over time', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('defaults to "Since ranked launch" and lists seasons newest first', async () => {
    const { select } = await renderRankCard('/')

    expect(select.value).toBe('since-launch')
    expect([...select.options].map((o) => o.textContent)).toEqual([
      'All time',
      'Since ranked launch',
      'Beta Season 2 (current)',
      'Beta Season 1',
    ])
    // Only the three seasonal points are in view.
    expect(screen.getByText('3 ranked matches')).toBeInTheDocument()
    // The current rank is from a season that has ended, so it says which.
    expect(screen.getByTitle(/Current rank/)).toHaveTextContent('Acolyte 6 · Beta Season 1')
  })

  it('round-trips rank_view through the URL and keeps other params', async () => {
    const { select } = await renderRankCard('/?account_id=1&rank_view=beta-season-1')
    expect(select.value).toBe('beta-season-1')

    fireEvent.change(select, { target: { value: 'all' } })
    expect(select.value).toBe('all')
    expect(screen.getByTestId('search')).toHaveTextContent('?account_id=1&rank_view=all')
    expect(screen.getByText('5 ranked matches')).toBeInTheDocument()

    // The default is never written to the URL.
    fireEvent.change(select, { target: { value: 'since-launch' } })
    expect(screen.getByTestId('search').textContent).toBe('?account_id=1')
  })

  it.each(['bogus', 'legacy'])('falls back to the default for rank_view=%s', async (bad) => {
    const { select } = await renderRankCard(`/?rank_view=${bad}`)
    expect(select.value).toBe('since-launch')
    expect(screen.getByText('3 ranked matches')).toBeInTheDocument()
  })

  it('names each point in its own rank system in the tooltip', async () => {
    const restore = stubSvgGeometry()
    try {
      const { container } = await renderRankCard('/?rank_view=all')
      const svg = screen.getByRole('img', { name: 'Rank badge over time' })
      // The fallback viewBox is 680 wide with 36px padding, so five points sit
      // at x = 36, 188, 340, 492, 644.
      fireEvent.mouseMove(svg, { clientX: 36, clientY: 50 })
      let tip = container.querySelector('.mmr-tooltip')!
      expect(tip).toHaveTextContent('Ritualist 5')
      expect(tip).toHaveTextContent('Legacy ranks')
      expect(tip.querySelector('image')).toHaveAttribute('href', legacy55.badge_url)

      fireEvent.mouseMove(svg, { clientX: 492, clientY: 50 })
      tip = container.querySelector('.mmr-tooltip')!
      expect(tip).toHaveTextContent('Mystic 5')
      expect(tip).toHaveTextContent('Beta Season 1')
    } finally {
      restore()
    }
  })

  it('never joins legacy and seasonal points with one line', async () => {
    const { container } = await renderRankCard('/?rank_view=all')

    const lines = [...container.querySelectorAll('.mmr-line')]
    expect(lines.map((l) => l.getAttribute('points')!.split(' ').length)).toEqual([2, 3])
    // A labeled divider marks where Beta Season 1 starts.
    const dividers = [...container.querySelectorAll('.mmr-season-divider')]
    expect(dividers.map((d) => d.textContent)).toEqual(['Beta Season 1'])
    // Low/High read each extreme point's own name.
    expect(screen.getByText('Low: Acolyte 4')).toBeInTheDocument()
    expect(screen.getByText('High: Emissary 2')).toBeInTheDocument()
  })

  it("shows a single season's peak", async () => {
    await renderRankCard('/?rank_view=beta-season-1')
    const peak = screen.getByText(/Peak in Beta Season 1/)
    expect(peak).toHaveTextContent('Mystic 5')
  })

  it('says so when the selected season has no ranked games yet', async () => {
    const { container } = await renderRankCard('/?rank_view=beta-season-2')
    expect(screen.getByText('No ranked games in Beta Season 2 yet.')).toBeInTheDocument()
    expect(container.querySelector('.mmr-chart')).toBeNull()
  })
})
