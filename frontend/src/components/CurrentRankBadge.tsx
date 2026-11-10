import type { CurrentRank, RankedSeason } from '../api/types'

// The account's current rank as a small badge (art + tier name/subtier), shown
// beside the Overview chart title and in the public profile header. This is the
// latest point of the rank series, server-resolved in its own season's rank
// system, so it matches what the game showed at the time. When that point is
// from a season that has ended (no games yet this season), the badge says which
// season it's from rather than passing an old rank off as current.
export function CurrentRankBadge({ rank, seasons }: { rank: CurrentRank; seasons: RankedSeason[] }) {
  const label = rank.name
    ? `${rank.name}${rank.subtier ? ` ${rank.subtier}` : ''}`
    : `Badge ${rank.badge}`
  const season = seasons.find((s) => s.slug === rank.season)
  return (
    <span className="current-rank" title={`Current rank · badge ${rank.badge}`}>
      <img className="current-rank-art" src={rank.badge_url} alt="" />
      <span
        className="current-rank-label"
        style={rank.color ? { color: rank.color } : undefined}
      >
        {label}
      </span>
      {season && !season.is_current && (
        <span className="current-rank-season">{` · ${season.label}`}</span>
      )}
    </span>
  )
}
