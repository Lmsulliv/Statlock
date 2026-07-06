import { isOwner } from '../config'
import { usePlayerPin } from '../player/PlayerContext'
import { useMe } from './queries'

// Can the viewer reach the management surfaces (Accounts importer, Era manager,
// the header gear menu, inline rename)? Under Steam login (auth mode) that means
// authenticated; in local/dev mode (or until /me resolves) it falls back to the
// build-time VITE_OWNER flag. On a public /player/:accountId view it is always
// false, so nothing writable renders there. This is convenience only — the API
// enforces the gate on every write.
export function useCanManage(): boolean {
  const pin = usePlayerPin()
  const me = useMe()
  if (pin) return false
  return me.data ? (me.data.auth_enabled ? me.data.authenticated : isOwner) : isOwner
}
