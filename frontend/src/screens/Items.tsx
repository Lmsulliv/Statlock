import { useMemo, useState } from 'react'
import { useItems, usePlayedHeroes, useSyncStatus } from '../api/queries'
import type { ItemRow } from '../api/types'
import { BaselineCell, DeltaCell } from '../components/cells'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { IntervalBar } from '../components/IntervalBar'
import { QueryBoundary } from '../components/QueryBoundary'
import { SampleSize } from '../components/SampleSize'
import { VERDICT_ORDER } from '../components/verdict'
import { VerdictBadge } from '../components/VerdictBadge'
import { useScope } from '../scope/useScope'

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
}

// Same column shape as Matchups, plus the extra purchase-timing column. Titles
// explain the columns that need interpreting (rule: don't make the reader guess).
const COLUMNS: Column[] = [
  { key: 'name', label: 'Item' },
  { key: 'games', label: 'Record' },
  { key: 'winrate', label: 'Win rate & 95% CI' },
  {
    key: 'global',
    label: 'Global baseline',
    title: 'The baseline win rate with this item across all tracked games at this scope.',
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
    key: 'timing',
    label: 'Purchase timing',
    title:
      'Your average purchase time minus the global average — when you buy this, not whether it wins.',
  },
  {
    key: 'verdict',
    label: 'Verdict',
    title:
      'Weighs sample size and confidence, not raw win rate — a thin sample reads as inconclusive, not a strong call.',
  },
]

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

export function Items() {
  const { scope } = useScope()
  const items = useItems(scope)
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
      <h1 className="screen-title">Items</h1>
      <p className="screen-sub">
        One row per item for the hero you’ve picked in the scope bar, sorted A→Z
        by default — click any header to re-sort. Win rate shows as a 95%
        confidence interval, never a bare percentage; color marks a confirmed
        verdict, not a big number. The purchase-timing column is independent of
        win rate — it’s when you buy the item versus everyone else.
      </p>
      {scope.heroId === null ? (
        <ItemsNoHero />
      ) : (
        <QueryBoundary query={items}>
          {(rows) =>
            rows.length === 0 ? (
              <ItemsEmpty />
            ) : (
              <ItemsTable rows={rows} sort={sort} onHeader={onHeader} />
            )
          }
        </QueryBoundary>
      )}
    </section>
  )
}

function ItemsTable({
  rows,
  sort,
  onHeader,
}: {
  rows: ItemRow[]
  sort: { key: SortKey; dir: 'asc' | 'desc' }
  onHeader: (key: SortKey) => void
}) {
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
            <td className="col-delta">{fmtTiming(r.purchase_timing_delta_s)}</td>
            <td>
              <VerdictBadge verdict={r.verdict} games={r.games} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// hero_id is required for items, so when no hero is picked we don't fetch — we
// point the user at the "My hero" control in the scope bar (rule: empty states
// explain themselves). usePlayedHeroes tells us how many heroes are available.
function ItemsNoHero() {
  const { scope } = useScope()
  const heroes = usePlayedHeroes(scope)
  const count = heroes.data?.length ?? 0
  return (
    <EmptyState title="Pick a hero to see item stats.">
      <p>
        Items are per-hero, so choose one with the <strong>My hero</strong>{' '}
        selector in the scope bar above.
        {count > 0 && ` You’ve played ${count} hero${count === 1 ? '' : 'es'} on this account.`}
      </p>
    </EmptyState>
  )
}

// Hero chosen, but no item meets the current scope. Explain why using live sync
// counts rather than a blank table (presentation rule 5).
function ItemsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No items to show for this hero yet.">
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
                Matches are ingested, but no item on this hero falls in the
                current scope. Try widening the rank range or switching the era
                or game mode.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
