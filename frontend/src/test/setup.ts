// Vitest setup: register jest-dom matchers (toBeInTheDocument, toBeVisible, …)
// and unmount any rendered tree after each test so tests don't leak DOM into
// one another.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
