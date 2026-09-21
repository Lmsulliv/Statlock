import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HeroCards } from './HeroCards'
import { heroRecord } from '../../test/fixtures'
import { mockFetch, renderWithProviders } from '../../test/renderWithProviders'

// A record that IS still deepening, so a provisional chip would render if the
// card asked for one. The longest hero names double as the truncation cases.
const provisional = (id: number, name: string, games: number) => ({
  ...heroRecord(id, name, games),
  provisional: true,
})

const records = {
  provisional: true,
  heroes: [
    provisional(5, 'Mo & Krill', 40),
    provisional(6, 'Grey Talon', 30),
    provisional(7, 'Lady Geist', 20),
    provisional(8, 'McGinnis', 10),
  ],
}

describe('HeroCards', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('omits the provisional chip: Overview is a glance surface', async () => {
    mockFetch({ '/api/hero-records': records })
    renderWithProviders(<HeroCards />)

    expect(await screen.findByText('Mo & Krill')).toBeInTheDocument()
    // The chip still renders on the Heroes tab; it is suppressed here only.
    expect(screen.queryByText(/early results, deepening/)).not.toBeInTheDocument()
  })

  it('gives each hero name a title so a truncated name stays readable', async () => {
    mockFetch({ '/api/hero-records': records })
    renderWithProviders(<HeroCards />)

    for (const name of ['Mo & Krill', 'Grey Talon', 'Lady Geist', 'McGinnis']) {
      const el = await screen.findByText(name)
      expect(el).toHaveClass('hero-card-name')
      expect(el).toHaveAttribute('title', name)
    }
  })

  it('labels the interval as a 95% CI and keeps the range unbreakable', async () => {
    mockFetch({ '/api/hero-records': records })
    renderWithProviders(<HeroCards />)

    // The confidence level is markup the Overview reveals via CSS; the range is
    // one text node so it cannot break at the en dash.
    const ci = (await screen.findAllByText(/CI/))[0]
    expect(ci).toHaveClass('interval-ci')
    expect(ci.textContent).toBe('95% CI 30%–70%')
  })
})
