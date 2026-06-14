// Stub for the four screens we intentionally haven't built yet. The route and
// nav work so the app runs end-to-end, but the build stops at the Matchups
// screen until its look is approved (CLAUDE.md: stop at phase boundaries).
export function Placeholder({ title }: { title: string }) {
  return (
    <section>
      <h1 className="screen-title">{title}</h1>
      <div className="empty-state">
        <div className="empty-title">Coming after Matchups approval.</div>
        <div className="empty-body">
          This screen is scaffolded but intentionally not built yet — we’re
          confirming the Matchups look first, then building the remaining four.
        </div>
      </div>
    </section>
  )
}
