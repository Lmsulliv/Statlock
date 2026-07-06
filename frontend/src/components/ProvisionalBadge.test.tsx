import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProvisionalBadge } from './ProvisionalBadge'

// The badge must never cause layout shift, so it is always in the DOM and only
// toggles a visibility class. We assert on the class + aria state (jsdom doesn't
// apply the external stylesheet, so computed visibility isn't observable here).
describe('ProvisionalBadge', () => {
  const text = /early results, deepening as matches are analyzed/

  it('is shown (no is-hidden, aria-hidden false) when the flag is true', () => {
    render(<ProvisionalBadge show={true} />)
    const badge = screen.getByText(text)
    expect(badge).toBeInTheDocument()
    expect(badge).not.toHaveClass('is-hidden')
    expect(badge).toHaveAttribute('aria-hidden', 'false')
  })

  it('stays in the DOM but hidden when the flag is false', () => {
    render(<ProvisionalBadge show={false} />)
    const badge = screen.getByText(text)
    expect(badge).toBeInTheDocument() // still present → no layout shift
    expect(badge).toHaveClass('is-hidden')
    expect(badge).toHaveAttribute('aria-hidden', 'true')
  })
})
