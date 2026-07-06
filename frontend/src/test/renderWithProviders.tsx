import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'

// Render a screen/component under the same providers the real app mounts: a
// fresh TanStack QueryClient (retries off so a mocked error surfaces at once,
// and no refetch-on-focus to keep tests deterministic) and a MemoryRouter whose
// initial entry seeds the URL — which is where Scope lives, so `route` controls
// account_id / game_mode / era just as the ScopeBar would.
export function renderWithProviders(
  ui: ReactElement,
  { route = '/' }: { route?: string } = {},
): RenderResult & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...result, client }
}

// A minimal fetch stub keyed by URL path. `routes` maps a path prefix (e.g.
// "/api/matchups") to either a static JSON value or a function returning one,
// so a test can advance a value across polls. Every request funnels through
// client.ts's fetchJson, so stubbing global.fetch covers the whole API surface.
// Returns the vi mock so tests can assert which endpoints were (not) called.
export function mockFetch(
  routes: Record<string, unknown | (() => unknown)>,
): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    // A polling query (refetchInterval) can fire once more as the tree unmounts
    // during teardown, after React Query has cleared its request context, calling
    // fetch with no argument. Answer it harmlessly rather than throwing on
    // input.toString(); a real request always carries a URL.
    if (input === undefined) return new Response('{}', { status: 404 })
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.split('?')[0]
    // Longest key first so a specific path ("/api/accounts/1/progress") wins over
    // a shorter prefix of it ("/api/accounts").
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => path === k || path.startsWith(k))
    if (key === undefined) {
      return new Response('{}', { status: 404 })
    }
    const entry = routes[key]
    const body = typeof entry === 'function' ? (entry as () => unknown)() : entry
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}
