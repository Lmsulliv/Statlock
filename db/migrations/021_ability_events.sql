-- Schema v21: recover ability level-ups from players[].items[] into two tables.
--
-- Motivation (docs/api-findings.md, "Ability level-up entries" + "Ability
-- level-up extraction rule", spike 13): each players[].items[] entry is either a
-- shop purchase (assets type=="upgrade") or an ability point (type=="ability").
-- ingest.parse keeps only the shop ids; the ability points survive only inside
-- matches.raw_json. This migration adds the queryable home for them.
--
-- Two tables:
--
--   abilities        -- reference load, sibling of items/heroes. The items table
--                       holds only type=="upgrade" rows, so ability name / slot /
--                       icon live nowhere in the DB today. refresh_assets loads
--                       these from the SAME /v1/assets/items response (no extra
--                       request) via tracker.reference.load_abilities.
--
--   ability_events   -- one row per ability point spent, for ALL players (mirrors
--                       match_item_purchases, which materializes every player).
--                       point_number is a derived 1-based ordinal after sorting a
--                       player's kept entries by game_time_s; same-second banked
--                       points tie (order arbitrary within a tie). hero_id is the
--                       PLAYER's played hero, stored from the roster row, never
--                       inferred from the ability's heroes list. Like kill_events
--                       it uses an autoincrement surrogate + delete-then-insert, so
--                       reprocess-archive can rebuild it idempotently from raw_json.
--                       account/team/won are resolved by joining match_players on
--                       (match_id, player_slot) at read time.
--
-- Purely additive: two CREATE TABLEs and indexes, no table rebuild, so no
-- foreign_keys toggling needed.

CREATE TABLE abilities (
    ability_id   INTEGER PRIMARY KEY,   -- assets item id where type=="ability"
    name         TEXT NOT NULL,
    ability_type TEXT,                  -- slot: signature / ultimate / innate / …
    image_url    TEXT,
    fetched_at   TEXT NOT NULL          -- ISO 8601
);

CREATE TABLE ability_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL REFERENCES matches(match_id),
    player_slot  INTEGER NOT NULL,
    account_id   INTEGER NOT NULL,
    hero_id      INTEGER,               -- the player's hero; NULL never fabricated
    ability_id   INTEGER NOT NULL,      -- the ability item_id (join abilities for name)
    point_number INTEGER NOT NULL,      -- derived 1-based ordinal, by game_time_s
    game_time_s  INTEGER                -- payload's only guaranteed ordering key; may be NULL
);

CREATE INDEX idx_ae_match_slot   ON ability_events(match_id, player_slot);
CREATE INDEX idx_ae_account_hero ON ability_events(account_id, hero_id);
