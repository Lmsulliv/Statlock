import { useAccounts, useEras, usePlayedHeroes, useRanks } from '../api/queries'
import type { Era, PlayedHero, Rank, TrackedAccount } from '../api/types'
import { HeroIcon } from '../components/HeroIcon'
import { RankRange } from '../components/RankRange'
import { scopeLabel } from './scopeLabel'
import { useScope } from './useScope'

export function ScopeBar() {
  const { scope, update } = useScope()
  const eras = useEras()
  const heroesQuery = usePlayedHeroes(scope)
  const ranksQuery = useRanks()
  const accountsQuery = useAccounts()
  const eraList: Era[] = eras.data?.eras ?? []
  const heroList: PlayedHero[] = heroesQuery.data ?? []
  const rankList: Rank[] = ranksQuery.data ?? []
  const accountList: TrackedAccount[] = accountsQuery.data ?? []
  // Native <option>s can't hold an <img>, so the icon shows the *selected* hero
  // beside the dropdown rather than one per row.
  const selectedHero = heroList.find((h) => h.hero_id === scope.heroId)
  // accountId === null means "the self account" (the server default), so the
  // dropdown shows the self account selected until another is chosen.
  const selfAccount = accountList.find((a) => a.is_self)
  const selectedAccountId = scope.accountId ?? selfAccount?.account_id ?? ''

  // The era span is two dropdowns (Start/End) over the same started_at-ordered
  // list. We work in list indices because a contiguous span is naturally an
  // index range; the empty-eraIds default maps to the full window.
  const lastIdx = eraList.length - 1
  const selectedIdxs = scope.eraIds
    .map((id) => eraList.findIndex((e) => e.era_id === id))
    .filter((i) => i >= 0)
  const startIdx = scope.eraIds.length === 0 ? 0 : Math.min(...selectedIdxs)
  const endIdx = scope.eraIds.length === 0 ? lastIdx : Math.max(...selectedIdxs)

  // The whole timeline (earliest..latest) collapses to [] so the backend uses
  // the all-time baseline snapshot, keeping the default identical to "All eras".
  const setSpan = (s: number, e: number) => {
    if (eraList.length === 0) return
    if (s === 0 && e === lastIdx) {
      update({ eraIds: [] })
    } else {
      update({ eraIds: eraList.slice(s, e + 1).map((era) => era.era_id) })
    }
  }

  return (
    <div className="scope-bar">
      <div className="scope-controls">
        <div className="scope-control">
          <span className="scope-label">Account</span>
          <select
            className="select-input"
            value={selectedAccountId}
            onChange={(e) => {
              const id = Number(e.target.value)
              // Picking the self account resets accountId to null so it stays out
              // of the URL (the default); any other account is set explicitly.
              update({
                accountId: selfAccount && id === selfAccount.account_id ? null : id,
              })
            }}
          >
            {accountList.map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {a.display_name ?? `Account ${a.account_id}`}
                {a.is_self ? ' (you)' : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="scope-control">
          <span className="scope-label">My hero</span>
          <div className="hero-select">
            {selectedHero && (
              <HeroIcon name={selectedHero.name} url={selectedHero.image_url} />
            )}
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
        </div>

        <div className="scope-control">
          <span className="scope-label">Start era</span>
          <select
            className="select-input"
            value={startIdx}
            // Clamp so start never lands after end (drag end forward if needed).
            onChange={(e) => setSpan(Number(e.target.value), Math.max(Number(e.target.value), endIdx))}
          >
            {eraList.map((era, i) => (
              <option key={era.era_id} value={i}>
                {era.label}
              </option>
            ))}
          </select>
        </div>

        <div className="scope-control">
          <span className="scope-label">End era</span>
          <select
            className="select-input"
            value={endIdx}
            // Clamp so end never lands before start (drag start back if needed).
            onChange={(e) => setSpan(Math.min(startIdx, Number(e.target.value)), Number(e.target.value))}
          >
            {eraList.map((era, i) => (
              <option key={era.era_id} value={i}>
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
        {scopeLabel(scope, eraList, heroList, rankList, accountList)}
      </div>
      <div className="scope-note">
        In-lane keeps only the enemies in your lane pairing and compares against
        the same-lane baseline; overall counts all five opponents.
      </div>
    </div>
  )
}
