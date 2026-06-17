// Small shared display formatters. Top-level src/ (like config.ts) because they
// are cross-screen presentation helpers, not data-fetching or table-cell logic.

// Game mode comes through as the raw API code; label it so a Street Brawl game
// isn't read as a ranked/Normal one. Unknown codes show the raw value.
export const gameModeLabel = (mode: string) =>
  mode === '1' ? 'Normal' : mode === '4' ? 'Street Brawl' : mode

// In-match clock as m:ss — used for item buy times and the kill/death feed.
export const fmtClock = (seconds: number | null) => {
  if (seconds === null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
