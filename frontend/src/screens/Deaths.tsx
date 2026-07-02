import { useDeathPatterns, useSyncStatus } from '../api/queries'
import type { DeathPatternsResponse, DeathTimelineBin } from '../api/types'
import { KillCountCell } from '../components/cells'
import { EmptyState } from '../components/EmptyState'
import { HeroIcon } from '../components/HeroIcon'
import { QueryBoundary } from '../components/QueryBoundary'
import { VERDICT_TONE } from '../components/verdict'
import { useScope } from '../scope/useScope'

const fmtNum = (x: number | null) =>
  x === null ? '—' : x.toLocaleString(undefined, { maximumFractionDigits: 2 })

export function Deaths() {
  const { scope } = useScope()
  const deaths = useDeathPatterns(scope)

  return (
    <section>
      <h1 className="screen-title">Deaths</h1>
      <p className="screen-sub">
        Where your deaths come from. Which enemy heroes kill you most often — raw
        counts, with the games you faced them for context, and no verdict, because
        there's no baseline for "how often a hero should kill you". And when in the
        game you die: deaths per game in each minute, next to the live average of
        everyone else at this scope. Fewer deaths than the field is good, so a bin
        below the baseline reads as a strength; a minute with no one else's data to
        compare against stays neutral.
      </p>
      <QueryBoundary query={deaths}>
        {(data) =>
          data.games === 0 ? (
            <DeathsEmpty />
          ) : (
            <DeathsBody data={data} />
          )
        }
      </QueryBoundary>
    </section>
  )
}

function DeathsBody({ data }: { data: DeathPatternsResponse }) {
  return (
    <>
      <p className="muted deaths-summary">
        {data.total_deaths.toLocaleString()} timed deaths across{' '}
        {data.games.toLocaleString()} games.
      </p>

      <h2 className="deaths-section-title">Who kills you</h2>
      <p className="muted deaths-note">Raw counts — no baseline, no verdict.</p>
      {data.by_enemy_hero.length === 0 ? (
        <EmptyState title="No deaths to an enemy hero recorded in this scope." />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Enemy</th>
              <th className="col-delta">Deaths</th>
              <th className="col-delta">Games faced</th>
            </tr>
          </thead>
          <tbody>
            {data.by_enemy_hero.map((r) => (
              <tr key={r.enemy_hero_id}>
                <td>
                  <span className="enemy-cell">
                    <HeroIcon name={r.enemy_hero_name} url={r.enemy_hero_image_url} />
                    {r.enemy_hero_name}
                  </span>
                </td>
                <td className="col-delta">
                  <KillCountCell value={r.deaths} games={r.games_faced} />
                </td>
                <td className="col-delta">{r.games_faced}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 className="deaths-section-title">Who damages you</h2>
      <p className="muted deaths-note">
        Average damage taken from each enemy hero per game — gross, pre-mitigation
        damage, so it's a relative ranking with no baseline or verdict.
      </p>
      {data.by_damage_source.length === 0 ? (
        <EmptyState title="No damage-by-source data recorded in this scope." />
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Enemy</th>
              <th className="col-delta">Damage / game</th>
              <th className="col-delta">Games faced</th>
            </tr>
          </thead>
          <tbody>
            {data.by_damage_source.map((r) => (
              <tr key={r.enemy_hero_id}>
                <td>
                  <span className="enemy-cell">
                    <HeroIcon name={r.enemy_hero_name} url={r.enemy_hero_image_url} />
                    {r.enemy_hero_name}
                  </span>
                </td>
                <td className="col-delta">{fmtNum(r.avg_per_game)}</td>
                <td className="col-delta">{r.games_faced}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 className="deaths-section-title">When you die</h2>
      <p className="muted deaths-note">
        Deaths per game by game-minute. Bars below the gold baseline (the field's
        average) are good; color marks a confirmed verdict only.
      </p>
      <TimingChart bins={data.timeline} />
    </>
  )
}

const H = 180
const PAD_TOP = 12
const PAD_BOTTOM = 24
const BAR_W = 20
const GAP = 6

// Hand-rolled SVG bar chart — no charting library (CLAUDE.md), styled with the
// shared tokens (mirrors the Trends sparkline). Bar height is your mean deaths
// per game in the minute; the dashed gold tick on each bar is the population
// baseline; the bar is tinted only when the server's verdict is confirmed.
function TimingChart({ bins }: { bins: DeathTimelineBin[] }) {
  if (bins.length === 0) {
    return <div className="trend-empty muted">No deaths recorded in this scope.</div>
  }

  const W = GAP + bins.length * (BAR_W + GAP)
  const plotH = H - PAD_TOP - PAD_BOTTOM
  const values = bins
    .flatMap((b) => [b.mean, b.baseline_mean])
    .filter((x): x is number => x !== null && Number.isFinite(x))
  const yMax = Math.max(0.001, ...values) * 1.1 // headroom so the tallest bar isn't flush
  const y = (v: number) => PAD_TOP + (1 - v / yMax) * plotH
  const yBase = PAD_TOP + plotH // the v == 0 line

  return (
    <div className="death-chart-wrap">
      <svg
        className="death-bars"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Deaths per game by game-minute"
      >
        {bins.map((b, i) => {
          const x = GAP + i * (BAR_W + GAP)
          const mean = b.mean ?? 0
          const top = y(mean)
          const baseY = b.baseline_mean !== null ? y(b.baseline_mean) : null
          const showLabel = b.minute % 5 === 0 || i === bins.length - 1
          return (
            <g key={b.minute}>
              <rect
                className={`death-bar tone-${VERDICT_TONE[b.verdict]}`}
                x={x}
                y={top}
                width={BAR_W}
                height={Math.max(0, yBase - top)}
              >
                <title>
                  {b.label}: {fmtNum(b.mean)} deaths/game ·{' '}
                  {b.baseline_mean === null
                    ? 'no baseline'
                    : `baseline ${fmtNum(b.baseline_mean)}`}{' '}
                  · {b.deaths} total over {b.games} games
                </title>
              </rect>
              {baseY !== null && (
                <line
                  className="death-baseline-tick"
                  x1={x - 1}
                  y1={baseY}
                  x2={x + BAR_W + 1}
                  y2={baseY}
                />
              )}
              {showLabel && (
                <text
                  className="death-axis-label"
                  x={x + BAR_W / 2}
                  y={H - 8}
                  textAnchor="middle"
                >
                  {b.minute}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="death-axis-caption muted">game minute →</div>
    </div>
  )
}

// No matches in scope: explain why with the live sync counts (presentation rule
// 5), mirroring PerformanceEmpty / TrendsEmpty.
function DeathsEmpty() {
  const sync = useSyncStatus()
  return (
    <EmptyState title="No deaths to show yet.">
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
                The worker hasn't ingested any matches yet. Add an account with{' '}
                <code>python -m ingest add-account &lt;id&gt; --self</code>, run{' '}
                <code>python -m ingest run-daemon</code>, and come back in an hour.
              </p>
            ) : (
              <p>
                Matches are ingested, but none fall in the current scope. Try
                widening the rank range, switching the era, or changing the game
                mode.
              </p>
            )}
          </>
        )}
      </QueryBoundary>
    </EmptyState>
  )
}
