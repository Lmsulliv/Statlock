// Sample sizes are always visible (presentation rule 3). Showing wins/games as
// raw counts keeps the reader anchored to how much evidence a rate rests on.
export function SampleSize({ games, wins }: { games: number; wins: number }) {
  return (
    <span className="sample">
      <span className="sample-main">
        {wins}/{games}
      </span>
      <span className="sample-sub">wins / games</span>
    </span>
  )
}
