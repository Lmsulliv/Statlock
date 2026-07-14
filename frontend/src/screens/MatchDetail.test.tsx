import { screen, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { baseRoutes } from '../test/fixtures'
import { mockFetch, renderWithProviders } from '../test/renderWithProviders'
import { MatchDetail } from './MatchDetail'

const player = (over: Record<string, unknown>) => ({
  player_slot: 1,
  account_id: 1,
  display_name: 'me',
  hero_id: 5,
  hero_name: 'Haze',
  image_url: null,
  team: 0,
  lane: 1,
  kills: 5,
  deaths: 3,
  assists: 7,
  net_worth: 12000,
  last_hits: 100,
  denies: 10,
  won: true,
  is_you: true,
  ...over,
})

const matchDetail = {
  match_id: 123,
  start_time: '2026-01-01T12:00:00Z',
  duration_s: 1800,
  game_mode: '1',
  winning_team: 0,
  average_badge_team0: null,
  average_badge_team1: null,
  account_id: 1,
  players: [
    player({}),
    player({
      player_slot: 7,
      account_id: 2,
      display_name: 'foe',
      hero_id: 7,
      hero_name: 'Abrams',
      team: 1,
      won: false,
      is_you: false,
    }),
  ],
  purchases: [
    { item_id: 1, item_name: 'Basic Magazine', item_image_url: null, purchase_time_s: 60, sold_time_s: 0 },
  ],
  abilities: [
    { ability_id: 10, ability_name: 'Fixation', ability_type: 'signature', image_url: null, point_number: 1, game_time_s: 30 },
    { ability_id: 20, ability_name: 'Smoke Bomb', ability_type: 'ultimate', image_url: null, point_number: 2, game_time_s: 90 },
  ],
  deaths: [
    {
      game_time_s: 120,
      victim_slot: 1,
      victim_hero_id: 5,
      victim_hero_name: 'Haze',
      victim_image_url: null,
      victim_team: 0,
      victim_is_you: true,
      killer_slot: 7,
      killer_hero_id: 7,
      killer_hero_name: 'Abrams',
      killer_image_url: null,
      killer_team: 1,
      killer_is_you: false,
    },
  ],
  trades: [
    {
      player_slot: 7,
      account_id: 2,
      display_name: 'foe',
      hero_id: 7,
      hero_name: 'Abrams',
      image_url: null,
      team: 1,
      kills_by_them_on_you: 2,
      kills_by_you_on_them: 1,
    },
  ],
}

function renderMatch() {
  return renderWithProviders(
    <Routes>
      <Route path="/matches/:matchId" element={<MatchDetail />} />
    </Routes>,
    { route: '/matches/123' },
  )
}

describe('MatchDetail', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('leads with the Kill trades section, before purchases and the timeline', async () => {
    mockFetch({ ...baseRoutes, '/api/matches': matchDetail })
    renderMatch()

    // Wait for the data to resolve.
    await screen.findByRole('heading', { name: 'Kill trades' })

    // Section headings (h2) in DOM order; the match header carries no heading.
    const order = screen
      .getAllByRole('heading', { level: 2 })
      .map((h) => h.textContent)

    expect(order[0]).toBe('Kill trades')
    expect(order.indexOf('Kill trades')).toBeLessThan(order.indexOf('Your purchases'))
    expect(order.indexOf('Kill trades')).toBeLessThan(
      order.indexOf('Kill / death timeline'),
    )
  })

  it('renders a SectionNav listing each section', async () => {
    mockFetch({ ...baseRoutes, '/api/matches': matchDetail })
    renderMatch()

    await screen.findByRole('heading', { name: 'Kill trades' })
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    expect(within(nav).getAllByRole('link', { name: 'Kill trades' }).length).toBeGreaterThan(0)
    expect(within(nav).getAllByRole('link', { name: 'Teams' }).length).toBeGreaterThan(0)
  })

  it('renders the ability order strip after purchases, in point order', async () => {
    mockFetch({ ...baseRoutes, '/api/matches': matchDetail })
    renderMatch()

    const heading = await screen.findByRole('heading', { name: 'Ability order' })
    const order = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(order.indexOf('Your purchases')).toBeLessThan(order.indexOf('Ability order'))

    // Both points render, in skill-up order, with their slot and clock.
    const strip = heading.closest('section') as HTMLElement
    const names = within(strip).getAllByText(/Fixation|Smoke Bomb/).map((n) => n.textContent)
    expect(names).toEqual(['Fixation', 'Smoke Bomb'])
    expect(within(strip).getByText('signature')).toBeInTheDocument()
  })

  it('shows a muted message when a match has no ability data', async () => {
    mockFetch({ ...baseRoutes, '/api/matches': { ...matchDetail, abilities: [] } })
    renderMatch()

    await screen.findByRole('heading', { name: 'Ability order' })
    expect(
      screen.getByText(/No ability data recorded for this account/i),
    ).toBeInTheDocument()
  })
})
