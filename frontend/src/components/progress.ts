import type { AccountProgress } from '../api/types'

// The onboarding import runs in two visible phases: first the prioritized recent
// window (your latest matches, fetched first so the app feels alive), then a
// background backfill of the rest of your history. This turns the raw progress
// counts into a single {done, total, phase} the card and the unlock line both
// render, so their wording and denominators always agree.
//
// - "recent": prioritized matches are still pending. The denominator is what the
//   recent window will hold (already analyzed + still prioritized), not the whole
//   history, so "12 of 50" tracks the window rather than every game ever played.
// - "backfill": the recent window is done but older matches are still loading.
//   The denominator is `known` (every discovery summary held).
// - "done": analysis has caught up; nothing left to show.
export type ProgressPhase = 'recent' | 'backfill' | 'done'

export interface ProgressCounts {
  done: number
  total: number
  phase: ProgressPhase
}

export function progressCounts(p: AccountProgress): ProgressCounts {
  if (p.prioritized_pending > 0) {
    return {
      done: p.analyzed,
      total: p.analyzed + p.prioritized_pending,
      phase: 'recent',
    }
  }
  if (p.backfill_pending > 0 || p.analyzed < p.known) {
    return { done: p.analyzed, total: p.known, phase: 'backfill' }
  }
  return { done: p.analyzed, total: p.known, phase: 'done' }
}
