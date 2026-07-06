import { Link } from 'react-router-dom'
import { useMe } from '../api/queries'
import { usePlayerPin } from '../player/PlayerContext'

// A link to the configured demo profile (DEMO_ACCOUNT_ID, surfaced on /api/auth/me
// as demo_account_id). Renders nothing when no demo is configured, or when we're
// already viewing that very profile, so it is safe to drop into any empty state.
// `variant="button"` is the prominent landing call-to-action; the default is a
// compact inline link for empty states.
export function DemoProfileLink({
  variant = 'link',
}: {
  variant?: 'button' | 'link'
}) {
  const me = useMe()
  const pin = usePlayerPin()
  const demoId = me.data?.demo_account_id ?? null
  if (demoId === null) return null
  if (pin?.accountId === demoId) return null

  const to = `/player/${demoId}`
  if (variant === 'button') {
    return (
      <Link className="btn btn-primary demo-cta" to={to}>
        View a live demo profile →
      </Link>
    )
  }
  return (
    <Link className="demo-link" to={to}>
      Or explore a live demo profile →
    </Link>
  )
}
