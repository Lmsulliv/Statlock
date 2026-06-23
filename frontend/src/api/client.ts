import type { Scope } from '../scope/useScope'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
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
    throw new ApiError(res.status, `Request to ${url} failed (${res.status})`)
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
    throw new ApiError(res.status, `Request to ${path} failed (${res.status})`)
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
