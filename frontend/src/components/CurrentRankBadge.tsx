import type { CurrentRank } from '../api/types'

// The account's current rank as a small badge (art + tier name/subtier), shown
// beside the Overview chart title and in the public profile header. This is the
// latest point of the rank series, server-resolved to a tier name + accent color,
// so it matches what the game shows in-client.
export function CurrentRankBadge({ rank }: { rank: CurrentRank }) {
  const label = rank.name
    ? `${rank.name}${rank.subtier ? ` ${rank.subtier}` : ''}`
    : `Badge ${rank.badge}`
  return (
    <span className="current-rank" title={`Current rank · badge ${rank.badge}`}>
      <img className="current-rank-art" src={rank.badge_url} alt="" />
      <span
        className="current-rank-label"
        style={rank.color ? { color: rank.color } : undefined}
      >
        {label}
      </span>
    </span>
  )
}
