import { useImprovement, usePlayedHeroes, useSyncStatus } from '../api/queries'
import type {
  Improvement as ImprovementData,
  ImprovementEntry,
  WinCondition,
} from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { QueryBoundary } from '../components/QueryBoundary'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// Signed percentage for the gap readout, e.g. +32% / -8%.
const signedPct = (x: number) => `${x >= 0 ? '+' : ''}${Math.round(x * 100)}%`

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
  // Reuse the played-heroes list the ScopeBar resolves names from (same query
  // key, so this is cached, not a new request) to name the selected hero.
  const heroesQuery = usePlayedHeroes(scope)
  const heroName =
    scope.heroId === null
      ? null
      : (heroesQuery.data?.find((h) => h.hero_id === scope.heroId)?.name ??
        `Hero ${scope.heroId}`)

  return (
    <section>
      <h1 className="screen-title">Directions for improvement</h1>
      <p className="screen-sub">
        A short, honest digest of just the calls the data actually supports
        {heroName ? `, scoped to ${heroName}` : ''}. Confirmed weaknesses and
        strengths have intervals that clear the global baseline; the watch list
        collects large gaps that are still short of confirmation, kept separate
        on purpose. Every rate is shown with its 95% interval.
      </p>
      <QueryBoundary query={improvement}>
        {(data) =>
          isEmpty(data) ? (
            <ImprovementEmpty heroName={heroName} />
          ) : (
            <>
              <WinConditions conditions={data.win_conditions} heroName={heroName} />
              <div className="improve">
                <KindDigest
                  heading="Hero improvement"
                  kind="matchup"
                  data={data}
                  heroName={heroName}
                />
                <KindDigest
                  heading="Item improvement"
                  kind="item"
                  data={data}
                  heroName={heroName}
                />
              </div>
            </>
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
  heroName,
}: {
  heading: string
  kind: ImprovementEntry['kind']
  data: ImprovementData
  heroName: string | null
}) {
  const pick = (list: ImprovementEntry[]) => list.filter((e) => e.kind === kind)
  return (
    <section className="improve-half">
      <h2 className="improve-heading">{heading}</h2>
      <ImprovementSection
        title="Confirmed weaknesses"
        hint="Significant negative deltas, largest first; these are the priorities."
        entries={pick(data.confirmed_weaknesses)}
        heroName={heroName}
      />
      <ImprovementSection
        title="Confirmed strengths"
        hint="Significant positive deltas to lean on."
        entries={pick(data.confirmed_strengths)}
        heroName={heroName}
      />
      <ImprovementSection
        title="Watch list"
        hint="Large gaps whose intervals fall just short of clearing the baseline, worth keeping an eye on."
        entries={pick(data.watch_list)}
        heroName={heroName}
        watch
      />
    </section>
  )
}

// "What wins your games": each surfaced condition splits the scoped matches in
// two and shows the win rate (with its 95% interval) on each side, plus the gap
// — the lever. The server only sends conditions where both sides clear the
// honesty floor and the intervals separate, so a thin or noisy split is simply
// absent rather than shown with a caveat. Honors the hero scope automatically
// (the API narrowed the splits to the selected hero's games).
function WinConditions({
  conditions,
  heroName,
}: {
  conditions: WinCondition[]
  heroName: string | null
}) {
  return (
    <section className="card improve-wins">
      <h2 className="improve-heading">What wins your games</h2>
      <p className="muted improve-hint">
        Early-game conditions that, when they hold, lift your win rate — the
        biggest lever first. Each splits your{heroName ? ` ${heroName}` : ''}{' '}
        games and compares the two sides; only gaps the data supports are shown.
      </p>
      {conditions.length === 0 ? (
        <p className="muted">
          No condition has enough games on both sides to call yet. Keep playing,
          or widen the scope.
        </p>
      ) : (
        <ul className="improve-list">
          {conditions.map((c) => (
            <li key={c.key} className="improve-item improve-win">
              <span>
                <strong>{c.label}</strong> <span className="muted">— {c.description}.</span>
                <br />
                You win <strong>{pct(c.met.rate)}</strong> ({pct(c.met.ci_low)}–
                {pct(c.met.ci_high)}) when it holds vs <strong>{pct(c.not_met.rate)}</strong>{' '}
                ({pct(c.not_met.ci_low)}–{pct(c.not_met.ci_high)}) when it doesn’t —{' '}
                <strong>{signedPct(c.gap)}</strong> swing.
              </span>{' '}
              <span className={`badge tone-${tierTone(c)}`}>
                {c.tier === 'clear' ? 'Clear' : 'Leaning'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

// Tone mirrors the verdict badges: vivid for a "clear" split, muted for
// "leaning"; green when meeting the condition helps (gap ≥ 0), red if it hurts.
function tierTone(c: WinCondition): string {
  const dir = c.gap >= 0 ? 'pos' : 'neg'
  return c.tier === 'clear' ? dir : `${dir}-weak`
}

function ImprovementSection({
  title,
  hint,
  entries,
  heroName,
  watch = false,
}: {
  title: string
  hint: string
  entries: ImprovementEntry[]
  heroName: string | null
  watch?: boolean
}) {
  return (
    <section className="card">
      <h3 className="card-title">
        {title} <span className="muted">({entries.length})</span>
      </h3>
      <p className="muted improve-hint">{hint}</p>
      {entries.length === 0 ? (
        <p className="muted">
          {heroName
            ? `No confirmed calls for ${heroName} yet at this scope.`
            : 'Nothing here at the current scope.'}
        </p>
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
  d.watch_list.length === 0 &&
  d.win_conditions.length === 0

// Nothing confirmed yet: explain why with live sync counts rather than a blank
// screen (presentation rule 5 / scenario 6).
function ImprovementEmpty({ heroName }: { heroName: string | null }) {
  const sync = useSyncStatus()
  return (
    <EmptyState
      title={heroName ? `Nothing to report for ${heroName} yet.` : 'Nothing to report yet.'}
    >
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
                {heroName
                  ? `No matchup or item on ${heroName} has enough evidence to call yet. `
                  : 'No matchup or item has enough evidence to call yet. '}
                Keep playing, or lower <strong>Min games</strong> / widen the rank
                range to see softer signals.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
