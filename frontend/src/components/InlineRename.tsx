import { useState } from 'react'
import { useClearName, useRenameName } from '../api/queries'
import { useCanManage } from '../api/useCanManage'

// An inline rename control reused by the Accounts list and each RecurringPlayers
// row. Shown only to viewers who can manage (never on a public /player profile).
// Each instance owns its own mutations, so its busy/error state is self-contained
// (no shared-mutation bookkeeping). Save sets a manual label (PUT); "Use Steam
// name" clears it (DELETE), reverting to the Steam persona then the bare id.
// Hiding it is convenience; the real gate is the API returning 403 on the write.
export function InlineRename({
  accountId,
  currentName,
}: {
  accountId: number
  currentName: string
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(currentName)
  const rename = useRenameName()
  const clear = useClearName()
  const canManage = useCanManage()

  if (!canManage) return null

  const busy = rename.isPending || clear.isPending
  const failed = rename.isError || clear.isError

  const save = () => {
    const name = draft.trim()
    if (!name) return
    rename.mutate({ accountId, displayName: name }, { onSuccess: () => setEditing(false) })
  }

  const reset = () => {
    clear.mutate(accountId, { onSuccess: () => setEditing(false) })
  }

  if (!editing) {
    return (
      <button
        type="button"
        className="btn btn-rename"
        onClick={() => {
          setDraft(currentName)
          setEditing(true)
        }}
      >
        Rename
      </button>
    )
  }

  return (
    <span className="account-edit">
      <input
        className="account-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') save()
          if (e.key === 'Escape') setEditing(false)
        }}
        autoFocus
      />
      <button
        type="button"
        className="btn btn-primary"
        disabled={busy || draft.trim() === ''}
        onClick={save}
      >
        {rename.isPending ? 'Saving...' : 'Save'}
      </button>
      <button type="button" className="btn" disabled={busy} onClick={reset}>
        {clear.isPending ? 'Resetting...' : 'Use Steam name'}
      </button>
      <button type="button" className="btn" disabled={busy} onClick={() => setEditing(false)}>
        Cancel
      </button>
      {failed && <span className="state-error">Rename failed. Are you the owner?</span>}
    </span>
  )
}
