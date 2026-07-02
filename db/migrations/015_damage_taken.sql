-- Schema v15: damage taken, two views from raw_json (zero-API backfill).
--
-- 1) match_players.player_damage_taken -- the net total damage you took in a
--    match, read from the LAST entry of the per-player stats[] series (the same
--    snapshot player_damage / healing already come from; docs/api-findings.md,
--    "Damage taken"). It is the net, post-mitigation number, so it rides the same
--    continuous-metric machinery as the other Performance columns: a new
--    PERF_METRICS entry (lower-is-better) against a live population baseline.
--    Nullable like the other stats[] totals -- a missing series is NULL, never 0.
--    Historical rows stay NULL until reprocess-archive backfills them from
--    raw_json (it now UPDATEs this column alongside the derived tables).
--
-- 2) damage_taken_sources -- which enemy dealt how much damage TO you, per match.
--    Sourced from match_info.damage_matrix (api-findings, "Damage taken"): each
--    (dealer_player_slot -> target_player_slot) chain carries a cumulative damage[]
--    series; we keep the final value, summed per (victim, source) pair. This is
--    GROSS, pre-mitigation output damage -- it does NOT reconcile with the net
--    player_damage_taken total -- so it backs a RELATIVE per-enemy-hero ranking on
--    the Deaths screen, never an absolute total and never a verdict.
--
--    source_slot is the dealer's player_slot; it is NULL when the dealer maps to
--    no roster player (environment: creeps / towers / boss / mid-boss), exactly
--    like kill_events.killer_slot. Hero / team are resolved at read time by
--    joining match_players on (match_id, source_slot), so nothing is denormalized
--    here and match_players stays the single source of truth (same shape as
--    kill_events migration 006 and laning_stats migration 010).
--
-- Purely additive: an ALTER ADD COLUMN and a CREATE TABLE, so no table rebuild
-- and no foreign_keys toggling needed.

ALTER TABLE match_players ADD COLUMN player_damage_taken INTEGER;

CREATE TABLE damage_taken_sources (
    match_id      INTEGER NOT NULL REFERENCES matches(match_id),
    victim_slot   INTEGER NOT NULL,   -- the player who took the damage
    source_slot   INTEGER,            -- the dealer's slot; NULL = environment/non-roster
    damage_taken  INTEGER NOT NULL,   -- gross (pre-mitigation) damage from that source
    PRIMARY KEY (match_id, victim_slot, source_slot)
);

CREATE INDEX idx_dts_match_victim ON damage_taken_sources(match_id, victim_slot);
