import { createContext, useContext, type ReactNode } from 'react'

// When the app is viewed through the public /player/:accountId route it runs
// "pinned": the account is fixed by the URL path (not the scope query param), and
// all management/write UI is hidden. A null pin is the normal owner-facing app.
export interface PlayerPin {
  accountId: number // Number(param); NaN for a non-numeric path -> friendly page
  basePath: string // e.g. "/player/900000", prefixed onto every in-app link
}

const PlayerContext = createContext<PlayerPin | null>(null)

export function PlayerProvider({
  pin,
  children,
}: {
  pin: PlayerPin | null
  children: ReactNode
}) {
  return <PlayerContext.Provider value={pin}>{children}</PlayerContext.Provider>
}

// The active pin, or null in the normal (owner-facing) app.
export function usePlayerPin(): PlayerPin | null {
  return useContext(PlayerContext)
}

// The link prefix for the current mode: "" normally, "/player/<id>" when pinned.
export function useBasePath(): string {
  return usePlayerPin()?.basePath ?? ''
}

// Prefix an absolute in-app path with the base. Root ("/") collapses to the bare
// base so the Overview tab is "/player/<id>", not "/player/<id>/".
export function withBasePath(base: string, to: string): string {
  if (!base) return to
  return to === '/' ? base : base + to
}

// Convenience: a function that prefixes any absolute path with the active base.
export function useHref(): (to: string) => string {
  const base = useBasePath()
  return (to: string) => withBasePath(base, to)
}
