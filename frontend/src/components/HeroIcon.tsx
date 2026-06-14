import { useState } from 'react'

// A fixed-size slot beside each matchup row. It shows the real hero art from
// heroes.image_url when the API provides it, and falls back to a neutral
// placeholder (initial letter) when the URL is missing or fails to load — so
// the layout is stable whether or not art is available.
export function HeroIcon({ name, url }: { name: string; url: string | null }) {
  const [broken, setBroken] = useState(false)
  const showImg = url && !broken

  return (
    <span className="hero-icon" aria-hidden="true">
      {showImg ? (
        <img src={url} alt="" loading="lazy" onError={() => setBroken(true)} />
      ) : (
        <span className="hero-icon-fallback">{name.charAt(0).toUpperCase()}</span>
      )}
    </span>
  )
}
