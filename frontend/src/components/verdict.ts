import type { Verdict } from '../api/types'

// Display label, color tone, and a sort rank for each of the five verdict
// tiers. Tone drives CSS only (significance, never magnitude): "clear" tiers are
// vivid, "leaning" tiers are muted, "not enough data" is neutral gray.

export const VERDICT_LABEL: Record<Verdict, string> = {
  clear_strength: 'Strength',
  leaning_strength: 'Leaning strength',
  not_enough_data: 'Neutral',
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
    return games < VERDICT_FLOOR ? 'Need more games' : 'Neutral'
  }
  return VERDICT_LABEL[verdict]
}

// Plain-language explainers for the shared info tooltips (no formulas). The bar
// tip describes what the interval means; the verdict tip explains why a given
// tier reads the way it does.
export const INTERVAL_TIP =
  "The bar shows the range we're 95% confident your true rate falls in. " +
  'The tick marks the typical rate for comparable players — where your range ' +
  'sits relative to it is what earns a verdict.'

// `games` defaults to Infinity so a caller without a sample count (the
// IntervalBar) still gets a sensible sentence and never claims "too few games".
export function verdictTip(verdict: Verdict, games: number = Infinity): string {
  switch (verdict) {
    case 'clear_strength':
    case 'clear_weakness':
      return 'Clear: the whole range sits on one side of the baseline, so the difference is real, not noise.'
    case 'leaning_strength':
    case 'leaning_weakness':
      return 'Leaning: your rate is off the baseline, but the range still touches it — a hint, not a firm call.'
    default:
      return games < VERDICT_FLOOR
        ? 'Not enough data: too few games, so the range is too wide to say anything honest yet.'
        : 'Not enough data: your range still includes the baseline, so we can’t call it either way.'
  }
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
