import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { InfoTip } from './InfoTip'
import { VerdictBadge } from './VerdictBadge'

describe('InfoTip', () => {
  it('opens on keyboard focus and closes on Escape', async () => {
    const user = userEvent.setup()
    render(
      <InfoTip tip="the range we’re 95% confident about">
        <span>Strength</span>
      </InfoTip>,
    )
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    await user.tab() // the trigger is the only focusable element
    expect(screen.getByRole('tooltip')).toHaveTextContent(/95% confident/)

    await user.keyboard('{Escape}')
    // Escape unpins; focus/hover may still hold it open, so blur then assert.
    await user.tab()
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('toggles a pinned tip on click (tap)', async () => {
    const user = userEvent.setup()
    render(
      <InfoTip tip="tap explainer">
        <span>Weakness</span>
      </InfoTip>,
    )
    const trigger = screen.getByText('Weakness').parentElement as HTMLElement

    await user.click(trigger)
    expect(screen.getByRole('tooltip')).toHaveTextContent('tap explainer')
    await user.click(trigger)
    // Still hovering from the pointer, so it stays; move away to confirm unpin.
    await user.unhover(trigger)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})

describe('VerdictBadge with InfoTip', () => {
  it('renders the verdict tip and no native title attribute', async () => {
    const user = userEvent.setup()
    const { container } = render(<VerdictBadge verdict="clear_strength" games={30} />)

    // No leftover native tooltip.
    expect(container.querySelector('[title]')).toBeNull()

    await user.tab()
    expect(screen.getByRole('tooltip')).toHaveTextContent(/Clear:/)
  })

  it('explains the small-sample neutral case in plain words', async () => {
    const user = userEvent.setup()
    render(<VerdictBadge verdict="not_enough_data" games={2} />)
    await user.tab()
    expect(screen.getByRole('tooltip')).toHaveTextContent(/too few games/)
  })
})
