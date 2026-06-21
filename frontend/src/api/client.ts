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

// The app's write paths. `body` is optional: the era confirm/dismiss POSTs carry
// the id in the URL and send none, while the account importer/namer send a JSON
// body. Content-Type is only set when there's a body to describe.
async function sendJson<T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      Accept: 'application/json',
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    throw new ApiError(res.status, `Request to ${path} failed (${res.status})`)
  }
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
