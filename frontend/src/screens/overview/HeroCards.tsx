import { Link, useLocation } from 'react-router-dom'
import { useHeroRecords } from '../../api/queries'
import { HeroIcon } from '../../components/HeroIcon'
import { IntervalBar } from '../../components/IntervalBar'
import { SampleSize } from '../../components/SampleSize'
import { useHref } from '../../player/PlayerContext'
import { searchWith } from '../../scope/search'
import { useScope } from '../../scope/useScope'

// How many hero cards fit the Overview column without crowding it.
const TOP_N = 4

// The most-played heroes as compact cards, each linking into that hero's full
// report on the Heroes tab. Sorting by games and slicing the top few is
// presentation ordering of server-computed rows; the interval and verdict come
// straight from /api/hero-records (baselined against your own overall rate).
//
// Deliberately no ProvisionalBadge here, unlike HeroList on the Heroes tab: the
// Overview is a glance surface, and the chip's 48 characters of nowrap text do
// not fit beside a hero name in a ~180px card. The sample size below the name
// already carries the "don't over-read this" signal. Heroes keeps the chip.
export function HeroCards() {
  const { scope } = useScope()
  const { search } = useLocation()
  const href = useHref()
  const records = useHeroRecords(scope)
  if (!records.data) return null
  const top = [...records.data.heroes].sort((a, b) => b.games - a.games).slice(0, TOP_N)
  if (top.length === 0) {
    return <p className="muted">No hero records in this scope yet.</p>
  }

  return (
    <div className="hero-cards">
      {top.map((h) => (
        <Link
          key={h.hero_id}
          className="hero-card"
          to={{ pathname: href('/heroes'), search: searchWith(search, 'hero_id', String(h.hero_id)) }}
        >
          <span className="hero-card-head">
            <span className="enemy-cell">
              <HeroIcon name={h.hero_name} url={h.hero_image_url} />
              {/* The name needs its own element to take text-overflow — a bare
                  text node can't be ellipsised — and the title keeps the full
                  name reachable once it is. */}
              <span className="hero-card-name" title={h.hero_name}>
                {h.hero_name}
              </span>
            </span>
          </span>
          <SampleSize games={h.games} wins={h.wins} />
          <IntervalBar
            winrate={h.winrate}
            ciLow={h.ci_low}
            ciHigh={h.ci_high}
            globalRate={h.global_rate}
            verdict={h.verdict}
          />
        </Link>
      ))}
    </div>
  )
}
