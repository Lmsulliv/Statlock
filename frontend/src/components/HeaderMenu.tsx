import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useCanManage } from '../api/useCanManage'

// The management surfaces (Accounts importer, Era manager) live behind this
// gear so they don't take nav space from the analytics tabs. Rendered only for
// viewers who can manage — the API still enforces the gate on every write.
// Popover mechanics mirror RankRange: a fixed backdrop closes on any click.
export function HeaderMenu() {
  const canManage = useCanManage()
  const { search } = useLocation()
  const [open, setOpen] = useState(false)
  if (!canManage) return null

  return (
    <div className="menu">
      <button
        type="button"
        className="menu-button"
        aria-label="Management"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Management"
        onClick={() => setOpen((o) => !o)}
      >
        <GearIcon />
      </button>
      {open && (
        <>
          <div className="menu-backdrop" onClick={() => setOpen(false)} />
          <div className="menu-panel" role="menu">
            <Link
              className="menu-item"
              role="menuitem"
              to={{ pathname: '/accounts', search }}
              onClick={() => setOpen(false)}
            >
              Accounts &amp; sync
            </Link>
            <Link
              className="menu-item"
              role="menuitem"
              to={{ pathname: '/eras', search }}
              onClick={() => setOpen(false)}
            >
              Era manager
            </Link>
          </div>
        </>
      )}
    </div>
  )
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="currentColor">
      <path d="M19.14 12.94c.04-.3.06-.61.06-.94s-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.488.488 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.03-1.58zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z" />
    </svg>
  )
}
