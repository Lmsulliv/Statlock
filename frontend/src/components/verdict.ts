import type { Verdict } from '../api/types'

// Display label, color tone, and a sort rank for each of the five verdict
// tiers. Tone drives CSS only (significance, never magnitude): "clear" tiers are
// vivid, "leaning" tiers are muted, "not enough data" is neutral gray.

export const VERDICT_LABEL: Record<Verdict, string> = {
  clear_strength: 'Strength',
  leaning_strength: 'Leaning strength',
  not_enough_data: 'Inconclusive',
  leaning_weakness: 'Leaning weakness',
  clear_weakness: 'Weakness',
}

// Mirror of stats/__init__.py VERDICT_FLOOR — used only to choose the neutral
// label (not to compute anything; the stat is decided server-side).
export const VERDICT_FLOOR = 5

// The neutral tier means two different things, so it reads two ways: a genuinely
// thin sample ("need more games") vs a real sample whose rate just sits too
// close to the baseline to call ("inconclusive").
export function verdictLabel(verdict: Verdict, games: number): string {
  if (verdict === 'not_enough_data') {
    return games < VERDICT_FLOOR ? 'Need more games' : 'Inconclusive'
  }
  return VERDICT_LABEL[verdict]
}

export function verdictHint(verdict: Verdict, games: number): string | undefined {
  if (verdict !== 'not_enough_data') return undefined
  return games < VERDICT_FLOOR
    ? `Fewer than ${VERDICT_FLOOR} games, too few to earn a verdict.`
    : 'Your rate is too close to the baseline to call; the confidence interval still includes it.'
}

export const VERDICT_TONE: Record<Verdict, string> = {
  clear_strength: 'pos',
  leaning_strength: 'pos-weak',
  not_enough_data: 'neutral',
  leaning_weakness: 'neg-weak',
  clear_weakness: 'neg',
}

// Ascending order from strongest weakness to strongest strength, so a plain
// ascending sort groups weaknesses first and clear calls at the extremes.
export const VERDICT_ORDER: Record<Verdict, number> = {
  clear_weakness: 0,
  leaning_weakness: 1,
  not_enough_data: 2,
  leaning_strength: 3,
  clear_strength: 4,
}
