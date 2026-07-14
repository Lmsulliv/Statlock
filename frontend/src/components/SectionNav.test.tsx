import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SectionNav, type SectionNavItem } from './SectionNav'

const SECTIONS: SectionNavItem[] = [
  { id: 'alpha', title: 'Alpha' },
  { id: 'beta', title: 'Beta' },
  { id: 'gamma', title: 'Gamma' },
]

// A fake IntersectionObserver that hands the test the callback so it can drive
// "this section scrolled into view" deterministically (jsdom has no real one).
function stubObserver() {
  let callback: IntersectionObserverCallback | null = null
  class FakeObserver {
    constructor(cb: IntersectionObserverCallback) {
      callback = cb
    }
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
    takeRecords = vi.fn(() => [])
    root = null
    rootMargin = ''
    thresholds = []
  }
  vi.stubGlobal('IntersectionObserver', FakeObserver as unknown as typeof IntersectionObserver)
  return (id: string) => {
    const entry = {
      isIntersecting: true,
      target: document.getElementById(id) as Element,
    } as IntersectionObserverEntry
    act(() => callback?.([entry], {} as IntersectionObserver))
  }
}

// Render the nav beside real sections carrying the ids it links to.
function renderNav() {
  return render(
    <>
      <SectionNav sections={SECTIONS} />
      <section id="alpha">Alpha body</section>
      <section id="beta">Beta body</section>
      <section id="gamma">Gamma body</section>
    </>,
  )
}

describe('SectionNav', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders every section title as an in-page anchor link', () => {
    stubObserver()
    renderNav()

    for (const s of SECTIONS) {
      // Rail + dropdown each render the list, so there are two links per title.
      const links = screen.getAllByRole('link', { name: s.title })
      expect(links.length).toBeGreaterThan(0)
      expect(links[0]).toHaveAttribute('href', `#${s.id}`)
    }
  })

  it('highlights the section currently in view', () => {
    const fire = stubObserver()
    renderNav()

    // Nothing active until the observer reports a section.
    expect(screen.getAllByRole('link', { name: 'Beta' })[0]).not.toHaveAttribute(
      'aria-current',
    )

    fire('beta')

    const betaLink = screen.getAllByRole('link', { name: 'Beta' })[0]
    expect(betaLink).toHaveAttribute('aria-current', 'true')
    expect(betaLink).toHaveClass('active')
    // A different section is not marked.
    expect(screen.getAllByRole('link', { name: 'Alpha' })[0]).not.toHaveAttribute(
      'aria-current',
    )
  })

  it('smooth-scrolls to a section on click instead of navigating', () => {
    stubObserver()
    const scrollSpy = vi.fn()
    // jsdom omits scrollIntoView; provide a spy for the click handler to call.
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      value: scrollSpy,
      configurable: true,
      writable: true,
    })
    renderNav()

    fireEvent.click(screen.getAllByRole('link', { name: 'Gamma' })[0])

    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
    // preventDefault means the hash never lands in the URL.
    expect(window.location.hash).toBe('')
  })
})
