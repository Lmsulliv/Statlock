import { act, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AccountProgress } from '../api/types'
import { AccountProgressCard } from './AccountProgressCard'
import { mockFetch, renderWithProviders } from '../test/renderWithProviders'

// Build a progress payload; callers vary analyzed / pending across polls.
function progress(p: Partial<AccountProgress>): AccountProgress {
  return {
    account_id: 1,
    known: 50,
    analyzed: 0,
    prioritized_pending: 0,
    backfill_pending: 0,
    deferred: 0,
    unavailable: 0,
    ...p,
  }
}

describe('AccountProgressCard', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('advances the bar as successive poll responses arrive, then removes itself', async () => {
    // The hook polls /api/accounts/{id}/progress every 5s; we drive that by
    // holding a mutable "current response" and re-triggering the fetch with
    // invalidateQueries (a poll tick landing), which is deterministic where the
    // real 5s interval under fake timers is not.
    let current = progress({ analyzed: 3, prioritized_pending: 47 })
    mockFetch({ '/api/accounts/1/progress': () => current })

    const { client } = renderWithProviders(<AccountProgressCard accountId={1} />)

    // First response: 3 of 50, bar at aria-valuenow 3.
    expect(await screen.findByText(/Analyzed 3 of 50 recent matches/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '3')

    // A later poll lands with more analyzed: 12 of 50.
    current = progress({ analyzed: 12, prioritized_pending: 38 })
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['account-progress', 1] })
    })
    expect(await screen.findByText(/Analyzed 12 of 50 recent matches/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '12')

    // Caught up: the card removes itself entirely.
    current = progress({ analyzed: 50, prioritized_pending: 0 })
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['account-progress', 1] })
    })
    await waitFor(() =>
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument(),
    )
    expect(screen.queryByText(/recent matches/)).not.toBeInTheDocument()
  })

  it('shows the background-backfill phase once the recent window is done', async () => {
    mockFetch({
      '/api/accounts/1/progress': progress({
        analyzed: 50,
        known: 220,
        prioritized_pending: 0,
        backfill_pending: 170,
      }),
    })
    renderWithProviders(<AccountProgressCard accountId={1} />)
    expect(
      await screen.findByText(/Full history loading in the background/),
    ).toBeInTheDocument()
    expect(screen.getByText(/50 of 220 matches analyzed/)).toBeInTheDocument()
  })

  it('hints at the ingest tool when matches are stuck, and not otherwise', async () => {
    // Some matches parked as deferred/unavailable: show the quiet submission
    // hint linking to the community ingest tool.
    mockFetch({
      '/api/accounts/1/progress': progress({
        analyzed: 40,
        known: 50,
        backfill_pending: 5,
        deferred: 3,
        unavailable: 2,
      }),
    })
    const { unmount } = renderWithProviders(<AccountProgressCard accountId={1} />)
    const link = await screen.findByRole('link', {
      name: /data submission to deadlock-api/,
    })
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/deadlock-api/deadlock-api-ingest',
    )
    unmount()

    // Nothing stuck: no hint, even while the backfill is still running.
    vi.unstubAllGlobals()
    mockFetch({
      '/api/accounts/1/progress': progress({
        analyzed: 40,
        known: 50,
        backfill_pending: 10,
      }),
    })
    renderWithProviders(<AccountProgressCard accountId={1} />)
    expect(
      await screen.findByText(/Full history loading in the background/),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/waiting on/),
    ).not.toBeInTheDocument()
  })
})
