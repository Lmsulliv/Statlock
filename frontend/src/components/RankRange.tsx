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
  const setTiers = (lo: number, hi: number) => onChange(tierToBadgeMin(lo), tierToBadgeMax(hi))

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
            <label className="rankrange-slider">
              <span className="scope-label">Lowest tier</span>
              <input
                type="range"
                min={0}
                max={MAX_TIER}
                step={1}
                value={tierMin}
                onChange={(e) => setTiers(Math.min(Number(e.target.value), tierMax), tierMax)}
              />
            </label>
            <label className="rankrange-slider">
              <span className="scope-label">Highest tier</span>
              <input
                type="range"
                min={0}
                max={MAX_TIER}
                step={1}
                value={tierMax}
                onChange={(e) => setTiers(tierMin, Math.max(Number(e.target.value), tierMin))}
              />
            </label>
            <button type="button" className="chip" onClick={() => setTiers(0, MAX_TIER)}>
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
