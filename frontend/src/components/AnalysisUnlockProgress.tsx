import { useAccountProgress, useEffectiveAccountId } from '../api/queries'
import { useScope } from '../scope/useScope'
import { progressCounts } from './progress'
import { ProgressBar } from './ProgressBar'

// The live "unlock" line for the deep-analysis empty states: instead of a bare
// "nothing yet", it tells the user this feature is filling in and shows the same
// analyzed count the onboarding card polls. Renders nothing when there's no
// account resolved or nothing discovered yet (known === 0) — the caller's static
// sync-count fallback covers that case. Statistics-free: it renders counts the
// API provides.
export function AnalysisUnlockProgress({ feature }: { feature: string }) {
  const { scope } = useScope()
  const accountId = useEffectiveAccountId(scope)
  const progress = useAccountProgress(accountId)
  const data = progress.data
  if (!data || data.known === 0) return null

  const { done, total } = progressCounts(data)
  return (
    <div className="unlock-progress">
      <p>
        <strong>{feature}</strong> unlocks as matches are analyzed:{' '}
        {done.toLocaleString()} of {total.toLocaleString()} done.
      </p>
      <ProgressBar value={done} max={total} />
    </div>
  )
}
