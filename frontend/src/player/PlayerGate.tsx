import type { ReactNode } from 'react'
import { usePlayerProfile } from '../api/queries'
import { DemoProfileLink } from '../components/DemoProfileLink'
import { EmptyState } from '../components/EmptyState'
import { usePlayerPin } from './PlayerContext'
import { ProfileHeader } from './ProfileHeader'

// Wraps the screens on a public /player/:accountId view. It resolves the pinned
// account's public profile and gates on it: an account we hold no data for (or a
// non-numeric path) gets a friendly "not tracked here" page instead of empty
// screens; a real account gets its profile header above the normal screens.
export function PlayerGate({ children }: { children: ReactNode }) {
  const pin = usePlayerPin()
  const accountId = pin?.accountId ?? NaN
  const profile = usePlayerProfile(accountId)
  const validId = Number.isFinite(accountId)

  // A non-numeric path or a fetch error resolves to "not tracked". While a valid
  // id is still loading, hold the screens back so we never flash empty tabs.
  if (!validId || profile.isError) return <NotTracked />
  if (profile.isPending) return <div className="state-msg">Loading profile…</div>
  if (!profile.data.has_data) return <NotTracked />

  return (
    <>
      <ProfileHeader profile={profile.data} />
      {children}
    </>
  )
}

function NotTracked() {
  return (
    <EmptyState title="This account isn’t tracked here.">
      <p>
        Once this player’s matches are ingested, this page shows their rank over
        time, hero records, laning and performance trends, and death patterns —
        all with honest confidence intervals.
      </p>
      <DemoProfileLink />
    </EmptyState>
  )
}
