import { useSyncStatus, useTilt } from '../../api/queries'
import type { TiltBucket, TiltResponse } from '../../api/types'
import { BaselineCell, DeltaCell } from '../../components/cells'
import { DemoProfileLink } from '../../components/DemoProfileLink'
import { EmptyState } from '../../components/EmptyState'
import { IntervalBar } from '../../components/IntervalBar'
import { ProvisionalBadge } from '../../components/ProvisionalBadge'
import { QueryBoundary } from '../../components/QueryBoundary'
import { SampleSize } from '../../components/SampleSize'
import { VerdictBadge } from '../../components/VerdictBadge'
import type { Scope } from '../../scope/useScope'

const pct = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// The old Tilt screen's body, retitled "Do you tilt?" by its Insights card.
// The two bucket tables sit side by side — they're the same shape and read
// together (session fatigue vs loss-streak pressure).
export function SessionsSection({ scope }: { scope: Scope }) {
  const tilt = useTilt(scope)
  return (
    <>
      <p className="muted improve-hint">
        Measures how you perform across a single sitting. Games are grouped into
        sessions, with a new session starting after a break of about three hours,
        then split by how deep into the session each game falls and by how many
        losses came right before it. Each group is judged against{' '}
        <strong>your own overall win rate</strong>, so a verdict means a real shift
        from how you usually play. Every rate shows its 95% interval, and thin
        groups stay marked “not enough data.”
      </p>
      <QueryBoundary query={tilt}>
        {(data) => (data.overall.games === 0 ? <TiltEmpty /> : <TiltBody data={data} />)}
      </QueryBoundary>
    </>
  )
}

function TiltBody({ data }: { data: TiltResponse }) {
  const sessions = `${data.sessions.toLocaleString()} session${data.sessions === 1 ? '' : 's'}`
  return (
    <div className="tilt">
      <div className="screen-badge-row">
        <ProvisionalBadge show={data.provisional} />
      </div>
      <p className="muted tilt-summary">
        {data.overall.games.toLocaleString()} games across {sessions} · a session
        is play with gaps under {data.session_gap_hours}h · baseline is your
        overall win rate, {pct(data.overall.winrate)}.
      </p>

      <div className="team-cols">
        <section className="tilt-section">
          <h3 className="improve-heading">By game number in session</h3>
          <p className="muted">
            Do you fade as a sitting wears on? Game 1 is the first after a{' '}
            {data.session_gap_hours}h+ break.
          </p>
          <TiltTable rows={data.by_session_index} firstHeader="Game #" />
        </section>

        <section className="tilt-section">
          <h3 className="improve-heading">By loss streak</h3>
          <p className="muted">
            After losing N in a row this session, how do you do on the next game?
            The streak resets after any win or a break.
          </p>
          <TiltTable rows={data.by_loss_streak} firstHeader="Going in" />
        </section>
      </div>
    </div>
  )
}

// Same columns as the matchup table, minus the hero icon: the first column is
// the bucket label and the baseline column is relabeled "Your overall" because
// that's the reference here. Rows render in the server's natural order (the
// progression is the signal), so this table isn't sortable.
function TiltTable({ rows, firstHeader }: { rows: TiltBucket[]; firstHeader: string }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>{firstHeader}</th>
          <th>Record</th>
          <th className="col-interval">Win rate &amp; 95% CI</th>
          <th title="Your overall win rate at this scope; the baseline each bucket is judged against.">
            Your overall
          </th>
          <th title="Shrinkage-adjusted rate minus your overall; thin samples are pulled toward your baseline first.">
            Adj. Δ
          </th>
          <th title="Weighs sample size and confidence, so a thin bucket reads as inconclusive.">
            Verdict
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <td className="tilt-bucket">{r.label}</td>
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

// No sessions in scope: explain why with the live sync counts (presentation
// rule 5 / scenario 6), mirroring the other empty states.
function TiltEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No sessions to analyze yet.">
      <p>
        How your win rate holds up deep into a play session and across loss
        streaks — your tilt signature — will appear here.
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
      <DemoProfileLink />
    </EmptyState>
  )
}
