import type { Verdict } from '../api/types'
import { VERDICT_TONE } from './verdict'

interface Props {
  winrate: number | null
  ciLow: number | null
  ciHigh: number | null
  globalRate: number | null
  verdict: Verdict
  // Win rates live on 0..1 and render as percentages. Continuous metrics (net
  // worth/min, KDA, ...) don't, so an optional linear `domain` maps raw values
  // into the bar and an optional `format` labels them. Both omitted -> the
  // original win-rate behavior, unchanged for every existing caller.
  domain?: { min: number; max: number }
  format?: (x: number | null) => string
}

const clamp = (x: number) => Math.max(0, Math.min(1, x))
const pctLabel = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// Win rate (or a continuous metric) rendered as a 95% confidence interval: a bar
// with whiskers, never a bare number (presentation rule 1). Tiny samples produce
// visibly huge whiskers — that is the feature working, not a bug.
//
// Color encodes significance, not magnitude (rule 2): the interval is tinted only
// when the server's verdict is a confirmed strength/weakness. We read `verdict`
// straight from the API and never recompute it. Positioning is the only thing
// computed here — pure layout, not statistics.
export function IntervalBar({
  winrate,
  ciLow,
  ciHigh,
  globalRate,
  verdict,
  domain,
  format,
}: Props) {
  const tone = VERDICT_TONE[verdict]
  const fmt = format ?? pctLabel

  // Map a raw value to its 0..1 position on the bar: identity for win rates,
  // linear within [min, max] for a continuous domain.
  const span = domain ? domain.max - domain.min || 1 : 1
  const posOf = (x: number) => (domain ? (x - domain.min) / span : x)
  const left = (x: number) => `${(clamp(posOf(x)) * 100).toFixed(2)}%`
  const hasInterval = ciLow !== null && ciHigh !== null

  const title =
    `${fmt(winrate)} · 95% CI ${fmt(ciLow)}–${fmt(ciHigh)}` +
    (globalRate !== null ? ` · baseline ${fmt(globalRate)}` : ' · no baseline')

  return (
    <div className="interval">
      <div className={`interval-bar tone-${tone}`} title={title}>
        <div className="interval-track" />
        {globalRate !== null && (
          <div className="interval-baseline" style={{ left: left(globalRate) }} />
        )}
        {hasInterval && (
          <div
            className="interval-whisker"
            style={{
              left: left(ciLow as number),
              width: `${(clamp(posOf(ciHigh as number)) - clamp(posOf(ciLow as number))) * 100}%`,
            }}
          >
            <span className="cap cap-low" />
            <span className="cap cap-high" />
          </div>
        )}
        {winrate !== null && (
          <div className="interval-point" style={{ left: left(winrate) }} />
        )}
      </div>
      <div className="interval-labels">
        <span className="interval-rate">{fmt(winrate)}</span>
        <span>
          CI {fmt(ciLow)}–{fmt(ciHigh)}
        </span>
        {globalRate !== null && (
          <span className="interval-baseline-key">baseline {fmt(globalRate)}</span>
        )}
      </div>
    </div>
  )
}
