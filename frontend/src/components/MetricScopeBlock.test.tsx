import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MetricField, PerformanceRow } from '../api/types'
import { MetricScopeBlock } from './MetricScopeBlock'

const metric = (key: string, label: string, games: number): MetricField => ({
  key,
  label,
  higher_is_better: true,
  games,
  mean: 100,
  ci_low: 90,
  ci_high: 110,
  baseline_mean: 95,
  baseline_games: 500,
  delta: 5,
  verdict: 'not_enough_data',
})

const row = (metrics: MetricField[], games = 20): PerformanceRow => ({
  scope: 'hero',
  hero_id: 5,
  hero_name: 'Haze',
  hero_image_url: null,
  games,
  metrics,
})

const LANING = [
  metric('net_worth', 'Net worth @ lane end', 20),
  metric('last_hits', 'Last hits @ lane end', 20),
  metric('denies', 'Denies @ lane end', 20),
]

describe('MetricScopeBlock', () => {
  it('hoists a qualifier every metric shares into the column header', () => {
    render(<MetricScopeBlock row={row(LANING)} qualifier="@ lane end" />)

    // Stated once, in the header.
    const header = screen.getByRole('columnheader', { name: /Metric/ })
    expect(header).toHaveTextContent('@ lane end')
    // ...and not repeated on any row.
    expect(screen.getByRole('cell', { name: 'Net worth' })).toBeInTheDocument()
    expect(screen.queryByRole('cell', { name: /@ lane end/ })).toBeNull()
  })

  it('leaves every label alone when one metric does not share the qualifier', () => {
    // A metric measured somewhere else must keep saying so, rather than being
    // covered by a heading that would be false for it.
    const mixed = [...LANING, metric('late_kills', 'Kills after 20:00', 20)]
    render(<MetricScopeBlock row={row(mixed)} qualifier="@ lane end" />)

    const header = screen.getByRole('columnheader', { name: /Metric/ })
    expect(header).not.toHaveTextContent('@ lane end')
    expect(screen.getByRole('cell', { name: 'Net worth @ lane end' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Kills after 20:00' })).toBeInTheDocument()
  })

  it('hides the sample column but still flags a metric with a different n', () => {
    // The column is only hidden where every metric's sample equals the block's
    // game count; a sparse metric must not silently inherit the bigger number.
    const sparse = [metric('net_worth', 'Net worth', 20), metric('healing', 'Healing', 4)]
    render(<MetricScopeBlock row={row(sparse)} showSample={false} />)

    expect(screen.queryByRole('columnheader', { name: 'Sample' })).toBeNull()
    expect(screen.getByRole('cell', { name: 'Net worth' })).toBeInTheDocument()
    const flagged = screen.getByRole('cell', { name: /Healing/ })
    expect(within(flagged).getByText('4 games')).toBeInTheDocument()
  })

  it('keeps the sample column by default', () => {
    render(<MetricScopeBlock row={row(LANING)} />)
    expect(screen.getByRole('columnheader', { name: 'Sample' })).toBeInTheDocument()
  })
})
