import { Link, useLocation } from 'react-router-dom'
import type { HeroRecord } from '../../api/types'
import { HeroIcon } from '../../components/HeroIcon'
import { IntervalBar } from '../../components/IntervalBar'
import { SampleSize } from '../../components/SampleSize'
import { useHref } from '../../player/PlayerContext'
import { searchWith, searchWithout } from '../../scope/search'

// The played-heroes sidebar: an "All heroes" entry (the aggregate matchup grid)
// then one row per hero record, most-played first. Selection is plain links —
// a hero row writes hero_id into the query string (the same param the scope
// bar's "My hero" select uses, so the two can never disagree), and "All heroes"
// switches to the /heroes/all path with hero_id dropped.
//
// Deliberately NO text chips here. The verdict and provisional badges both set
// `white-space: nowrap`, and the provisional one is 48 characters — in a 280px
// column it can't fit and overflowed its row. Both now live in the detail panel
// for the selected hero, where there is room to state them in full. What's left
// is the record and the interval bar, which carry the same signal graphically.
export function HeroList({
  heroes,
  activeHeroId,
  allActive,
}: {
  heroes: HeroRecord[]
  activeHeroId: number | null
  allActive: boolean
}) {
  const { search } = useLocation()
  const href = useHref()
  // Most-played first: pure presentation ordering of server-computed rows.
  const ordered = [...heroes].sort((a, b) => b.games - a.games)
  return (
    <nav className="hero-list" aria-label="Played heroes">
      <Link
        className={allActive ? 'hero-list-item active' : 'hero-list-item'}
        to={{ pathname: href('/heroes/all'), search: searchWithout(search, 'hero_id') }}
      >
        <span className="hero-list-head">
          <span className="enemy-cell">All heroes</span>
        </span>
        <span className="muted hero-list-sub">Aggregate matchup grid</span>
      </Link>
      {ordered.map((h) => {
        const active = !allActive && h.hero_id === activeHeroId
        return (
          <Link
            key={h.hero_id}
            className={active ? 'hero-list-item active' : 'hero-list-item'}
            to={{ pathname: href('/heroes'), search: searchWith(search, 'hero_id', String(h.hero_id)) }}
            aria-current={active ? 'page' : undefined}
          >
            <span className="hero-list-head">
              <span className="enemy-cell">
                <HeroIcon name={h.hero_name} url={h.hero_image_url} />
                {/* The name needs its own element to take text-overflow — a
                    bare text node can't be ellipsised — and the title keeps the
                    full name reachable once it is. Same pattern as HeroCards. */}
                <span className="hero-list-name" title={h.hero_name}>
                  {h.hero_name}
                </span>
              </span>
            </span>
            <span className="hero-list-sub">
              <SampleSize games={h.games} wins={h.wins} />
            </span>
            <IntervalBar
              winrate={h.winrate}
              ciLow={h.ci_low}
              ciHigh={h.ci_high}
              globalRate={h.global_rate}
              verdict={h.verdict}
            />
          </Link>
        )
      })}
    </nav>
  )
}
