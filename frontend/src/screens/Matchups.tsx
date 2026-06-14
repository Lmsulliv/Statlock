import { useMatchups, useSyncStatus } from '../api/queries'
import type { MatchupRow } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { IntervalBar } from '../components/IntervalBar'
import { QueryBoundary } from '../components/QueryBoundary'
import { SampleSize } from '../components/SampleSize'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

const fmtPct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)
const fmtDelta = (x: number | null) =>
  x === null ? '—' : `${x > 0 ? '+' : ''}${Math.round(x * 100)} pts`

export function Matchups() {
  const { scope } = useScope()
  const matchups = useMatchups(scope)

  return (
    <section>
      <h1 className="screen-title">Matchups</h1>
      <p className="screen-sub">
        One row per enemy hero. Win rate shows as a 95% confidence interval — a
        bar with whiskers — never a bare percentage. The gold dashed line is the
        global baseline; color marks a confirmed verdict, not a big number.
      </p>
      <QueryBoundary query={matchups}>
        {(rows) =>
          rows.length === 0 ? <MatchupsEmpty /> : <MatchupsTable rows={rows} />
        }
      </QueryBoundary>
    </section>
  )
}

function MatchupsTable({ rows }: { rows: MatchupRow[] }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Enemy hero</th>
          <th>Record</th>
          <th className="col-interval">Win rate &amp; 95% CI</th>
          <th>Global baseline</th>
          <th>Adj. delta</th>
          <th>Verdict</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.enemy_hero_id}>
            <td className="col-hero">{r.enemy_hero_name}</td>
            <td>
              <SampleSize games={r.games} wins={r.wins} />
            </td>
            <td className="col-interval">
              <IntervalBar
                winrate={r.winrate}
                ciLow={r.ci_low}
                ciHigh={r.ci_high}
                globalRate={r.global_rate}
                verdict={r.verdict}
              />
            </td>
            <td>
              <div>{fmtPct(r.global_rate)}</div>
              <div className="muted">{r.global_matches.toLocaleString()} games</div>
            </td>
            <td className="col-delta">{fmtDelta(r.delta)}</td>
            <td>
              <VerdictBadge verdict={r.verdict} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// When there are no rows, say why — using the live sync counts rather than a
// blank table (presentation rule 5). The two causes look different: nothing
// ingested yet vs. data present but filtered out by the current scope.
function MatchupsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No matchups to show yet.">
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
                Matches are ingested, but none meet the current scope. Try
                lowering <strong>Min games</strong> or widening the rank range.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
