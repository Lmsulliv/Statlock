import { Link, useLocation, useParams } from 'react-router-dom'
import { useMatchDetail } from '../api/queries'
import type {
  DeathEvent,
  MatchDetail as MatchDetailData,
  MatchDetailPlayer,
  MatchPurchase,
} from '../api/types'
import { HeroIcon } from '../components/HeroIcon'
import { QueryBoundary } from '../components/QueryBoundary'
import { fmtClock, gameModeLabel } from '../format'
import { useScope } from '../scope/useScope'

const fmtDateTime = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export function MatchDetail() {
  const { matchId } = useParams()
  const id = Number(matchId)
  const { scope } = useScope()
  const { search } = useLocation()
  // Pass the scoped account so "you" matches the Overview the click came from.
  const detail = useMatchDetail(id, scope.accountId)

  return (
    <section>
      {/* Carry `search` so returning to Overview keeps the active scope. */}
      <Link to={{ pathname: '/', search }} className="back-link">
        ← Back to Overview
      </Link>
      <h1 className="screen-title">Match detail</h1>
      <QueryBoundary query={detail}>{(data) => <MatchBody data={data} />}</QueryBoundary>
    </section>
  )
}

function MatchBody({ data }: { data: MatchDetailData }) {
  const you = data.players.find((p) => p.is_you) ?? null
  const team0 = data.players.filter((p) => p.team === 0)
  const team1 = data.players.filter((p) => p.team === 1)
  // Team names (Amber/Sapphire) are an unverified open question in api-findings,
  // so label relative to you when we can, and fall back to the raw team number.
  const label = (team: number) =>
    you ? (you.team === team ? 'Your team' : 'Enemy') : `Team ${team}`

  return (
    <div className="match-detail">
      <section className="card match-head">
        <span className="mode-tag">{gameModeLabel(data.game_mode)}</span>
        {you && (
          <span className={`result ${you.won ? 'result-win' : 'result-loss'}`}>
            {you.won ? 'Win' : 'Loss'}
          </span>
        )}
        <span className="muted">{fmtClock(data.duration_s)}</span>
        <span className="muted">{fmtDateTime(data.start_time)}</span>
      </section>

      <div className="team-cols">
        <RosterColumn title={label(0)} players={team0} />
        <RosterColumn title={label(1)} players={team1} />
      </div>

      <section className="card">
        <h2 className="card-title">Your purchases</h2>
        <Purchases purchases={data.purchases} />
      </section>

      <section className="card">
        <h2 className="card-title">Kill / death timeline</h2>
        <Timeline deaths={data.deaths} />
      </section>
    </div>
  )
}

function RosterColumn({ title, players }: { title: string; players: MatchDetailPlayer[] }) {
  return (
    <section className="card">
      <h2 className="card-title">{title}</h2>
      <div className="roster">
        {players.map((p) => (
          <PlayerRow key={p.player_slot} p={p} />
        ))}
      </div>
    </section>
  )
}

function PlayerRow({ p }: { p: MatchDetailPlayer }) {
  return (
    <div className={`player-row${p.is_you ? ' you' : ''}`}>
      <span className="enemy-cell">
        <HeroIcon name={p.hero_name} url={p.image_url} />
        <span>
          {p.hero_name}
          {p.is_you && <span className="you-tag"> (you)</span>}
        </span>
      </span>
      <span className="player-kda">
        {p.kills ?? '—'}/{p.deaths ?? '—'}/{p.assists ?? '—'}
      </span>
      <span className="player-souls">{p.net_worth?.toLocaleString() ?? '—'}</span>
      <span className="muted player-lane">{p.lane != null ? `Lane ${p.lane}` : '—'}</span>
    </div>
  )
}

function Purchases({ purchases }: { purchases: MatchPurchase[] }) {
  if (purchases.length === 0) {
    return <p className="muted">No purchases recorded for this account in this match.</p>
  }
  return (
    <ul className="purchase-list">
      {purchases.map((b) => (
        <li key={b.item_id} className="purchase">
          <HeroIcon name={b.item_name} url={b.item_image_url} />
          <span className="purchase-name">{b.item_name}</span>
          <span className="muted purchase-time">
            {fmtClock(b.purchase_time_s)}
            {b.sold_time_s > 0 && ' · sold'}
          </span>
        </li>
      ))}
    </ul>
  )
}

function Timeline({ deaths }: { deaths: DeathEvent[] }) {
  if (deaths.length === 0) {
    return <p className="muted">No kill/death events recorded for this match.</p>
  }
  return (
    <ol className="timeline">
      {deaths.map((d, i) => {
        const mine = d.killer_is_you || d.victim_is_you
        return (
          <li
            key={`${d.victim_slot}-${d.game_time_s}-${i}`}
            className={`timeline-row${mine ? ' timeline-you' : ''}`}
          >
            <span className="muted timeline-time">{fmtClock(d.game_time_s)}</span>
            <span className="timeline-actor">
              {d.killer_hero_name ? (
                <>
                  <HeroIcon name={d.killer_hero_name} url={d.killer_image_url} />
                  {d.killer_hero_name}
                  {d.killer_is_you && <span className="you-tag"> (you)</span>}
                </>
              ) : (
                <span className="muted">Environment</span>
              )}
            </span>
            <span className="timeline-arrow" aria-label="killed">
              →
            </span>
            <span className="timeline-actor">
              <HeroIcon name={d.victim_hero_name} url={d.victim_image_url} />
              {d.victim_hero_name}
              {d.victim_is_you && <span className="you-tag"> (you)</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
