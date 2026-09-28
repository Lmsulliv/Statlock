import type { ReactNode } from 'react'
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
import { QueryBoundary } from '../../components/QueryBoundary'
import { SampleSize } from '../../components/SampleSize'
import { SectionNav, type SectionNavItem } from '../../components/SectionNav'
import { VERDICT_TONE, verdictLabel, verdictTip } from '../../components/verdict'
import type { Scope } from '../../scope/useScope'
import { ItemTable } from './ItemTable'
import { MatchupTable } from './MatchupTable'

// One hero's full report: header record card, then matchups on that hero, item
// verdicts, skill order, laning numbers, and per-hero performance. Every section
// reuses the endpoint the old standalone screen used, narrowed to this hero —
// matchups and items via the hero_id scope param, laning and performance by
// keeping only this hero's row from the account-wide response (the same client
// filter the old screens applied for the "My hero" selection).
//
// The page is long, so it carries the shared SectionNav rail (a sticky anchor
// list with scroll-spy, as on Insights and MatchDetail). The sections below are
// ONE list: the rail's entries and the rendered cards both come from
// HERO_SECTIONS, so a section can't exist in one and not the other.

// Everything a section body needs. Passed whole rather than as loose props so
// adding a section never means threading a new argument through the list.
interface HeroCtx {
  scope: Scope
  heroId: number
  heroName: string
  record: HeroRecord | undefined
}

interface HeroSection {
  id: string
  // Short form, for the rail. The card can state a longer heading via `heading`.
  title: string
  heading?: (c: HeroCtx) => ReactNode
  hint?: (c: HeroCtx) => ReactNode
  body: (c: HeroCtx) => ReactNode
}

// The single source for both the rail and the rendered stack. Each entry owns
// its own body, so turning these into sub-tabs later is rendering the active
// entry instead of mapping them all — no section body moves.
const HERO_SECTIONS: HeroSection[] = [
  {
    id: 'hero-record',
    title: 'Record',
    heading: (c) => (
      <span className="enemy-cell">
        <HeroIcon name={c.heroName} url={c.record?.hero_image_url ?? null} />
        {c.heroName}
      </span>
    ),
    body: (c) => <RecordBody {...c} />,
  },
  {
    id: 'hero-matchups',
    title: 'Matchups',
    heading: (c) => `Matchups on ${c.heroName}`,
    hint: (c) => (
      <>
        Your win rate against each enemy hero while playing {c.heroName}, vs the
        global baseline. Color marks a confirmed verdict only.
      </>
    ),
    body: (c) => <MatchupsBody {...c} />,
  },
  {
    id: 'hero-items',
    title: 'Items',
    hint: (c) => (
      <>
        Win rate with each item on {c.heroName}, plus how your purchase timing
        compares to everyone else — the timing column is independent of win rate.
      </>
    ),
    body: (c) => <ItemsBody {...c} />,
  },
  {
    id: 'hero-skills',
    title: 'Skill order',
    heading: () => (
      <>
        Skill order{' '}
        <InfoTip tip="Descriptive — what you leveled and when, across your own games on this hero. There's no baseline for skill order, so this is a record of what you did, not a verdict.">
          <span className="skill-info">What's this?</span>
        </InfoTip>
      </>
    ),
    hint: (c) => (
      <>
        Your most common opening (first four points) and the ability you max
        first on {c.heroName}, split by wins and losses once each side has enough
        games.
      </>
    ),
    body: (c) => <SkillsBody {...c} />,
  },
  {
    id: 'hero-laning',
    title: 'Laning',
    hint: () => (
      <>
        Net worth, last hits, denies, and deaths to your lane opponent at the
        10-minute mark, vs the live baseline of everyone else at this scope.
      </>
    ),
    body: (c) => <LaningBody {...c} />,
  },
  {
    id: 'hero-performance',
    title: 'Performance',
    hint: () => (
      <>
        Per-game numbers — net worth per minute, KDA, damage, healing — vs the
        live baseline. A lower-is-better metric like deaths reads as a strength
        when you beat the field.
      </>
    ),
    body: (c) => <PerformanceBody {...c} />,
  },
]

// Derived, not a second hand-written list — and module-level, so the identity is
// stable across renders and SectionNav's IntersectionObserver isn't rebuilt on
// every one.
const HERO_NAV: SectionNavItem[] = HERO_SECTIONS.map(({ id, title }) => ({ id, title }))

