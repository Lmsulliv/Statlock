import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

interface Props<T> {
  query: UseQueryResult<T>
  children: (data: T) => ReactNode
}

// A tiny wrapper that turns a TanStack query into one of three honest states:
// loading, a friendly error (most often: the API isn't running), or the data.
export function QueryBoundary<T>({ query, children }: Props<T>) {
  if (query.isPending) {
    return <div className="state-msg">Loading…</div>
  }
  if (query.isError) {
    return (
      <div className="state-msg state-error">
        Couldn’t reach the API. Is the backend running on{' '}
        <code>localhost:8000</code> (<code>python -m uvicorn api.app:app --reload</code>)?
      </div>
    )
  }
  return <>{children(query.data)}</>
}
