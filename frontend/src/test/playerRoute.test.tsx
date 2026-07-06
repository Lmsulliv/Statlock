import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { baseRoutes, playerProfile, sync } from './fixtures'
import { mockFetch, renderWithProviders } from './renderWithProviders'

const ACCOUNT = 900000

// The public profile view scoped to ACCOUNT: normal screens, read-only, with a
// profile header and zero management UI.
const overview = {
  account_id: ACCOUNT,
  mmr_series: [],
  current_rank: null,
  last_matches: [],
  sync,
  provisional: false,
}

// An authenticated owner — the strongest test that the pin, not login state,
// governs the read-only chrome.
const ownerMe = {
  auth_enabled: true,
  authenticated: true,
  user_id: 1,
  account_id: 1,
  display_name: 'me',
  demo_account_id: null,
}

const playerRoutes = {
  ...baseRoutes,
  '/api/auth/me': ownerMe,
  '/api/players/': playerProfile(ACCOUNT),
  '/api/overview': overview,
}

describe('public /player/:accountId route', () => {
  // Each test starts with the pinned-account routes; a few re-stub with a variant
  // (mockFetch replaces the global fetch stub, so the last call wins).
  beforeEach(() => mockFetch(playerRoutes))
  afterEach(() => vi.unstubAllGlobals())

  it('renders the five tabs and a profile header for a tracked account', async () => {
    const { container } = renderWithProviders(<App />, { route: `/player/${ACCOUNT}` })

    const nav = [...container.querySelectorAll('.app-nav a')].map((a) => a.textContent)
    expect(nav).toEqual(['Overview', 'Heroes', 'Performance', 'Insights', 'Improvement'])
    expect(await screen.findByText('Public profile')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: String(ACCOUNT) })).toBeInTheDocument()
  })

  it('hides all management and write UI even for an authenticated owner', async () => {
    renderWithProviders(<App />, { route: `/player/${ACCOUNT}` })

    await screen.findByText('Public profile')
    // No gear menu, no login/logout, no account switcher.
    expect(screen.queryByLabelText('Management')).not.toBeInTheDocument()
    expect(screen.queryByText('Log out')).not.toBeInTheDocument()
    expect(screen.queryByText('Account')).not.toBeInTheDocument()
  })

  it('pins every data request to the profile account', async () => {
    const fn = mockFetch(playerRoutes)
    renderWithProviders(<App />, { route: `/player/${ACCOUNT}` })

    await screen.findByText('Public profile')
    const overviewCall = fn.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes('/api/overview'))
    expect(overviewCall).toContain(`account_id=${ACCOUNT}`)
  })

  it('prefixes the nav links with the profile base path', async () => {
    const { container } = renderWithProviders(<App />, { route: `/player/${ACCOUNT}` })

    const hrefs = [...container.querySelectorAll('.app-nav a')].map((a) => a.getAttribute('href'))
    expect(hrefs[0]).toBe(`/player/${ACCOUNT}`) // Overview collapses to the base
    expect(hrefs[1]).toBe(`/player/${ACCOUNT}/heroes`)
  })

  it('does not register the management routes under a pin', async () => {
    renderWithProviders(<App />, { route: `/player/${ACCOUNT}/accounts` })

    // Profile header renders, but the Accounts screen is unreachable by URL.
    await screen.findByText('Public profile')
    await waitFor(() =>
      expect(screen.queryByText('Tracked accounts')).not.toBeInTheDocument(),
    )
  })

  it('shows a friendly page for an account with no data', async () => {
    mockFetch({ ...playerRoutes, '/api/players/': playerProfile(ACCOUNT, { has_data: false }) })
    renderWithProviders(<App />, { route: `/player/${ACCOUNT}` })

    expect(await screen.findByText(/isn.t tracked here/)).toBeInTheDocument()
  })

  it('shows the friendly page for a non-numeric account path', async () => {
    mockFetch(playerRoutes)
    renderWithProviders(<App />, { route: '/player/not-a-number' })

    expect(await screen.findByText(/isn.t tracked here/)).toBeInTheDocument()
  })

  it('keeps a legacy sub-path redirect under the pin', async () => {
    mockFetch({ ...playerRoutes, '/api/matchups': [] })
    renderWithProviders(<App />, { route: `/player/${ACCOUNT}/matchups` })

    // Redirects to /player/900000/heroes/all: the all-heroes grid, still pinned.
    expect(await screen.findByText('Matchups across all heroes')).toBeInTheDocument()
    expect(screen.getByText('Public profile')).toBeInTheDocument()
  })
})
