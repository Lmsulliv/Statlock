import { useState } from 'react'
import type { Rank } from '../api/types'
import { FULL_BADGE_MAX, FULL_BADGE_MIN } from '../scope/useScope'

interface Props {
  ranks: Rank[]
  badgeMin: number
  badgeMax: number
  onChange: (badgeMin: number, badgeMax: number) => void
}

// Badges are tier*10 + subtier; the baselines are bracketed by tier, so the
// selector snaps to whole tiers (0..11). Tier 11 (Eternus) tops out at 116.
const MAX_TIER = 11
const badgeToTier = (badge: number) => Math.max(0, Math.min(MAX_TIER, Math.floor(badge / 10)))
const tierToBadgeMin = (tier: number) => tier * 10
const tierToBadgeMax = (tier: number) => (tier >= MAX_TIER ? FULL_BADGE_MAX : tier * 10 + 9)

export function RankRange({ ranks, badgeMin, badgeMax, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const tierMin = badgeToTier(badgeMin)
  const tierMax = badgeToTier(badgeMax)
  const isFull = badgeMin <= FULL_BADGE_MIN && badgeMax >= FULL_BADGE_MAX

  const byTier = (t: number) => ranks.find((r) => r.tier === t)
  // The two handles can't cross: each clamps against the other.
  const setLo = (lo: number) =>
    onChange(tierToBadgeMin(Math.min(lo, tierMax)), tierToBadgeMax(tierMax))
  const setHi = (hi: number) =>
    onChange(tierToBadgeMin(tierMin), tierToBadgeMax(Math.max(hi, tierMin)))

  // Both range inputs sit on ONE track. When the thumbs meet they'd overlap and
  // only the top one stays grabbable, so we lift whichever handle still has room
  // to move: at a shared point in the lower half the max thumb (can go up) wins,
  // in the upper half the min thumb (can go down) wins.
  const collapsed = tierMin === tierMax
  const loOnTop = collapsed && tierMin > MAX_TIER / 2
  const pct = (tier: number) => (tier / MAX_TIER) * 100

  return (
    <div className="rankrange">
      <button type="button" className="rankrange-button" onClick={() => setOpen((o) => !o)}>
        {isFull ? (
          <span>All ranks</span>
        ) : (
          <span className="rankrange-current">
            <RankBadge rank={byTier(tierMin)} />
            <span className="muted">–</span>
            <RankBadge rank={byTier(tierMax)} />
          </span>
        )}
        <span className="rankrange-caret">▾</span>
      </button>

      {open && (
        <>
          <div className="rankrange-backdrop" onClick={() => setOpen(false)} />
          <div className="rankrange-popover">
            <div className="rankrange-readout">
              <RankBadge rank={byTier(tierMin)} large />
              <span className="muted">→</span>
              <RankBadge rank={byTier(tierMax)} large />
            </div>

            <div className="rankrange-dual">
              <div className="rankrange-rail" />
              <div
                className="rankrange-fill"
                style={{ left: `${pct(tierMin)}%`, right: `${100 - pct(tierMax)}%` }}
              />
              <input
                type="range"
                className="rankrange-input"
                min={0}
                max={MAX_TIER}
                step={1}
                value={tierMin}
                style={{ zIndex: loOnTop ? 5 : 3 }}
                aria-label="Lowest rank"
                onChange={(e) => setLo(Number(e.target.value))}
              />
              <input
                type="range"
                className="rankrange-input"
                min={0}
                max={MAX_TIER}
                step={1}
                value={tierMax}
                style={{ zIndex: loOnTop ? 3 : 4 }}
                aria-label="Highest rank"
                onChange={(e) => setHi(Number(e.target.value))}
              />
            </div>

            <button
              type="button"
              className="chip"
              onClick={() => onChange(FULL_BADGE_MIN, FULL_BADGE_MAX)}
            >
              Reset to all ranks
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function RankBadge({ rank, large }: { rank: Rank | undefined; large?: boolean }) {
  if (!rank) return <span className="muted">?</span>
  return (
    <span className={large ? 'rank-badge rank-badge-lg' : 'rank-badge'} title={rank.name}>
      <img src={rank.badge_url} alt="" loading="lazy" />
      <span>{rank.name}</span>
    </span>
  )
}
