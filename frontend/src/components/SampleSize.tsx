// Sample sizes are always visible (presentation rule 3). Showing wins/games as
// raw counts keeps the reader anchored to how much evidence a rate rests on.
// `wins` is optional: continuous-metric rows have no win/loss, just a sample of n
// observations, so they show the count alone.
export function SampleSize({ games, wins }: { games: number; wins?: number }) {
  if (wins === undefined) {
    return (
      <span className="sample">
        <span className="sample-main">{games}</span>
        <span className="sample-sub">games</span>
      </span>
    )
  }
  return (
    <span className="sample">
      <span className="sample-main">
        {wins}/{games}
      </span>
      <span className="sample-sub">wins / games</span>
    </span>
  )
}