export function HeroDetail({
  scope,
  heroId,
  record,
}: {
  scope: Scope
  heroId: number
  record: HeroRecord | undefined
}) {
  const ctx: HeroCtx = {
    scope,
    heroId,
    heroName: record?.hero_name ?? `Hero ${heroId}`,
    record,
  }

  return (
    <div className="sectionnav-layout">
      <div className="sectionnav-content">
        {HERO_SECTIONS.map((s) => (
          <section key={s.id} id={s.id} className="card anchor-section">
            <h2 className="card-title">{s.heading ? s.heading(ctx) : s.title}</h2>
            {s.hint && <p className="muted improve-hint">{s.hint(ctx)}</p>}
            {s.body(ctx)}
          </section>
        ))}
      </div>
      <SectionNav sections={HERO_NAV} />
    </div>
  )
}

// The hero's own record. The verdict leads here in full — label plus the
// sentence explaining why it reads that way — because the hero list no longer
// carries a verdict chip: a 48-character "still deepening" chip and a nowrap
// verdict badge can't fit a 280px column, so both moved to where there's room
// to state them properly.
function RecordBody({ record }: HeroCtx) {
  if (!record) {
    return <p className="muted">No record for this hero at the current scope.</p>
  }
  return (
    <>
      <div className="hero-detail-record">
        <SampleSize games={record.games} wins={record.wins} />
        <IntervalBar
          winrate={record.winrate}
          ciLow={record.ci_low}
          ciHigh={record.ci_high}
          globalRate={record.global_rate}
          verdict={record.verdict}
        />
      </div>
      <div className={`hero-verdict tone-${VERDICT_TONE[record.verdict]}`}>
        <span className="hero-verdict-label">
          {verdictLabel(record.verdict, record.games)}
        </span>
        <span className="hero-verdict-why">
          {verdictTip(record.verdict, record.games)}
        </span>
      </div>
      {record.provisional && (
        <p className="muted hero-detail-provisional">
          Early results, deepening as matches are analyzed — some of your games on
          this hero are still being ingested, so these numbers will improve.
        </p>
      )}
      <p className="muted hero-detail-note">
        The gold marker is your overall win rate at this scope,
        judged against your usual stats, not the field's.
      </p>
    </>
  )
}

function MatchupsBody({ scope, heroId }: HeroCtx) {
  const matchups = useMatchups({ ...scope, heroId })
  return (
    <QueryBoundary query={matchups}>
      {(rows) =>
        rows.length === 0 ? (
          <p className="muted">No matchups on this hero in the current scope.</p>
        ) : (
          <MatchupTable rows={rows} />
        )
      }
    </QueryBoundary>
  )
}

function ItemsBody({ scope, heroId }: HeroCtx) {
  const items = useItems({ ...scope, heroId })
  return (
    <QueryBoundary query={items}>
      {(rows) =>
        rows.length === 0 ? (
          <p className="muted">No items on this hero in the current scope.</p>
        ) : (
          <ItemTable rows={rows} />
        )
      }
    </QueryBoundary>
  )
}

function SkillsBody({ scope, heroId }: HeroCtx) {
  const skillOrder = useHeroSkillOrder({ ...scope, heroId })
  return (
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
  )
}

function LaningBody({ scope, heroId }: HeroCtx) {
  const laning = useLaning({ ...scope, heroId })
  return (
    <QueryBoundary query={laning}>
      {(rows) => {
        const shown = onlyHero(rows, heroId)
        return shown.length === 0 ? (
          <p className="muted">No laning data for this hero in the current scope.</p>
        ) : (
          // Every laning metric is read at the same lane-end mark, so the
          // qualifier is stated once in the column header instead of on all four
          // rows; and the per-metric sample always equals the block's game count
          // here (a match either has a lane-end snapshot or has no row at all),
          // so the Sample column would only restate the number beside the hero.
          shown.map((row) => (
            <MetricScopeBlock
              key={row.hero_id ?? 'all'}
              row={row}
              qualifier="@ lane end"
              showSample={false}
            />
          ))
        )
      }}
    </QueryBoundary>
  )
}

function PerformanceBody({ scope, heroId }: HeroCtx) {
  const performance = usePerformance({ ...scope, heroId })
  return (
    <QueryBoundary query={performance}>
      {(data) => {
        const shown = onlyHero(data.rows, heroId)
        return shown.length === 0 ? (
          <p className="muted">No performance data for this hero in the current scope.</p>
        ) : (
          // Sample column kept here, unlike Laning: these metrics are dropped
          // per-metric when NULL, so a hero can have 56 games but only 29 with
          // damage recorded. Hiding the column would let a verdict look like it
          // rests on the hero's full game count when it doesn't.
          shown.map((row) => <MetricScopeBlock key={row.hero_id ?? 'all'} row={row} />)
        )
      }}
    </QueryBoundary>
  )
}

const onlyHero = (rows: PerformanceRow[], heroId: number) =>
  rows.filter((row) => row.hero_id === heroId)

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
