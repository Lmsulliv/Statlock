import { useHeroRecords, useSyncStatus } from '../../api/queries'
import type { HeroRecord } from '../../api/types'
import { AnalysisUnlockProgress } from '../../components/AnalysisUnlockProgress'
import { DemoProfileLink } from '../../components/DemoProfileLink'
import { EmptyState } from '../../components/EmptyState'
import { ModeNotice } from '../../components/ModeNotice'
import { QueryBoundary } from '../../components/QueryBoundary'
import { GAME_MODE_NORMAL, useScope, type Scope } from '../../scope/useScope'
import { AllHeroesGrid } from './AllHeroesGrid'
import { HeroDetail } from './HeroDetail'
import { HeroList } from './HeroList'

// The Heroes tab: a played-heroes list beside a per-hero detail (matchups,
// items, laning, performance), merging the old Matchups and Items tabs.
// /heroes/all renders the aggregate matchup grid instead of a hero detail.
export function Heroes({ allHeroes = false }: { allHeroes?: boolean }) {
  const { scope } = useScope()
  // Every section compares against Normal-population baselines, so the whole
  // tab stays Normal-only; gating here keeps the queries from firing on Brawl.
  if (scope.gameMode !== GAME_MODE_NORMAL) {
    return (
      <section>
        <h1 className="screen-title">Heroes</h1>
        <ModeNotice />
      </section>
    )
  }
  return <HeroesBody scope={scope} allHeroes={allHeroes} />
}

function HeroesBody({ scope, allHeroes }: { scope: Scope; allHeroes: boolean }) {
  const records = useHeroRecords(scope)
  return (
    <section>
      <h1 className="screen-title">Heroes</h1>
      <p className="screen-sub">
        Every hero you play, with its record judged against your own overall win
        rate. Pick one for the full report — matchups, items, laning, and
        per-game performance on that hero — or “All heroes” for the aggregate
        matchup grid.
      </p>
      <QueryBoundary query={records}>
        {(data) =>
          data.heroes.length === 0 ? (
            <HeroesEmpty />
          ) : (
            <HeroesLayout scope={scope} heroes={data.heroes} allHeroes={allHeroes} />
          )
        }
      </QueryBoundary>
    </section>
  )
}

function HeroesLayout({
  scope,
  heroes,
  allHeroes,
}: {
  scope: Scope
  heroes: HeroRecord[]
  allHeroes: boolean
}) {
  // No hero picked -> open on the most played one, so a user with data never
  // sees an empty picker. Resolved in memory (a max over server-computed game
  // counts — presentation, not statistics); the URL only changes when the user
  // actually picks a hero.
  const mostPlayed = heroes.reduce((a, b) => (b.games > a.games ? b : a))
  const effectiveHeroId = scope.heroId ?? mostPlayed.hero_id
  const record = heroes.find((h) => h.hero_id === effectiveHeroId)

  return (
    <div className="heroes-layout">
      <HeroList heroes={heroes} activeHeroId={effectiveHeroId} allActive={allHeroes} />
      {allHeroes ? (
        <AllHeroesGrid scope={scope} />
      ) : (
        <HeroDetail scope={scope} heroId={effectiveHeroId} record={record} />
      )}
    </div>
  )
}

// No hero records at all: explain why with the live sync counts rather than a
// blank layout (presentation rule 5).
function HeroesEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No heroes to show yet.">
      <p>
        Your played heroes — each with games, win rate, a 95% confidence
        interval, and a verdict against your own overall rate — will list here,
        with a full per-hero report beside them.
      </p>
      <AnalysisUnlockProgress feature="Hero analysis" />
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
                picking another account.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
      <DemoProfileLink />
    </EmptyState>
  )
}
