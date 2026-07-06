// Pure query-string helpers for links that adjust one scope param while
// carrying the rest — e.g. a Heroes list row linking to /heroes?hero_id=N with
// the era/rank/mode selection intact. URL manipulation only, no statistics.
export function searchWith(search: string, key: string, value: string): string {
  const sp = new URLSearchParams(search)
  sp.set(key, value)
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export function searchWithout(search: string, key: string): string {
  const sp = new URLSearchParams(search)
  sp.delete(key)
  const s = sp.toString()
  return s ? `?${s}` : ''
}
