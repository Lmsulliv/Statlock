-- Schema v6: materialize per-kill attribution from death_details into kill_events.
--
-- Why a table: the per-match view already derives a kill/death feed from
-- death_details[] on every read (api.match_detail.parse_deaths). Aggregate
-- kill-trade features (who kills whom, when) would have to re-walk every match's
-- raw_json on every query, which doesn't scale. Materializing each death once
-- lets both readers use cheap indexed joins instead.
--
-- Why only slots, resolved by join: a kill_events row carries victim_slot /
-- killer_slot and FKs match_id -> matches. Hero / account / team come from
-- joining match_players on (match_id, player_slot) at read time, so nothing is
-- denormalized here -- match_players stays the single source of truth and can't
-- drift out of step with this table. killer_slot is NULL when the killer maps to
-- no roster slot (a tower or creep kill): there's nothing to join to, but the
-- event is still recorded rather than dropped.
--
-- Purely additive: no table rebuild, so no foreign_keys toggling needed.

CREATE TABLE kill_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(match_id),
    game_time_s INTEGER,
    victim_slot INTEGER NOT NULL,
    killer_slot INTEGER            -- NULL when killed by a non-player (tower/creep)
);

CREATE INDEX idx_ke_match_victim ON kill_events(match_id, victim_slot);
CREATE INDEX idx_ke_match_killer ON kill_events(match_id, killer_slot);
