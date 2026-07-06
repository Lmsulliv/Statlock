import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  baseRoutes,
  emptyPerformance,
  sync,
} from '../../test/fixtures'
import { mockFetch, renderWithProviders } from '../../test/renderWithProviders'
import { Heroes } from './Heroes'

describe('Heroes', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('opens on the most played hero when no hero is selected', async () => {
    const fn = mockFetch({
      ...baseRoutes,
      '/api/matchups': [],
      '/api/items': [],
      '/api/laning': [],
      '/api/performance': emptyPerformance,
    })
    renderWithProviders(<Heroes />, { route: '/heroes' })

    // Haze (30 games) outranks Abrams (5): her detail opens, no empty picker.
    expect(await screen.findByText('Matchups on Haze')).toBeInTheDocument()
    const call = fn.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/api/items'))
    expect(call).toContain('hero_id=5')
  })

  it('renders the aggregate matchup grid for the all-heroes view', async () => {
    mockFetch({ ...baseRoutes, '/api/matchups': [] })
    renderWithProviders(<Heroes allHeroes />, { route: '/heroes/all' })

    expect(await screen.findByText('Matchups across all heroes')).toBeInTheDocument()
    expect(screen.queryByText(/Matchups on Haze/)).not.toBeInTheDocument()
  })

  it('shows the Normal-only note under Street Brawl and fetches nothing', () => {
    const fn = mockFetch({})
    renderWithProviders(<Heroes />, { route: '/heroes?game_mode=4' })

    expect(
      screen.getByText(/Deep analysis is available for Normal matches only/),
    ).toBeInTheDocument()
    const recordCalls = fn.mock.calls.filter((c) =>
      String(c[0]).includes('/api/hero-records'),
    )
    expect(recordCalls).toHaveLength(0)
  })

  it('sells the empty state with a live analyzed count', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/hero-records': { provisional: false, heroes: [] },
      '/api/accounts': [{ account_id: 1, display_name: 'me', is_self: true }],
      '/api/accounts/1/progress': {
        account_id: 1, known: 50, analyzed: 12,
        prioritized_pending: 38, backfill_pending: 0, deferred: 0,
      },
      '/api/sync-status': { ...sync, fetched: 0 },
    })
    renderWithProviders(<Heroes />, { route: '/heroes' })

    expect(await screen.findByText(/12 of 50 done/)).toBeInTheDocument()
    expect(screen.getByText(/played heroes/i)).toBeInTheDocument()
  })
})
