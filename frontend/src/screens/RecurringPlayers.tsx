import { useRecurringPlayers, useSyncStatus } from '../api/queries'
import type { RecurringPlayer, RecurringPlayersResponse } from '../api/types'
import { BaselineCell, DeltaCell } from '../components/cells'
import { EmptyState } from '../components/EmptyState'
import { InlineRename } from '../components/InlineRename'
import { IntervalBar } from '../components/IntervalBar'
import { QueryBoundary } from '../components/QueryBoundary'
import { SampleSize } from '../components/SampleSize'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// display_name is resolved server-side (manual label > Steam persona > id), so
// it is normally a string; the ?? keeps a defensive fallback to the bare id.
const playerLabel = (p: RecurringPlayer) =>
  p.display_name ?? `Account ${p.account_id}`

export function RecurringPlayers() {
  const { scope } = useScope()
  const recurring = useRecurringPlayers(scope)

  return (
    <section>
      <h1 className="screen-title">Recurring players</h1>
      <p className="screen-sub">
        The other real players who keep turning up across your matches, split
        into <strong>teammates</strong> (your win rate <em>with</em> them) and{' '}
        <strong>opponents</strong> (your win rate <em>against</em> them). Each is
        judged against <strong>your own win rate</strong> over the same matches, so
        a verdict means you do better or worse with (or against) that player than
        you usually do. Every rate shows its 95% interval, and players you've only
        met a few times stay “not enough data.” Switching to <strong>in-lane</strong>{' '}
        keeps only the teammates and opponents who shared your lane pairing, so a
        co-player's shared-game count drops accordingly.
      </p>
      <QueryBoundary query={recurring}>
        {(data) =>
          data.overall.games === 0 ? (
            <RecurringEmpty />
          ) : (
            <RecurringBody data={data} />
          )
        }
      </QueryBoundary>
    </section>
  )
}

function RecurringBody({ data }: { data: RecurringPlayersResponse }) {
  const baseline = data.hero_id !== null ? 'your win rate on this hero' : 'your overall win rate'
  return (
    <div className="recurring">
      <p className="muted recurring-summary">
        {data.overall.games.toLocaleString()} games · baseline is {baseline},{' '}
        {pct(data.overall.winrate)} · a player is listed once you've shared at
        least {data.min_co_occurrence} games; thinner than that and they're left
        off, and under the verdict floor they read “not enough data.”
      </p>

      <section className="recurring-section">
        <h2 className="improve-heading">Teammates: win rate with</h2>
        <p className="muted">
          Players who keep landing on your team, most-shared first. A confirmed
          strength is someone you genuinely win more alongside.
        </p>
        <RecurringTable rows={data.teammates} firstHeader="Teammate" />
      </section>

      <section className="recurring-section">
        <h2 className="improve-heading">Opponents: win rate against</h2>
        <p className="muted">
          Players you keep running into on the other side. A confirmed weakness is
          a genuine nemesis: you beat them less than you usually do.
        </p>
        <RecurringTable rows={data.opponents} firstHeader="Opponent" />
      </section>
    </div>
  )
}

// Same columns as Tilt (and Matchups, minus the hero icon): the first column is
// the player, the baseline column is "Your overall" because that's the reference
// here. Rows render in the server's order (most-shared first), so this table
// isn't re-sortable.
function RecurringTable({ rows, firstHeader }: { rows: RecurringPlayer[]; firstHeader: string }) {
  if (rows.length === 0) {
    return (
      <p className="muted">
        No {firstHeader.toLowerCase()}s have shared enough of your matches yet.
      </p>
    )
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>{firstHeader}</th>
          <th>Record</th>
          <th className="col-interval">Win rate &amp; 95% CI</th>
          <th title="Your overall win rate at this scope; the baseline each player is judged against.">
            Your overall
          </th>
          <th title="Shrinkage-adjusted rate minus your overall; thin samples are pulled toward your baseline first.">
            Adj. Δ
          </th>
          <th title="Weighs sample size and confidence, so a thin record reads as inconclusive.">
            Verdict
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.account_id}>
            <td className="player-name">
              <span className="player-name-cell">
                <span className="player-name-text">{playerLabel(r)}</span>
                <InlineRename accountId={r.account_id} currentName={playerLabel(r)} />
              </span>
            </td>
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
              <BaselineCell rate={r.global_rate} matches={r.global_matches} />
            </td>
            <td className="col-delta">
              <DeltaCell value={r.delta} games={r.games} />
            </td>
            <td>
              <VerdictBadge verdict={r.verdict} games={r.games} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// No matches in scope: explain why with the live sync counts (presentation rule
// 5 / scenario 6), mirroring the other screens' empty states.
function RecurringEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No recurring players to show yet.">
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
                widening the rank range, switching the era or game mode, or picking
                another account.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
