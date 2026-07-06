import { useMatchups, useSyncStatus } from '../../api/queries'
import { AnalysisUnlockProgress } from '../../components/AnalysisUnlockProgress'
import { EmptyState } from '../../components/EmptyState'
import { QueryBoundary } from '../../components/QueryBoundary'
import type { Scope } from '../../scope/useScope'
import { MatchupTable } from './MatchupTable'

// The aggregate matchup grid — the old Matchups screen's body, verbatim. Still
// honors scope.heroId as the "my hero" filter, so a legacy /matchups?hero_id=N
// deep link (redirected here) behaves exactly as it used to.
export function AllHeroesGrid({ scope }: { scope: Scope }) {
  const matchups = useMatchups(scope)
  return (
    <div>
      <h2 className="card-title">Matchups across all heroes</h2>
      <p className="screen-sub">
        One row per enemy hero, sorted A→Z by default; click any header to
        re-sort. Win rate always shows as a 95% confidence interval (a bar with
        whiskers), and the gold dashed line marks the global baseline when
        available. Color marks a confirmed verdict, lighting up only when the
        interval clears the baseline.
      </p>
      <QueryBoundary query={matchups}>
        {(rows) => (rows.length === 0 ? <MatchupsEmpty /> : <MatchupTable rows={rows} />)}
      </QueryBoundary>
    </div>
  )
}

// When there are no rows, say why — using the live sync counts rather than a
// blank table (presentation rule 5).
function MatchupsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No matchups to show yet.">
      <p>
        Your win rate against each enemy hero, with 95% confidence intervals and
        honest verdicts against the global baseline, will appear here.
      </p>
      {/* Live "X of Y done" while a fresh import is still analyzing; renders
          nothing once there's a full-metadata dataset or no account yet. */}
      <AnalysisUnlockProgress feature="Matchup analysis" />
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
                Matches are ingested, but none fall in the current scope. Try
                widening the rank range, switching the era or game mode, or
                setting the lane view to Overall.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
