import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  baseRoutes,
  emptyPerformance,
  sync,
} from '../../test/fixtures'
import { mockFetch, renderWithProviders } from '../../test/renderWithProviders'
import { Heroes } from './Heroes'

const ability = (id: number, name: string, type: string) => ({
  ability_id: id,
  ability_name: name,
  ability_type: type,
  image_url: null,
})

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

  it('renders the skill-order section with counts and no verdict badge', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/matchups': [],
      '/api/items': [],
      '/api/laning': [],
      '/api/performance': emptyPerformance,
      '/api/hero-skill-order': {
        hero_id: 5,
        games: 12,
        opening: { sequence: [ability(1, 'Fixation', 'signature'), ability(2, 'Sift', 'signature')], games: 7, considered: 12 },
        first_maxed: { ability: ability(1, 'Fixation', 'signature'), games: 8, considered: 12 },
        split: {
          wins: {
            games: 6,
            opening: { sequence: [ability(1, 'Fixation', 'signature')], games: 5, considered: 6 },
            first_maxed: { ability: ability(1, 'Fixation', 'signature'), games: 5, considered: 6 },
          },
          losses: {
            games: 6,
            opening: { sequence: [ability(2, 'Sift', 'signature')], games: 4, considered: 6 },
            first_maxed: { ability: ability(2, 'Sift', 'signature'), games: 3, considered: 6 },
          },
        },
      },
    })
    renderWithProviders(<Heroes />, { route: '/heroes' })

    // Wait for the async skill-order data (the heading is static and renders first).
    const allGames = await screen.findByText('All games')
    const section = allGames.closest('section') as HTMLElement
    // Overall + wins + losses blocks all render.
    expect(within(section).getByText('All games')).toBeInTheDocument()
    expect(within(section).getByText('In wins')).toBeInTheDocument()
    expect(within(section).getByText('In losses')).toBeInTheDocument()
    expect(within(section).getAllByText('Fixation').length).toBeGreaterThan(0)
    expect(within(section).getByText('7 of 12 games')).toBeInTheDocument()
    // Descriptive: never a verdict badge.
    expect(within(section).queryByText(/Strength|Weakness|Neutral|Need more games/)).toBeNull()
  })

  it('hides the wins/losses split below the sample floor', async () => {
    mockFetch({
      ...baseRoutes,
      '/api/matchups': [],
      '/api/items': [],
      '/api/laning': [],
      '/api/performance': emptyPerformance,
      '/api/hero-skill-order': {
        hero_id: 5,
        games: 3,
        opening: { sequence: [ability(1, 'Fixation', 'signature')], games: 2, considered: 3 },
        first_maxed: { ability: ability(1, 'Fixation', 'signature'), games: 2, considered: 3 },
        split: null,
      },
    })
    renderWithProviders(<Heroes />, { route: '/heroes' })

    const note = await screen.findByText(/split appears once you have enough games/)
    const section = note.closest('section') as HTMLElement
    expect(within(section).queryByText('In wins')).toBeNull()
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
