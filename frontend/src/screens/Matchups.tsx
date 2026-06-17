import { useMemo, useState } from 'react'
import { useMatchups, useSyncStatus } from '../api/queries'
import type { MatchupRow } from '../api/types'
import { BaselineCell, DeltaCell } from '../components/cells'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { IntervalBar } from '../components/IntervalBar'
import { QueryBoundary } from '../components/QueryBoundary'
import { SampleSize } from '../components/SampleSize'
import { VERDICT_ORDER } from '../components/verdict'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

type SortKey = 'name' | 'games' | 'winrate' | 'global' | 'raw_delta' | 'delta' | 'verdict'

interface Column {
  key: SortKey
  label: string
  title?: string
}

// Order matches the table; every column is sortable. Titles explain the
// columns that need interpreting (rule: don't make the reader guess).
const COLUMNS: Column[] = [
  { key: 'name', label: 'Enemy hero' },
  { key: 'games', label: 'Record' },
  { key: 'winrate', label: 'Win rate & 95% CI' },
  {
    key: 'global',
    label: 'Global baseline',
    title: 'The baseline win rate across all tracked games at this scope.',
  },
  {
    key: 'raw_delta',
    label: 'Global Δ',
    title: 'Your win rate minus the global baseline (plain difference).',
  },
  {
    key: 'delta',
    label: 'Adj. Δ',
    title:
      'Shrinkage-adjusted rate minus global — thin samples are pulled toward the baseline first.',
  },
  {
    key: 'verdict',
    label: 'Verdict',
    title:
      'Weighs sample size and confidence, not raw win rate — a thin sample reads as inconclusive, not a strong call.',
  },
]

function sortValue(r: MatchupRow, key: SortKey): number | string | null {
  switch (key) {
    case 'name':
      return r.enemy_hero_name.toLowerCase()
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
    case 'verdict':
      return VERDICT_ORDER[r.verdict]
  }
}

export function Matchups() {
  const { scope } = useScope()
  const matchups = useMatchups(scope)
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

  return (
    <section>
      <h1 className="screen-title">Matchups</h1>
      <p className="screen-sub">
        One row per enemy hero, sorted A→Z by default — click any header to
        re-sort. Win rate shows as a 95% confidence interval (a bar with
        whiskers), never a bare percentage; the gold dashed line marks the global
        baseline when available, and color marks a confirmed verdict, not a big
        number.
      </p>
      <QueryBoundary query={matchups}>
        {(rows) =>
          rows.length === 0 ? (
            <MatchupsEmpty />
          ) : (
            <MatchupsTable rows={rows} sort={sort} onHeader={onHeader} />
          )
        }
      </QueryBoundary>
    </section>
  )
}

function MatchupsTable({
  rows,
  sort,
  onHeader,
}: {
  rows: MatchupRow[]
  sort: { key: SortKey; dir: 'asc' | 'desc' }
  onHeader: (key: SortKey) => void
}) {
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
    <table className="data-table">
      <thead>
        <tr>
          {COLUMNS.map((c) => {
            const active = sort.key === c.key
            return (
              <th
                key={c.key}
                title={c.title}
                aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                className={
                  (c.key === 'name' ? 'col-hero ' : '') +
                  (c.key === 'winrate' ? 'col-interval ' : '') +
                  'th-sortable' +
                  (active ? ' active' : '')
                }
                onClick={() => onHeader(c.key)}
              >
                {c.label}
                <span className="sort-caret">{active ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
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
            <td className="col-interval">
              <IntervalBar
                winrate={r.winrate}
                ciLow={r.ci_low}
                ciHigh={r.ci_high}
                globalRate={r.global_rate}
                verdict={r.verdict}
              />
            </td>
            <td>
              <BaselineCell rate={r.global_rate} matches={r.global_matches} />
            </td>
            <td className="col-delta">
              <DeltaCell value={r.raw_delta} games={r.games} />
            </td>
            <td className="col-delta">
              <DeltaCell value={r.delta} games={r.games} />
            </td>
            <td>
              <VerdictBadge verdict={r.verdict} games={r.games} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// When there are no rows, say why — using the live sync counts rather than a
// blank table (presentation rule 5).
function MatchupsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No matchups to show yet.">
      <QueryBoundary query={sync}>
        {(s) => (
          <>
            <p>
              {s.fetched.toLocaleString()} matches fetched ·{' '}
              {s.queue_depth.toLocaleString()} queued ·{' '}
              {s.unavailable.toLocaleString()} unavailable.
            </p>
            {s.fetched === 0 ? (
              <p>
                The worker hasn’t ingested any matches yet. Add an account with{' '}
                <code>python -m ingest add-account &lt;id&gt; --self</code>, run{' '}
                <code>python -m ingest run-daemon</code>, and come back in an hour.
              </p>
            ) : (
              <p>
                Matches are ingested, but none fall in the current scope. Try
                widening the rank range, switching the era or game mode, or
                setting the lane view to Overall.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
