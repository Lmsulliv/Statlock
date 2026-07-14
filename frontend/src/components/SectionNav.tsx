import { useEffect, useState } from 'react'

export interface SectionNavItem {
  id: string
  title: string
}

// A slim anchor menu for a section-based page. Each entry links to a section by
// id and smooth-scrolls to it; the entry for the section currently in view is
// highlighted via an IntersectionObserver. Dependency-free and keyboard
// accessible — the entries are real <a> anchors, so focus and Enter work
// natively, and the narrow-width variant is a native <details> dropdown.
//
// Layout: the parent wraps content + this nav in `.sectionnav-layout`; the CSS
// places the rail in the right column on desktop and swaps to the dropdown
// below 1100px. The sections themselves live in the sibling content column and
// carry matching `id`s (plus `.anchor-section` for the scroll offset).
export function SectionNav({ sections }: { sections: SectionNavItem[] }) {
  const [activeId, setActiveId] = useState<string | null>(null)

  useEffect(() => {
    // jsdom (the test DOM) has no IntersectionObserver; skip highlighting there.
    if (typeof IntersectionObserver === 'undefined') return

    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the first entry that's within the active band. rootMargin frames
        // a band near the top of the viewport so "in view" means "at the top",
        // not merely "on screen" (which would light several sections at once).
        const visible = entries.filter((e) => e.isIntersecting)
        if (visible.length > 0) {
          setActiveId(visible[0].target.id)
        }
      },
      { rootMargin: '-20% 0px -65% 0px', threshold: 0 },
    )

    const els = sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => el !== null)
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [sections])

  const onJump = (event: React.MouseEvent<HTMLAnchorElement>, id: string) => {
    event.preventDefault()
    // Optional call: jsdom doesn't implement scrollIntoView (see Insights.tsx).
    document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    setActiveId(id)
    // Close the narrow-width dropdown after a pick.
    event.currentTarget.closest('details')?.removeAttribute('open')
  }

  const list = (
    <div className="section-nav-list">
      {sections.map((s) => (
        <a
          key={s.id}
          className={`section-nav-link${activeId === s.id ? ' active' : ''}`}
          href={`#${s.id}`}
          aria-current={activeId === s.id ? 'true' : undefined}
          onClick={(e) => onJump(e, s.id)}
        >
          {s.title}
        </a>
      ))}
    </div>
  )

  return (
    <nav className="section-nav" aria-label="Sections">
      <div className="section-nav-rail">{list}</div>
      <details className="section-nav-dropdown">
        <summary>Jump to…</summary>
        {list}
      </details>
    </nav>
  )
}
