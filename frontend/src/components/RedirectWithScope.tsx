import { Navigate, useLocation } from 'react-router-dom'
import { useHref } from '../player/PlayerContext'

// Legacy-route redirect that carries the scope query string along, so old
// bookmarks like /matchups?hero_id=5&badge_min=30 land in the new structure
// with their scope intact. Under a public /player profile the destination is
// base-prefixed so the redirect stays on the pinned account. `replace` keeps the
// dead URL out of history.
export function RedirectWithScope({ to }: { to: string }) {
  const { search } = useLocation()
  const href = useHref()
  return <Navigate to={{ pathname: href(to), search }} replace />
}
