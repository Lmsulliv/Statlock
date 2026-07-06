import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

// A small, accessible info tooltip. The wrapped element IS the trigger (no extra
// ⓘ icon), so it works inline in dense tables without adding clutter. It opens on
// hover and keyboard focus, and a click/tap pins it open (with a full-screen
// backdrop, like HeaderMenu, so an outside tap dismisses it). Escape always
// closes. The tip text is associated via aria-describedby so screen readers
// announce it. `block` makes the trigger a full-width block (for the IntervalBar,
// whose content is block-level); the default is an inline-block (for badges).
export function InfoTip({
  tip,
  block = false,
  className,
  children,
}: {
  tip: ReactNode
  block?: boolean
  className?: string // extra classes merged onto the trigger (e.g. layout)
  children: ReactNode
}) {
  const [hovering, setHovering] = useState(false)
  const [pinned, setPinned] = useState(false)
  const tipId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const open = hovering || pinned
  const Tag = block ? 'div' : 'span'

  // Close the pinned tip on Escape, restoring focus predictability.
  useEffect(() => {
    if (!pinned) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPinned(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [pinned])

  return (
    <Tag
      ref={rootRef as never}
      className={[block ? 'infotip infotip-block' : 'infotip', className]
        .filter(Boolean)
        .join(' ')}
      tabIndex={0}
      aria-describedby={open ? tipId : undefined}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onFocus={() => setHovering(true)}
      onBlur={() => setHovering(false)}
      onClick={(e) => {
        // Toggle the pinned state without following any link the trigger wraps.
        e.preventDefault()
        e.stopPropagation()
        setPinned((p) => !p)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          setPinned((p) => !p)
        }
      }}
    >
      {children}
      {pinned && (
        // Outside-tap dismissal for the pinned (touch/click) state.
        <span
          className="infotip-backdrop"
          aria-hidden="true"
          onClick={(e) => {
            e.stopPropagation()
            setPinned(false)
          }}
        />
      )}
      {open && (
        <span role="tooltip" id={tipId} className="infotip-panel">
          {tip}
        </span>
      )}
    </Tag>
  )
}
