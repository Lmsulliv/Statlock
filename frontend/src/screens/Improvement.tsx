import { useImprovement, useSyncStatus } from '../api/queries'
import type { Improvement as ImprovementData, ImprovementEntry } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { QueryBoundary } from '../components/QueryBoundary'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// "Against Haze" for a matchup, "With Soul Shredder" for an item — the entry's
// kind picks the preposition; `subject` is the display name the server chose.
const lead = (e: ImprovementEntry) =>
  e.kind === 'matchup' ? `Against ${e.subject}` : `With ${e.subject}`

// Each kind carries its own art field; HeroIcon falls back to the initial when
// the URL is null, so the same icon component serves heroes and items.
const iconUrl = (e: ImprovementEntry) =>
  e.kind === 'matchup' ? (e.enemy_hero_image_url ?? null) : (e.item_image_url ?? null)

export function Improvement() {
  const { scope } = useScope()
  const improvement = useImprovement(scope)

  return (
    <section>
      <h1 className="screen-title">Directions for improvement</h1>
      <p className="screen-sub">
        A short, honest digest — not every number, just the calls the data
        actually supports. Confirmed weaknesses and strengths have intervals that
        clear the global baseline; the watch list is large gaps that aren’t
        confirmed yet, kept separate on purpose. Every rate is shown with its 95%
        interval, never bare.
      </p>
      <QueryBoundary query={improvement}>
        {(data) =>
          isEmpty(data) ? (
            <ImprovementEmpty />
          ) : (
            <div className="improve">
              <KindDigest heading="Hero improvement" kind="matchup" data={data} />
              <KindDigest heading="Item improvement" kind="item" data={data} />
            </div>
          )
        }
      </QueryBoundary>
    </section>
  )
}

// One labeled half of the digest — heroes or items. The three verdict groups
// are the same as before; we just split each one by `kind` so a hero line never
// lands in the item half (and vice versa). The grouping itself is the server's,
// so scenario 5 (no unconfirmed delta outside the watch list) still holds.
function KindDigest({
  heading,
  kind,
  data,
}: {
  heading: string
  kind: ImprovementEntry['kind']
  data: ImprovementData
}) {
  const pick = (list: ImprovementEntry[]) => list.filter((e) => e.kind === kind)
  return (
    <section className="improve-half">
      <h2 className="improve-heading">{heading}</h2>
      <ImprovementSection
        title="Confirmed weaknesses"
        hint="Significant negative deltas, largest first — these are the priorities."
        entries={pick(data.confirmed_weaknesses)}
      />
      <ImprovementSection
        title="Confirmed strengths"
        hint="Significant positive deltas — what to lean on."
        entries={pick(data.confirmed_strengths)}
      />
      <ImprovementSection
        title="Watch list"
        hint="Large gaps whose intervals don’t yet clear the baseline — not confirmed, just worth watching."
        entries={pick(data.watch_list)}
        watch
      />
    </section>
  )
}

function ImprovementSection({
  title,
  hint,
  entries,
  watch = false,
}: {
  title: string
  hint: string
  entries: ImprovementEntry[]
  watch?: boolean
}) {
  return (
    <section className="card">
      <h3 className="card-title">
        {title} <span className="muted">({entries.length})</span>
      </h3>
      <p className="muted improve-hint">{hint}</p>
      {entries.length === 0 ? (
        <p className="muted">Nothing here at the current scope.</p>
      ) : (
        <ul className="improve-list">
          {entries.map((e) => (
            <li key={`${e.kind}-${e.subject}-${e.hero_id ?? ''}`} className="improve-item">
              <HeroIcon name={e.subject} url={iconUrl(e)} />
              <span>
                <strong>{lead(e)}</strong> ({e.games} games) you win {pct(e.winrate)} vs a global{' '}
                {pct(e.global_rate)} [CI {pct(e.ci_low)}–{pct(e.ci_high)}].
                {watch && <span className="muted"> Need more games to confirm.</span>}
              </span>{' '}
              <VerdictBadge verdict={e.verdict} games={e.games} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const isEmpty = (d: ImprovementData) =>
  d.confirmed_weaknesses.length === 0 &&
  d.confirmed_strengths.length === 0 &&
  d.watch_list.length === 0

// Nothing confirmed yet: explain why with live sync counts rather than a blank
// screen (presentation rule 5 / scenario 6).
function ImprovementEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="Nothing to report yet.">
      <QueryBoundary query={sync}>
        {(s) => (
          <>
            <p>
              {s.fetched.toLocaleString()} matches fetched ·{' '}
              {s.queue_depth.toLocaleString()} queued ·{' '}
              {s.unavailable.toLocaleString()} unavailable.
            </p>
            {s.fetched === 0 ? (
              <p>
                The worker hasn’t ingested any matches yet. Add an account with{' '}
                <code>python -m ingest add-account &lt;id&gt; --self</code>, run{' '}
                <code>python -m ingest run-daemon</code>, and come back in an hour.
              </p>
            ) : (
              <p>
                No matchup or item has enough evidence to call yet. Keep playing,
                or lower <strong>Min games</strong> / widen the rank range to see
                softer signals.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
