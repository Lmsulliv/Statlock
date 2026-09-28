import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'

// Gap between the trigger and the panel, and the minimum distance the panel
// keeps from the viewport edge when it has to shift.
const GAP = 6
const MARGIN = 8

// A small, accessible info tooltip. The wrapped element IS the trigger (no extra
// ⓘ icon), so it works inline in dense tables without adding clutter. It opens on
// hover and keyboard focus, and a click/tap pins it open (with a full-screen
// backdrop, like HeaderMenu, so an outside tap dismisses it). Escape always
// closes. The tip text is associated via aria-describedby so screen readers
// announce it. `block` makes the trigger a full-width block (for the IntervalBar,
// whose content is block-level); the default is an inline-block (for badges).
//
// The panel is PORTALED to <body> and positioned `fixed`, rather than absolutely
// inside the trigger. An absolutely positioned panel doesn't reflow its siblings,
// but its overflow still extends the scrollable region of any ancestor: every
// table here sits in `.table-scroll { overflow-x: auto }`, so a tip near a
// table's right edge grew that table's scroll and clipped itself, and one near
// the page edge grew document.scrollWidth into a page scrollbar. Living in
// <body> at `fixed`, it has no ancestor whose scroll region it could extend, so
// that whole class of bug is gone rather than patched. Positioning then has to
// be computed by hand (the project has no positioning library, and this needs no
// new dependency — createPortal ships with react-dom), including the collision
// handling the CSS-only version never had: flip above when there's no room
// below, shift sideways to stay on screen.
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
  // null until measured: the panel renders hidden for one frame so it can be
  // sized, then is placed. Without that it would flash at the top-left corner.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const tipId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLSpanElement>(null)
  const open = hovering || pinned
  const Tag = block ? 'div' : 'span'

  const place = useCallback(() => {
    const trigger = rootRef.current
    const panel = panelRef.current
    if (!trigger || !panel) return
    const t = trigger.getBoundingClientRect()
    const p = panel.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight

    // Below the trigger by default; flip above only when below would overflow
    // AND above actually fits, so a cramped viewport still shows something.
    let top = t.bottom + GAP
    if (top + p.height > vh - MARGIN && t.top - GAP - p.height >= MARGIN) {
      top = t.top - GAP - p.height
    }
    // Left-aligned to the trigger, shifted (never flipped) to stay on screen.
    let left = t.left
    if (left + p.width > vw - MARGIN) left = vw - MARGIN - p.width
    if (left < MARGIN) left = MARGIN

    setPos({ top, left })
  }, [])

  // Measure + place once per open, then follow the trigger while it's open.
  // `capture: true` catches scrolls in any ancestor scroller (.table-scroll),
  // not just the document. No listeners are attached while closed.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  // Close the pinned tip on Escape, restoring focus predictability.
  useEffect(() => {
    if (!pinned) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPinned(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [pinned])

  const panel = (
    <span
      ref={panelRef}
      role="tooltip"
      id={tipId}
      // A hover tip is inert: the panel is no longer a DOM descendant of the
      // trigger, so a hoverable panel would fire the trigger's mouseleave the
      // moment the pointer crossed into it and close the very thing being
      // reached for. A PINNED tip was opened deliberately (tap/keyboard), so it
      // takes pointer events and its text stays selectable.
      className={`infotip-panel${pinned ? ' infotip-panel-pinned' : ''}`}
      style={{
        top: pos?.top ?? 0,
        left: pos?.left ?? 0,
        visibility: pos ? 'visible' : 'hidden',
      }}
    >
      {tip}
    </span>
  )

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
      {/* Both overlays portal to <body>: the panel so it can't extend any
          ancestor's scroll region, the backdrop so it layers against the panel
          rather than against whatever stacking context the trigger sits in. */}
      {pinned &&
        createPortal(
          // Outside-tap dismissal for the pinned (touch/click) state.
          <span
            className="infotip-backdrop"
            aria-hidden="true"
            onClick={(e) => {
              e.stopPropagation()
              setPinned(false)
            }}
          />,
          document.body,
        )}
      {open && createPortal(panel, document.body)}
    </Tag>
  )
}
