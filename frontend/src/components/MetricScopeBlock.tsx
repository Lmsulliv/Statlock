import type { MetricField, PerformanceRow } from '../api/types'
import { MetricBaselineCell, NumberDeltaCell } from './cells'
import { HeroIcon } from './HeroIcon'
import { IntervalBar } from './IntervalBar'
import { SampleSize } from './SampleSize'
import { VerdictBadge } from './VerdictBadge'

const fmtNum = (x: number | null) =>
  x === null ? '—' : x.toLocaleString(undefined, { maximumFractionDigits: 2 })

// Linear axis for one metric's interval bar, derived from the finite numbers in
// the row. Pure layout (the API owns the statistics): padded so whiskers don't
// touch the edges, with a unit window fallback when everything collapses to one
// value (e.g. a single game, or zero variance).
function domainFor(m: MetricField): { min: number; max: number } {
  const xs = [m.mean, m.ci_low, m.ci_high, m.baseline_mean].filter(
    (x): x is number => x !== null && Number.isFinite(x),
  )
  if (xs.length === 0) return { min: 0, max: 1 }
  const min = Math.min(...xs)
  const max = Math.max(...xs)
  if (min === max) {
    const pad = Math.abs(min) || 1
    return { min: min - pad, max: max + pad }
  }
  const pad = (max - min) * 0.08
  return { min: min - pad, max: max + pad }
}

// One scope block of a continuous-metric report (Performance overall, Laning,
// or a hero's section on the Heroes screen): the "Overall" or per-hero heading
// plus a metric table of mean/CI/baseline/delta/verdict rows. Shared so the
// three callers can't drift apart — it used to live copy-pasted in both the
// Performance and Laning screens.
//
// `qualifier` hoists a suffix every metric label shares ("@ lane end") out of
// the rows and into the Metric column header, so it's stated once instead of on
// every line. `showSample` hides the per-metric sample column where it's known
// to duplicate the block's own game count.
export function MetricScopeBlock({
  row,
  qualifier,
  showSample = true,
}: {
  row: PerformanceRow
  qualifier?: string
  showSample?: boolean
}) {
  // Hoist ONLY if every metric in the block actually ends with the qualifier.
  // If a future metric is measured at a different point, its label keeps its own
  // inline qualifier and nothing is hoisted — a wrong shared heading would be a
  // silent lie about when the number was taken.
  const hoisted =
    qualifier !== undefined &&
    row.metrics.length > 0 &&
    row.metrics.every((m) => m.label.endsWith(qualifier))
  const labelOf = (m: MetricField) =>
    hoisted ? m.label.slice(0, m.label.length - qualifier.length).trimEnd() : m.label

  return (
    <div className="perf-block">
      <h2 className="perf-scope-title">
        {row.scope === 'overall' ? (
          'Overall'
        ) : (
          <span className="enemy-cell">
            <HeroIcon name={row.hero_name ?? '?'} url={row.hero_image_url} />
            {row.hero_name}
          </span>
        )}
        <span className="muted perf-scope-games">{row.games} games</span>
      </h2>
      <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>
              Metric
              {hoisted && <span className="col-qualifier"> {qualifier}</span>}
            </th>
            {showSample && <th>Sample</th>}
            <th className="col-interval">Mean &amp; 95% CI</th>
            <th>Baseline</th>
            <th className="col-delta">Δ vs baseline</th>
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {row.metrics.map((m) => (
            <MetricRow
              key={m.key}
              m={m}
              label={labelOf(m)}
              showSample={showSample}
              rowGames={row.games}
            />
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}

function MetricRow({
  m,
  label,
  showSample,
  rowGames,
}: {
  m: MetricField
  label: string
  showSample: boolean
  rowGames: number
}) {
  // With the column hidden, a metric whose sample is SMALLER than the block's
  // game count (a sparse metric the API honestly reports its own n for) still
  // says so inline. The column is only hidden where the two are known to agree,
  // and this makes that assumption self-checking rather than load-bearing.
  const oddSample = !showSample && m.games !== rowGames
  return (
    <tr>
      <td title={m.higher_is_better ? 'Higher is better' : 'Lower is better'}>
        {label}
        {oddSample && <span className="muted metric-odd-sample"> {m.games} games</span>}
      </td>
      {showSample && (
        <td>
          <SampleSize games={m.games} />
        </td>
      )}
      <td className="col-interval">
        <IntervalBar
          winrate={m.mean}
          ciLow={m.ci_low}
          ciHigh={m.ci_high}
          globalRate={m.baseline_mean}
          verdict={m.verdict}
          domain={domainFor(m)}
          format={fmtNum}
        />
      </td>
      <td>
        <MetricBaselineCell mean={m.baseline_mean} games={m.baseline_games} />
      </td>
      <td className="col-delta">
        <NumberDeltaCell value={m.delta} games={m.games} />
      </td>
      <td>
        <VerdictBadge verdict={m.verdict} games={m.games} />
      </td>
    </tr>
  )
}
