import { screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import {
  baseRoutes,
  emptyDeaths,
  emptyPerformance,
  emptyRecurring,
  emptyTilt,
  emptyTrends,
  sync,
} from './fixtures'
import { mockFetch, renderWithProviders } from './renderWithProviders'

// The full app under MemoryRouter: the five-tab nav, the legacy-route
// redirects (scope query string preserved), the header gear gating, and the
// sync indicator.
describe('App routes', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders exactly five nav tabs and no management links', async () => {
    // Auth mode, logged out: the one state where canManage is false regardless
    // of the local VITE_OWNER fallback (which is true in dev .env.local).
    mockFetch({
      ...baseRoutes,
      '/api/auth/me': {
        auth_enabled: true,
        authenticated: false,
        user_id: null,
        account_id: null,
        display_name: null,
        demo_account_id: null,
      },
      '/api/overview': {
        account_id: null,
        mmr_series: [],
        current_rank: null,
        last_matches: [],
        sync,
        provisional: false,
      },
    })
    const { container } = renderWithProviders(<App />, { route: '/' })

    const nav = [...container.querySelectorAll('.app-nav a')].map((a) => a.textContent)
    expect(nav).toEqual(['Overview', 'Heroes', 'Performance', 'Insights', 'Improvement'])
    // Until /api/auth/me resolves the gear may flash via the VITE_OWNER
    // fallback; once it does, a logged-out viewer has no management surface.
    await waitFor(() =>
      expect(screen.queryByLabelText('Management')).not.toBeInTheDocument(),
    )
  })

  it('shows the gear and reaches /accounts only when the viewer can manage', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/auth/me': {
        auth_enabled: true,
        authenticated: true,
        user_id: 1,
        account_id: 1,
        display_name: 'me',
        demo_account_id: null,
      },
    })
    renderWithProviders(<App />, { route: '/accounts' })

    expect(await screen.findByLabelText('Management')).toBeInTheDocument()
    expect(await screen.findByText('Tracked accounts')).toBeInTheDocument()
  })

  it('does not register /accounts for a logged-out viewer in auth mode', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/auth/me': {
        auth_enabled: true,
        authenticated: false,
        user_id: null,
        account_id: null,
        display_name: null,
        demo_account_id: null,
      },
    })
    renderWithProviders(<App />, { route: '/accounts' })

    // /api/auth/me resolves to "can't manage": the gear disappears and the
    // route is unregistered, so nothing renders in main.
    await waitFor(() =>
      expect(screen.queryByText('Tracked accounts')).not.toBeInTheDocument(),
    )
  })

  it('shows the sync indicator with the queue depth', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/sync-status': { ...sync, queue_depth: 3 },
      '/api/overview': {
        account_id: null,
        mmr_series: [],
        current_rank: null,
        last_matches: [],
        sync,
        provisional: false,
      },
    })
    renderWithProviders(<App />, { route: '/' })

    expect(await screen.findByText('3 queued')).toBeInTheDocument()
  })

  it('redirects /matchups to the all-heroes grid, scope intact', async () => {
    const fn = mockFetch({ ...baseRoutes, '/api/matchups': [] })
    renderWithProviders(<App />, { route: '/matchups?hero_id=5&badge_min=30' })

    expect(await screen.findByText('Matchups across all heroes')).toBeInTheDocument()
    const call = fn.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/api/matchups'))
    expect(call).toContain('hero_id=5')
    expect(call).toContain('badge_min=30')
  })

  it('redirects /items?hero_id=5 into that hero’s detail', async () => {
    const fn = mockFetch({
      ...baseRoutes,
      '/api/matchups': [],
      '/api/items': [],
      '/api/laning': [],
      '/api/performance': emptyPerformance,
    })
    renderWithProviders(<App />, { route: '/items?hero_id=5' })

    expect(await screen.findByText('Matchups on Haze')).toBeInTheDocument()
    const call = fn.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/api/items'))
    expect(call).toContain('hero_id=5')
  })

  it('redirects /laning and /trends into the Performance segments', async () => {
    mockFetch({ ...baseRoutes, '/api/laning': [] })
    const first = renderWithProviders(<App />, { route: '/laning' })
    expect(await screen.findByText(/your early game laid bare/)).toBeInTheDocument()
    first.unmount()

    mockFetch({ ...baseRoutes, '/api/trends': emptyTrends })
    renderWithProviders(<App />, { route: '/trends?game_mode=4' })
    expect(await screen.findByText(/Are you getting better/)).toBeInTheDocument()
  })

  it('redirects the deaths/tilt/recurring routes into Insights sections', async () => {
    const routes = {
      ...baseRoutes,
      '/api/death-patterns': emptyDeaths,
      '/api/tilt': emptyTilt,
      '/api/recurring-players': emptyRecurring,
    }
    for (const legacy of ['/deaths', '/tilt', '/recurring-players']) {
      mockFetch(routes)
      const view = renderWithProviders(<App />, { route: legacy })
      expect(await screen.findByText('Do you tilt?')).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Insights' })).toBeInTheDocument()
      view.unmount()
      vi.unstubAllGlobals()
    }
  })
})
