import type { PlayerProfile } from '../api/types'
import { CurrentRankBadge } from '../components/CurrentRankBadge'

// The small header atop a public /player/:accountId view: the account's
// label-free display name and current rank badge. It stands in for the account
// switcher (hidden on public profiles) so a visitor always knows whose stats
// they're reading.
export function ProfileHeader({ profile }: { profile: PlayerProfile }) {
  return (
    <div className="profile-header">
      <div className="profile-identity">
        <span className="profile-eyebrow">Public profile</span>
        <h1 className="profile-name">{profile.display_name}</h1>
      </div>
      {profile.current_rank && <CurrentRankBadge rank={profile.current_rank} />}
    </div>
  )
}
