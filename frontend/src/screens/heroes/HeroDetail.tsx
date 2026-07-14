import {
  useHeroSkillOrder,
  useItems,
  useLaning,
  useMatchups,
  usePerformance,
} from '../../api/queries'
import type { HeroRecord, PerformanceRow, SkillFacts } from '../../api/types'
import { HeroIcon } from '../../components/HeroIcon'
import { InfoTip } from '../../components/InfoTip'
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
  const skillOrder = useHeroSkillOrder(heroScope)
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
        <h2 className="card-title">
          Skill order{' '}
          <InfoTip tip="Descriptive — what you leveled and when, across your own games on this hero. There's no baseline for skill order, so this is a record of what you did, not a verdict.">
            <span className="skill-info">What's this?</span>
          </InfoTip>
        </h2>
        <p className="muted improve-hint">
          Your most common opening (first four points) and the ability you max
          first on {heroName}, split by wins and losses once each side has enough
          games.
        </p>
        <QueryBoundary query={skillOrder}>
          {(data) =>
            data.games === 0 ? (
              <p className="muted">No ability data for this hero in the current scope.</p>
            ) : (
              <div className="skill-order">
                <div className="skill-block">
                  <div className="skill-block-head">
                    <span className="skill-scope-label">All games</span>
                    <SampleSize games={data.games} />
                  </div>
                  <SkillFactsView facts={data} />
                </div>
                {data.split ? (
                  <>
                    <div className="skill-block">
                      <div className="skill-block-head">
                        <span className="skill-scope-label">In wins</span>
                        <SampleSize games={data.split.wins.games} />
                      </div>
                      <SkillFactsView facts={data.split.wins} />
                    </div>
                    <div className="skill-block">
                      <div className="skill-block-head">
                        <span className="skill-scope-label">In losses</span>
                        <SampleSize games={data.split.losses.games} />
                      </div>
                      <SkillFactsView facts={data.split.losses} />
                    </div>
                  </>
                ) : (
                  <p className="muted skill-split-note">
                    Wins-vs-losses split appears once you have enough games on each
                    side.
                  </p>
                )}
              </div>
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

// The two descriptive skill-order facts (opening sequence + first-maxed) for one
// set of games. Raw counts, no verdict — just what you did, and in how many games.
function SkillFactsView({ facts }: { facts: SkillFacts }) {
  return (
    <div className="skill-facts">
      <div className="skill-line">
        <span className="skill-fact-label">Opening (first 4)</span>
        {facts.opening ? (
          <>
            <span className="skill-seq">
              {facts.opening.sequence.map((a, i) => (
                <span key={i} className="skill-ability">
                  <HeroIcon name={a.ability_name} url={a.image_url} />
                  <span className="skill-ability-name">{a.ability_name}</span>
                  {i < facts.opening!.sequence.length - 1 && (
                    <span className="skill-arrow" aria-hidden="true">→</span>
                  )}
                </span>
              ))}
            </span>
            <span className="muted skill-count">
              {facts.opening.games} of {facts.opening.considered} games
            </span>
          </>
        ) : (
          <span className="muted">Not enough points recorded.</span>
        )}
      </div>
      <div className="skill-line">
        <span className="skill-fact-label">First maxed</span>
        {facts.first_maxed ? (
          <>
            <span className="skill-ability">
              <HeroIcon
                name={facts.first_maxed.ability.ability_name}
                url={facts.first_maxed.ability.image_url}
              />
              <span className="skill-ability-name">
                {facts.first_maxed.ability.ability_name}
              </span>
            </span>
            <span className="muted skill-count">
              {facts.first_maxed.games} of {facts.first_maxed.considered} games
            </span>
          </>
        ) : (
          <span className="muted">No ability reached level 4.</span>
        )}
      </div>
    </div>
  )
}
