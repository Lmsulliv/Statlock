import { Link, useLocation } from 'react-router-dom'
import { useImprovement } from '../../api/queries'
import { HeroIcon } from '../../components/HeroIcon'
import { VerdictBadge } from '../../components/VerdictBadge'
import { useHref } from '../../player/PlayerContext'
import { useScope } from '../../scope/useScope'
import { iconUrl, lead } from '../Improvement'

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// The Overview's 30-second hook: the top three calls from the improvement
// digest, linking into the full Improvement screen. Confirmed weaknesses come
// first, then the watch list fills up to three — both lists arrive
// server-ranked (largest gap first), so this is a slice, not a re-ranking.
// Renders nothing while loading, on error, or when the digest is empty: it's a
// supplemental strip, and Improvement itself owns the explanatory empty state.
export function FocusAreas() {
  const { scope } = useScope()
  const { search } = useLocation()
  const href = useHref()
  const improvement = useImprovement(scope)
  if (!improvement.data) return null
  const entries = [
    ...improvement.data.confirmed_weaknesses,
    ...improvement.data.watch_list,
  ].slice(0, 3)
  if (entries.length === 0) return null

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Focus areas</h2>
        <Link className="card-link" to={{ pathname: href('/improvement'), search }}>
          Full digest →
        </Link>
      </div>
      <div className="focus-strip">
        {entries.map((e) => (
          <Link
            key={`${e.kind}-${e.subject}-${e.hero_id ?? ''}`}
            className="focus-card"
            to={{ pathname: href('/improvement'), search }}
          >
            <span className="focus-card-head">
              <HeroIcon name={e.subject} url={iconUrl(e)} />
              <strong>{lead(e)}</strong>
            </span>
            <span className="focus-card-body">
              You win {pct(e.winrate)} vs a global {pct(e.global_rate)} over{' '}
              {e.games} games [CI {pct(e.ci_low)}–{pct(e.ci_high)}].
            </span>
            <VerdictBadge verdict={e.verdict} games={e.games} />
          </Link>
        ))}
      </div>
    </section>
  )
}
