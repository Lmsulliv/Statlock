import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  baseRoutes,
  emptyDeaths,
  emptyRecurring,
  emptyTilt,
} from '../../test/fixtures'
import { mockFetch, renderWithProviders } from '../../test/renderWithProviders'
import { Insights } from './Insights'

const routes = {
  ...baseRoutes,
  '/api/death-patterns': emptyDeaths,
  '/api/tilt': emptyTilt,
  '/api/recurring-players': emptyRecurring,
}

describe('Insights', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders all three sections on one screen', async () => {
    mockFetch(routes)
    renderWithProviders(<Insights />, { route: '/insights' })

    // Query by heading role: the titles now also appear as SectionNav links.
    expect(screen.getByRole('heading', { name: 'Deaths' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Do you tilt?' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recurring players' })).toBeInTheDocument()
    // The section nav lists each section as a jump link (rail + dropdown copy).
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(within(nav).getAllByRole('link', { name: 'Deaths' }).length).toBeGreaterThan(0)
    expect(within(nav).getAllByRole('link', { name: 'Do you tilt?' }).length).toBeGreaterThan(0)
    expect(
      within(nav).getAllByRole('link', { name: 'Recurring players' }).length,
    ).toBeGreaterThan(0)
    // Each section owns its explanatory empty state.
    expect(await screen.findByText(/No deaths to show yet/)).toBeInTheDocument()
    expect(await screen.findByText(/No sessions to analyze yet/)).toBeInTheDocument()
  })

  it('under Street Brawl mutes Deaths and Players but keeps the session analysis', async () => {
    const fn = mockFetch(routes)
    renderWithProviders(<Insights />, { route: '/insights/deaths?game_mode=4' })

    // Per-section gate: the Normal-only sections carry a compact note...
    expect(screen.getAllByText(/Normal matches only —/)).toHaveLength(2)
    // ...while the tilt section still fetches and renders.
    expect(await screen.findByText(/No sessions to analyze yet/)).toBeInTheDocument()
    const deathCalls = fn.mock.calls.filter((c) =>
      String(c[0]).includes('/api/death-patterns'),
    )
    expect(deathCalls).toHaveLength(0)
  })
})
