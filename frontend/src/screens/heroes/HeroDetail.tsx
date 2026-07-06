import { useItems, useLaning, useMatchups, usePerformance } from '../../api/queries'
import type { HeroRecord, PerformanceRow } from '../../api/types'
import { HeroIcon } from '../../components/HeroIcon'
import { IntervalBar } from '../../components/IntervalBar'
import { MetricScopeBlock } from '../../components/MetricScopeBlock'
import { ProvisionalBadge } from '../../components/ProvisionalBadge'
import { QueryBoundary } from '../../components/QueryBoundary'
import { SampleSize } from '../../components/SampleSize'
import { VerdictBadge } from '../../components/VerdictBadge'
import type { Scope } from '../../scope/useScope'
import { ItemTable } from './ItemTable'
import { MatchupTable } from './MatchupTable'

// One hero's full report: header record card, then matchups on that hero, item
// verdicts, laning numbers, and per-hero performance. Every section reuses the
// endpoint the old standalone screen used, narrowed to this hero — matchups and
// items via the hero_id scope param, laning and performance by keeping only
// this hero's row from the account-wide response (the same client filter the
// old screens applied for the "My hero" selection).
export function HeroDetail({
  scope,
  heroId,
  record,
}: {
  scope: Scope
  heroId: number
  record: HeroRecord | undefined
}) {
  const heroScope: Scope = { ...scope, heroId }
  const heroName = record?.hero_name ?? `Hero ${heroId}`

  const matchups = useMatchups(heroScope)
  const items = useItems(heroScope)
  const laning = useLaning(heroScope)
  const performance = usePerformance(heroScope)

  const onlyHero = (rows: PerformanceRow[]) => rows.filter((row) => row.hero_id === heroId)

  return (
    <div className="hero-detail">
      <section className="card hero-detail-head">
        <div className="hero-detail-title">
          <HeroIcon name={heroName} url={record?.hero_image_url ?? null} />
          <h2 className="card-title">{heroName}</h2>
          {record && <ProvisionalBadge show={record.provisional} />}
        </div>
        {record ? (
          <div className="hero-detail-record">
            <SampleSize games={record.games} wins={record.wins} />
            <IntervalBar
              winrate={record.winrate}
              ciLow={record.ci_low}
              ciHigh={record.ci_high}
              globalRate={record.global_rate}
              verdict={record.verdict}
            />
            <VerdictBadge verdict={record.verdict} games={record.games} />
          </div>
        ) : (
          <p className="muted">No record for this hero at the current scope.</p>
        )}
        <p className="muted hero-detail-note">
          The gold marker is your overall win rate at this scope — the record is
          judged against your usual self, not the field.
        </p>
      </section>

      <section className="card">
        <h2 className="card-title">Matchups on {heroName}</h2>
        <p className="muted improve-hint">
          Your win rate against each enemy hero while playing {heroName}, vs the
          global baseline. Color marks a confirmed verdict only.
        </p>
        <QueryBoundary query={matchups}>
          {(rows) =>
            rows.length === 0 ? (
              <p className="muted">No matchups on this hero in the current scope.</p>
            ) : (
              <MatchupTable rows={rows} />
            )
          }
        </QueryBoundary>
      </section>

      <section className="card">
        <h2 className="card-title">Items</h2>
        <p className="muted improve-hint">
          Win rate with each item on {heroName}, plus how your purchase timing
          compares to everyone else — the timing column is independent of win rate.
        </p>
        <QueryBoundary query={items}>
          {(rows) =>
            rows.length === 0 ? (
              <p className="muted">No items on this hero in the current scope.</p>
            ) : (
              <ItemTable rows={rows} />
            )
          }
        </QueryBoundary>
      </section>

      <section className="card">
        <h2 className="card-title">Laning</h2>
        <p className="muted improve-hint">
          Net worth, last hits, denies, and deaths to your lane opponent at the
          10-minute mark, vs the live baseline of everyone else at this scope.
        </p>
        <QueryBoundary query={laning}>
          {(rows) => {
            const shown = onlyHero(rows)
            return shown.length === 0 ? (
              <p className="muted">No laning data for this hero in the current scope.</p>
            ) : (
              shown.map((row) => <MetricScopeBlock key={row.hero_id ?? 'all'} row={row} />)
            )
          }}
        </QueryBoundary>
      </section>

      <section className="card">
        <h2 className="card-title">Performance</h2>
        <p className="muted improve-hint">
          Per-game numbers — net worth per minute, KDA, damage, healing — vs the
          live baseline. A lower-is-better metric like deaths reads as a strength
          when you beat the field.
        </p>
        <QueryBoundary query={performance}>
          {(data) => {
            const shown = onlyHero(data.rows)
            return shown.length === 0 ? (
              <p className="muted">No performance data for this hero in the current scope.</p>
            ) : (
              shown.map((row) => <MetricScopeBlock key={row.hero_id ?? 'all'} row={row} />)
            )
          }}
        </QueryBoundary>
      </section>
    </div>
  )
}
