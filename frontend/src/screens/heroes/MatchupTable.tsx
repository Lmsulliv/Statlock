import { useMemo, useState } from 'react'
import type { MatchupRow } from '../../api/types'
import { BaselineCell, DeltaCell, KillCountCell } from '../../components/cells'
import { HeroIcon } from '../../components/HeroIcon'
import { IntervalBar } from '../../components/IntervalBar'
import { SampleSize } from '../../components/SampleSize'
import { VERDICT_ORDER } from '../../components/verdict'
import { VerdictBadge } from '../../components/VerdictBadge'

type SortKey =
  | 'name'
  | 'games'
  | 'kills_on_you'
  | 'your_kills'
  | 'winrate'
  | 'global'
  | 'raw_delta'
  | 'delta'
  | 'verdict'

interface Column {
  key: SortKey
  label: string
  title?: string
  // A second sortable value sharing this column's cell, for a figure rendered
  // on the interval bar rather than in a column of its own.
  also?: { key: SortKey; label: string; title: string }
}

// Order matches the table; every column is sortable. Titles explain the
// columns that need interpreting (rule: don't make the reader guess).
//
// The two delta columns are gone as columns: the adjusted delta is drawn at the
// end of the win-rate bar (same number, one fewer column to scroll past) and
// keeps its sort via this column's `also`; the plain delta moved into the bar's
// tooltip, since the baseline column beside it already shows what it's measured
// from. Nine columns forced a horizontal scroll at every viewport.
const COLUMNS: Column[] = [
  { key: 'name', label: 'Enemy hero' },
  { key: 'games', label: 'Record' },
  {
    key: 'kills_on_you',
    label: 'Kills on you',
    title:
      'Raw kills this enemy hero landed on you, summed across every game faced.',
  },
  {
    key: 'your_kills',
    label: 'Your kills',
    title:
      'Raw kills you landed on this enemy hero, summed across every game faced.',
  },
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
    title: 'The baseline win rate across all tracked games at this scope.',
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

function sortValue(r: MatchupRow, key: SortKey): number | string | null {
  switch (key) {
    case 'name':
      return r.enemy_hero_name.toLowerCase()
    case 'games':
      return r.games
    case 'kills_on_you':
      return r.kills_by_them_on_you
    case 'your_kills':
      return r.kills_by_you_on_them
    case 'winrate':
      return r.winrate
    case 'global':
      return r.global_rate
    case 'raw_delta':
      return r.raw_delta
    case 'delta':
      return r.delta
    case 'verdict':
      return VERDICT_ORDER[r.verdict]
  }
}

// The sortable matchup table, extracted from the old Matchups screen so both
// the "All heroes" grid and a hero's detail section render the same thing. The
// sort state lives inside: it resets when the table unmounts, exactly like the
// old per-screen state did.
export function MatchupTable({ rows }: { rows: MatchupRow[] }) {
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
      // Nulls (no baseline / no rate) always sink to the bottom, both directions.
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
          <tr key={r.enemy_hero_id}>
            <td className="col-hero">
              <span className="enemy-cell">
                <HeroIcon name={r.enemy_hero_name} url={r.enemy_hero_image_url} />
                {r.enemy_hero_name}
              </span>
            </td>
            <td>
              <SampleSize games={r.games} wins={r.wins} />
            </td>
            <td className="col-delta">
              <KillCountCell value={r.kills_by_them_on_you} games={r.games} />
            </td>
            <td className="col-delta">
              <KillCountCell value={r.kills_by_you_on_them} games={r.games} />
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
