import { useLaning, useSyncStatus } from '../../api/queries'
import { AnalysisUnlockProgress } from '../../components/AnalysisUnlockProgress'
import { DemoProfileLink } from '../../components/DemoProfileLink'
import { EmptyState } from '../../components/EmptyState'
import { MetricScopeBlock } from '../../components/MetricScopeBlock'
import { QueryBoundary } from '../../components/QueryBoundary'
import type { Scope } from '../../scope/useScope'

// The old Laning screen's body: early-game numbers at the lane-end mark per
// hero and overall, vs the live population baseline.
export function LaningSection({ scope }: { scope: Scope }) {
  const laning = useLaning(scope)
  return (
    <>
      <p className="screen-sub">
        Lane outcomes drive Deadlock games, so this is your early game laid bare. Tracks
        net worth, last hits, denies, and deaths to your lane opponent at the
        10-minute mark for each hero and
        overall, and compares with the live baseline of everyone else at this scope. Every
        player is measured at the same fixed point in the match, so the numbers are
        directly comparable. Each metric shows a mean with its 95% confidence
        interval, and the gold dashed line marks the baseline
        when there is one. Color marks a confirmed verdict only. Matches that ended
        before laning closed, or that nobody else has data for, show personal data only.
      </p>
      <QueryBoundary query={laning}>
        {(rows) => {
          // heroId null renders all rows; a set heroId keeps only that hero's
          // block, which naturally drops the overall row (its hero_id is null).
          const shown =
            scope.heroId === null
              ? rows
              : rows.filter((row) => row.hero_id === scope.heroId)
          return shown.length === 0 ? (
            <LaningEmpty />
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
function LaningEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No laning data to show yet.">
      <p>
        Your net worth, last hits, denies, and deaths at the 10-minute mark for
        each hero, next to the field's baseline, will appear here.
      </p>
      <AnalysisUnlockProgress feature="Laning analysis" />
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
