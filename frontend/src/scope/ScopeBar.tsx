import { useEras, usePlayedHeroes, useRanks } from '../api/queries'
import type { Era, PlayedHero, Rank } from '../api/types'
import { RankRange } from '../components/RankRange'
import { scopeLabel } from './scopeLabel'
import { useScope } from './useScope'

export function ScopeBar() {
  const { scope, update } = useScope()
  const eras = useEras()
  const heroesQuery = usePlayedHeroes(scope)
  const ranksQuery = useRanks()
  const eraList: Era[] = eras.data?.eras ?? []
  const heroList: PlayedHero[] = heroesQuery.data ?? []
  const rankList: Rank[] = ranksQuery.data ?? []

  return (
    <div className="scope-bar">
      <div className="scope-controls">
        <div className="scope-control">
          <span className="scope-label">My hero</span>
          <select
            className="select-input"
            value={scope.heroId ?? ''}
            onChange={(e) =>
              update({ heroId: e.target.value === '' ? null : Number(e.target.value) })
            }
          >
            <option value="">All my heroes</option>
            {heroList.map((h) => (
              <option key={h.hero_id} value={h.hero_id}>
                {h.name}
              </option>
            ))}
          </select>
        </div>

        <div className="scope-control">
          <span className="scope-label">Era</span>
          <select
            className="select-input"
            value={scope.eraIds[0] ?? ''}
            onChange={(e) =>
              update({ eraIds: e.target.value === '' ? [] : [Number(e.target.value)] })
            }
          >
            <option value="">All eras</option>
            {eraList.map((era) => (
              <option key={era.era_id} value={era.era_id}>
                {era.label}
              </option>
            ))}
          </select>
        </div>

        <div className="scope-control">
          <span className="scope-label">Rank range</span>
          <RankRange
            ranks={rankList}
            badgeMin={scope.badgeMin}
            badgeMax={scope.badgeMax}
            onChange={(badgeMin, badgeMax) => update({ badgeMin, badgeMax })}
          />
        </div>

        <div className="scope-control">
          <span className="scope-label">Lane</span>
          <div className="toggle" role="group" aria-label="Lane view">
            <button
              type="button"
              className={scope.inLane ? 'toggle-opt' : 'toggle-opt active'}
              onClick={() => update({ inLane: false })}
            >
              Overall
            </button>
            <button
              type="button"
              className={scope.inLane ? 'toggle-opt active' : 'toggle-opt'}
              onClick={() => update({ inLane: true })}
            >
              In-lane
            </button>
          </div>
        </div>

        <div className="scope-control">
          <span className="scope-label">Min games</span>
          <input
            type="number"
            min={1}
            className="num-input"
            value={scope.minGames}
            onChange={(e) => update({ minGames: Math.max(1, Number(e.target.value) || 1) })}
          />
        </div>

        <div className="scope-control">
          <span className="scope-label">Mode</span>
          <select
            className="select-input"
            value={scope.gameMode}
            onChange={(e) => update({ gameMode: e.target.value })}
          >
            <option value="1">Normal</option>
            <option value="4">Street Brawl</option>
          </select>
        </div>
      </div>

      <div className="scope-active" title="The exact scope these numbers describe">
        {scopeLabel(scope, eraList, heroList, rankList)}
      </div>
      <div className="scope-note">
        In-lane keeps only the enemies in your lane pairing and compares against
        the same-lane baseline; overall counts all five opponents.
      </div>
    </div>
  )
}
