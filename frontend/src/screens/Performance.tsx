import { usePerformance, usePlayedHeroes, useSyncStatus } from '../api/queries'
import type { MetricField, PerformanceRow } from '../api/types'
import { MetricBaselineCell, NumberDeltaCell } from '../components/cells'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { IntervalBar } from '../components/IntervalBar'
import { QueryBoundary } from '../components/QueryBoundary'
import { SampleSize } from '../components/SampleSize'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

const fmtNum = (x: number | null) =>
  x === null ? '—' : x.toLocaleString(undefined, { maximumFractionDigits: 2 })

// Linear axis for one metric's interval bar, derived from the finite numbers in
// the row. Pure layout (the API owns the statistics): padded so whiskers don't
// touch the edges, with a unit window fallback when everything collapses to one
// value (e.g. a single game, or zero variance).
function domainFor(m: MetricField): { min: number; max: number } {
  const xs = [m.mean, m.ci_low, m.ci_high, m.baseline_mean].filter(
    (x): x is number => x !== null && Number.isFinite(x),
  )
  if (xs.length === 0) return { min: 0, max: 1 }
  const min = Math.min(...xs)
  const max = Math.max(...xs)
  if (min === max) {
    const pad = Math.abs(min) || 1
    return { min: min - pad, max: max + pad }
  }
  const pad = (max - min) * 0.08
  return { min: min - pad, max: max + pad }
}

export function Performance() {
  const { scope } = useScope()
  const performance = usePerformance(scope)
  const heroes = usePlayedHeroes(scope)
  // The "My hero" dropdown drives what this screen shows: a chosen hero narrows
  // to that one block, "All my heroes" (heroId null) shows the overall row plus
  // every hero. The name in the title keeps the active scope legible.
  const heroLabel =
    scope.heroId === null
      ? 'All heroes'
      : (heroes.data?.find((h) => h.hero_id === scope.heroId)?.name ??
        `Hero ${scope.heroId}`)

  return (
    <section>
      <h1 className="screen-title">
        Performance <span className="muted">· {heroLabel}</span>
      </h1>
      <p className="screen-sub">
        Your per-game numbers — net worth per minute, KDA, damage, healing — for
        each hero and overall, next to the live baseline of everyone else at this
        scope. Each metric shows a mean with its 95% confidence interval (a bar
        with whiskers); the gold dashed line marks the baseline when there is one.
        Color marks a confirmed verdict only — and a metric where lower is better,
        like deaths, reads as a strength when you beat the field. Metrics nobody
        else has data for show personal-only, never a comparison against nothing.
      </p>
      <QueryBoundary query={performance}>
        {(rows) => {
          // heroId null renders all rows; a set heroId keeps only that hero's
          // block, which naturally drops the overall row (its hero_id is null).
          const shown =
            scope.heroId === null
              ? rows
              : rows.filter((row) => row.hero_id === scope.heroId)
          return shown.length === 0 ? (
            <PerformanceEmpty />
          ) : (
            shown.map((row) => (
              <ScopeBlock key={`${row.scope}-${row.hero_id ?? 'all'}`} row={row} />
            ))
          )
        }}
      </QueryBoundary>
    </section>
  )
}

function ScopeBlock({ row }: { row: PerformanceRow }) {
  return (
    <div className="perf-block">
      <h2 className="perf-scope-title">
        {row.scope === 'overall' ? (
          'Overall'
        ) : (
          <span className="enemy-cell">
            <HeroIcon name={row.hero_name ?? '?'} url={row.hero_image_url} />
            {row.hero_name}
          </span>
        )}
        <span className="muted perf-scope-games">{row.games} games</span>
      </h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Sample</th>
            <th className="col-interval">Mean &amp; 95% CI</th>
            <th>Baseline</th>
            <th className="col-delta">Δ vs baseline</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {row.metrics.map((m) => (
            <MetricRow key={m.key} m={m} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MetricRow({ m }: { m: MetricField }) {
  return (
    <tr>
      <td title={m.higher_is_better ? 'Higher is better' : 'Lower is better'}>
        {m.label}
      </td>
      <td>
        <SampleSize games={m.games} />
      </td>
      <td className="col-interval">
        <IntervalBar
          winrate={m.mean}
          ciLow={m.ci_low}
          ciHigh={m.ci_high}
          globalRate={m.baseline_mean}
          verdict={m.verdict}
          domain={domainFor(m)}
          format={fmtNum}
        />
      </td>
      <td>
        <MetricBaselineCell mean={m.baseline_mean} games={m.baseline_games} />
      </td>
      <td className="col-delta">
        <NumberDeltaCell value={m.delta} games={m.games} />
      </td>
      <td>
        <VerdictBadge verdict={m.verdict} games={m.games} />
      </td>
    </tr>
  )
}

// When there are no rows, say why — using the live sync counts rather than a
// blank table (presentation rule 5). Mirrors MatchupsEmpty.
function PerformanceEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No performance data to show yet.">
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
                The worker hasn't ingested any matches yet. Add an account with{' '}
                <code>python -m ingest add-account &lt;id&gt; --self</code>, run{' '}
                <code>python -m ingest run-daemon</code>, and come back in an hour.
              </p>
            ) : (
              <p>
                Matches are ingested, but none fall in the current scope. Try
                widening the rank range, switching the era, or changing the game
                mode.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
