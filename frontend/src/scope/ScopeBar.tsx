import { useEras } from '../api/queries'
import type { Era } from '../api/types'
import { scopeLabel } from './scopeLabel'
import { FULL_BADGE_MAX, FULL_BADGE_MIN, useScope } from './useScope'

export function ScopeBar() {
  const { scope, update } = useScope()
  const eras = useEras()
  const eraList: Era[] = eras.data?.eras ?? []

  const toggleEra = (id: number) => {
    const set = new Set(scope.eraIds)
    if (set.has(id)) set.delete(id)
    else set.add(id)
    update({ eraIds: [...set].sort((a, b) => a - b) })
  }

  return (
    <div className="scope-bar">
      <div className="scope-controls">
        <div className="scope-control">
          <span className="scope-label">Era</span>
          <div className="era-toggles">
            {eraList.length === 0 ? (
              <span className="muted">All eras</span>
            ) : (
              eraList.map((e) => (
                <button
                  key={e.era_id}
                  type="button"
                  className={scope.eraIds.includes(e.era_id) ? 'chip active' : 'chip'}
                  onClick={() => toggleEra(e.era_id)}
                >
                  {e.label}
                </button>
              ))
            )}
          </div>
        </div>

        <div className="scope-control">
          <span className="scope-label">
            Rank range (badge {scope.badgeMin}–{scope.badgeMax})
          </span>
          <div className="range-row">
            <input
              type="range"
              min={FULL_BADGE_MIN}
              max={FULL_BADGE_MAX}
              value={scope.badgeMin}
              aria-label="Minimum badge"
              onChange={(e) =>
                update({ badgeMin: Math.min(Number(e.target.value), scope.badgeMax) })
              }
            />
            <input
              type="range"
              min={FULL_BADGE_MIN}
              max={FULL_BADGE_MAX}
              value={scope.badgeMax}
              aria-label="Maximum badge"
              onChange={(e) =>
                update({ badgeMax: Math.max(Number(e.target.value), scope.badgeMin) })
              }
            />
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
        {scopeLabel(scope, eraList)}
      </div>
    </div>
  )
}
