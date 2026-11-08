import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../test/renderWithProviders'
import { Accounts } from './Accounts'
import { Eras } from './Eras'

// A failed write should say what the server said. These pin the regression where
// a 403 "Writes are disabled" (DEADLOCK_OPEN_WRITES unset) surfaced as "session
// looks stale" on Accounts and "is the backend running?" on Eras.

const WRITES_DISABLED =
  'Writes are disabled: set DEADLOCK_BASE_URL to enable Steam login, ' +
  'or DEADLOCK_OPEN_WRITES=1 for a private single-user deploy.'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

// mockFetch only answers 200s, so stub fetch directly: reads succeed with the
// given bodies, and every non-GET answers with `writeResponse()`.
function stubFetch(reads: Record<string, unknown>, writeResponse: () => Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method && init.method !== 'GET') return writeResponse()
      const path = String(input).split('?')[0]
      return path in reads ? json(reads[path]) : json({}, 404)
    }),
  )
}

const SYNC_STATUS = {
  queue: {},
  queue_depth: 0,
  fetched: 0,
  unavailable: 0,
  last_discovery_at: null,
  last_maintenance_at: null,
  pending_era_candidates: 0,
}

function submitAccount() {
  fireEvent.change(screen.getByPlaceholderText('891231519'), {
    target: { value: '891231519' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Queue account' }))
}

describe('write error messages', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('Accounts shows the server detail for a 403 instead of guessing "stale session"', async () => {
    stubFetch({ '/api/accounts': [], '/api/sync-status': SYNC_STATUS }, async () =>
      json({ detail: WRITES_DISABLED }, 403),
    )
    renderWithProviders(<Accounts />)
    submitAccount()
    expect(await screen.findByText(WRITES_DISABLED)).toBeInTheDocument()
    expect(screen.queryByText(/session looks stale/)).not.toBeInTheDocument()
  })

  it('Accounts keeps the stale-session wording when a 403 carries no detail', async () => {
    stubFetch({ '/api/accounts': [], '/api/sync-status': SYNC_STATUS }, async () =>
      new Response('', { status: 403 }),
    )
    renderWithProviders(<Accounts />)
    submitAccount()
    expect(await screen.findByText(/session looks stale/)).toBeInTheDocument()
  })

  it('Accounts falls back to "is the backend running?" on a network failure', async () => {
    stubFetch({ '/api/accounts': [], '/api/sync-status': SYNC_STATUS }, async () => {
      throw new TypeError('Failed to fetch')
    })
    renderWithProviders(<Accounts />)
    submitAccount()
    expect(await screen.findByText(/Is the backend running\?/)).toBeInTheDocument()
  })

  it('Eras shows the server detail when dismissing a candidate is refused', async () => {
    const candidate = {
      candidate_id: 7,
      post_url: 'https://example.com/patch',
      post_title: 'Gameplay Update',
      posted_at: '2026-10-01T00:00:00Z',
      change_lines: 120,
      score: 9.5,
      status: 'pending',
    }
    stubFetch({ '/api/eras': { eras: [], pending_candidates: [candidate] } }, async () =>
      json({ detail: WRITES_DISABLED }, 403),
    )
    renderWithProviders(<Eras />)
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }))
    expect(await screen.findByText(WRITES_DISABLED)).toBeInTheDocument()
    expect(screen.queryByText(/Is the backend running\?/)).not.toBeInTheDocument()
  })
})
