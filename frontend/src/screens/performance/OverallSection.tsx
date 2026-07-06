import { usePerformance, useSyncStatus } from '../../api/queries'
import { DemoProfileLink } from '../../components/DemoProfileLink'
import { EmptyState } from '../../components/EmptyState'
import { MetricScopeBlock } from '../../components/MetricScopeBlock'
import { QueryBoundary } from '../../components/QueryBoundary'
import type { Scope } from '../../scope/useScope'

// The old Performance screen's body: continuous per-game metrics per hero and
// overall, vs the live population baseline.
export function OverallSection({ scope }: { scope: Scope }) {
  const performance = usePerformance(scope)
  return (
    <>
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
        {(data) => {
          // The endpoint wraps the rows in a provisional flag; unwrap here.
          // heroId null renders all rows; a set heroId keeps only that hero's
          // block, which naturally drops the overall row (its hero_id is null).
          const shown =
            scope.heroId === null
              ? data.rows
              : data.rows.filter((row) => row.hero_id === scope.heroId)
          return shown.length === 0 ? (
            <PerformanceEmpty />
          ) : (
            shown.map((row) => (
              <MetricScopeBlock key={`${row.scope}-${row.hero_id ?? 'all'}`} row={row} />
            ))
          )
        }}
      </QueryBoundary>
    </>
  )
}

// When there are no rows, say why — using the live sync counts rather than a
// blank table (presentation rule 5).
function PerformanceEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No performance data to show yet.">
      <p>
        Your per-game metrics — net worth per minute, KDA, damage, and more —
        each measured against the live population baseline for comparable
        players, will appear here.
      </p>
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
      <DemoProfileLink />
    </EmptyState>
  )
}
