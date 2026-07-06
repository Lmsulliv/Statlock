import { EmptyState } from './EmptyState'

// Shown on the deep-analysis views when the scope is Street Brawl. Brawl is a
// minor mode and must never mix into Normal analytics (matchups, items, laning,
// deaths, improvement, recurring players, performance all need Normal metadata
// and Normal-population baselines), so instead of an empty table we say plainly
// where Brawl data does live and how to get back to Normal.
export function ModeNotice() {
  return (
    <EmptyState title="Deep analysis is available for Normal matches only.">
      <p>
        Street Brawl games are tracked in <strong>Overview</strong>,{' '}
        <strong>Performance › Over time</strong>, and{' '}
        <strong>Insights › Do you tilt?</strong>. This view compares against the
        Normal population, so it stays Normal-only.
      </p>
      <p>
        Switch <strong>Mode</strong> back to <strong>Normal</strong> in the scope
        bar to see it.
      </p>
    </EmptyState>
  )
}
