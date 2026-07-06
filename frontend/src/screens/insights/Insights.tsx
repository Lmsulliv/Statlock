import { useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { GAME_MODE_NORMAL, useScope } from '../../scope/useScope'
import { DeathsSection } from './DeathsSection'
import { PlayersSection } from './PlayersSection'
import { SessionsSection } from './SessionsSection'

type SectionId = 'deaths' | 'sessions' | 'players'

// The Insights tab: the three coaching analyses that aren't hero- or
// metric-shaped — death patterns, session/tilt analysis, and recurring
// players — as cards on one screen. Each is deep-linkable via the URL path
// (/insights/deaths etc., NOT a query param, which the scope bar would erase);
// landing on a section scrolls it into view.
export function Insights() {
  const { section } = useParams()
  const { scope } = useScope()
  const brawl = scope.gameMode !== GAME_MODE_NORMAL

  const refs: Record<SectionId, React.RefObject<HTMLElement>> = {
    deaths: useRef<HTMLElement>(null),
    sessions: useRef<HTMLElement>(null),
    players: useRef<HTMLElement>(null),
  }

  useEffect(() => {
    if (section && section in refs) {
      // Optional call: jsdom (the test runner's DOM) doesn't implement it.
      refs[section as SectionId].current?.scrollIntoView?.({ block: 'start' })
    }
    // refs is stable per render; only the section drives the scroll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section])

  return (
    <section>
      <h1 className="screen-title">Insights</h1>
      <p className="screen-sub">
        The coaching views: where your deaths come from, whether a long sitting
        or a loss streak really changes how you play, and which recurring
        teammates and opponents shift your results.
      </p>
      <div className="insights">
        <section ref={refs.deaths} id="deaths" className="card insight-section">
          <h2 className="card-title">Deaths</h2>
          {brawl ? <SectionModeNote /> : <DeathsSection scope={scope} />}
        </section>

        {/* Session analysis reads win/loss + match times, which Brawl summaries
            already carry, so it renders under Street Brawl too. */}
        <section ref={refs.sessions} id="sessions" className="card insight-section">
          <h2 className="card-title">Do you tilt?</h2>
          <SessionsSection scope={scope} />
        </section>

        <section ref={refs.players} id="players" className="card insight-section">
          <h2 className="card-title">Recurring players</h2>
          {brawl ? <SectionModeNote /> : <PlayersSection scope={scope} />}
        </section>
      </div>
    </section>
  )
}

// A compact, per-section variant of ModeNotice: under Street Brawl only the
// sections that need Normal metadata/baselines go quiet, while "Do you tilt?"
// keeps rendering — a full-screen notice would wrongly mute it too.
function SectionModeNote() {
  return (
    <p className="muted">
      Normal matches only — this section compares against the Normal population.
      Switch <strong>Mode</strong> back to <strong>Normal</strong> in the scope
      bar to see it.
    </p>
  )
}
