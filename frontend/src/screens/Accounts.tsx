import { useState } from 'react'
import { ApiError } from '../api/client'
import { useAccounts, useAddAccount, useSyncStatus } from '../api/queries'
import type { SyncStatus, TrackedAccount } from '../api/types'
import { EmptyState } from '../components/EmptyState'
import { InlineRename } from '../components/InlineRename'
import { QueryBoundary } from '../components/QueryBoundary'

const fmtTime = (iso: string | null) => {
  if (!iso) return 'not yet'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

// Turn a failed mutation into a human sentence. The owner gate (403) and an
// unparseable id (400) are the two the user can actually act on; anything else
// is most likely the backend being down.
function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 400)
      return "That doesn’t look like an account id, SteamID64, or profile URL."
    if (error.status === 403)
      return 'Account management is owner-only. Set DEADLOCK_OWNER on the API.'
  }
  return 'Something went wrong. Is the backend running?'
}

export function Accounts() {
  return (
    <section>
      <h1 className="screen-title">Accounts</h1>
      <p className="screen-sub">
        Track a new account by id, SteamID64, or profile URL, and give the ones
        you track friendly names. Adding an account only queues it; the worker
        discovers and ingests its matches on its next cycle, so nothing is fetched
        while you wait here.
      </p>
      <div className="accounts">
        <AddAccountForm />
        <SyncPanel />
        <AccountList />
      </div>
    </section>
  )
}

function AddAccountForm() {
  const [identifier, setIdentifier] = useState('')
  const [name, setName] = useState('')
  const add = useAddAccount()

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const account_id = identifier.trim()
    if (!account_id) return
    const display_name = name.trim()
    add.mutate(
      { account_id, display_name: display_name || undefined },
      {
        onSuccess: () => {
          setIdentifier('')
          setName('')
        },
      },
    )
  }

  return (
    <section className="card">
      <h2 className="card-title">Add an account</h2>
      <form className="account-form" onSubmit={onSubmit}>
        <label className="account-field">
          <span className="account-label">Account id / SteamID64 / profile URL</span>
          <input
            className="account-input account-id-input"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="891231519"
          />
        </label>
        <label className="account-field">
          <span className="account-label">Display name (optional)</span>
          <input
            className="account-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="smurf"
          />
        </label>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={add.isPending || identifier.trim() === ''}
        >
          {add.isPending ? 'Queuing…' : 'Queue account'}
        </button>
      </form>
      {add.isError && <p className="state-error">{errorMessage(add.error)}</p>}
      {add.isSuccess && (
        <p className="state-ok">
          Queued account {add.data.account_id}. The worker will ingest its
          matches on its next discovery cycle.
        </p>
      )}
    </section>
  )
}

// The worker's heartbeat, polled every 5s so a freshly queued account's matches
// can be seen draining. This is an honest status (queue depth + last discovery),
// not a progress bar — if the worker isn't running, pending simply stays put.
function SyncPanel() {
  const sync = useSyncStatus(5000)
  return (
    <section className="card">
      <h2 className="card-title">Ingestion status</h2>
      <QueryBoundary query={sync}>
        {(s: SyncStatus) => (
          <div className="sync-grid">
            <div>
              <div className="sync-num">{s.queue_depth.toLocaleString()}</div>
              <div className="muted">
                queued {s.queue_depth > 0 ? '· ingesting…' : '· idle'}
              </div>
            </div>
            <div>
              <div className="sync-num">{s.fetched.toLocaleString()}</div>
              <div className="muted">matches fetched</div>
            </div>
            <div>
              <div className="sync-num-sm">{fmtTime(s.last_discovery_at)}</div>
              <div className="muted">last discovery</div>
            </div>
          </div>
        )}
      </QueryBoundary>
    </section>
  )
}

function AccountList() {
  const accounts = useAccounts()

  return (
    <section className="card">
      <h2 className="card-title">Tracked accounts</h2>
      <QueryBoundary query={accounts}>
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="No accounts tracked yet.">
              <p>Add one above to start ingesting its matches.</p>
            </EmptyState>
          ) : (
            <ul className="account-list">
              {rows.map((a) => (
                <AccountRow key={a.account_id} account={a} />
              ))}
            </ul>
          )
        }
      </QueryBoundary>
    </section>
  )
}

function AccountRow({ account }: { account: TrackedAccount }) {
  // display_name is resolved server-side now; use it as the editable starting
  // point, falling back to the bare id if it is ever absent.
  const currentName = account.display_name ?? String(account.account_id)
  return (
    <li className="account-item">
      <div className="account-meta">
        <span className="account-name">
          {currentName}
          {account.is_self && <span className="badge tone-neutral">self</span>}
        </span>
        <span className="muted">{account.account_id}</span>
      </div>
      <InlineRename accountId={account.account_id} currentName={currentName} />
    </li>
  )
}
