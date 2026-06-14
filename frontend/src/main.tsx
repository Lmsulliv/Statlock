import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import './styles/tokens.css'
import './styles/app.css'

// One QueryClient for the whole app. A "query client" is TanStack Query's cache
// + scheduler: it dedupes requests, caches responses, and refetches when a
// query's key changes. Conservative defaults: data is fresh for 30s and we
// don't refetch on window focus — this is a personal dashboard, not a live feed.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
