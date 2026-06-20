import { useConfirmCandidate, useDismissCandidate, useEras } from '../api/queries'
import type { Era, EraCandidate, ErasResponse } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { QueryBoundary } from '../components/QueryBoundary'

const fmtDate = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString()
}

const fmtNum = (x: number | null) => (x === null ? '—' : x.toLocaleString())
const fmtScore = (x: number | null) => (x === null ? '—' : x.toFixed(1))

export function Eras() {
  const eras = useEras()

  return (
    <section>
      <h1 className="screen-title">Era manager</h1>
      <p className="screen-sub">
        Eras are the balance windows your stats are scoped to. The system proposes
        candidates from patch notes; you decide. Confirming one closes the current
        era at the patch date and re-bins every match in place, reusing the data
        already stored, so the new era has correctly scoped numbers immediately.
      </p>
      <QueryBoundary query={eras}>
        {(data) =>
          data.eras.length === 0 && data.pending_candidates.length === 0 ? (
            <ErasEmpty data={data} />
          ) : (
            <div className="eras">
              <Candidates candidates={data.pending_candidates} />
              <EraList eras={data.eras} />
            </div>
          )
        }
      </QueryBoundary>
    </section>
  )
}

function Candidates({ candidates }: { candidates: EraCandidate[] }) {
  const confirm = useConfirmCandidate()
  const dismiss = useDismissCandidate()

  // One shared mutation each, so only the row currently in flight is disabled
  // (mutation.variables holds the id we last passed it).
  const busy = (id: number) =>
    (confirm.isPending && confirm.variables === id) ||
    (dismiss.isPending && dismiss.variables === id)

  return (
    <section className="card">
      <h2 className="card-title">
        Pending candidates <span className="muted">({candidates.length})</span>
      </h2>
      {(confirm.isError || dismiss.isError) && (
        <p className="state-error">Couldn’t update the candidate. Is the backend running?</p>
      )}
      {candidates.length === 0 ? (
        <p className="muted">
          No candidates awaiting review. New ones appear here when the nightly
          maintenance loop flags a patch.
        </p>
      ) : (
        <ul className="candidate-list">
          {candidates.map((c) => (
            <li key={c.candidate_id} className="candidate">
              <div className="candidate-main">
                <a href={c.post_url} target="_blank" rel="noreferrer">
                  {c.post_title ?? c.post_url}
                </a>
                <div className="muted">
                  Posted {fmtDate(c.posted_at)} · {fmtNum(c.change_lines)} change lines ·
                  score {fmtScore(c.score)}
                </div>
              </div>
              <div className="candidate-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy(c.candidate_id)}
                  onClick={() => confirm.mutate(c.candidate_id)}
                >
                  Confirm
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busy(c.candidate_id)}
                  onClick={() => dismiss.mutate(c.candidate_id)}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function EraList({ eras }: { eras: Era[] }) {
  return (
    <section className="card">
      <h2 className="card-title">
        Eras <span className="muted">({eras.length})</span>
      </h2>
      {eras.length === 0 ? (
        <p className="muted">No eras defined yet. Confirm a candidate to create the first one.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Era</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {eras.map((e) => (
              <tr key={e.era_id}>
                <td className="col-hero">{e.label}</td>
                <td className="muted">{fmtDate(e.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

// Empty DB: no eras and no candidates. Explain it rather than show a blank page
// (presentation rule 5 / scenario 6).
function ErasEmpty({ data }: { data: ErasResponse }) {
  return (
    <EmptyState title="No eras yet.">
      <p>{data.message ?? 'No eras defined.'}</p>
      <p>
        Eras are created by confirming a candidate. The nightly maintenance loop
        proposes them from patch notes; once the worker has run and flagged a
        patch, candidates show up here for review.
      </p>
    </EmptyState>
  )
}
