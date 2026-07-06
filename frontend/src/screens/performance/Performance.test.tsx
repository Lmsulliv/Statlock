import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { baseRoutes, emptyTrends } from '../../test/fixtures'
import { mockFetch, renderWithProviders } from '../../test/renderWithProviders'
import { Performance } from './Performance'

describe('Performance', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('gates Overall behind Normal mode but keeps Over time Brawl-friendly', async () => {
    mockFetch({ ...baseRoutes, '/api/trends': emptyTrends })
    const overall = renderWithProviders(<Performance segment="overall" />, {
      route: '/performance?game_mode=4',
    })
    expect(
      screen.getByText(/Deep analysis is available for Normal matches only/),
    ).toBeInTheDocument()
    overall.unmount()

    renderWithProviders(<Performance segment="trends" />, {
      route: '/performance/trends?game_mode=4',
    })
    expect(await screen.findByText(/Are you getting better/)).toBeInTheDocument()
    expect(
      screen.queryByText(/Deep analysis is available for Normal matches only/),
    ).not.toBeInTheDocument()
  })

  it('segment links carry the scope query string', () => {
    mockFetch({ ...baseRoutes, '/api/trends': emptyTrends })
    renderWithProviders(<Performance segment="trends" />, {
      route: '/performance/trends?game_mode=4',
    })

    const link = screen.getByRole('link', { name: 'Laning' })
    expect(link.getAttribute('href')).toBe('/performance/laning?game_mode=4')
  })
})
