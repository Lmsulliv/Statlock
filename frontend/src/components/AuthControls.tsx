import { useLogout, useMe } from '../api/queries'

// Header login/logout controls. Rendered only when Steam login is configured
// (auth_enabled); in local/dev single-user mode there's nothing to log into, so
// this renders nothing. Login is a full-page navigation to the API (which
// redirects to Steam); logout is a CSRF-protected POST that then reloads.
export function AuthControls() {
  const me = useMe()
  const logout = useLogout()
  const data = me.data

  if (!data || !data.auth_enabled) return null

  if (!data.authenticated) {
    // `href`, not a fetch: the login endpoint 302s to Steam, so it must be a
    // top-level navigation the browser follows.
    return (
      <div className="auth-controls">
        <a className="btn btn-primary" href="/api/auth/login">
          Log in with Steam
        </a>
      </div>
    )
  }

  const name =
    data.display_name ?? (data.account_id !== null ? String(data.account_id) : 'You')

  return (
    <div className="auth-controls">
      <span className="auth-user" title="Signed in via Steam">
        {name}
      </span>
      <button
        type="button"
        className="btn"
        onClick={() => logout.mutate()}
        disabled={logout.isPending}
      >
        {logout.isPending ? 'Logging out…' : 'Log out'}
      </button>
    </div>
  )
}
