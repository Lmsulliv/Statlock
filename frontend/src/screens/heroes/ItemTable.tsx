import { useMemo, useState } from 'react'
import type { ItemRow } from '../../api/types'
import { BaselineCell, DeltaCell } from '../../components/cells'
import { HeroIcon } from '../../components/HeroIcon'
import { IntervalBar } from '../../components/IntervalBar'
import { SampleSize } from '../../components/SampleSize'
import { VERDICT_ORDER } from '../../components/verdict'
import { VerdictBadge } from '../../components/VerdictBadge'

// Purchase-timing delta is plain seconds (personal avg minus global avg). Render
// it as a m:ss clock plus a direction word, since "3:40 later than average" is
// far more actionable than a signed number. It's independent of win rate.
function fmtTiming(s: number | null): string {
  if (s === null) return '—'
  if (Math.round(s) === 0) return 'on avg'
  const total = Math.round(Math.abs(s))
  const clock = `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
  return `${clock} ${s > 0 ? 'later' : 'earlier'}`
}

type SortKey = 'name' | 'games' | 'winrate' | 'global' | 'raw_delta' | 'delta' | 'timing' | 'verdict'

interface Column {
  key: SortKey
  label: string
  title?: string
  // A second sortable value sharing this column's cell, for a figure rendered
  // on the interval bar rather than in a column of its own.
  also?: { key: SortKey; label: string; title: string }
}

// Same column shape as the matchup table, plus the extra purchase-timing column.
// Titles explain the columns that need interpreting. The two delta columns fold
// into the win-rate bar exactly as they do there — see MatchupTable.
const COLUMNS: Column[] = [
  { key: 'name', label: 'Item' },
  { key: 'games', label: 'Record' },
  {
    key: 'winrate',
    label: 'Win rate & 95% CI',
    also: {
      key: 'delta',
      label: 'Adj. Δ',
      title:
        'Shrinkage-adjusted rate minus global; thin samples are pulled toward the baseline first. Shown at the end of the bar.',
    },
  },
  {
    key: 'global',
    label: 'Global baseline',
    title: 'The baseline win rate with this item across all tracked games at this scope.',
  },
  {
    key: 'timing',
    label: 'Purchase timing',
    title:
      'Your average purchase time minus the global average, capturing when you buy this item independent of its win rate.',
  },
  {
    key: 'verdict',
    label: 'Verdict',
    title:
      'Weighs sample size and confidence, so a thin sample reads as inconclusive until a well-supported gap earns a verdict.',
  },
]

// The plain global delta no longer has a column, so the bar's tooltip states it.
const rawDeltaTip = (raw: number | null) =>
  raw === null
    ? 'No global baseline at this scope, so there is no plain difference to state.'
    : `Global Δ ${raw > 0 ? '+' : ''}${Math.round(raw * 100)} pts (your rate minus the baseline, unadjusted).`

function sortValue(r: ItemRow, key: SortKey): number | string | null {
  switch (key) {
    case 'name':
      return r.item_name.toLowerCase()
    case 'games':
      return r.games
    case 'winrate':
      return r.winrate
    case 'global':
      return r.global_rate
    case 'raw_delta':
      return r.raw_delta
    case 'delta':
      return r.delta
    case 'timing':
      return r.purchase_timing_delta_s
    case 'verdict':
      return VERDICT_ORDER[r.verdict]
  }
}

// The sortable per-hero item table, extracted from the old Items screen so the
// hero detail view renders it as one section. Sort state lives inside, like
// MatchupTable.
export function ItemTable({ rows }: { rows: ItemRow[] }) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({
    key: 'name',
    dir: 'asc',
  })

  const onHeader = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'name' ? 'asc' : 'desc' },
    )

  const sorted = useMemo(() => {
    const out = [...rows]
    out.sort((a, b) => {
      const va = sortValue(a, sort.key)
      const vb = sortValue(b, sort.key)
      // Nulls (no baseline / no rate / no timing) always sink to the bottom.
      if (va === null && vb === null) return 0
      if (va === null) return 1
      if (vb === null) return -1
      const cmp =
        typeof va === 'string' && typeof vb === 'string'
          ? va.localeCompare(vb)
          : va < vb
            ? -1
            : va > vb
              ? 1
              : 0
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return out
  }, [rows, sort])

  return (
    <div className="table-scroll">
    <table className="data-table wide-table">
      <thead>
        <tr>
          {COLUMNS.map((c) => {
            const active = sort.key === c.key
            const alsoActive = c.also !== undefined && sort.key === c.also.key
            const caret = sort.dir === 'asc' ? '▲' : '▼'
            return (
              <th
                key={c.key}
                title={c.title}
                aria-sort={
                  active || alsoActive
                    ? sort.dir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                }
                className={
                  (c.key === 'name' ? 'col-hero ' : '') +
                  (c.key === 'winrate' ? 'col-interval ' : '') +
                  'th-sortable' +
                  (active || alsoActive ? ' active' : '')
                }
                onClick={() => onHeader(c.key)}
              >
                {c.label}
                <span className="sort-caret">{active ? caret : ''}</span>
                {/* Second sort key in the same cell, for the value drawn on the
                    bar. stopPropagation so it doesn't also trigger the th's own
                    sort, which wraps the rest of the cell. */}
                {c.also && (
                  <span
                    className={alsoActive ? 'th-split active' : 'th-split'}
                    title={c.also.title}
                    onClick={(e) => {
                      e.stopPropagation()
                      onHeader(c.also!.key)
                    }}
                  >
                    · {c.also.label}
                    <span className="sort-caret">{alsoActive ? caret : ''}</span>
                  </span>
                )}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.item_id}>
            <td className="col-hero">
              {/* HeroIcon is a generic name+url icon; reused here for item art. */}
              <span className="enemy-cell">
                <HeroIcon name={r.item_name} url={r.item_image_url} />
                {r.item_name}
              </span>
            </td>
            <td>
              <SampleSize games={r.games} wins={r.wins} />
            </td>
            <td className="col-interval">
              <IntervalBar
                winrate={r.winrate}
                ciLow={r.ci_low}
                ciHigh={r.ci_high}
                globalRate={r.global_rate}
                verdict={r.verdict}
                deltaLabel={<DeltaCell value={r.delta} games={r.games} />}
                extraTip={rawDeltaTip(r.raw_delta)}
              />
            </td>
            <td>
              <BaselineCell rate={r.global_rate} matches={r.global_matches} />
            </td>
            <td className="col-delta">{fmtTiming(r.purchase_timing_delta_s)}</td>
            <td>
              <VerdictBadge verdict={r.verdict} games={r.games} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}
