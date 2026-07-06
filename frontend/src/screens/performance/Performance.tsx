import { NavLink, useLocation } from 'react-router-dom'
import { usePlayedHeroes } from '../../api/queries'
import { ModeNotice } from '../../components/ModeNotice'
import { useHref } from '../../player/PlayerContext'
import { GAME_MODE_NORMAL, useScope } from '../../scope/useScope'
import { LaningSection } from './LaningSection'
import { OverallSection } from './OverallSection'
import { TrendsSection } from './TrendsSection'

export type PerformanceSegment = 'overall' | 'laning' | 'trends'

// The segment lives in the URL path (/performance, /performance/laning,
// /performance/trends), NOT in a query param: the scope bar rebuilds the query
// string from scope keys only, so a ?tab= param would be erased by the first
// scope change. NavLinks carry `search` so the scope follows segment switches.
const SEGMENTS: { to: string; label: string; end: boolean }[] = [
  { to: '/performance', label: 'Overall', end: true },
  { to: '/performance/laning', label: 'Laning', end: false },
  { to: '/performance/trends', label: 'Over time', end: false },
]

// One Performance tab with three views: Overall (continuous metrics), Laning
// (early game), and Over time (the old Trends screen). Gating is per-segment:
// Overall and Laning compare against Normal-population baselines so they stay
// Normal-only; Over time renders under Street Brawl too, exactly as the old
// standalone screens behaved.
export function Performance({ segment }: { segment: PerformanceSegment }) {
  const { scope } = useScope()
  const { search } = useLocation()
  const href = useHref()
  const brawl = scope.gameMode !== GAME_MODE_NORMAL
  // The "My hero" selection narrows Overall/Laning to that hero's block; name
  // it in the title so the active scope stays legible (cached — same query key
  // as the ScopeBar's picker).
  const heroes = usePlayedHeroes(scope)
  const heroLabel =
    scope.heroId === null
      ? 'All heroes'
      : (heroes.data?.find((h) => h.hero_id === scope.heroId)?.name ??
        `Hero ${scope.heroId}`)
  const showHeroLabel = segment !== 'trends' && !brawl

  return (
    <section>
      <h1 className="screen-title">
        Performance{showHeroLabel && <span className="muted"> · {heroLabel}</span>}
      </h1>
      <div className="toggle segment-nav" role="group" aria-label="Performance view">
        {SEGMENTS.map((s) => (
          <NavLink
            key={s.to}
            to={{ pathname: href(s.to), search }}
            end={s.end}
            className={({ isActive }) => (isActive ? 'toggle-opt active' : 'toggle-opt')}
          >
            {s.label}
          </NavLink>
        ))}
      </div>
      {segment === 'overall' && (brawl ? <ModeNotice /> : <OverallSection scope={scope} />)}
      {segment === 'laning' && (brawl ? <ModeNotice /> : <LaningSection scope={scope} />)}
      {segment === 'trends' && <TrendsSection scope={scope} />}
    </section>
  )
}
