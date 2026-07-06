import { useAccountProgress } from '../api/queries'
import { progressCounts } from './progress'
import { ProgressBar } from './ProgressBar'

// The live onboarding module: "Analyzed X of Y recent matches" with a bar,
// polled every 5s by useAccountProgress while the drain works. It disappears on
// its own once analysis catches up (phase 'done'), so callers can mount it
// unconditionally. Two phases so the prioritized recent window reads differently
// from the slower background backfill of full history (see progressCounts).
export function AccountProgressCard({ accountId }: { accountId: number | null }) {
  const progress = useAccountProgress(accountId)
  const data = progress.data
  if (!data) return null

  const { done, total, phase } = progressCounts(data)
  if (phase === 'done') return null

  return (
    <section className="card progress-card">
      {phase === 'recent' ? (
        <>
          <h2 className="card-title">
            Analyzed {done.toLocaleString()} of {total.toLocaleString()} recent matches
          </h2>
          <ProgressBar value={done} max={total} />
          <p className="muted progress-note">
            Prioritizing your latest matches so results appear within seconds.
          </p>
        </>
      ) : (
        <>
          <h2 className="card-title">Full history loading in the background</h2>
          <ProgressBar value={done} max={total} />
          <p className="muted progress-note">
            {done.toLocaleString()} of {total.toLocaleString()} matches analyzed.
            Your recent matches are ready; older games keep filling in.
          </p>
        </>
      )}
    </section>
  )
}
