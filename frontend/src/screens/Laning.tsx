import { useLaning, useSyncStatus } from '../api/queries'
import type { LaningRow, MetricField } from '../api/types'
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
// value (e.g. a single game, or zero variance). Mirrors Performance's domainFor.
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

export function Laning() {
  const { scope } = useScope()
  const laning = useLaning(scope)

  return (
    <section>
      <h1 className="screen-title">Laning</h1>
      <p className="screen-sub">
        Lane outcomes drive Deadlock games, so this is your early game laid bare. Tracks
        net worth, last hits, and denies at the 10-minute mark for each hero and
        overall, and compares with the live baseline of everyone else at this scope. Every
        player is measured at the same fixed point in the match, so the numbers are
        directly comparable. Each metric shows a mean with its 95% confidence
        interval, and the gold dashed line marks the baseline
        when there is one. Color marks a confirmed verdict only. Matches that ended
        before laning closed, or that nobody else has data for, show personal data only.
      </p>
      <QueryBoundary query={laning}>
        {(rows) =>
          rows.length === 0 ? (
            <LaningEmpty />
          ) : (
            rows.map((row) => (
              <ScopeBlock key={`${row.scope}-${row.hero_id ?? 'all'}`} row={row} />
            ))
          )
        }
      </QueryBoundary>
    </section>
  )
}

function ScopeBlock({ row }: { row: LaningRow }) {
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
// blank table (presentation rule 5). Mirrors PerformanceEmpty.
function LaningEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No laning data to show yet.">
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
