import { useSyncStatus } from '../api/queries'

const fmtDateTime = (iso: string | null) => {
  if (iso === null) return 'never'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

// The worker's heartbeat, demoted from an Overview card to a small header pill.
// The dot is a literal operational state (amber while matches are queued,
// green when drained) — like the win/loss colors, not a statistical verdict, so
// presentation rule 2 is intact. The full old card text lives in the tooltip.
export function SyncIndicator() {
  const sync = useSyncStatus()
  if (!sync.data) return null
  const s = sync.data
  const busy = s.queue_depth > 0
  const title = [
    `${s.fetched.toLocaleString()} fetched · ${s.queue_depth.toLocaleString()} queued · ${s.unavailable.toLocaleString()} unavailable`,
    `Last discovery: ${fmtDateTime(s.last_discovery_at)}`,
    `Last maintenance: ${fmtDateTime(s.last_maintenance_at)}`,
    ...(s.message ? [s.message] : []),
  ].join('\n')
  return (
    <span className="sync-pill" title={title}>
      <span className={busy ? 'sync-dot busy' : 'sync-dot'} aria-hidden="true" />
      {busy ? `${s.queue_depth.toLocaleString()} queued` : 'Synced'}
    </span>
  )
}
