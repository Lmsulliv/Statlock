-- Schema v10: materialize each player's end-of-laning snapshot into laning_stats.
--
-- Why a table: the early-game (laning) report compares a player's net worth /
-- last hits / denies at the ~10-minute mark to the live population at the same
-- mark. Those values live in the per-player stats[] time series inside raw_json
-- (one snapshot per ~180 s; see docs/api-findings.md, "Per-player stats[] time
-- series"). The baseline averages across EVERY player in EVERY match, so reading
-- it would mean re-walking ~1.5 MB of JSON per match on every request -- the same
-- problem kill_events solved. We pick the lane-end snapshot once and store it.
--
-- Which snapshot, and what last_hits means: stats.laning.laning_mark picks the
-- latest snapshot with time_stamp_s <= 600 (LANE_END_S); sampled_at_s records
-- which one, so the read layer is honest about the mark. The per-snapshot
-- last_hits field is null in the payload, so last_hits here is the snapshot's
-- creep_kills (the cumulative last-hit proxy, api-findings). A player whose match
-- never reached the lane-end mark gets no row (NULL, never a fabricated 0).
--
-- Why only the snapshot values, resolved by join: a laning_stats row carries
-- match_id + player_slot and the three metrics. Hero / account / team / lane all
-- come from joining match_players on (match_id, player_slot) at read time, so
-- nothing is denormalized here -- match_players stays the single source of truth
-- and can't drift out of step (exactly like kill_events, migration 006).
--
-- Purely additive: no table rebuild, so no foreign_keys toggling needed.

CREATE TABLE laning_stats (
    match_id     INTEGER NOT NULL REFERENCES matches(match_id),
    player_slot  INTEGER NOT NULL,
    net_worth    INTEGER,            -- cumulative net worth at the lane-end snapshot
    last_hits    INTEGER,            -- snapshot creep_kills (per-snapshot last_hits is null)
    denies       INTEGER,            -- cumulative denies at the lane-end snapshot
    sampled_at_s INTEGER,            -- the snapshot's time_stamp_s (which mark we read)
    PRIMARY KEY (match_id, player_slot)
);

CREATE INDEX idx_ls_match_slot ON laning_stats(match_id, player_slot);
