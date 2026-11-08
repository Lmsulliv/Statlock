import type { Scope } from '../scope/useScope'

// `detail` is the server's own explanation (FastAPI's {"detail": "..."} body),
// when it sent a human-readable one. It names the real cause -- e.g. "Writes are
// disabled: set DEADLOCK_OPEN_WRITES=1..." -- where a status code alone is
// ambiguous (403 means both "stale CSRF token" and "writes not enabled").
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// The string `detail` from an error response, or null. Validation errors (422)
// carry a list there, and an unhandled 500 is plain text, not JSON; both yield
// null so callers fall back to their own wording.
async function readDetail(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      return typeof detail === 'string' && detail.trim() !== '' ? detail : null
    }
  } catch {
    // Not JSON: nothing the server wanted to tell us.
  }
  return null
}

// The server's explanation for a failed request, if it gave one. A network
// failure (backend down) throws a TypeError rather than an ApiError, so it lands
// here as null too.
export function serverDetail(error: unknown): string | null {
  return error instanceof ApiError ? error.detail : null
}

// Translate the frontend Scope into the API's query params. Badge range, min
// games and game mode are sent explicitly so each request is self-describing;
// era_ids is omitted when empty, which the server defines as all-time
// (presentation-spec rule 6: "all time" is an explicit choice, not a blank).
export function scopeParams(scope: Scope): Record<string, string> {
  const params: Record<string, string> = {
    badge_min: String(scope.badgeMin),
    badge_max: String(scope.badgeMax),
    min_games: String(scope.minGames),
    game_mode: scope.gameMode,
  }
  if (scope.accountId !== null) params.account_id = String(scope.accountId)
  if (scope.eraIds.length > 0) params.era_ids = scope.eraIds.join(',')
  if (scope.heroId !== null) params.hero_id = String(scope.heroId)
  if (scope.inLane) params.in_lane = 'true'
  return params
}

export async function fetchJson<T>(
  path: string,
  params?: Record<string, string>,
): Promise<T> {
  const qs = params ? new URLSearchParams(params).toString() : ''
  const url = qs ? `${path}?${qs}` : path
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) {
    throw new ApiError(
      res.status,
      `Request to ${url} failed (${res.status})`,
      await readDetail(res),
    )
  }
  return (await res.json()) as T
}

// The double-submit CSRF token: the backend sets a readable `csrf` cookie at
// login, and every write must echo it in the X-CSRF-Token header (the API
// compares the two). Absent in local/dev mode (auth off), where the API doesn't
// check it. Reading our own cookie is safe -- only the httpOnly session cookie is
// hidden from JS.
function csrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)csrf=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : null
}

// The app's write paths. `body` is optional: the era confirm/dismiss POSTs carry
// the id in the URL and send none, while the account importer/namer send a JSON
// body. Content-Type is only set when there's a body to describe.
async function sendJson<T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  const csrf = csrfToken()
  const res = await fetch(path, {
    method,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    throw new ApiError(
      res.status,
      `Request to ${path} failed (${res.status})`,
      await readDetail(res),
    )
  }
  // 204 No Content (e.g. logout) has no body to parse.
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const postJson = <T>(path: string, body?: unknown) =>
  sendJson<T>('POST', path, body)

export const patchJson = <T>(path: string, body: unknown) =>
  sendJson<T>('PATCH', path, body)

export const putJson = <T>(path: string, body: unknown) =>
  sendJson<T>('PUT', path, body)

// DELETE carries no body; the namer's clear path reverts a label by id alone.
export const deleteJson = <T>(path: string) => sendJson<T>('DELETE', path)
