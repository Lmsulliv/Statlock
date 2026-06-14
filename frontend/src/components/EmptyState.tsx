import type { ReactNode } from 'react'

// Empty states explain themselves (presentation rule 5): never a blank table or
// a raw error.
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
    </div>
  )
}
