import type { Verdict } from '../api/types'
import { VERDICT_TONE } from './verdict'

interface Props {
  winrate: number | null
  ciLow: number
  ciHigh: number
  globalRate: number | null
  verdict: Verdict
}

const clamp = (x: number) => Math.max(0, Math.min(1, x))
const pct = (x: number) => `${(clamp(x) * 100).toFixed(2)}%`
const fmt = (x: number | null) => (x === null ? '—' : `${Math.round(x * 100)}%`)

// Win rate rendered as a 95% confidence interval: a bar with whiskers, never a
// bare percentage (presentation rule 1). Tiny samples produce visibly huge
// whiskers — that is the feature working, not a bug.
//
// Color encodes significance, not magnitude (rule 2): the interval is tinted
// only when the server's verdict is a confirmed strength/weakness. An
// unconfirmed row stays neutral gray no matter how lopsided the point looks. We
// read `verdict` straight from the API and never recompute it.
export function IntervalBar({ winrate, ciLow, ciHigh, globalRate, verdict }: Props) {
  const tone = VERDICT_TONE[verdict]

  const title =
    `Win rate ${fmt(winrate)} · 95% CI ${fmt(ciLow)}–${fmt(ciHigh)}` +
    (globalRate !== null ? ` · global baseline ${fmt(globalRate)}` : ' · no baseline')

  return (
    <div className="interval">
      <div className={`interval-bar tone-${tone}`} title={title}>
        <div className="interval-track" />
        {globalRate !== null && (
          <div className="interval-baseline" style={{ left: pct(globalRate) }} />
        )}
        <div
          className="interval-whisker"
          style={{ left: pct(ciLow), width: pct(ciHigh - ciLow) }}
        >
          <span className="cap cap-low" />
          <span className="cap cap-high" />
        </div>
        {winrate !== null && (
          <div className="interval-point" style={{ left: pct(winrate) }} />
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
